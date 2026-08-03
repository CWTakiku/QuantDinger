# 研究工厂会话详情 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 QuantDinger 研究工厂内原生展示 RD-Agent 会话的总览指标、假设、因子代码/成败、反馈、净值曲线与编码进化回放，并支持指标 CSV 下载。

**Architecture:** Bridge 从 `log/<session>/Loop_*` 目标 pickle 抽取 JSON；QD 鉴权代理；Vue 同页 SessionDetail 组件按 `include` 懒加载大字段。

**Tech Stack:** Python 3.10 + Flask（bridge）、QD Flask 代理、Vue 2 + Ant Design Vue + ECharts、pytest。

**Spec:** `QuantDinger/docs/superpowers/specs/2026-08-03-research-factory-session-detail-design.md`

## Global Constraints

- 只读会话产物，不改挖因子流程；RD UI 按钮保留。
- 禁止 `FileStorage.iter_msg()` 全量扫 pkl；只 glob 目标路径。
- Commit message 中文 Conventional Commits（若用户要求提交时）。
- 前端文案中文；API `error` 英文/既有风格均可，前端展示用中文提示。
- P0+P1+P2 一次交付。

## File Map

| File | Responsibility |
|------|----------------|
| `rdagent-workspace/rdagent_bridge/session_detail.py` | pickle → detail/metrics dict |
| `rdagent-workspace/rdagent_bridge/app.py` | `/v1/sessions/<id>/detail`, `/metrics.csv` |
| `rdagent-workspace/rdagent_bridge/tests/test_session_detail.py` | bridge 单元测试 |
| `QuantDinger/.../rdagent_bridge/client.py` | `session_detail`, `session_metrics_csv` |
| `QuantDinger/.../routes/rdagent.py` | QD 代理路由 |
| `QuantDinger/.../tests/test_rdagent_session_detail.py` | 代理测试 |
| `QuantDinger-Vue/src/api/rdagent.js` | API 封装 |
| `QuantDinger-Vue/src/views/rdagent/SessionDetail.vue` | 详情 UI |
| `QuantDinger-Vue/src/views/rdagent/index.vue` | 接入详情区 |

---

### Task 1: Bridge — metric 映射与 detail 抽取核心

**Files:**
- Create: `rdagent-workspace/rdagent_bridge/session_detail.py`
- Test: `rdagent-workspace/rdagent_bridge/tests/test_session_detail.py`

**Interfaces:**
- Produces: `parse_include(raw: str | None) -> set[str]`
- Produces: `series_to_metrics(series) -> dict[str, float | None]`
- Produces: `build_session_detail(workspace: Path, session_id: str, include: set[str]) -> dict`
- Produces: `build_metrics_csv_rows(detail: dict) -> list[dict]`

- [ ] **Step 1: Write failing tests**

```python
# rdagent_bridge/tests/test_session_detail.py
from pathlib import Path
import pickle
import pandas as pd
import pytest

from rdagent_bridge.session_detail import (
    parse_include,
    series_to_metrics,
    build_session_detail,
    build_metrics_csv_rows,
)


def test_parse_include_default():
    assert parse_include(None) == {"summary", "loops", "factors"}
    assert "evolution" in parse_include("all")


def test_series_to_metrics_keys():
    s = pd.Series({
        "IC": 0.1,
        "ICIR": 0.2,
        "Rank IC": 0.3,
        "Rank ICIR": 0.4,
        "1day.excess_return_with_cost.annualized_return": 0.05,
        "1day.excess_return_with_cost.max_drawdown": -0.1,
        "1day.excess_return_with_cost.information_ratio": 0.5,
    })
    m = series_to_metrics(s)
    assert m["ic"] == pytest.approx(0.1)
    assert m["annualized_return"] == pytest.approx(0.05)


def test_build_session_detail_missing_session(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_session_detail(tmp_path, "2026-08-01_00-00-00-1", {"summary"})


def test_build_session_detail_loop_without_runner(tmp_path):
    sid = "2026-08-01_12-00-00-9"
    loop = tmp_path / "log" / sid / "Loop_0" / "direct_exp_gen" / "hypothesis generation" / "1"
    loop.mkdir(parents=True)
    # minimal stand-in object with .hypothesis attribute
    class H:
        hypothesis = "h text"
        reason = "r text"
        concise_reason = concise_observation = concise_justification = concise_knowledge = None
    pickle.dump(H(), open(loop / "h.pkl", "wb"))
    detail = build_session_detail(tmp_path, sid, {"summary", "loops", "factors"})
    assert detail["session_id"] == sid
    assert detail["loops"][0]["metrics"] is None
    assert detail["loops"][0]["hypothesis"]["hypothesis"] == "h text"
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

Run: `cd /Users/taki/quant/rdagent-workspace && /Users/taki/miniforge3/envs/rdagent/bin/python -m pytest rdagent_bridge/tests/test_session_detail.py -q --tb=line`

- [ ] **Step 3: Implement `session_detail.py`**

实现要点（完整实现写入该文件）：

```python
METRIC_KEYS = {
    "ic": "IC",
    "icir": "ICIR",
    "rank_ic": "Rank IC",
    "rank_icir": "Rank ICIR",
    "annualized_return": "1day.excess_return_with_cost.annualized_return",
    "max_drawdown": "1day.excess_return_with_cost.max_drawdown",
    "information_ratio": "1day.excess_return_with_cost.information_ratio",
}
DEFAULT_INCLUDE = {"summary", "loops", "factors"}
ALL_INCLUDE = DEFAULT_INCLUDE | {"code", "equity", "evolution"}

def parse_include(raw): ...
def series_to_metrics(series): ...  # None-safe float
def _latest_pkl(root: Path, pattern: str) -> Path | None: ...
def _load_pickle(path: Path): ...
def _hypothesis_dict(obj) -> dict: ...
def _feedback_dict(obj) -> dict: ...
def _factor_list(exp, coder_list, evo_feedback, include) -> list: ...
def _downsample_equity(df) -> list[dict]: ...  # date, account, bench, return
def build_session_detail(workspace, session_id, include) -> dict: ...
def build_metrics_csv_rows(detail) -> list[dict]: ...
```

`build_session_detail`：校验 session_id（复用 `sessions.resolve_session_dir`）；枚举 `Loop_*`；每 loop 按 spec glob；baseline 取首个有 runner 的 `based_experiments[0].result`。

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Integration smoke on real session**

```bash
/Users/taki/miniforge3/envs/rdagent/bin/python - <<'PY'
from pathlib import Path
from rdagent_bridge.session_detail import build_session_detail, parse_include
ws = Path('/Users/taki/quant/rdagent-workspace')
d = build_session_detail(ws, '2026-08-02_11-48-06-768124', parse_include('all'))
assert d['baseline']['ic'] is not None
assert d['loops'][0]['metrics']['ic'] is not None
print('loops', len(d['loops']), 'ic0', d['loops'][0]['metrics']['ic'])
PY
```

Expected: prints loop count ≥ 1 and IC ≈ 0.0297

---

### Task 2: Bridge HTTP — detail + metrics.csv

**Files:**
- Modify: `rdagent-workspace/rdagent_bridge/app.py`
- Modify: `rdagent-workspace/rdagent_bridge/tests/test_app_http.py`

**Interfaces:**
- Consumes: `build_session_detail`, `build_metrics_csv_rows`, `parse_include`
- Produces: `GET /v1/sessions/<id>/detail`, `GET /v1/sessions/<id>/metrics.csv`

- [ ] **Step 1: Add HTTP tests**

```python
def test_session_detail_ok(client, auth_headers, tmp_workspace_with_session):
    r = client.get("/v1/sessions/2026-08-01_12-00-00-9/detail", headers=auth_headers)
    assert r.status_code == 200
    assert "loops" in r.get_json()

def test_session_metrics_csv(client, auth_headers, tmp_workspace_with_session):
    r = client.get("/v1/sessions/2026-08-01_12-00-00-9/metrics.csv", headers=auth_headers)
    assert r.status_code == 200
    assert "text/csv" in r.content_type
    assert b"ic," in r.data or b"ic\n" in r.data or b"ic" in r.data
```

（按现有 `test_app_http.py` fixture 风格适配 workspace。）

- [ ] **Step 2: Implement routes in `app.py`**

```python
@app.get("/v1/sessions/<session_id>/detail")
def session_detail(session_id: str):
    auth = _require_token(); ...
    include = parse_include(request.args.get("include"))
    try:
        return jsonify(build_session_detail(config.workspace, session_id, include))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

@app.get("/v1/sessions/<session_id>/metrics.csv")
def session_metrics_csv(session_id: str):
    ...
    detail = build_session_detail(..., parse_include("summary,loops"))
    rows = build_metrics_csv_rows(detail)
    # csv.DictWriter → Response attachment
```

- [ ] **Step 3: Run** `pytest rdagent_bridge/tests/test_app_http.py -q --tb=short`
- [ ] **Step 4: Restart bridge** via `scripts/start_bridge_detached.py` after kill old PID；curl 真实 session detail。

---

### Task 3: QD client + routes

**Files:**
- Modify: `QuantDinger/backend_api_python/app/services/rdagent_bridge/client.py`
- Modify: `QuantDinger/backend_api_python/app/routes/rdagent.py`
- Create: `QuantDinger/backend_api_python/tests/test_rdagent_session_detail.py`

**Interfaces:**
- Produces: `RdAgentBridgeClient.session_detail(session_id: str, include: str | None = None) -> dict`
- Produces: `RdAgentBridgeClient.session_metrics_csv(session_id: str) -> tuple[bytes, str]`  # body, content_type
- Produces: routes `GET /api/rdagent/sessions/<id>/detail`, `.../metrics.csv`

- [ ] **Step 1: Client methods**（timeout 用 `max(self.timeout_s, 60)`）

```python
def session_detail(self, session_id: str, include: str | None = None) -> dict[str, Any]:
    params = {}
    if include:
        params["include"] = include
    return self._request("GET", f"/v1/sessions/{session_id}/detail", params=params)

def session_metrics_csv(self, session_id: str) -> tuple[bytes, str]:
    # similar to download_export but return bytes + content-type
```

- [ ] **Step 2: Routes**

```python
@rdagent_blp.route("/sessions/<string:session_id>/detail", methods=["GET"])
@login_required
@admin_required
def rdagent_session_detail(session_id):
    include = request.args.get("include")
    return _success(get_bridge_client().session_detail(session_id, include=include))

@rdagent_blp.route("/sessions/<string:session_id>/metrics.csv", methods=["GET"])
...
    body, ctype = get_bridge_client().session_metrics_csv(session_id)
    return Response(body, mimetype=ctype or "text/csv", headers={...})
```

- [ ] **Step 3: Tests with monkeypatched client**
- [ ] **Step 4: Run** `cd QuantDinger/backend_api_python && .venv/bin/python -m pytest tests/test_rdagent_session_detail.py -q`
- [ ] **Step 5: Deploy** `docker cp` client.py + routes 进 `quantdinger-backend` 并 `docker compose restart backend`

---

### Task 4: Vue API + SessionDetail 总览/循环（P0+P1 骨架）

**Files:**
- Modify: `QuantDinger-Vue/src/api/rdagent.js`
- Create: `QuantDinger-Vue/src/views/rdagent/SessionDetail.vue`
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue`

**Interfaces:**
- Produces: `fetchSessionDetail(id, include)`, `downloadSessionMetricsCsv(id)`
- Produces: `<SessionDetail :session-id="..." @import="..." />`

- [ ] **Step 1: API**

```js
export function fetchSessionDetail (id, include) {
  return request({
    url: `/api/rdagent/sessions/${encodeURIComponent(id)}/detail`,
    method: 'get',
    params: include ? { include } : undefined,
    timeout: 60000
  })
}

export function downloadSessionMetricsCsv (id) {
  return request({
    url: `/api/rdagent/sessions/${encodeURIComponent(id)}/metrics.csv`,
    method: 'get',
    responseType: 'blob',
    timeout: 60000
  })
}
```

- [ ] **Step 2: SessionDetail.vue**

结构：

- props: `sessionId`
- data: `detail`, `loading`, `showTrueOnly`, `activeLoop`, `loopTab` (`research|dev|feedback|evolution`)
- mounted/watch sessionId → `load('summary,loops,factors')`
- 总览：`a-table` 指标；ECharts 折线（IC / annualized_return / max_drawdown）；假设列表
- 循环：`a-select` Loop + `a-tabs`
- 研究 Tab：hypothesis / reason
- 开发 Tab：因子名 ✔️/❌；点开后再 `load` 含 `code`
- 反馈 Tab：指标对比 + decision 文案；进入时 `include=equity`；ECharts account vs bench
- 进化 Tab：进入时 `include=evolution`；按 evo_loop 列表展示
- 按钮：下载 CSV、`$emit('import', sessionId)`

样式跟 `index.vue` 的 `workspace-card` 一致。

- [ ] **Step 3: index.vue**

- 会话表操作列加「查看详情」
- `selectedDetailSessionId`
- 会话表与导入区之间插入 `<session-detail v-if="selectedDetailSessionId" ...>`
- `@import` 填 `importForm.session_id`

- [ ] **Step 4: 本地验证**（Vite 或 rebuild frontend；至少 API + 组件无语法错误）

---

### Task 5: P2 打磨 — 进化回放完整度 + CSV + 验收

**Files:**
- Modify: `session_detail.py`（evolution 序列）
- Modify: `SessionDetail.vue`（进化 UI、CSV 下载触发）

- [ ] **Step 1: 确认 evolution 结构**

```json
"evolution": [
  {"evo_loop": 0, "decision": true, "feedback": "...", "code": "..."},
  {"evo_loop": 1, "decision": false, "feedback": "...", "code": "..."}
]
```

每个 factor 一条 `evolution` 数组（在 `include=evolution` 时填充）。

- [ ] **Step 2: UI** 时间线 / Collapse；失败轮标红。

- [ ] **Step 3: CSV 下载** blob → `URL.createObjectURL` → `<a download>`。

- [ ] **Step 4: 端到端验收清单（对照 spec §9）**

1. 研究工厂打开样本会话，Loop_0 IC 与 pickle 一致  
2. 假设、因子成败、代码、decision 可见  
3. 净值曲线可见  
4. 编码进化可回放  
5. metrics.csv 可下载  
6. 「打开 UI」仍可用  

- [ ] **Step 5: 如需持久化前端** — `FRONTEND_SRC_PATH=/Users/taki/quant/QuantDinger-Vue docker compose -f docker-compose.yml -f docker-compose.build.yml build frontend && ... up -d frontend`；或继续用热修/Vite。

---

## Spec Coverage Check

| Spec 项 | Task |
|---------|------|
| detail API + include | 1–2 |
| metrics.csv | 2, 5 |
| QD 代理 | 3 |
| 总览表/折线/假设筛选 | 4 |
| 研究/开发/反馈 + 代码/净值 | 4 |
| 编码进化 | 4–5 |
| 保留 RD UI | 4（不删除按钮） |
| 测试 | 1–3 |

## Placeholder Scan

无 TBD/TODO 步骤；真实 session id 与路径已写明。
