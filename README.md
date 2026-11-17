# 分布式采集调度引擎

面向大规模数据采集团队的分布式采集调度引擎。覆盖采集节点集群管理、任务分片调度、结果聚合归并、节点健康监控、采集流量均衡、断点续采恢复与全局采集看板的完整分布式采集链路。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 采集节点集群 | `cluster` | 集群节点 | 节点编号、节点地址、节点角色 |
| 任务分片调度 | `shard` | 分片任务 | 分片编号、采集计划、分片范围 |
| 结果聚合归并 | `merge` | 聚合任务 | 任务编号、采集计划、分片总数 |
| 节点状态巡检 | `nodecheck` | 巡检记录 | 记录编号、节点编号、CPU使用率 |
| 采集流量均衡 | `balance` | 均衡策略 | 策略编号、负载算法、权重因子 |
| 断点续采恢复 | `resume` | 断点记录 | 断点编号、采集计划、断点位置 |
| 全局采集看板 | `dashboard` | 看板面板 | 面板编号、面板名称、显示指标 |
| 采集配置模板 | `crawlconf` | 配置模板 | 模板编号、模板名称、目标类型 |
| 采集数据源池 | `datasource` | 数据源条目 | 条目编号、数据源名称、源类型 |
| 模板发布链路 | `snapshot` / `release` / `reference` | 配置快照、模板发布版本、模板引用清单 | 快照编号、版本编号、清单编号 |
| 采集日志解析 | `logparse` | 日志模板 | 模板编号、日志类型、解析规则 |
| 采集频率控制 | `ratelimit` | 频率规则 | 规则编号、目标站点、请求间隔 |
| 采集代理路由 | `proxy` | 代理配置 | 配置编号、代理地址、代理协议 |
| RPA执行记录 | `robot` | 执行记录 | 记录编号、流程模板、执行节点 |
| 采集结果校验 | `verify` | 校验规则 | 规则编号、采集计划、校验类型 |
| 采集操作审计 | `audit` | 审计记录 | 记录编号、操作类型、操作对象 |
| 采集存储分层 | `storage` | 存储策略 | 策略编号、存储层级、文件格式 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 模板发布链路

配置模板 + 配置参数 + 数据源协议 → 校验（字段条件、参数阈值、协议范围）→ 配置快照
→ 模板发布版本 → 数据源引用清单。链路数据落在数据源模块，业务规则在
`backend/app/services/release.py`，接口挂在 `/api/datasource/release/...`：

- `POST /release/snapshots` 生成配置快照：判定标准覆盖条件、范围和上限，任一不满足不允许保存。
- `POST /release/snapshots/{快照编号}/review` 快照评审；发布版本只能绑定一份通过评审的配置快照。
- `POST /release/versions` 创建发布版本：落到模板发布规则评审待处理队列（`GET /release/queue`）；
  协议不匹配（如 CRAW-0002 × DATA-0002）不得生成新版本；并发发布只能有一个版本成功；
  任何参数超限都不留可引用记录，失败后保留上一份可回退快照。
- `POST /release/versions/{版本编号}/review` 发布规则评审：通过后数据源池更新模板引用清单——
  差量复核只切换差量条目、回退点仍指向旧快照；全量发布整体更新、回退点改为上一完整草稿版本；
  旧引用的任务继续读取原快照。
- `GET /release/references/{清单编号}/reconcile` 引用清单核对发布版本和配置快照；
  `POST /release/references/{清单编号}/rollback` 回退到回退点快照。
- `GET /release/summary` 模板发布规则评审汇总；看板模块经
  `GET /api/dashboard/release-summary` 同步取到汇总与发布版本数据。

领域：数据工程
子方向：数据采集、爬虫与RPA
