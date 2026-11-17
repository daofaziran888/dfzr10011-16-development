"""采集数据源池接口：维护数据源条目，覆盖接入源、暂停源、移除源等动作。

同时承接模板发布链路：配置快照生成与评审、模板发布版本评审待处理队列、
数据源引用清单的核对与回退。发布链路数据都落在数据源模块。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.datasource import DatasourceService
from app.services.release import ReleaseService

router = APIRouter(prefix="/api/datasource", tags=["采集数据源池"])

service = DatasourceService()
release_service = ReleaseService()

LIST_FIELDS = ["条目编号", "数据源名称", "源类型", "接入协议", "认证方式", "优先级", "最近采集", "条目状态"]
STATUSES = ["草稿", "已接入", "已暂停", "已移除"]
SNAPSHOT_STATUSES = ["待评审", "已通过评审", "已发布", "已驳回"]
RELEASE_STATUSES = ["待评审", "已发布", "已驳回", "已回退"]


# ---------- 模板发布链路（静态路径，需先于 /{entry_id} 声明） ----------

@router.get("/release/summary")
def release_summary() -> dict[str, Any]:
    """模板发布规则评审汇总：快照、发布版本、待处理队列与引用清单的汇总口径。"""
    return release_service.summary()


@router.get("/release/queue")
def release_queue() -> dict[str, Any]:
    """模板发布规则评审待处理队列：待评审的发布版本及其快照信息。"""
    items = release_service.review_queue()
    return {"items": items, "total": len(items)}


@router.get("/release/snapshots", response_model=PageResult[dict])
def list_snapshots(
    keyword: str | None = Query(default=None, description="按快照编号或模板编号检索"),
    status: str | None = Query(default=None, description="待评审、已通过评审、已发布、已驳回"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """配置快照列表：从配置模板、配置参数和数据源协议校验生成。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = release_service.list_snapshots(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/release/snapshots", response_model=ActionResult)
def create_snapshot(payload: EntryPayload) -> ActionResult:
    """生成配置快照：判定标准覆盖条件、范围和上限，任一不满足都不允许保存。"""
    row, errors = release_service.create_snapshot(payload.values)
    if errors:
        return ActionResult(ok=False, message="；".join(errors))
    assert row is not None
    return ActionResult(ok=True, message=f"配置快照 {row['快照编号']} 已生成，进入评审", entry=row)


@router.post("/release/snapshots/{snap_no}/review", response_model=ActionResult)
def review_snapshot(snap_no: str, payload: EntryPayload) -> ActionResult:
    """快照评审：通过后才能绑定发布版本；驳回的快照不可引用。"""
    decision = str(payload.values.get("结论") or "").strip()
    row, message = release_service.review_snapshot(snap_no, decision)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/release/versions", response_model=PageResult[dict])
def list_release_versions(
    keyword: str | None = Query(default=None, description="按版本编号或模板编号检索"),
    status: str | None = Query(default=None, description="待评审、已发布、已驳回、已回退"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """模板发布版本数据：每个版本只绑定一份通过评审的配置快照。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = release_service.list_releases(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/release/versions", response_model=ActionResult)
def create_release_version(payload: EntryPayload) -> ActionResult:
    """创建发布版本：协议不匹配（如 CRAW-0002 × DATA-0002）不得生成新版本；
    并发发布只能有一个版本成功；参数超限不留可引用记录。"""
    row, message = release_service.create_release(payload.values)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.post("/release/versions/{rel_no}/review", response_model=ActionResult)
def review_release_version(rel_no: str, payload: EntryPayload) -> ActionResult:
    """发布规则评审：通过后数据源池更新模板引用清单；驳回则保留上一份可回退快照。"""
    decision = str(payload.values.get("结论") or "").strip()
    row, message = release_service.review_release(rel_no, decision)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/release/references", response_model=PageResult[dict])
def list_references(
    keyword: str | None = Query(default=None, description="按清单编号或模板编号检索"),
    status: str | None = Query(default=None, description="生效中等状态"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """模板引用清单：数据源池按发布版本更新的引用口径。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = release_service.list_references(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/release/references/{ref_no}/reconcile")
def reconcile_reference(ref_no: str) -> dict[str, Any]:
    """核对：引用清单逐项核对当前发布版本与配置快照，结果写回清单。"""
    result, message = release_service.reconcile_reference(ref_no)
    if result is None:
        raise HTTPException(status_code=404, detail=message)
    return result


@router.post("/release/references/{ref_no}/rollback", response_model=ActionResult)
def rollback_reference(ref_no: str) -> ActionResult:
    """回退：引用清单切回回退点快照，旧引用任务继续读取原快照。"""
    row, message = release_service.rollback_reference(ref_no)
    if row is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=row)


@router.get("/release/references/{ref_no}/tasks")
def reference_tasks(ref_no: str) -> dict[str, Any]:
    """任务引用读取：旧引用的任务继续读取原快照。"""
    pins, message = release_service.reference_tasks(ref_no)
    if pins is None:
        raise HTTPException(status_code=404, detail=message)
    return {"清单编号": ref_no, "引用任务": pins, "message": message}


# ---------- 数据源条目 ----------

@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集数据源池清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "datasource", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按条目编号检索"),
    status: str | None = Query(default=None, description="草稿、已接入、已暂停、已移除"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按条目编号与状态过滤采集数据源池列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条数据源条目明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"数据源条目 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条数据源条目，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="数据源条目已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条数据源条目执行接入源、暂停源、移除源；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
