# 量化模型发布 + 回测/实盘按需推理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究工厂可发布「量化模型」；回测/实盘选用后按策略调仓日异步准备分数（bridge infer）再执行策略。

**Architecture:** `qd_quant_models` 存配方元数据并绑定 `(alpha_source, alpha_version)`；`ensure_scores` 对缺失 as_of 调用已有 `infer_and_import_session_scores`；回测经 Celery/`qd_agent_jobs`（或等价后台任务）先 prepare 再跑 `StrategyV2BacktestService`；实盘在调仓回调前 hook ensure，失败则跳过下单。

**Tech Stack:** Flask Human API、Postgres、Celery（已有 agent_jobs）、Vue 2、rdagent-bridge `/v1/infer`、pytest

**Spec:** `docs/superpowers/specs/2026-08-04-quant-model-backtest-design.md`

## Global Constraints

- 分数只写入 `qd_external_alpha_scores`；不改 `get_external_alpha_scores` PIT 语义，禁止沙箱内同步懒推理
- Bridge 推理经现有 `RdAgentBridgeClient.infer_session` / `infer_and_import_session_scores`；强制 `version=model.alpha_version`
- 实盘推理失败：**跳过调仓** + 告警；默认不沿用过期分
- 纸交易与实盘均可 ensure，但必须经过现有 live/paper 与 `_preflight_live_strategy`
- Commit message：中文 Conventional Commits；**仅在用户明确要求时 git commit**
- 分期：P0 注册表+发布 → P1 回测异步准备 → P2 实盘 hook

## File Map

| 文件 | 职责 |
|------|------|
| `backend_api_python/migrations/20260804_quant_models.sql` | `qd_quant_models` DDL |
| `backend_api_python/migrations/init.sql` | 同步建表片段 |
| `backend_api_python/app/services/quant_models/store.py` | CRUD / publish / list |
| `backend_api_python/app/services/quant_models/schedule.py` | 调仓日 / as_of 展开 |
| `backend_api_python/app/services/quant_models/ensure_scores.py` | 缺日检测 + infer 导入 |
| `backend_api_python/app/services/quant_models/jobs.py` | prepare_and_backtest 任务体 |
| `backend_api_python/app/routes/quant_models.py` | Human API |
| `backend_api_python/app/routes/backtest_center.py` | `run-with-model` / job 查询 |
| `backend_api_python/app/openapi/register.py` | 注册蓝图 |
| `backend_api_python/app/tasks/quant_model_jobs.py` | Celery task 入口 |
| `QuantDinger-Vue/src/api/quantModels.js` | 前端 API |
| `QuantDinger-Vue/src/views/rdagent/index.vue` | 发布 UI |
| `QuantDinger-Vue/src/views/backtest-center/index.vue` | 选模型 + 任务进度 |
| `backend_api_python/app/services/trading_executor.py`（或实盘调度入口） | 调仓前 ensure hook |
| `docs/examples/strategy_v2_external_alpha_score_weekly.py` | 可选注释：可用 model_key |
| `backend_api_python/tests/test_quant_models_*.py` | 单测 |

---

## Phase P0 — 注册表 + 发布 + 列表

### Task 1: Migration `qd_quant_models`

**Files:**
- Create: `backend_api_python/migrations/20260804_quant_models.sql`
- Modify: `backend_api_python/migrations/init.sql`（追加同等 DDL）

- [ ] **Step 1: 写入 DDL**

```sql
CREATE TABLE IF NOT EXISTS qd_quant_models (
  id               BIGSERIAL PRIMARY KEY,
  model_key        VARCHAR(80)  NOT NULL,
  display_name     VARCHAR(200) NOT NULL,
  status           VARCHAR(20)  NOT NULL DEFAULT 'draft',
  kind             VARCHAR(20)  NOT NULL,
  alpha_source     VARCHAR(80)  NOT NULL,
  alpha_version    VARCHAR(120) NOT NULL,
  universe         VARCHAR(80)  NOT NULL DEFAULT 'csi300',
  owner_user_id    BIGINT,
  provenance_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
  metrics_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  published_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (model_key),
  UNIQUE (alpha_source, alpha_version)
);
CREATE INDEX IF NOT EXISTS idx_qd_quant_models_published
  ON qd_quant_models (status, published_at DESC);
```

- [ ] **Step 2: 在运行中的 Postgres 执行 migration**（docker exec 或项目既有 migrate 脚本）

- [ ] **Step 3: 验证**

```bash
docker exec quantdinger-db psql -U postgres -d quantdinger -c '\d qd_quant_models'
```

Expected: 表存在且含 UNIQUE(model_key)

---

### Task 2: Store — publish / list / archive

**Files:**
- Create: `backend_api_python/app/services/quant_models/__init__.py`
- Create: `backend_api_python/app/services/quant_models/store.py`
- Test: `backend_api_python/tests/test_quant_models_store.py`

**Interfaces:**
- Produces:
  - `publish_quant_model(*, display_name, kind, session_id, loop_index, universe, owner_user_id, model_key=None, alpha_source="rdagent", metrics=None) -> dict`
  - `list_quant_models(*, status="published", owner_user_id=None) -> list[dict]`
  - `get_quant_model(model_key: str) -> dict | None`
  - `archive_quant_model(model_key: str) -> dict`
- `alpha_version` 默认：`qm_{model_key}`；`model_key` 默认由 `session_id`+`loop`+`kind` slug 生成（截断至 80）

- [ ] **Step 1: 写失败单测（mock db）** — `test_publish_rejects_bad_kind`、`test_publish_sets_alpha_version`

- [ ] **Step 2: 实现 store.py 最小 CRUD**

- [ ] **Step 3: 跑测**

```bash
cd backend_api_python && python -m pytest tests/test_quant_models_store.py -q
```

Expected: PASS

---

### Task 3: Human API `/api/quant-models`

**Files:**
- Create: `backend_api_python/app/routes/quant_models.py`
- Modify: `backend_api_python/app/openapi/register.py`
- Test: `backend_api_python/tests/test_quant_models_routes.py`（mock store）

**Interfaces:**
- `POST /api/quant-models/publish` `@login_required` `@admin_required`
- `GET /api/quant-models?status=published`
- `POST /api/quant-models/<model_key>/archive`

- [ ] **Step 1: 路由单测（未登录 401 / 发布成功）**

- [ ] **Step 2: 实现并 register blueprint prefix `/api/quant-models`**

- [ ] **Step 3: docker cp + restart backend；curl 冒烟（需 admin cookie/token）**

---

### Task 4: 研究工厂「发布为量化模型」UI

**Files:**
- Create: `QuantDinger-Vue/src/api/quantModels.js`
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue`（发布表单：会话、Loop、kind、显示名）

- [ ] **Step 1: API `publishQuantModel` / `fetchQuantModels`**

- [ ] **Step 2: UI 区块「发布为量化模型」+ 成功展示 model_key / alpha_version**

- [ ] **Step 3: `docker compose ... build frontend` 并 `--pull never` 部署；页面冒烟**

---

## Phase P1 — ensure_scores + 回测异步准备

### Task 5: 调仓日历展开

**Files:**
- Create: `backend_api_python/app/services/quant_models/schedule.py`
- Test: `backend_api_python/tests/test_quant_models_schedule.py`

**Interfaces:**
- `infer_schedule_from_strategy(code: str, params: dict) -> Literal["weekly","daily"]`
- `expand_rebalance_dates(schedule, start: date, end: date, *, weekday=1) -> list[date]`
- `to_score_as_ofs(rebalance_dates, score_lag_days: int) -> list[date]`

启发式：源码含 `run_weekly` → weekly；含 `run_daily` → daily；params.`infer_schedule` 优先；默认 weekly。

- [ ] **Step 1: 单测周频周一、日频、lag**

- [ ] **Step 2: 实现**

- [ ] **Step 3: pytest PASS**

---

### Task 6: `ensure_scores`

**Files:**
- Create: `backend_api_python/app/services/quant_models/ensure_scores.py`
- Test: `backend_api_python/tests/test_quant_models_ensure_scores.py`

**Interfaces:**
- Consumes: `get_quant_model`, `list_external_alpha_as_ofs`（store 已有或新增按 version 列 as_of）, `infer_and_import_session_scores`
- Produces: `ensure_quant_model_scores(model: dict, as_ofs: list[date], *, on_progress=None) -> dict`  
  返回 `{ missing_before, inferred, still_missing, export_meta? }`

算法：
1. `present = set(as_ofs for panel)`  
2. `missing = sorted(set(as_ofs) - present)`  
3. 若空：返回  
4. 否则 `infer_and_import_session_scores(session_id=..., loop_index=..., mode=kind, source=alpha_source, version=alpha_version, universe=..., start=min(missing), end=max(missing), do_import=True)`  
5. 再查仍缺 → 列入 `still_missing`

- [ ] **Step 1: mock infer + fake present as_ofs 单测**

- [ ] **Step 2: 实现**

- [ ] **Step 3: pytest PASS**

---

### Task 7: 异步 `prepare_and_backtest` 任务

**Files:**
- Create: `backend_api_python/app/services/quant_models/jobs.py`
- Create: `backend_api_python/app/tasks/quant_model_jobs.py`
- Modify: `backend_api_python/app/celery_app.py`（include 新 task 模块）
- Modify: `backend_api_python/app/routes/backtest_center.py`  
  - `POST /api/backtest/run-with-model` → submit job  
  - `GET /api/backtest/model-jobs/<job_id>`

**Interfaces:**
- Job payload: `model_key`, `user_id`, backtest `_prepare_run` 所需字段  
- Progress phases: `preparing` → `backtesting` → `succeeded` | `failed`  
- 复用 `app.utils.agent_jobs.submit_job`（kind=`quant_model_prepare_backtest`）或回测中心专用表；**优先复用 agent_jobs** 以免新表

实现要点：
1. `model = get_quant_model(model_key)`；覆盖 `params["source"]=alpha_source`, `params["version"]=alpha_version`  
2. 读策略代码 → schedule → as_ofs  
3. `ensure_quant_model_scores(..., on_progress=...)`；若 `still_missing` 非空 → fail  
4. `StrategyV2BacktestService().run(**prepared)`  
5. 结果写入 job result

- [ ] **Step 1: 单测 job 函数（mock ensure + backtest）**

- [ ] **Step 2: 路由 + Celery 注册**

- [ ] **Step 3: 容器热更新 backend + celery-worker；用已发布模型短区间冒烟**

---

### Task 8: 回测中心 UI — 选模型 + 进度

**Files:**
- Modify: `QuantDinger-Vue/src/api/strategy.js` 或 `quantModels.js`（runWithModel / fetchModelJob）
- Modify: `QuantDinger-Vue/src/views/backtest-center/index.vue`

行为：
- `usesExternalAlphaParams` 时加载 published models；选中后写入 `params.source/version`（或只存 `model_key` 由后端覆盖）
- `run()` 走 `run-with-model`；轮询 job 至完成；展示 `done_dates/total_dates`

- [ ] **Step 1: API 封装**

- [ ] **Step 2: UI**

- [ ] **Step 3: 重建 frontend 部署验证**

---

## Phase P2 — 实盘调仓前 ensure

### Task 9: 定位实盘调仓入口并挂钩

**Files:**
- Modify: 实盘调度实际调用策略回调处（先用搜索确认：`trading_executor.py` / portfolio runtime / scheduler worker）
- Create: `backend_api_python/app/services/quant_models/live_hook.py`  
  - `ensure_before_rebalance(strategy_row, trade_date) -> bool`（False=跳过）

逻辑：
1. 从策略 params 读 `model_key` 或 `(source,version)` 反查模型  
2. `as_of = trade_date - score_lag_days`  
3. `ensure_quant_model_scores(model, [as_of])`  
4. 若 still_missing：打 error 日志/告警，return False  
5. True 则继续原回调

- [ ] **Step 1: 写 `live_hook` 单测（成功 / 失败跳过）**

- [ ] **Step 2: 接入真实调度路径（最小侵入）**

- [ ] **Step 3: 文档说明实盘绑定方式（params.`model_key`）**

---

### Task 10: 验收对照 Spec §11

- [ ] 发布 Loop_7 模型可见  
- [ ] 回测选模型短区间：进度 → 结果；二跑增量不重复全量  
- [ ] 兼容旧 source/version 手选路径仍可用  
- [ ] 模拟 ensure 失败：实盘路径跳过调仓  

---

## Spec Coverage Check

| Spec 要求 | Task |
|-----------|------|
| `qd_quant_models` | T1–T2 |
| 发布 / 列表 API + 研究页 | T3–T4 |
| 调仓日历 / lag | T5 |
| ensure + bridge infer | T6 |
| 回测异步 prepare+run | T7–T8 |
| 实盘自动跟推理、失败跳过 | T9 |
| 验收 | T10 |
| 非目标（懒推理、多模型合成） | 不做 |

## Placeholder Scan

无 TBD；Celery vs 专用表在 T7 明确「优先 agent_jobs」。

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-04-quant-model-backtest.md`.**

两种执行方式：

1. **Subagent-Driven（推荐）** — 每任务新开子代理，任务间审查  
2. **Inline Execution** — 本会话按计划连续实现，设检查点  

你选哪一种？回复 `1` 或 `2` 即可开始 P0。
