# 研究工厂会话深度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究工厂会话详情补齐累积 SOTA 因子库、模型深度、按 Loop 精确导入、交互图加强、parquet 全量因子矩阵。

**Architecture:** Bridge 从 Loop pickle / workspace 抽取 JSON；独立 `factor_matrix` 路由抽样 parquet；`export_session(loop_index=)` 限定产物；QD 代理；Vue `SessionDetail` + 导入表单一次交付。

**Tech Stack:** Python 3.10（bridge）、Flask（QD）、Vue 2 + Ant Design Vue + ECharts、pytest、pandas/pyarrow 读 parquet。

**Spec:** `QuantDinger/docs/superpowers/specs/2026-08-03-research-factory-session-depth-design.md`

## Global Constraints

- 只读会话产物；不改挖因子流程；保留「打开 RD UI」。
- `include` 懒加载大字段；parquet 禁止整表进浏览器；CSV 设 `max_rows`。
- 不传 `loop_index` 的导入行为必须与现网一致。
- 用 `action` / 文件名 / `QlibFactorExperiment` 推断 kind，避免裸 `isinstance(..., ModelExperiment)`。
- Commit message 中文 Conventional Commits（仅在用户要求提交时执行）。
- 前端文案中文。

---

## File map

| File | Role |
|------|------|
| `rdagent-workspace/rdagent_bridge/session_detail.py` | action/kind、artifacts、sota_library、logs、model 字段 |
| `rdagent-workspace/rdagent_bridge/export_scores.py` | `loop_index` 精确导出、`list_exportable_loops` |
| `rdagent-workspace/rdagent_bridge/factor_matrix.py` | parquet schema + 抽样 + CSV |
| `rdagent-workspace/rdagent_bridge/app.py` | factor-matrix 路由；export body 透传 loop_index |
| `rdagent-workspace/rdagent_bridge/tests/test_session_detail*.py` | 深度字段单测 |
| `rdagent-workspace/rdagent_bridge/tests/test_export_scores.py` | loop 导出 |
| `rdagent-workspace/rdagent_bridge/tests/test_factor_matrix.py` | 矩阵抽样 |
| `QuantDinger/.../rdagent_bridge/client.py` | export/detail/matrix API |
| `QuantDinger/.../rdagent_bridge/import_session.py` | loop_index + version 默认 |
| `QuantDinger/.../routes/rdagent.py` | 代理路由 |
| `QuantDinger/.../tests/test_rdagent_*.py` | 导入/代理单测 |
| `QuantDinger-Vue/src/api/rdagent.js` | API 封装 |
| `QuantDinger-Vue/src/views/rdagent/SessionDetail.vue` | 库/模型/矩阵/图表 |
| `QuantDinger-Vue/src/views/rdagent/index.vue` | 导入 Loop 下拉 |
| `QuantDinger/docs/RDAGENT_EXTERNAL_ALPHA_CN.md` | 文档一句+导入说明 |

---

### Task 1: Bridge — hypothesis.action / kind / artifacts（因子+模型）

**Files:**
- Modify: `rdagent-workspace/rdagent_bridge/session_detail.py`
- Modify: `rdagent-workspace/rdagent_bridge/tests/test_session_detail.py`（或新建 `test_session_detail_depth.py`）

**Interfaces:**
- Produces: `loops[].hypothesis.action`, `loops[].kind`, `loops[].artifacts[]`（仍填充兼容字段 `factors`）
- `_read_workspace_code(workspace) -> str | None` 读 `factor.py` 或 `model.py`
- formulation: `task.factor_formulation or task.formulation`

- [ ] **Step 1: 写失败单测（合成对象，不依赖真实 pickle）**

```python
def test_hypothesis_dict_includes_action():
    class H:
        hypothesis = "h"
        reason = "r"
        action = "model"
        concise_reason = concise_observation = concise_justification = concise_knowledge = None
    from rdagent_bridge.session_detail import _hypothesis_dict
    assert _hypothesis_dict(H())["action"] == "model"

def test_artifact_formulation_falls_back_to_factor_formulation():
    # 构造 minimal task/workspace/feedback，断言 artifacts[0]["formulation"]
    ...

def test_read_workspace_code_prefers_model_py_when_present():
    ...
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd rdagent-workspace && python -m pytest rdagent_bridge/tests/test_session_detail_depth.py -q --tb=short`

- [ ] **Step 3: 实现**

扩展 `_hypothesis_dict` 增加 `"action"`。  
将 `_read_factor_code` 泛化为 `_read_workspace_code`（`model.py` 优先若存在，否则 `factor.py`）。  
`_factor_list` 重命名/扩展为 `_artifact_list`：写入 `kind`、`architecture`、`model_type`、`hyperparameters`、`training_hyperparameters`、`feedbacks` 字典；`formulation` 回退；`code` 走 `_read_workspace_code`。  
`_parse_loop` 设置 `kind`（action 或由文件推断），`factors` = artifacts（兼容），另设 `artifacts`。

- [ ] **Step 4: pytest 绿灯**

---

### Task 2: Bridge — sota_library + training_log + has_exportable_pred

**Files:**
- Modify: `session_detail.py`
- Modify: `tests/test_session_detail_depth.py`
- Modify: `parse_include` / `ALL_INCLUDE` / `VALID_INCLUDE` 增加 `sota_library`,`logs`

**Interfaces:**
- Produces: `summary.sota_library[]`, `summary.sota_model`, `summary.exportable_loops[]`
- Produces: `loops[].training_log`, `loops[].stdout`, `loops[].has_exportable_pred`

- [ ] **Step 1: 单测 sota 累积规则**

```python
def test_build_sota_library_uses_accepted_factor_loops():
    # loops: 0 decision True factors [A,B]; 1 model; 2 decision True factor [C]
    # expect names A,B,C with first_accepted_loop
    ...

def test_training_log_truncated_when_include_logs(tmp_path):
    # 写超大 Qlib_execute_log pickle str，assert len(training_log) <= cap
    ...
```

- [ ] **Step 2: 实现 `_build_sota_library(loops)`、读 `running/Qlib_execute_log/**/*.pkl`（最新）、与 export 探测复用 `find_pred_for_loop`（可先 stub has_exportable 为「Loop 下存在 pred/h5」）**

- [ ] **Step 3: `build_session_detail` 在 summary 中填入 sota_library / sota_model / exportable_loops（exportable 可在 Task 3 完善）**

- [ ] **Step 4: pytest 绿灯**

---

### Task 3: Bridge — 按 Loop 导出

**Files:**
- Modify: `rdagent-workspace/rdagent_bridge/export_scores.py`
- Modify: `rdagent-workspace/rdagent_bridge/tests/test_export_scores.py`
- Modify: `rdagent-workspace/rdagent_bridge/app.py`（`POST /v1/export` body）

**Interfaces:**
- Produces:
  - `find_pred_file_for_loop(session_dir, workspace, loop_index: int) -> Path | None`
  - `list_exportable_loops(session_dir, workspace) -> list[dict]`
  - `export_session(..., loop_index: int | None = None)`

- [ ] **Step 1: 失败单测**

```python
def test_export_session_with_loop_index_uses_only_that_loop(tmp_path, monkeypatch):
    # 两个 Loop 目录各挂不同 pred；export loop_index=0 断言行来自 loop0
    ...

def test_export_session_missing_loop_raises_file_not_found(tmp_path):
    ...
```

- [ ] **Step 2: 实现 `find_pred_file_for_loop`：仅扫描 `Loop_{n}` runner 关联 workspace；`list_exportable_loops` 返回 `{loop_index, kind?, artifact, mtime}`**

- [ ] **Step 3: `export_session` 分支；`app.py` 读取 `loop_index`（int 或 null）**

- [ ] **Step 4: pytest 绿灯；Task 2 的 `exportable_loops` 改为调用 `list_exportable_loops`**

---

### Task 4: Bridge — factor_matrix 模块 + HTTP

**Files:**
- Create: `rdagent-workspace/rdagent_bridge/factor_matrix.py`
- Create: `rdagent-workspace/rdagent_bridge/tests/test_factor_matrix.py`
- Modify: `app.py`

**Interfaces:**
- `build_factor_matrix(workspace, session_id, *, loop_index=None, sample_dates=5, max_symbols=50, columns=None) -> dict`
- `build_factor_matrix_csv(..., max_rows=500_000) -> tuple[str, str]`  # (filename, text) 或 generator
- Routes:
  - `GET /v1/sessions/<id>/factor-matrix`
  - `GET /v1/sessions/<id>/factor-matrix.csv`

- [ ] **Step 1: 用合成 MultiIndex DataFrame 写 parquet，单测列名、抽样日期数、max_symbols**

```python
def test_build_factor_matrix_sample_bounds(tmp_path):
    # 写 combined_factors_df.parquet 到假 workspace，挂到 session loop
    out = build_factor_matrix(...)
    assert set(out["factor_names"]) == {"f_a", "f_b"}
    assert len(out["sample"]) <= 5
```

- [ ] **Step 2: 实现查找 parquet（优先指定 loop 的 runner workspace）；只读列 + 尾部日期抽样**

- [ ] **Step 3: 注册 HTTP；CSV `max_rows` 截断**

- [ ] **Step 4: pytest + 可选 `test_app_http` 冒烟**

---

### Task 5: QD 代理 + 导入 loop_index

**Files:**
- Modify: `QuantDinger/backend_api_python/app/services/rdagent_bridge/client.py`
- Modify: `QuantDinger/backend_api_python/app/services/rdagent_bridge/import_session.py`
- Modify: `QuantDinger/backend_api_python/app/routes/rdagent.py`
- Modify/Create: `tests/test_rdagent_import_session.py`、`tests/test_rdagent_*` 相关

**Interfaces:**
- `client.export_session(..., loop_index: int | None = None)`
- `client.factor_matrix(session_id, **params) -> dict`
- `client.factor_matrix_csv(...) -> bytes/str`
- `import_session_scores(..., loop_index: int | None = None)`
- `default_session_version(session_id, loop_index=None)` → 有 loop 时 `session_<id>_loop<N>`

- [ ] **Step 1: 单测 import 传 loop_index 到 client.export_session；version 默认带 `_loop7`**

- [ ] **Step 2: 实现 client / import_session / routes**
  - `GET /api/rdagent/sessions/<id>/factor-matrix`
  - `GET /api/rdagent/sessions/<id>/factor-matrix.csv`
  - `POST import-from-session` 读 `loop_index`

- [ ] **Step 3: pytest 绿灯**

---

### Task 6: Vue — SessionDetail 深度 UI

**Files:**
- Modify: `QuantDinger-Vue/src/api/rdagent.js`
- Modify: `QuantDinger-Vue/src/views/rdagent/SessionDetail.vue`

**Interfaces:**
- `fetchFactorMatrix(id, params)`
- `downloadFactorMatrixCsv(id, params)`

- [ ] **Step 1: API 封装**

- [ ] **Step 2: 总览增加 SOTA 因子库表（展开懒加载 code：`include=code,sota_library`）**

- [ ] **Step 3: Loop 下拉显示 `Loop_N · 因子|模型`；`kind===model` 时开发区改为模型卡片（architecture / hyperparams / model.py / training_log / feedbacks）**

- [ ] **Step 4: 新增 Tab「因子矩阵」：列清单、覆盖区间、抽样表、下载 CSV**

- [ ] **Step 5: 总览 ECharts 折线加强（IC / Rank IC / 年化 / 回撤）；反馈净值图保留缩放**

- [ ] **Step 6: 手动点选本地会话冒烟（或组件级不影响 CI）**

---

### Task 7: Vue — 导入表单选 Loop

**Files:**
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue`
- Modify: `QuantDinger-Vue/src/api/rdagent.js`（import body）

- [ ] **Step 1: 打开导入前拉取 `detail?include=summary`（或列表已有 exportable），填充 Loop 下拉**

- [ ] **Step 2: `importFromSession` POST 带 `loop_index`；未选则不传；version 预填 `session_*_loopN`**

- [ ] **Step 3: 文案：「不选 Loop = 最新预测产物」**

---

### Task 8: 文档 + 本地镜像验证

**Files:**
- Modify: `QuantDinger/docs/RDAGENT_EXTERNAL_ALPHA_CN.md`

- [ ] **Step 1: 文档补充：按 Loop 导入、详情深度（库/模型/矩阵）**

- [ ] **Step 2: 重启 bridge；`docker compose build backend frontend`（`FRONTEND_SRC_PATH`）并 recreate**

- [ ] **Step 3: 对照验收清单（spec §6）用手点 `qlib_quant` 多轮会话**

---

## Spec coverage check

| Spec 要求 | Task |
|-----------|------|
| sota_library / 轨迹 | 2, 6 |
| 模型深度 + logs | 1, 2, 6 |
| 按 Loop 导入 | 3, 5, 7 |
| 交互图 | 6 |
| parquet 矩阵 + CSV | 4, 5, 6 |
| 兼容旧导入 | 3, 5, 7 |
| 文档 | 8 |

## Placeholder scan

无 TBD/TODO；接口名前后一致：`loop_index`、`sota_library`、`factor_matrix`、`artifacts`/`factors` 双写兼容。
