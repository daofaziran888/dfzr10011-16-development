"""模板发布链路业务规则：配置快照生成、发布版本评审与数据源引用清单更新。

链路：配置模板 + 配置参数 + 数据源协议
  → 校验（字段条件、参数阈值、协议范围）
  → 生成配置快照
  → 模板发布版本（落到数据源模块的模板发布规则评审待处理队列）
  → 数据源池读取发布版本并更新模板引用清单。

判定标准覆盖条件、范围和上限，任一不满足都不允许保存；
任何参数超限都不会留下可引用的发布记录。
"""
from __future__ import annotations

import json
import threading
from datetime import date
from typing import Any

from app.store import store

SNAPSHOT_TABLE = "snapshot"
RELEASE_TABLE = "release"
REFERENCE_TABLE = "reference"
CRAWLCONF_TABLE = "crawlconf"
DATASOURCE_TABLE = "datasource"

# 参数阈值（上限）：任一参数超限都不允许保存快照、生成发布版本
PARAM_LIMITS = {"并发数": 64, "超时秒": 300, "重试次数": 10}
# 字段条件：配置参数必须包含这些字段，且取值必须是非负数字
REQUIRED_PARAM_FIELDS = ["并发数", "超时秒", "重试次数"]
SNAPSHOT_TYPES = ["全量", "差量"]
PUBLISH_MODES = ["全量发布", "差量复核"]
# 发布方式与快照类型一一对应：全量发布针对全量快照，差量复核针对差量快照
MODE_SNAPSHOT_TYPE = {"全量发布": "全量", "差量复核": "差量"}
SNAPSHOT_STATUSES = ["待评审", "已通过评审", "已发布", "已驳回"]
RELEASE_STATUSES = ["待评审", "已发布", "已驳回", "已回退"]

# 同一模板的并发发布锁：并发发布只能有一个版本成功
_locks_guard = threading.Lock()
_template_locks: dict[str, threading.Lock] = {}


def _template_lock(template_no: str) -> threading.Lock:
    with _locks_guard:
        return _template_locks.setdefault(template_no, threading.Lock())


def _today() -> str:
    return date.today().isoformat()


def _parse_params(raw: Any) -> dict[str, Any] | None:
    """配置参数统一按 JSON 对象解析；解析失败返回 None。"""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _dump_params(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, sort_keys=True)


def _parse_protocols(raw: Any) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").replace("、", ",").replace("/", ",").split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def _find_by_no(table: str, field: str, no: str) -> dict[str, Any] | None:
    for row in store.rows(table):
        if str(row.get(field, "")) == no:
            return row
    return None


def _next_no(table: str, field: str, prefix: str) -> str:
    max_no = 0
    for row in store.rows(table):
        no = str(row.get(field, ""))
        if no.startswith(f"{prefix}-"):
            try:
                max_no = max(max_no, int(no.split("-", 1)[1]))
            except ValueError:
                continue
    return f"{prefix}-{max_no + 1:04d}"


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _template_protocols(template: dict[str, Any] | None) -> list[str]:
    """模板声明的协议范围，写在配置参数的「协议范围」字段里。"""
    if template is None:
        return []
    params = _parse_params(template.get("配置参数"))
    if not params:
        return []
    return _parse_protocols(params.get("协议范围"))


def _validate_params(params: dict[str, Any]) -> list[str]:
    """参数判定：字段条件 + 参数阈值（上限）。"""
    errors: list[str] = []
    for field in REQUIRED_PARAM_FIELDS:
        if field not in params:
            errors.append(f"字段条件不满足：配置参数缺少「{field}」")
            continue
        value = params[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            errors.append(f"字段条件不满足：「{field}」必须是非负数字，当前为 {value!r}")
    for field, limit in PARAM_LIMITS.items():
        value = params.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > limit:
            errors.append(f"参数超限：「{field}」={value} 超过上限 {limit}")
    return errors


def _protocol_errors(
    template: dict[str, Any] | None,
    datasource: dict[str, Any] | None,
    protocols: list[str],
    template_no: str,
    datasource_no: str,
) -> list[str]:
    """范围判定：协议范围必须在模板声明范围内，且覆盖数据源接入协议。"""
    errors: list[str] = []
    declared = _template_protocols(template)
    access = str((datasource or {}).get("接入协议") or "").strip()
    if declared:
        outside = [item for item in protocols if item not in declared]
        if outside:
            errors.append(
                f"范围不满足：协议范围 {'、'.join(outside)} 超出模板 {template_no} "
                f"声明的协议范围 {'、'.join(declared)}"
            )
    if access:
        if protocols and access not in protocols:
            errors.append(f"范围不满足：数据源 {datasource_no} 的接入协议 {access} 不在快照协议范围内")
        if declared and access not in declared:
            errors.append(
                f"范围不满足：模板 {template_no} 与数据源 {datasource_no} 协议不匹配"
                f"（接入协议 {access} 不在模板声明范围 {'、'.join(declared)} 内）"
            )
    return errors


class ReleaseService:
    """模板发布链路：快照、发布版本、引用清单都落在数据源模块。"""

    # ---------- 配置快照 ----------

    def list_snapshots(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(SNAPSHOT_TABLE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("快照编号", "")) or keyword in str(row.get("模板编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return self._paginate(rows, page, size)

    def create_snapshot(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """从配置模板、配置参数和数据源协议生成配置快照。

        判定标准覆盖条件、范围和上限，任一不满足都不允许保存。
        """
        errors, parsed = self._validate_snapshot_payload(values)
        if errors:
            return None, errors
        row = {
            "id": _next_id(SNAPSHOT_TABLE),
            "快照编号": _next_no(SNAPSHOT_TABLE, "快照编号", "SNAP"),
            "模板编号": parsed["模板编号"],
            "条目编号": parsed["条目编号"],
            "快照类型": parsed["快照类型"],
            "配置参数": _dump_params(parsed["params"]),
            "协议范围": ",".join(parsed["protocols"]),
            "校验结论": "通过",
            "校验明细": "字段条件满足；参数均未超限；协议范围与数据源接入协议匹配",
            "status": "待评审",
            "pending": True,
            "abnormal": False,
            "创建时间": _today(),
        }
        store.rows(SNAPSHOT_TABLE).append(row)
        return row, []

    def review_snapshot(self, snap_no: str, decision: str) -> tuple[dict[str, Any] | None, str]:
        row = _find_by_no(SNAPSHOT_TABLE, "快照编号", snap_no)
        if row is None:
            return None, f"配置快照 {snap_no} 不存在"
        if row.get("status") != "待评审":
            return None, f"配置快照 {snap_no} 当前状态为「{row.get('status')}」，仅待评审快照可评审"
        if decision == "通过":
            row["status"] = "已通过评审"
            row["pending"] = True
            row["abnormal"] = False
            return row, f"配置快照 {snap_no} 已通过评审，可绑定发布版本"
        if decision == "驳回":
            row["status"] = "已驳回"
            row["pending"] = False
            row["abnormal"] = True
            return row, f"配置快照 {snap_no} 已驳回，不可绑定发布版本"
        return None, f"评审结论「{decision}」不支持，只能是 通过/驳回"

    def _validate_snapshot_payload(self, values: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        template_no = str(values.get("模板编号") or "").strip()
        datasource_no = str(values.get("条目编号") or "").strip()
        snap_type = str(values.get("快照类型") or "").strip()
        template = _find_by_no(CRAWLCONF_TABLE, "模板编号", template_no) if template_no else None
        datasource = _find_by_no(DATASOURCE_TABLE, "条目编号", datasource_no) if datasource_no else None
        if not template_no:
            errors.append("字段条件不满足：缺少「模板编号」")
        elif template is None:
            errors.append(f"字段条件不满足：配置模板 {template_no} 不存在")
        if not datasource_no:
            errors.append("字段条件不满足：缺少「条目编号」")
        elif datasource is None:
            errors.append(f"字段条件不满足：数据源条目 {datasource_no} 不存在")
        if snap_type not in SNAPSHOT_TYPES:
            errors.append(f"字段条件不满足：快照类型必须是 {'/'.join(SNAPSHOT_TYPES)}")
        params = _parse_params(values.get("配置参数"))
        if params is None:
            errors.append("字段条件不满足：配置参数必须是 JSON 对象")
            params = {}
        else:
            errors.extend(_validate_params(params))
        protocols = _parse_protocols(values.get("协议范围"))
        if not protocols:
            errors.append("范围不满足：协议范围不能为空")
        if template is not None and datasource is not None:
            errors.extend(_protocol_errors(template, datasource, protocols, template_no, datasource_no))
        parsed = {
            "模板编号": template_no,
            "条目编号": datasource_no,
            "快照类型": snap_type,
            "params": params,
            "protocols": protocols,
        }
        return errors, parsed

    # ---------- 模板发布版本 ----------

    def list_releases(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(RELEASE_TABLE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("版本编号", "")) or keyword in str(row.get("模板编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return self._paginate(rows, page, size)

    def review_queue(self) -> list[dict[str, Any]]:
        """模板发布规则评审待处理队列：待评审的发布版本，附快照信息。"""
        queue: list[dict[str, Any]] = []
        for row in store.rows(RELEASE_TABLE):
            if row.get("status") != "待评审":
                continue
            snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", str(row.get("绑定快照", "")))
            item = dict(row)
            item["快照类型"] = (snapshot or {}).get("快照类型", "—")
            item["校验结论"] = (snapshot or {}).get("校验结论", "—")
            queue.append(item)
        return queue

    def latest_releases(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = sorted(store.rows(RELEASE_TABLE), key=lambda row: int(row.get("id", 0)), reverse=True)
        return rows[:limit]

    def create_release(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """创建模板发布版本：只能绑定一份通过评审的配置快照。

        协议不匹配（如 CRAW-0002 × DATA-0002）不得生成新版本；
        并发发布只能有一个版本成功；参数超限不留可引用记录。
        """
        template_no = str(values.get("模板编号") or "").strip()
        datasource_no = str(values.get("条目编号") or "").strip()
        snap_no = str(values.get("快照编号") or "").strip()
        mode = str(values.get("发布方式") or "").strip()
        missing = [
            name for name, val in (("模板编号", template_no), ("条目编号", datasource_no), ("快照编号", snap_no))
            if not val
        ]
        if missing:
            return None, f"字段条件不满足：缺少 {'、'.join(missing)}"
        lock = _template_lock(template_no)
        if not lock.acquire(blocking=False):
            return None, f"模板 {template_no} 存在进行中的发布，并发发布仅允许一个版本成功"
        try:
            template = _find_by_no(CRAWLCONF_TABLE, "模板编号", template_no)
            if template is None:
                return None, f"配置模板 {template_no} 不存在"
            datasource = _find_by_no(DATASOURCE_TABLE, "条目编号", datasource_no)
            if datasource is None:
                return None, f"数据源条目 {datasource_no} 不存在"
            rollback = self._current_rollback(template_no, datasource_no)
            # 协议不匹配不得生成新版本，失败后保留上一份可回退快照
            declared = _template_protocols(template)
            access = str(datasource.get("接入协议") or "").strip()
            if declared and access and access not in declared:
                return None, (
                    f"模板 {template_no} 与数据源 {datasource_no} 协议不匹配"
                    f"（接入协议 {access} 不在模板声明范围 {'、'.join(declared)} 内），"
                    f"未生成新版本；保留上一份可回退快照 {rollback}"
                )
            if mode not in PUBLISH_MODES:
                return None, f"发布方式必须是 {'/'.join(PUBLISH_MODES)}"
            for row in store.rows(RELEASE_TABLE):
                if row.get("模板编号") == template_no and row.get("status") == "待评审":
                    return None, (
                        f"模板 {template_no} 已存在待评审版本 {row.get('版本编号')}，"
                        "并发发布仅允许一个版本成功"
                    )
            snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", snap_no)
            if snapshot is None:
                return None, f"配置快照 {snap_no} 不存在"
            if snapshot.get("status") != "已通过评审":
                return None, (
                    f"发布版本只能绑定一份通过评审的配置快照，"
                    f"快照 {snap_no} 当前状态为「{snapshot.get('status')}」"
                )
            if snapshot.get("模板编号") != template_no or snapshot.get("条目编号") != datasource_no:
                return None, f"快照 {snap_no} 不属于模板 {template_no} 与数据源 {datasource_no} 的组合"
            # 参数复验：任何参数超限都不能留下可引用的发布记录
            params = _parse_params(snapshot.get("配置参数")) or {}
            param_errors = _validate_params(params)
            if param_errors:
                return None, f"{'；'.join(param_errors)}，未生成新版本；保留上一份可回退快照 {rollback}"
            snap_protocols = _parse_protocols(snapshot.get("协议范围"))
            if access and access not in snap_protocols:
                return None, (
                    f"快照 {snap_no} 协议范围 {'、'.join(snap_protocols)} 不覆盖数据源接入协议 {access}，"
                    f"未生成新版本；保留上一份可回退快照 {rollback}"
                )
            for row in store.rows(RELEASE_TABLE):
                if row.get("绑定快照") == snap_no and row.get("status") in ("待评审", "已发布"):
                    return None, f"快照 {snap_no} 已绑定有效版本 {row.get('版本编号')}，一份快照只能绑定一个有效版本"
            if MODE_SNAPSHOT_TYPE[mode] != snapshot.get("快照类型"):
                return None, f"发布方式「{mode}」需要{MODE_SNAPSHOT_TYPE[mode]}快照，快照 {snap_no} 为「{snapshot.get('快照类型')}」"
            if mode == "差量复核" and not self._published_snapshot(template_no):
                return None, f"模板 {template_no} 没有已发布快照，差量复核缺少基准版本"
            row = {
                "id": _next_id(RELEASE_TABLE),
                "版本编号": _next_no(RELEASE_TABLE, "版本编号", "REL"),
                "模板编号": template_no,
                "条目编号": datasource_no,
                "绑定快照": snap_no,
                "发布方式": mode,
                "回退点快照": rollback,
                "评审结论": "—",
                "status": "待评审",
                "pending": True,
                "abnormal": False,
                "创建时间": _today(),
                "发布时间": "—",
            }
            store.rows(RELEASE_TABLE).append(row)
            return row, f"发布版本 {row['版本编号']} 已进入模板发布规则评审待处理队列"
        finally:
            lock.release()

    def review_release(self, rel_no: str, decision: str) -> tuple[dict[str, Any] | None, str]:
        """模板发布规则评审：通过后数据源池更新模板引用清单。"""
        row = _find_by_no(RELEASE_TABLE, "版本编号", rel_no)
        if row is None:
            return None, f"发布版本 {rel_no} 不存在"
        if row.get("status") != "待评审":
            return None, f"发布版本 {rel_no} 当前状态为「{row.get('status')}」，仅待评审版本可评审"
        if decision == "驳回":
            row["status"] = "已驳回"
            row["评审结论"] = "驳回"
            row["pending"] = False
            row["abnormal"] = True
            return row, f"发布版本 {rel_no} 已驳回，引用清单未改动，保留上一份可回退快照 {row.get('回退点快照')}"
        if decision != "通过":
            return None, f"评审结论「{decision}」不支持，只能是 通过/驳回"
        template_no = str(row.get("模板编号", ""))
        datasource_no = str(row.get("条目编号", ""))
        snap_no = str(row.get("绑定快照", ""))
        # 发布规则评审复验：条件、范围、上限
        template = _find_by_no(CRAWLCONF_TABLE, "模板编号", template_no)
        datasource = _find_by_no(DATASOURCE_TABLE, "条目编号", datasource_no)
        snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", snap_no)
        errors: list[str] = []
        if template is None:
            errors.append(f"条件不满足：配置模板 {template_no} 不存在")
        if datasource is None:
            errors.append(f"条件不满足：数据源条目 {datasource_no} 不存在")
        if snapshot is None:
            errors.append(f"条件不满足：绑定快照 {snap_no} 不存在")
        if snapshot is not None:
            params = _parse_params(snapshot.get("配置参数")) or {}
            errors.extend(_validate_params(params))
        if template is not None and datasource is not None and snapshot is not None:
            errors.extend(
                _protocol_errors(
                    template, datasource, _parse_protocols(snapshot.get("协议范围")), template_no, datasource_no
                )
            )
        if errors:
            row["status"] = "已驳回"
            row["评审结论"] = "驳回"
            row["pending"] = False
            row["abnormal"] = True
            return row, f"评审未通过：{'；'.join(errors)}；未生成可引用发布记录，保留上一份可回退快照 {row.get('回退点快照')}"
        with _template_lock(template_no):
            row["status"] = "已发布"
            row["评审结论"] = "通过"
            row["pending"] = False
            row["abnormal"] = False
            row["发布时间"] = _today()
            if snapshot is not None:
                snapshot["status"] = "已发布"
                snapshot["pending"] = False
            reference = self._apply_to_reference(row, snapshot or {})
            row["回退点快照"] = reference["回退点快照"]
        return row, (
            f"发布版本 {rel_no} 评审通过已发布，数据源引用清单 {reference['清单编号']} "
            f"已按「{row.get('发布方式')}」更新，旧引用任务继续读取原快照"
        )

    def _apply_to_reference(self, release: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """数据源池读取发布版本并更新模板引用清单。

        差量复核：只切换差量条目，回退点仍指向旧快照；
        全量发布：引用清单整体更新，回退点改为上一完整草稿版本。
        """
        template_no = str(release.get("模板编号", ""))
        datasource_no = str(release.get("条目编号", ""))
        snap_no = str(release.get("绑定快照", ""))
        mode = str(release.get("发布方式", ""))
        params = _parse_params(snapshot.get("配置参数")) or {}
        reference = self._find_reference(template_no, datasource_no)
        if reference is None:
            reference = {
                "id": _next_id(REFERENCE_TABLE),
                "清单编号": _next_no(REFERENCE_TABLE, "清单编号", "REF"),
                "条目编号": datasource_no,
                "模板编号": template_no,
                "当前发布版本": "—",
                "当前快照": "—",
                "回退点快照": snap_no,
                "引用条目": "{}",
                "最近切换条目": "—",
                "更新方式": "—",
                "引用任务": "—",
                "核对结果": "待核对",
                "status": "生效中",
                "pending": False,
                "abnormal": False,
                "更新时间": "—",
            }
            store.rows(REFERENCE_TABLE).append(reference)
        old_current = str(reference.get("当前快照", "—"))
        if mode == "差量复核":
            old_items = _parse_params(reference.get("引用条目")) or {}
            diff_keys = [key for key, value in params.items() if old_items.get(key) != value]
            merged = {**old_items, **params}
            reference["引用条目"] = _dump_params(merged)
            reference["最近切换条目"] = ",".join(diff_keys) if diff_keys else "—"
            # 回退点仍指向旧快照：不改动 reference["回退点快照"]
        else:
            reference["引用条目"] = _dump_params(params)
            reference["最近切换条目"] = ",".join(params.keys())
            # 回退点改为上一完整草稿版本：被替换的当前快照是全量时才移动回退点，
            # 当前为差量快照则回退点保持指向上一份完整版本
            old_snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", old_current)
            if old_snapshot is not None and old_snapshot.get("快照类型") == "全量":
                reference["回退点快照"] = old_current
        reference["当前发布版本"] = release.get("版本编号", "—")
        reference["当前快照"] = snap_no
        reference["更新方式"] = mode
        reference["核对结果"] = "待核对"
        reference["status"] = "生效中"
        reference["pending"] = False
        reference["abnormal"] = False
        reference["更新时间"] = _today()
        # 引用任务保持原快照绑定：旧引用的任务继续读取原快照
        return reference

    # ---------- 模板引用清单 ----------

    def list_references(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(REFERENCE_TABLE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("清单编号", "")) or keyword in str(row.get("模板编号", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return self._paginate(rows, page, size)

    def reconcile_reference(self, ref_no: str) -> tuple[dict[str, Any] | None, str]:
        """数据源引用清单核对发布版本和配置快照。"""
        reference = _find_by_no(REFERENCE_TABLE, "清单编号", ref_no)
        if reference is None:
            return None, f"模板引用清单 {ref_no} 不存在"
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, note: str) -> None:
            checks.append({"名称": name, "通过": ok, "说明": note})

        release = _find_by_no(RELEASE_TABLE, "版本编号", str(reference.get("当前发布版本", "")))
        add(
            "发布版本有效",
            release is not None and release.get("status") == "已发布",
            f"当前发布版本 {reference.get('当前发布版本')} 状态：{(release or {}).get('status', '缺失')}",
        )
        snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", str(reference.get("当前快照", "")))
        add(
            "版本绑定快照一致",
            release is not None and release.get("绑定快照") == reference.get("当前快照"),
            f"版本绑定快照 {(release or {}).get('绑定快照', '—')}，清单当前快照 {reference.get('当前快照')}",
        )
        add(
            "快照校验通过",
            snapshot is not None and snapshot.get("校验结论") == "通过" and snapshot.get("status") in ("已通过评审", "已发布"),
            f"快照 {reference.get('当前快照')} 校验结论 {(snapshot or {}).get('校验结论', '缺失')}，状态 {(snapshot or {}).get('status', '—')}",
        )
        params = _parse_params((snapshot or {}).get("配置参数")) or {}
        param_errors = _validate_params(params)
        add("参数未超限", not param_errors, "；".join(param_errors) if param_errors else "全部参数在上限内")
        template = _find_by_no(CRAWLCONF_TABLE, "模板编号", str(reference.get("模板编号", "")))
        datasource = _find_by_no(DATASOURCE_TABLE, "条目编号", str(reference.get("条目编号", "")))
        protocol_errors = _protocol_errors(
            template,
            datasource,
            _parse_protocols((snapshot or {}).get("协议范围")),
            str(reference.get("模板编号", "")),
            str(reference.get("条目编号", "")),
        )
        add("协议范围匹配", not protocol_errors, "；".join(protocol_errors) if protocol_errors else "接入协议在模板声明范围内")
        items = _parse_params(reference.get("引用条目")) or {}
        uncovered = [key for key, value in params.items() if items.get(key) != value]
        add(
            "引用条目覆盖快照参数",
            not uncovered,
            "全部一致" if not uncovered else f"条目 {'、'.join(uncovered)} 与快照参数不一致",
        )
        ok = all(check["通过"] for check in checks)
        reference["核对结果"] = "一致" if ok else "异常"
        reference["abnormal"] = not ok
        return {
            "清单编号": ref_no,
            "结果": reference["核对结果"],
            "检查项": checks,
            "引用任务": self._task_pins(reference),
        }, f"引用清单 {ref_no} 核对完成：{reference['核对结果']}"

    def rollback_reference(self, ref_no: str) -> tuple[dict[str, Any] | None, str]:
        """回退到回退点快照；旧引用任务继续读取原快照。"""
        reference = _find_by_no(REFERENCE_TABLE, "清单编号", ref_no)
        if reference is None:
            return None, f"模板引用清单 {ref_no} 不存在"
        rollback_snap = str(reference.get("回退点快照", ""))
        snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", rollback_snap)
        if snapshot is None:
            return None, f"回退点快照 {rollback_snap} 不存在，无法回退"
        target = None
        for row in store.rows(RELEASE_TABLE):
            if row.get("绑定快照") == rollback_snap and row.get("status") == "已发布":
                target = row
        if target is None:
            return None, f"回退点快照 {rollback_snap} 没有已发布的版本可回退"
        current = _find_by_no(RELEASE_TABLE, "版本编号", str(reference.get("当前发布版本", "")))
        if current is not None and current.get("版本编号") != target.get("版本编号") and current.get("status") == "已发布":
            current["status"] = "已回退"
        params = _parse_params(snapshot.get("配置参数")) or {}
        reference["当前快照"] = rollback_snap
        reference["当前发布版本"] = target.get("版本编号", "—")
        reference["引用条目"] = _dump_params(params)
        reference["最近切换条目"] = ",".join(params.keys())
        reference["更新方式"] = "回退恢复"
        reference["核对结果"] = "待核对"
        reference["status"] = "生效中"
        reference["abnormal"] = False
        reference["更新时间"] = _today()
        return reference, f"引用清单 {ref_no} 已回退到快照 {rollback_snap}，旧引用任务继续读取原快照"

    def reference_tasks(self, ref_no: str) -> tuple[list[dict[str, Any]] | None, str]:
        reference = _find_by_no(REFERENCE_TABLE, "清单编号", ref_no)
        if reference is None:
            return None, f"模板引用清单 {ref_no} 不存在"
        return self._task_pins(reference), f"引用清单 {ref_no} 的任务引用读取正常"

    def _task_pins(self, reference: dict[str, Any]) -> list[dict[str, Any]]:
        """引用任务按绑定时的快照读取：旧引用的任务继续读取原快照。"""
        pins: list[dict[str, Any]] = []
        raw = str(reference.get("引用任务", "") or "")
        for item in raw.split(";"):
            item = item.strip()
            if not item or item == "—" or "@" not in item:
                continue
            task_no, snap_no = item.split("@", 1)
            snapshot = _find_by_no(SNAPSHOT_TABLE, "快照编号", snap_no.strip())
            pins.append({
                "任务编号": task_no.strip(),
                "快照编号": snap_no.strip(),
                "快照状态": (snapshot or {}).get("status", "缺失"),
                "配置参数": (snapshot or {}).get("配置参数", "—"),
            })
        return pins

    # ---------- 汇总 ----------

    def summary(self) -> dict[str, Any]:
        """模板发布规则评审汇总：供数据源模块与其它业务入口同步读取。"""
        snapshots = store.rows(SNAPSHOT_TABLE)
        releases = store.rows(RELEASE_TABLE)
        references = store.rows(REFERENCE_TABLE)

        def count(rows: list[dict[str, Any]], status: str) -> int:
            return sum(1 for row in rows if row.get("status") == status)

        timestamps = [
            str(row.get("创建时间", "")) for row in snapshots + releases
        ] + [str(row.get("更新时间", "")) for row in references]
        timestamps = [item for item in timestamps if item and item != "—"]
        return {
            "配置快照": {
                "总数": len(snapshots),
                "待评审": count(snapshots, "待评审"),
                "已通过评审": count(snapshots, "已通过评审"),
                "已发布": count(snapshots, "已发布"),
                "已驳回": count(snapshots, "已驳回"),
            },
            "模板发布版本": {
                "总数": len(releases),
                "待评审": count(releases, "待评审"),
                "已发布": count(releases, "已发布"),
                "已驳回": count(releases, "已驳回"),
                "已回退": count(releases, "已回退"),
            },
            "评审待处理队列": count(releases, "待评审"),
            "模板引用清单": {
                "总数": len(references),
                "一致": sum(1 for row in references if row.get("核对结果") == "一致"),
                "待核对": sum(1 for row in references if row.get("核对结果") == "待核对"),
                "异常": sum(1 for row in references if row.get("核对结果") == "异常"),
            },
            "最近更新": max(timestamps) if timestamps else "—",
        }

    def dashboard_feed(self) -> dict[str, Any]:
        """其它业务入口同步读取：评审汇总 + 待处理队列 + 发布版本数据。"""
        return {
            "summary": self.summary(),
            "queue": self.review_queue(),
            "versions": self.latest_releases(5),
        }

    # ---------- 内部 ----------

    def _find_reference(self, template_no: str, datasource_no: str) -> dict[str, Any] | None:
        for row in store.rows(REFERENCE_TABLE):
            if row.get("模板编号") == template_no and row.get("条目编号") == datasource_no:
                return row
        return None

    def _published_snapshot(self, template_no: str) -> dict[str, Any] | None:
        for row in store.rows(SNAPSHOT_TABLE):
            if row.get("模板编号") == template_no and row.get("status") == "已发布":
                return row
        return None

    def _current_rollback(self, template_no: str, datasource_no: str) -> str:
        reference = self._find_reference(template_no, datasource_no)
        if reference is not None and str(reference.get("回退点快照", "—")) != "—":
            return str(reference["回退点快照"])
        published = self._published_snapshot(template_no)
        if published is not None:
            return str(published.get("快照编号"))
        return "—"

    @staticmethod
    def _paginate(rows: list[dict[str, Any]], page: int, size: int) -> tuple[list[dict[str, Any]], int]:
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total
