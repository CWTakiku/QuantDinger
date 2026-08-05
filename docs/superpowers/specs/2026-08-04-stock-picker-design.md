# 选股模块设计

> 日期：2026-08-04  
> 状态：已确认；实现计划见 `docs/superpowers/plans/2026-08-04-stock-picker.md`  
> 范围：侧栏「选股」页（admin）— 已发布量化模型列表、组成查看、按模型推理、Top-N 选股名单  
> 相关：`docs/superpowers/specs/2026-08-04-quant-model-backtest-design.md`、`docs/superpowers/specs/2026-08-02-qd-rdagent-bridge-module-design.md`、`docs/superpowers/specs/2026-08-03-research-factory-session-depth-design.md`

## 1. 背景与问题

用户心智：

1. **研究工厂**负责挖因子 / 训模型 / 发布为「量化模型」。  
2. **选股**消费已发布模型：看它由什么组成、对指定日期推理分数、看当日 Top-N 名单。  
3. 「组成」≠ 当日持仓列表，而是：  
   - **因子清单**（名称、简述/公式）  
   - **学习器身份**（`kind=model` 时的 `model_type` / `architecture`，如 LightGBM、PyTorch/LSTM；`kind=factor` 时标明纯因子合成）

现状缺口：

| 能力 | 现状 |
|------|------|
| 独立「选股」入口 | 无；能力散落在研究工厂与回测中心 |
| 按已发布模型看组成 | 需手动打开原会话详情；`qd_quant_models` 仅存 provenance 指针 |
| 按 `model_key` 一键推理 | 无 HTTP；仅有 `ensure_quant_model_scores`（回测/实盘内部）与研究页 `infer-from-session` |
| Top-N 选股预览 | 有 `alpha-preview` 排名表，无选股页 + `top_n` 产品化 |

## 2. 目标与非目标

### 2.1 目标（本期）

1. 侧栏在 **研究工厂之前** 增加 **选股**（`/stock-picker`，**仅 admin**）。  
2. 列出 **已发布** 量化模型（复用 `GET /api/quant-models/`）。  
3. 选中模型后展示 **组成**：因子清单 + 学习器（若有）。  
4. 对已发布模型执行 **ensure/推理**（按 as_of 或区间补齐 `qd_external_alpha_scores`）。  
5. 展示指定 `as_of` 的 **Top-N 选股名单**（分数降序前 N，默认与策略模板一致可配，默认 **30**）。

### 2.2 非目标（本期不做）

- 非 admin 开放。  
- 在选股页 **发布 / 归档** 模型（仍在研究工厂）。  
- 真实成交持仓 / 账本成分。  
- 把研究工厂推理 UI 整页迁走。  
- 强制把完整因子代码快照进 DB（组成按需从 bridge 拉摘要；bridge 不可达时降级提示）。

## 3. 概念澄清

| 层级 | kind | 用户看到的「组成」 |
|------|------|-------------------|
| 单个 Loop | `factor` **或** `model`（互斥） | 因子 Loop → 因子清单；模型 Loop → 学习器卡 |
| 整场 Session | 可先后有两类 Loop | 模型 Loop 的特征通常来自更早因子 Loop |
| 已发布量化模型 | 只绑 **一个** Loop → 单一 `kind` | 详情 API 按该 Loop 摘要；`kind=model` 时可附带同会话因子库摘要（若 bridge 可得） |

## 4. 信息架构与 UI

### 4.1 路由与菜单

| 项 | 值 |
|----|-----|
| path | `/stock-picker` |
| name | `StockPicker` |
| component | `@/views/stock-picker` |
| meta.title | `menu.dashboard.stockPicker` → 中文「选股」 |
| meta.permission | `['admin']` |
| meta.icon | 建议 `stock` / `fund` / `bar-chart`（实现时与现有 icon 集对齐） |
| 顺序 | 写在 `router.config.js` 中 **研究工厂（`/rdagent`）之前** |

### 4.2 页面三栏（桌面）

```
┌──────────────┬────────────────────────┬──────────────────────────┐
│ 已发布模型   │ 组成                   │ 推理与 Top-N 选股        │
│ 列表         │ factor: 因子表         │ as_of / 区间             │
│              │ model: 学习器 + 因子摘要│ [刷新分数]               │
│              │                        │ Top-N 表 (rank/symbol/…) │
└──────────────┴────────────────────────┴──────────────────────────┘
```

移动端：列表 → 详情 Tabs（组成 | 选股）。

### 4.3 交互要点

1. 进入页默认加载 `status=published` 列表；自动选中第一项（若有）。  
2. 选中模型 → `GET /api/quant-models/<model_key>` 拉组成；失败时展示 provenance 原文 +「会话详情暂不可用」。  
3. 「刷新分数」→ `POST .../ensure-scores`，body 含 `as_ofs` 或 `start`+`end`；完成后刷新 preview。  
4. Top-N：对当前 `alpha_source` + `alpha_version` + `as_of` 调 preview；UI `top_n` 默认 30，可改（5–100）。  
5. Bridge / ensure 失败：中文错误（与研究工厂一致的「请先启动 rdagent-bridge」映射）。

## 5. API 设计

权限：下列接口均 `@login_required` + `@admin_required`（与现有 quant-models 一致）。

### 5.1 已有

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/quant-models/` | 列表；`?status=published` |
| GET | `/api/rdagent/alpha-preview` | `source, version, as_of, limit, order` |

### 5.2 新增

#### `GET /api/quant-models/<model_key>`

返回模型行 + `composition` 摘要。

```json
{
  "code": 1,
  "data": {
    "model_key": "…",
    "display_name": "…",
    "kind": "model",
    "alpha_source": "rdagent",
    "alpha_version": "qm_…",
    "universe": "csi300",
    "provenance_json": { "session_id": "…", "loop_index": 3, "mode": "model" },
    "metrics_json": {},
    "composition": {
      "available": true,
      "kind": "model",
      "session_id": "…",
      "loop_index": 3,
      "learner": {
        "name": "…",
        "model_type": "pytorch",
        "architecture": "LSTM",
        "hyperparameters": {}
      },
      "factors": [
        { "name": "…", "formulation": "…", "description": "…" }
      ],
      "bridge_error": null
    }
  }
}
```

组成装配规则：

1. 读 `get_quant_model(model_key)`；不存在 → 404。  
2. 用 provenance 调 bridge `session detail`（include：`loops,factors`；需要学习器字段时含 code/sota 所需最小集）。  
3. 定位 `loop_index`：  
   - `kind=factor`：该 Loop 的 artifacts/factors → `composition.factors`；`learner=null`。  
   - `kind=model`：该 Loop 的 model artifact → `learner`；`factors` 优先取同 Loop（若无）否则取会话 `sota_library` / 最近 factor Loop 摘要（名称级即可，本期可不内嵌完整 code）。  
4. Bridge 失败：`composition.available=false`，填 `bridge_error` 中文短句；仍返回模型元数据。

#### `POST /api/quant-models/<model_key>/ensure-scores`

Body（二选一）：

```json
{ "as_ofs": ["2026-04-10", "2026-04-11"] }
```

或

```json
{ "start": "2026-04-01", "end": "2026-04-10" }
```

行为：

1. 加载模型；非 published 可拒绝或仍允许（**建议仅 published**）。  
2. 将日期规范为 `list[date]`（`start/end` 展开为闭区间日历日，具体交易日过滤可复用现有 ensure/infer 路径能力；若现有只认请求日列表，则由调用方传 `as_ofs` 或后端用简单日历展开后交给 `ensure_quant_model_scores`）。  
3. 调用已有 `ensure_quant_model_scores(model, as_ofs)`。  
4. 返回 `{ missing_before, inferred, still_missing, export_meta? }`。

超时：与 bridge infer 一致，前端长超时（建议 ≥ 300s 可配置）。

### 5.3 Preview 与 Top-N

本期 **不强制** 改 `alpha-preview` 契约：前端用 `limit=top_n` 且 `order=desc` 即可。  
若实现时 `limit` 语义已是返回行数，则直接对齐；否则在选股页对 rows 做 `slice(0, top_n)`。

可选增强（非必须）：`alpha-preview` 增加 `top_n` 别名参数，文档化「选股语义」。

## 6. 前端结构

| 文件 | 职责 |
|------|------|
| `src/views/stock-picker/index.vue` | 页面壳 + 三栏布局 |
| `src/api/quantModels.js` | 增 `fetchQuantModel(key)`、`ensureQuantModelScores(key, body)` |
| `src/api/rdagent.js` | 复用 `fetchAlphaPreview` |
| `src/config/router.config.js` | 注册路由（rdagent 前） |
| `src/locales/lang/strategy-v2.js` 或 `zh-CN.js` | `menu.dashboard.stockPicker` 等文案 |

组件可从小块抽离（`ModelList` / `CompositionPanel` / `SelectionPanel`），避免复制整页 `rdagent/index.vue`。

## 7. 与周边模块关系

```
研究工厂 --发布--> qd_quant_models
                      │
选股 ---------------> 详情组成 / ensure-scores / alpha-preview
                      │
回测中心 -----------> 选用模型 + run-with-model（已有，不变）
```

## 8. 错误与降级

| 情况 | 行为 |
|------|------|
| 无已发布模型 | 空态引导：去研究工厂发布 |
| Bridge 离线 | 组成降级；ensure 返回 503 + 启动 bridge 提示 |
| 无分数日 | preview 空态；引导点「刷新分数」 |
| 模型已归档 | 列表默认不展示；直链详情可 404 或只读提示 |

## 9. 测试要点

1. 路由：admin 可见且位于 rdagent 前；非 admin 不可见。  
2. `GET .../<key>`：factor / model 两种 composition 形状；bridge mock 失败时 `available=false`。  
3. `POST .../ensure-scores`：调用 `ensure_quant_model_scores`；缺 provenance 返回 400。  
4. 前端：选模型 → 组成渲染；ensure 成功后 Top-N 表有数据。  
5. 回归：`/api/quant-models` 无尾斜杠不 308 丢端口（已修 `strict_slashes`）。

## 10. 实现顺序建议

1. 后端：`GET` 详情 + composition 装配；单测。  
2. 后端：`POST` ensure-scores；单测。  
3. 前端：路由 + 空壳 + 列表。  
4. 前端：组成面板 + 推理/Top-N 面板。  
5. 文案与手动联调（bridge 在线）。

## 11. 开放决策（已拍板）

| 项 | 决定 |
|----|------|
| 方案 | B（选股专用薄 API） |
| 权限 | 仅 admin |
| 组成 | 因子清单 + 学习器身份（非持仓） |
| 能力 | 推理 + Top-N 名单 |
| 默认 Top-N | 30 |
| 发布入口 | 仍在研究工厂 |
