# 量化模型发布 + 回测/实盘按需推理设计

> 日期：2026-08-04  
> 状态：已确认；实现计划见 `docs/superpowers/plans/2026-08-04-quant-model-backtest.md`  
> 范围：研究工厂「发布为量化模型」→ 回测/实盘选用 → 按策略调仓日异步准备分数 → 执行策略  
> 相关：`docs/superpowers/specs/2026-08-02-qd-rdagent-bridge-module-design.md`、`docs/superpowers/specs/2026-08-01-external-alpha-score-bridge-design.md`、已实现的 Bridge `POST /v1/infer` 与 QD `POST /api/rdagent/infer-from-session`

## 1. 背景与问题

用户心智模型：

1. **研究工厂产物** = 量化模型（`因子+模型` 或仅 `因子`）。  
2. **回测**时选择量化模型 + 策略 + 日期区间 → 系统在区间内按策略节奏推理 → 再跑策略。  
3. **实盘**启用后，在每个调仓点自动跟推理，再执行策略。

现状缺口：

| 能力 | 现状 |
|------|------|
| 研究产物的一等公民「量化模型」 | 无；仅有会话/Loop + External Alpha `(source, version)` |
| 回测选模型 | 手填/下拉 `source`+`version` 面板 |
| 回测区间内自动推理 | 需在研究页手动「推理并导入」 |
| 实盘调仓前自动推理 | 无 |

分数 PIT 存储与策略读分 API **保持不变**（`qd_external_alpha_scores` + `get_external_alpha_scores`）。

## 2. 目标与非目标

### 2.1 目标（本期）

1. 新增 **量化模型注册表** `qd_quant_models`：发布、列表、归档。  
2. 研究工厂 UI：**发布为量化模型**（绑定会话/Loop/kind + alpha 面板身份）。  
3. 回测中心：优先选择 **已发布量化模型**；运行后走 **异步任务**：准备缺日分数 → 再跑现有 Strategy V2 回测；可轮询进度。  
4. 实盘：策略绑定量化模型后，**调仓前** `ensure_scores(as_of)`；失败则 **跳过本次调仓** 并告警（默认不允许沿用过期分）。  
5. 推理节奏 **跟随策略**：周频 → 调仓日（如周一）；日频 → 每个交易日；`score_lag_days` 计入 as_of。

### 2.2 非目标（二期）

- 多模型实时合成加权（可在策略内手写；本期不提供平台级合成器）。  
- 盘中高频重推理 / tick 级。  
- 全市场、与策略无关的统一预计算服务（可作为性能优化后续加）。  
- 修改 `get_external_alpha_scores` 为沙箱内同步懒推理（禁止：易卡死、难控超时）。

### 2.3 已确认决策

| 项 | 选择 |
|----|------|
| 分数何时产生 | 回测/实盘 **按需推理**（非仅发布时灌满） |
| 推理日集合 | **由策略调仓日历决定** |
| 回测体验 | **异步任务 + 进度**（准备 → 回测） |
| 架构 | 方案 1：注册表 + 准备分数任务 + 复用 External Alpha |
| 推理失败（实盘） | **跳过调仓** + 告警（默认不沿用旧分） |
| 实盘范围 | 纸交易与实盘均支持自动推理，**仍受现有 live/paper 开关与风控约束** |

## 3. 概念模型

```text
研究工厂 Session / Loop
        │ publish
        ▼
  qd_quant_models（配方元数据）
        │ alpha_source + alpha_version
        ▼
  qd_external_alpha_scores（PIT 分数）
        ▲
        │ ensure_scores / infer
  rdagent-bridge POST /v1/infer
        │
策略（回测或实盘）── get_external_alpha_scores(as_of, source, version)
```

- **量化模型**：冻结配方 + 分数面板身份；不代替策略。  
- **策略**：持仓规则与调度（`run_weekly` / `run_daily` 等）。  
- **准备分数**：对给定 as_of 集合调用 bridge infer（可批量窗口），写入同一 `(source, version)`。

## 4. 数据模型

### 4.1 表 `qd_quant_models`

```sql
CREATE TABLE qd_quant_models (
  id               BIGSERIAL PRIMARY KEY,
  model_key        VARCHAR(80)  NOT NULL,
  display_name     VARCHAR(200) NOT NULL,
  status           VARCHAR(20)  NOT NULL DEFAULT 'draft',  -- draft|published|archived
  kind             VARCHAR(20)  NOT NULL,                 -- model|factor
  alpha_source     VARCHAR(80)  NOT NULL,
  alpha_version    VARCHAR(120) NOT NULL,
  universe         VARCHAR(80)  NOT NULL DEFAULT 'csi300',
  owner_user_id    BIGINT,
  provenance_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
  -- session_id, loop_index, mode, workspace hints, bridge session path
  metrics_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  -- optional: IC/IR snapshot from research loop
  published_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (model_key),
  UNIQUE (alpha_source, alpha_version)
);

CREATE INDEX idx_qd_quant_models_published
  ON qd_quant_models (status, published_at DESC);
```

`provenance_json` 最低字段：`session_id`, `loop_index`, `mode`（`model`|`factor`）。

`alpha_version` 建议稳定、可读：`qm_{model_key}`（发布时生成；避免与手动 infer 的 `session_*_infer_*` 混淆，或允许用户指定后缀）。

### 4.2 分数表

不改 `qd_external_alpha_scores` 契约；准备任务写入的 `source/version` 必须等于模型上的 `alpha_source/alpha_version`。

### 4.3 异步任务

优先复用现有 Agent/回测任务基础设施（若已有 `qd_agent_jobs` 或 backtest run 表可扩展）；否则新增轻量 `qd_quant_model_jobs`：

| 字段 | 说明 |
|------|------|
| `job_type` | `prepare_and_backtest` \| `prepare_live` \| `ensure_scores` |
| `model_id` / `model_key` | 目标模型 |
| `status` | `queued` \| `preparing` \| `backtesting` \| `succeeded` \| `failed` |
| `progress_json` | `{ total_dates, done_dates, current_as_of, phase }` |
| `error` | 失败信息 |
| `result_json` | 回测 run_id 等 |

## 5. API（Human）

### 5.1 模型

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/quant-models/publish` | 从 RD 会话发布；body: `session_id`, `loop_index`, `kind`, `display_name`, `universe?`, `model_key?` |
| `GET` | `/api/quant-models?status=published` | 回测/实盘下拉 |
| `POST` | `/api/quant-models/{id}/archive` | 归档 |

发布 **不强制** 长推理；可选后续加 `warmup_max_asofs`（本期可不做）。

### 5.2 回测准备 + 运行

扩展现有 `POST /api/backtest/run` **或** 新增：

`POST /api/backtest/run-with-model`

请求要点：

- `model_key`（或 `model_id`）  
- 原有：`sourceId`（策略脚本）、日期、资金、`params`（策略参数；`source/version` 由服务端用模型覆盖）  
- 返回：`job_id`（异步）

`GET /api/backtest/jobs/{job_id}`：进度与最终回测结果引用。

### 5.3 实盘 ensure

内部服务（非必须独立 HTTP）：`ensure_quant_model_scores(model, as_ofs: list[date]) -> PrepareResult`  
实盘调度在调仓回调前调用；也可暴露管理用：

`POST /api/quant-models/{id}/ensure-scores` `{ as_ofs: [...] }` 供运维/调试。

## 6. 准备分数算法

```text
输入: model, strategy_schedule, start, end, score_lag_days
1. dates = expand_rebalance_calendar(schedule, start, end)  # weekly Mondays / daily sessions
2. as_ofs = { d - lag for d in dates } ∩ qlib/trading calendar（或业务日历）
3. missing = as_ofs - already_present(source, version)
4. if missing empty: return ok
5. batch infer:
   - 调用 bridge infer：session/loop/mode/universe 来自 provenance
   - start/end 覆盖 missing 的连续窗口（或按批 max_asofs）
   - 写入 alpha_source/version（与模型一致，覆盖 infer 默认 version）
6. 校验 missing 已落库；仍缺则 fail（回测）或 skip（实盘调仓）
```

Bridge 已有 `POST /v1/infer`；QD 侧复用 `infer_and_import_session_scores`，强制 `version=model.alpha_version`、`do_import=True`。

**批量策略**：对缺失日取 `[min(missing), max(missing)]` 一次 infer（模型模式已支持窗口），避免逐日重复加载模型；若窗口过大可按月切片。

## 7. 调度推断（MVP）

优先级：

1. 策略 params 显式 `infer_schedule`: `weekly` | `daily`  
2. 否则解析策略源码启发式：存在 `run_weekly` → weekly；`run_daily` → daily  
3. 默认：`weekly`（与 External Alpha 周频模板一致）

`score_lag_days` 默认读策略 params（示例模板为 1）。

## 8. UI

### 8.1 研究工厂

- 「发布为量化模型」：会话、Loop、kind、显示名  
- 列表：已发布模型（可跳转回测）

### 8.2 回测中心

- 若策略使用外部 alpha：下拉 **量化模型**（published）优先于裸 source/version（可保留高级「手动面板」折叠）  
- 运行后展示任务进度：准备分数 `k/n` → 回测中 → 完成  

### 8.3 实盘 / 策略配置

- 绑定量化模型（写入策略 params 或独立绑定表）  
- 运行日志中展示：今日 ensure 成功 / 跳过调仓原因  

## 9. 实盘自动跟推理

```text
调度触发调仓点
  → resolve model from strategy binding
  → as_of = trade_date - score_lag_days
  → ensure_scores([as_of])
  → on failure: skip rebalance + alert (no order)
  → on success: existing strategy callback + trading executor
```

安全：

- 不绕过 `_preflight_live_strategy`、paper/live 开关、冲突检测。  
- 默认 **禁止** 用过期分下单；二期可配置 `stale_max_days`（本期不做）。

## 10. 风险与限制

| 风险 | 缓解 |
|------|------|
| 长区间首次准备慢 | 异步进度；按月切片；缓存已有 as_of |
| Bridge/GPU 不可用 | 任务失败可重试；实盘跳过调仓 |
| 模型与策略宇宙不一致 | 发布与回测校验 universe；提示 |
| 自定义因子面板偏旧 | 已知限制；kind=model 仍以 bridge infer 为准；文档说明 |

## 11. 验收标准

1. 可将会话 Loop_7 发布为 published 模型，并在 `GET /api/quant-models` 见到。  
2. 回测选择该模型 + 周频 External Alpha 策略 + 区间 → 任务先准备调仓日分数再出回测结果；进度可查。  
3. 重复回测同一区间不重复全量推理（缺日增量）。  
4. 纸交易/实盘策略绑定模型后，调仓前自动 ensure；人为制造 infer 失败时 **不下单** 并有告警日志。  
5. 不破坏现有手填 source/version 回测路径（兼容或高级入口）。

## 12. 实现分期建议

| 阶段 | 内容 |
|------|------|
| P0 | 表 + publish/list API + 研究页发布 |
| P1 | `ensure_scores` + 回测异步 prepare_and_backtest + UI 进度 |
| P2 | 实盘调仓前 hook + 告警 |
| P3（可选） | 发布时 warmup、手动面板折叠、月切批次优化 |

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-08-04 | 初稿：方案 1 + 回测按需推理 + 异步进度；纳入实盘自动跟推理；失败跳过调仓 |
| 2026-08-04 | Task 9 实现：实盘调仓前 ensure hook 落地。挂钩点为 `TradingExecutor._run_strategy_loop` 中 `session.process(frames)` 之前；新增 `app/services/quant_models/live_hook.py::ensure_before_rebalance(strategy, trade_date)`。绑定方式：策略 `params.model_key`（或 `source`+`version` 反查 published 模型）。`as_of = trade_date - score_lag_days`（默认 1）。失败/`still_missing` → 跳过本次调仓（不下单）+ warning 日志；无绑定 → no-op。每个 `trade_date` 仅 ensure 一次（`last_ensured_trade_date` 去重）。不绕过 `_preflight_live_strategy` / paper-live 开关 / 冲突检测；默认禁止沿用过期分。 |
