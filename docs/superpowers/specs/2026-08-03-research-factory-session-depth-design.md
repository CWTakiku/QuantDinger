# 研究工厂会话深度（因子库 / 模型 / 按 Loop 导入 / 交互图 / 因子矩阵）设计

日期：2026-08-03  
状态：已确认  
前置：`2026-08-03-research-factory-session-detail-design.md`（P0–P2 已落地）  
范围：`rdagent-workspace` bridge + QuantDinger 后端代理 + QuantDinger-Vue 研究工厂  
交付：一期全量（单规格一次实现）

## 1. 背景与目标

会话详情已能展示按 Loop 的指标、本轮因子、反馈与编码进化，但仍有缺口：

1. 「开发」只显示本轮新因子，看不到**累积 SOTA 因子库**与跨 Loop 轨迹。
2. `qlib_quant` 模型环几乎看不到 `model.py` / 结构 / 超参 / 训练日志。
3. 「导入 External Alpha」取**最新** `pred.pkl`，不能按成功 Loop 精确导入。
4. 交互图弱于 RD Streamlit（净值以外的研究图不足）。
5. 未暴露 workspace 内 `combined_factors_df.parquet` 的全量因子矩阵。

**目标**：研究工厂内对齐并超过 RD UI 常用深度查看能力；导入可指定 Loop；可浏览 parquet 全量因子列与抽样截面。

**非目标**：

- 不在 Docker 内跑挖因子；不替换 RD 挖因子流程。
- 不像素级复刻 Streamlit 全部控件；「打开 RD UI」按钮保留兜底。
- 不做商用级因子归因 / Barra。
- 不把整张 parquet 无截断塞进浏览器（必须抽样 / 分页 / 可选 CSV 导出）。

## 2. 用户流程

1. 打开会话详情 → **总览**：指标表 + Loop 折线 + **累积 SOTA 因子库** + 假设轨迹（可筛成功）。
2. **研发循环**：选 Loop；旁注 `因子` / `模型`。
   - 因子环 → 研究 / 开发（本轮因子）/ 反馈（对比+净值图）/ 编码进化。
   - 模型环 → 研究 / **模型**（结构、超参、`model.py`、训练日志、编码反馈细分）/ 反馈 / 编码进化。
3. **因子矩阵** Tab：列出 parquet 全部因子列、覆盖区间；预览抽样截面；可下载矩阵 CSV（可选截断行数）。
4. **导入 External Alpha**：选 `source` + **Loop**（有预测产物的轮次）+ version 预填；未选 Loop 则保持「最新产物」兼容旧行为。

## 3. 架构

```
Vue SessionDetail (+ 导入表单)
  → QD GET  /api/rdagent/sessions/<id>/detail?include=...
  → QD GET  /api/rdagent/sessions/<id>/factor-matrix?...(可选独立接口)
  → QD POST /api/rdagent/import-from-session  { session_id, loop_index?, ... }
       ↓
Bridge session_detail / export_scores / factor_matrix
       ↓
log/<session>/Loop_* pickle
git_ignore_folder/RD-Agent_workspace/<id>/
  - factor.py | model.py
  - combined_factors_df.parquet
  - pred.pkl | result.h5
```

原则：

- Bridge 只读解析；大字段靠 `include` 懒加载。
- QD 鉴权代理；不落库会话详情正文。
- 因子矩阵默认返回 schema + 抽样；全量 CSV 走下载接口，设行数上限与超时。

## 4. Bridge API

### 4.1 扩展 `GET /v1/sessions/<session_id>/detail`

`include` 新增（可与现有组合）：

| 值 | 说明 |
|----|------|
| `sota_library` | 累积 SOTA 因子库摘要（默认可并入 `summary` 或显式请求） |
| `logs` | 截断后的 `Qlib_execute_log` 文本 |
| `factor_matrix` | 矩阵 schema + 小抽样（亦可用独立路由，见 4.3） |
| `charts` | 总览/反馈所需时序（若未含在 equity 内） |

每 loop 增补字段：

```text
loops[].hypothesis.action          # "factor" | "model" | null
loops[].kind                       # 同 action，缺省时由 workspace 文件推断
loops[].artifacts[]                # 统一替代/扩展 factors[]（兼容旧 factors）
  # FactorTask:
  name, description, formulation (=factor_formulation||formulation),
  variables, coding_success, feedbacks{execution,code,shape,value,final},
  code (factor.py, include=code)
  # ModelTask:
  name, description, formulation, architecture, model_type,
  hyperparameters, training_hyperparameters,
  code (model.py, include=code),
  same feedbacks
loops[].training_log               # include=logs；截断（如末 200KB）
loops[].stdout                     # runner.stdout（可空）
loops[].has_exportable_pred        # 是否存在可导入的 pred.pkl/result.h5
loops[].sota_library_snapshot[]    # 可选：该轮 runner.based_experiments 中的因子名列表
```

`summary` 增补：

```text
summary.sota_library[]:
  name, first_accepted_loop, last_seen_loop,
  formulation?, coding_success?,
  code? (仅 include 含 code|sota_library 且显式要代码时)
summary.sota_model:                # 最后一次 decision=True 的模型环摘要
  loop_index, name, architecture?, model_type?
summary.exportable_loops[]:        # [{loop_index, kind, mtime, artifact}]
```

SOTA 库构建规则：

1. 扫描各 Loop：`feedback.decision == True` 且 `kind==factor`（或 action=factor）的本轮因子名并入集合，记录 `first_accepted_loop`。
2. 若 runner 存在：从最新成功因子环的 `based_experiments` 中过滤 `QlibFactorExperiment`，用其 `sub_tasks` 校正名称集合（权威于 pickle 累积语义）。
3. 代码：按因子名在对应 Loop workspace 的 `file_dict["factor.py"]` 或磁盘文件解析；找不到则 `code=null`。

### 4.2 按 Loop 导出

扩展 `export_session(workspace, session_id, source, version, universe, loop_index=None)`：

- `loop_index is None`：保持现行为（全会话最新 `pred.pkl` 优先，否则 `result.h5`）。
- `loop_index=N`：仅在 `log/<session>/Loop_N/` 的 runner 关联 workspace 中查找 `pred.pkl`/`result.h5`；找不到则 404 明确错误。

Bridge HTTP：

- 现有 `POST /v1/export`（或等价）body 增加可选 `loop_index: int`。

QD：

- `POST /api/rdagent/import-from-session` body 增加可选 `loop_index`。
- 默认 version：若带 loop，建议 `session_<id>_loop<N>`（可被客户端覆盖）。

### 4.3 因子矩阵

两种等价交付（实现选一，推荐独立 GET 以免撑爆 detail）：

**推荐** `GET /v1/sessions/<id>/factor-matrix`

| Query | 默认 | 说明 |
|-------|------|------|
| `loop_index` | 最新有 parquet 的成功/末轮 | 指定 workspace 来源 |
| `sample_dates` | 5 | 抽样交易日数（从末尾往前） |
| `max_symbols` | 50 | 每截面最多标的 |
| `columns` | all | 可选逗号分隔因子名子集 |

响应示意：

```json
{
  "session_id": "...",
  "loop_index": 7,
  "parquet_path": "...",
  "factor_names": ["f1", "f2", "..."],
  "as_of_min": "2016-01-04",
  "as_of_max": "2026-05-20",
  "n_dates": 2500,
  "n_symbols_est": 300,
  "sample": [
    {"as_of": "2026-05-20", "rows": [{"symbol": "SH600000", "f1": 0.1, "f2": -0.2}]}
  ]
}
```

下载：`GET /v1/sessions/<id>/factor-matrix.csv?loop_index=&max_rows=500000`（超限截断并在 header 注明）。

数据源：优先 runner workspace 的 `combined_factors_df.parquet`；MultiIndex `(datetime, instrument)`。

### 4.4 交互图数据

- 复用/加强现有 `equity`（账户、基准、收益）。
- `include=charts` 或总览用已有 `loops[].metrics` 画 IC/Rank IC/年化/回撤折线（前端即可，无需新后端若 metrics 已全）。
- 若 RD「Quantitative Backtesting Chart」另有可序列化字段，映射为 `loops[].chart_series`（日频，可降采样到 ≤2000 点）。

## 5. 前端（研究工厂）

### 5.1 总览

- 保留指标表与假设列表。
- 新增 **SOTA 因子库** 表：`name / 首次采纳 Loop / 公式摘要`；行展开加载代码。
- 指标折线：IC、Rank IC、年化、回撤（成功轮可高亮）。

### 5.2 研发循环

- Loop 选项显示 `Loop_N · 因子|模型`；无产物的导入禁用提示。
- **开发** Tab：`kind=factor` 时保持因子 collapse。
- **模型** Tab（或开发 Tab 在 model 时切换文案）：结构、超参、`model.py`、训练日志、细分反馈。
- **反馈**：SOTA vs 本轮对比文案修正（勿写死「仅基线」）；净值图可缩放。

### 5.3 因子矩阵 Tab

- 因子列清单 + 覆盖区间。
- 抽样表（日期 × 标的 × 因子子集）。
- 按钮：刷新抽样、下载 CSV。

### 5.4 导入表单

- 增加 Loop 下拉（来自 `summary.exportable_loops`）。
- 文案：说明「不选 = 最新产物；选 Loop = 仅该轮预测」。

## 6. 验收标准

1. `qlib_quant` 多轮会话：总览 SOTA 库因子数 ≥ 各成功因子环本轮因子去重并集（允许与 based_experiments 对齐后的权威集）。
2. 任意模型 Loop：可读 `model.py`，可见 architecture/model_type（有则显示），训练日志非空或明确「无日志」。
3. 指定 `loop_index` 导入后，落库 `version` 可区分，且 `as_of` 范围与该轮产物一致；与「最新导入」在末轮失败时结果可不同。
4. 因子矩阵：`factor_names` 覆盖 parquet 全部列；抽样可渲染；CSV 下载可用。
5. 总览折线与反馈净值可交互；数据来自同一会话 pickle/chart，不依赖 19899。
6. 回归：不传 `loop_index` 的导入行为与现网一致；旧 `include` 参数仍可用。

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| pickle 大、workspace 多 | 严格 `include`；代码/日志/矩阵懒加载 |
| parquet 过大 OOM | 只读列名 + 尾部日期抽样；CSV `max_rows` |
| Loop 无 pred | `has_exportable_pred=false`，导入禁用并提示 |
| `FactorExperiment` 类型别名坑 | 用 `QlibFactorExperiment` / `action` / 文件名推断，避免裸 `isinstance(..., ModelExperiment)` |
| 配方字段名不一致 | `formulation` 回退 `factor_formulation` |

## 8. 实现顺序（仍属一期，可检节点合并 PR）

1. Bridge：action/kind、artifacts、sota_library、logs、model.py  
2. Bridge：export `loop_index` + exportable_loops  
3. Bridge：factor-matrix + CSV  
4. Vue：总览库、模型 Tab、导入 Loop、矩阵 Tab、图表加强  
5. QD 代理与测试；重建本地 frontend/backend 镜像验证  

## 9. 文档

- 更新 `docs/RDAGENT_EXTERNAL_ALPHA_CN.md`：按 Loop 导入、详情深度能力说明。
