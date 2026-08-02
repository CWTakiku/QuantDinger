# QD ↔ RD-Agent 研究工厂模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 QuantDinger 增加「研究工厂」模块，经本机 `rdagent-bridge:19901` HTTP 启停 RD-Agent、查看会话/日志，并编排导出会话分数导入 `qd_external_alpha_scores`。

**Architecture:** Vue → QD Human API `/api/rdagent/*`（鉴权代理）→ 宿主机 bridge → micromamba/`rdagent fin_*` + `log/` + 可选 `server_ui:19899`。QD Docker **不**安装 RD 依赖；分数落库复用 `persist_external_alpha_scores`。

**Tech Stack:** Python 3.10（bridge，复用 `rdagent` env）、Flask/FastAPI 轻量 HTTP（bridge 用 stdlib/`flask` 二选一，见 Task 1）、QD Python 3.12 Flask + flask-smorest、Vue 2 async-router、pytest

**Spec:** `docs/superpowers/specs/2026-08-02-qd-rdagent-bridge-module-design.md`

## Global Constraints

- 研究进程仅跑在本机 `RDAGENT_WORKSPACE`（默认 `/Users/taki/quant/rdagent-workspace`）；禁止把 rdagent/pyqlib 写入 `backend_api_python/requirements*.txt` 或 Dockerfile
- Bridge 默认端口 **19901**；RD UI **19899**；同时最多 **1** 个挖因子任务
- scenario 白名单：`fin_factor` | `fin_quant`；`step_n` 上限 **20**
- 鉴权：bridge 要求头 `X-RDAgent-Bridge-Token`；QD 路由 `@admin_required`
- LLM / CUSTOM_API_KEY 只存在于 `rdagent-workspace/.env`，不得经 QD API 回传前端
- 导入默认 `source=rdagent`；禁止默认启用日期 remap 烟测脚本（`bridge_rdagent_scores_recent.py`）
- Commit message：中文 Conventional Commits（`feat:` / `fix:` / `test:` / `docs:` / `chore:`）
- 一期不做：MCP tools 实装、iframe 嵌 UI、多任务队列

## File Map

| 文件 | 职责 |
|------|------|
| `rdagent-workspace/rdagent_bridge/__init__.py` | 包标识 |
| `rdagent-workspace/rdagent_bridge/config.py` | 环境变量与常量 |
| `rdagent-workspace/rdagent_bridge/jobs.py` | 单任务进程管理 |
| `rdagent-workspace/rdagent_bridge/sessions.py` | 列 `log/` 会话 |
| `rdagent-workspace/rdagent_bridge/export_scores.py` | 调现有导出脚本 / 找 result |
| `rdagent-workspace/rdagent_bridge/app.py` | Flask HTTP `:19901` |
| `rdagent-workspace/scripts/run_rdagent_bridge.sh` | 启动脚本 |
| `rdagent-workspace/docs/BRIDGE_CN.md` | bridge 运维 |
| `QuantDinger/.../app/services/rdagent_bridge/client.py` | HTTP 客户端 |
| `QuantDinger/.../app/services/rdagent_bridge/import_session.py` | 下载/读 CSV → persist |
| `QuantDinger/.../app/routes/rdagent.py` | Human API |
| `QuantDinger/.../app/openapi/register.py` / `tags.py` | 注册 |
| `QuantDinger/.../tests/test_rdagent_bridge_client.py` | 客户端单测（mock） |
| `QuantDinger/.../tests/test_rdagent_routes.py` | 路由单测（mock） |
| `QuantDinger-Vue/src/api/rdagent.js` | 前端 API |
| `QuantDinger-Vue/src/views/rdagent/index.vue` | 研究工厂页 |
| `QuantDinger-Vue/src/config/router.config.js` | 菜单 |
| `QuantDinger-Vue/src/locales/lang/zh-CN.js`（及 en-US 若有对称键） | i18n |
| `QuantDinger/docs/RDAGENT_EXTERNAL_ALPHA_CN.md` | 追加模块入口说明 |

仓库边界：bridge 代码提交在 `rdagent-workspace`（若该目录非 git，则以文件落地 + 在 QD docs 引用路径）；QD/Vue 改动在各自仓库提交。

---

## Phase P0 — Bridge + 代理 + 页面（启停/状态/日志/会话）

### Task 1: Bridge 配置与任务管理（无 HTTP）

**Files:**
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/__init__.py`
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/config.py`
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/jobs.py`
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/sessions.py`
- Test: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/tests/test_jobs_unit.py`

**Interfaces:**
- Produces:
  - `BridgeConfig`：`workspace: Path`, `token: str`, `port: int=19901`, `micromamba: Path`, `env_name: str="rdagent"`, `max_step_n: int=20`
  - `JobManager.start(scenario: str, step_n: int, timeout_h: float | None) -> dict`
  - `JobManager.stop(job_id: str) -> dict`
  - `JobManager.get(job_id: str) -> dict | None`
  - `JobManager.list_jobs() -> list[dict]`
  - `list_sessions(workspace: Path) -> list[dict]`  # `{id, path, mtime}`
- Consumes: 本机 micromamba；`workspace/.env` 由子进程 `source` 或 `env` 注入已有变量（勿打印密钥）

- [ ] **Step 1: 写失败单测（无真实 spawn）**

```python
# rdagent_bridge/tests/test_jobs_unit.py
from pathlib import Path
from rdagent_bridge.jobs import JobManager

def test_reject_unknown_scenario(tmp_path):
    jm = JobManager(workspace=tmp_path, run_cmd=lambda *a, **k: None)
    try:
        jm.start("fin_model", step_n=1)
        assert False, "should raise"
    except ValueError as e:
        assert "scenario" in str(e).lower()

def test_reject_step_n_too_large(tmp_path):
    jm = JobManager(workspace=tmp_path, run_cmd=lambda *a, **k: None, max_step_n=20)
    try:
        jm.start("fin_factor", step_n=21)
        assert False
    except ValueError:
        pass

def test_single_job_lock(tmp_path):
    calls = []
    def fake_run(cmd, **kwargs):
        class P:
            pid = 123
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0
        calls.append(cmd)
        return P()
    jm = JobManager(workspace=tmp_path, run_cmd=fake_run)
    j1 = jm.start("fin_factor", step_n=1)
    try:
        jm.start("fin_factor", step_n=1)
        assert False
    except RuntimeError as e:
        assert "already" in str(e).lower() or "running" in str(e).lower()
    assert j1["status"] == "running"
```

- [ ] **Step 2: 运行单测确认失败**

```bash
cd /Users/taki/quant/rdagent-workspace
PYTHONPATH=. /Users/taki/miniforge3/micromamba run -n rdagent python -m pytest rdagent_bridge/tests/test_jobs_unit.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `config.py` / `jobs.py` / `sessions.py`**

`config.py` 要点：

```python
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class BridgeConfig:
    workspace: Path
    token: str
    port: int = 19901
    micromamba: Path = Path.home() / "miniforge3" / "micromamba"
    env_name: str = "rdagent"
    max_step_n: int = 20

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        ws = Path(os.environ.get("RDAGENT_WORKSPACE", str(Path.home() / "quant" / "rdagent-workspace"))).resolve()
        token = os.environ.get("RDAGENT_BRIDGE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("RDAGENT_BRIDGE_TOKEN is required")
        port = int(os.environ.get("RDAGENT_BRIDGE_PORT", "19901"))
        return cls(workspace=ws, token=token, port=port)
```

`jobs.py` 要点：用可注入 `run_cmd`（默认 `subprocess.Popen`）；命令形如：

```text
micromamba run -n rdagent rdagent <scenario> --step-n <n>
```

工作目录=`workspace`；环境合并 `os.environ` + 从 `workspace/.env` 解析的非空键（可用简单 KEY=VAL 解析，跳过注释）；stdout/stderr 写入 `workspace/logs/bridge_job_<id>.log`；`job_id` 用 uuid4 hex 前 12 位；状态字段：`id, scenario, step_n, status, pid, log_file, session_hint, started_at, finished_at, exit_code, error`。

`sessions.py`：扫描 `workspace/log/*`，目录名匹配时间戳且存在 `__session__` 或任意子目录则列入。

- [ ] **Step 4: 再跑单测**

Expected: PASS

- [ ] **Step 5: Commit（若 workspace 在 git 内）**

```bash
git add rdagent_bridge/
git commit -m "$(cat <<'EOF'
feat: 新增本机rdagent桥接任务管理

EOF
)"
```

---

### Task 2: Bridge Flask HTTP 服务

**Files:**
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/app.py`
- Create: `/Users/taki/quant/rdagent-workspace/scripts/run_rdagent_bridge.sh`
- Create: `/Users/taki/quant/rdagent-workspace/docs/BRIDGE_CN.md`
- Test: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/tests/test_app_http.py`

**Interfaces:**
- Consumes: `BridgeConfig`, `JobManager`, `list_sessions`
- Produces: HTTP 路由（见 spec §4.2）；未实现的 `export` / `ui` 可先返回 501 或最小实现（UI 探活用 `urllib` 打 19899）

- [ ] **Step 1: 写 HTTP 鉴权与 health 测试**

```python
# rdagent_bridge/tests/test_app_http.py
import os
os.environ["RDAGENT_BRIDGE_TOKEN"] = "test-token"
os.environ["RDAGENT_WORKSPACE"] = "/tmp/rdagent_ws_test"

from rdagent_bridge.app import create_app

def test_health_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("RDAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RDAGENT_BRIDGE_TOKEN", "test-token")
    app = create_app()
    c = app.test_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

def test_jobs_require_token(tmp_path, monkeypatch):
    monkeypatch.setenv("RDAGENT_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RDAGENT_BRIDGE_TOKEN", "test-token")
    app = create_app()
    c = app.test_client()
    assert c.get("/v1/jobs").status_code == 401
    r = c.get("/v1/jobs", headers={"X-RDAgent-Bridge-Token": "test-token"})
    assert r.status_code == 200
```

- [ ] **Step 2: 跑测确认失败 → 实现 `create_app()`**

依赖：`flask`（`rdagent` env 已有则复用；否则 `micromamba run -n rdagent pip install flask`）。

路由实现清单：

| 方法 | 路径 |
|------|------|
| GET | `/health` |
| GET/POST | `/v1/jobs` |
| GET | `/v1/jobs/<id>` |
| POST | `/v1/jobs/<id>/stop` |
| GET | `/v1/jobs/<id>/logs?tail=200` |
| GET | `/v1/sessions` |
| GET | `/v1/ui` |
| POST | `/v1/ui/start` |  # 可用绝对路径启动 server_ui app.py；已占用则返回 running |
| POST | `/v1/export` |  # Task 5 再充实；此处可先 501 |

- [ ] **Step 3: `run_rdagent_bridge.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/miniforge3}"
export RDAGENT_WORKSPACE="${RDAGENT_WORKSPACE:-$ROOT}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
set -a
# shellcheck disable=SC1091
source "$ROOT/.env"
set +a
: "${RDAGENT_BRIDGE_TOKEN:?set RDAGENT_BRIDGE_TOKEN in .env}"
exec "$MAMBA_ROOT_PREFIX/micromamba" run -n rdagent python -m rdagent_bridge.app
```

在 `.env.example`（若无则新建）增加：

```text
RDAGENT_BRIDGE_TOKEN=change-me
RDAGENT_BRIDGE_PORT=19901
```

- [ ] **Step 4: 文档 `docs/BRIDGE_CN.md`**（启动、健康检查、与 QD `host.docker.internal`）

- [ ] **Step 5: 本地手测**

```bash
export RDAGENT_BRIDGE_TOKEN=dev-token
# 启动后：
curl -s http://127.0.0.1:19901/health
curl -s -H "X-RDAgent-Bridge-Token: $RDAGENT_BRIDGE_TOKEN" http://127.0.0.1:19901/v1/sessions
```

Expected: JSON `ok: true`；sessions 含已有 log 目录

- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 提供rdagent桥接http服务

EOF
)"
```

---

### Task 3: QD Bridge HTTP 客户端 + 配置

**Files:**
- Create: `backend_api_python/app/services/rdagent_bridge/__init__.py`
- Create: `backend_api_python/app/services/rdagent_bridge/client.py`
- Create: `backend_api_python/app/services/rdagent_bridge/errors.py`
- Test: `backend_api_python/tests/test_rdagent_bridge_client.py`
- Modify: Docker/compose 文档或 `.env.example`（若项目有）：`RDAGENT_BRIDGE_URL`、`RDAGENT_BRIDGE_TOKEN`

**Interfaces:**
- Produces:
  - `class RdAgentBridgeError(Exception): status_code: int; code: str; message: str`
  - `class RdAgentBridgeClient:`
    - `health() -> dict`
    - `list_jobs() -> list`
    - `start_job(scenario: str, step_n: int, timeout_h: float | None = None) -> dict`
    - `get_job(job_id: str) -> dict`
    - `stop_job(job_id: str) -> dict`
    - `job_logs(job_id: str, tail: int = 200) -> dict`
    - `list_sessions() -> list`
    - `ui_status() -> dict`
    - `ui_start() -> dict`
- Consumes: `urllib.request` 或项目已有 `requests`；超时 `RDAGENT_BRIDGE_TIMEOUT_S`（默认 30）

- [ ] **Step 1: 失败单测（httpx/urllib mock）**

```python
def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("RDAGENT_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("RDAGENT_BRIDGE_URL", "http://127.0.0.1:19901")
    from app.services.rdagent_bridge.client import RdAgentBridgeClient
    try:
        RdAgentBridgeClient.from_env()
        assert False
    except RdAgentBridgeError as e:
        assert e.status_code == 500

def test_health_maps_connection_error(monkeypatch):
    monkeypatch.setenv("RDAGENT_BRIDGE_TOKEN", "t")
    monkeypatch.setenv("RDAGENT_BRIDGE_URL", "http://127.0.0.1:1")
    from app.services.rdagent_bridge.client import RdAgentBridgeClient
    c = RdAgentBridgeClient.from_env()
    try:
        c.health()
        assert False
    except RdAgentBridgeError as e:
        assert e.status_code == 503
        assert "bridge" in e.code.lower() or "rdagent" in e.code.lower()
```

- [ ] **Step 2: 实现 client（401/5xx 映射为 `RdAgentBridgeError`）**

响应约定：bridge 返回 JSON；QD 不改写业务字段，仅包一层。

- [ ] **Step 3: pytest 通过**

```bash
cd backend_api_python
python -m pytest tests/test_rdagent_bridge_client.py -q
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 新增rdagent桥接客户端

EOF
)"
```

---

### Task 4: QD Human API 路由 + OpenAPI

**Files:**
- Create: `backend_api_python/app/routes/rdagent.py`
- Modify: `backend_api_python/app/openapi/tags.py` — 增加 `RDAGENT = "RDAgent"` 并加入 `ALL_TAGS`
- Modify: `backend_api_python/app/openapi/register.py` — `("/api/rdagent", "RDAgent")` 与 `register_blueprint(rdagent_blp, "/api/rdagent")`
- Test: `backend_api_python/tests/test_rdagent_routes.py`
- Run: `python scripts/export_openapi.py`；`pytest tests/test_openapi.py -q`

**Interfaces:**
- Consumes: `RdAgentBridgeClient`, `@admin_required`, `@login_required`
- Produces: 与 universe 一致的 `{code, msg, data}`；bridge 错误 → `code=0`, HTTP 用 `exc.status_code`

路由：

| QD | Bridge |
|----|--------|
| `GET /status` | `health` + 可选 `list_jobs` 当前 running |
| `GET /jobs` | `list_jobs` |
| `POST /jobs` | body `{scenario, step_n, timeout_h?}` → `start_job` |
| `GET /jobs/<id>` | `get_job` |
| `POST /jobs/<id>/stop` | `stop_job` |
| `GET /jobs/<id>/logs` | `job_logs` |
| `GET /sessions` | `list_sessions` |
| `GET /ui` | `ui_status` |
| `POST /ui/start` | `ui_start` |

- [ ] **Step 1: 路由测试（mock client）**

```python
def test_status_requires_admin(client):
    # 使用项目既有 flask test client fixture；非 admin 期望 403
    ...

def test_status_ok(monkeypatch, admin_client):
    class Fake:
        def health(self): return {"ok": True, "workspace": "/tmp"}
        def list_jobs(self): return []
    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    r = admin_client.get("/api/rdagent/status")
    assert r.status_code == 200
    assert r.get_json()["code"] == 1
```

（按仓库现有 auth fixture 名称改写；若无 admin fixture，用 `unittest.mock` patch `g.user` + 直接调 view。）

- [ ] **Step 2: 实现 `rdagent.py` 并注册**

- [ ] **Step 3: 导出 OpenAPI + 测试**

```bash
cd backend_api_python
python scripts/export_openapi.py
python -m pytest tests/test_rdagent_routes.py tests/test_openapi.py -q
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 暴露rdagent研究工厂代理接口

EOF
)"
```

---

### Task 5: Vue 研究工厂页（P0 UI）

**Files:**
- Create: `QuantDinger-Vue/src/api/rdagent.js`
- Create: `QuantDinger-Vue/src/views/rdagent/index.vue`
- Modify: `QuantDinger-Vue/src/config/router.config.js` — 在 `universe-manager` 后插入路由
- Modify: `QuantDinger-Vue/src/locales/lang/zh-CN.js` — `menu.dashboard.rdagent: '研究工厂'`
- Modify: `QuantDinger-Vue/src/locales/lang/en-US.js`（若存在对称 `menu.dashboard.*`）— `RD-Agent Lab`

**Interfaces:**
- Consumes: `/api/rdagent/*`（项目 `request`/`axios` 封装与 `api/universe.js` 同风格）

- [ ] **Step 1: `api/rdagent.js`**

```javascript
import request from '@/utils/request'

export function fetchRdagentStatus () {
  return request({ url: '/rdagent/status', method: 'get' })
}
export function fetchRdagentJobs () {
  return request({ url: '/rdagent/jobs', method: 'get' })
}
export function startRdagentJob (data) {
  return request({ url: '/rdagent/jobs', method: 'post', data })
}
export function stopRdagentJob (id) {
  return request({ url: `/rdagent/jobs/${id}/stop`, method: 'post' })
}
export function fetchRdagentJobLogs (id, params) {
  return request({ url: `/rdagent/jobs/${id}/logs`, method: 'get', params })
}
export function fetchRdagentSessions () {
  return request({ url: '/rdagent/sessions', method: 'get' })
}
export function startRdagentUi () {
  return request({ url: '/rdagent/ui/start', method: 'post' })
}
```

（确认 `request` 的 baseURL 是否已含 `/api` 前缀——与 `api/universe.js` 保持一致。）

- [ ] **Step 2: 路由**

```javascript
{
  path: '/rdagent',
  name: 'RdAgentLab',
  component: () => import('@/views/rdagent'),
  meta: { title: 'menu.dashboard.rdagent', keepAlive: false, icon: 'experiment', permission: ['admin'] }
}
```

- [ ] **Step 3: `views/rdagent/index.vue` 区块**

1. Alert：bridge 离线时中文提示「请先在本机启动 rdagent-bridge（端口 19901）」  
2. 表单：scenario 下拉、`step_n`（1–20）、启动/停止  
3. 状态 + `<pre>` 日志尾部（定时 3s 刷新当 running）  
4. 会话 Table + 「打开 UI」`window.open('http://127.0.0.1:19899')`  
5. 文案注明：挖因子在本机执行，非 Docker 内  

保持与现有 Ant Design Vue 页面风格一致（参考 `universe-manager`），避免过度设计。

- [ ] **Step 4: 本地联调清单**

1. 启动 bridge（Task 2 脚本）  
2. QD backend 设置 `RDAGENT_BRIDGE_URL` / `RDAGENT_BRIDGE_TOKEN`  
3. admin 登录 → 打开「研究工厂」→ 状态已连接  
4. `step_n=1` 启动 → 见 running/日志  

- [ ] **Step 5: Commit（Vue 仓库）**

```bash
git commit -m "$(cat <<'EOF'
feat: 新增研究工厂页面对接rdagent

EOF
)"
```

---

## Phase P1 — 导出会话并导入 External Alpha

### Task 6: Bridge `POST /v1/export` + 下载

**Files:**
- Create: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/export_scores.py`
- Modify: `/Users/taki/quant/rdagent-workspace/rdagent_bridge/app.py`
- Reuse: `scripts/export_qlib_pred_to_qd_csv.py`

**Interfaces:**
- `export_session(workspace, session_id, source, version, universe) -> dict`  
  返回 `{export_id, path, row_count, as_of_min, as_of_max, source, version}`  
- `GET /v1/export/<export_id>/download` → `text/csv` 附件  
- 在会话目录递归查找最新的 `result.h5` / `pred.pkl`（按 mtime）；找不到则 400 + 明确错误

- [ ] **Step 1: 单测用临时 CSV fixture 验证 export 元数据**

- [ ] **Step 2: 实现查找 + 调用导出脚本（subprocess 或 import 函数）**  
  输出目录：`workspace/exports/<export_id>.csv`

- [ ] **Step 3: HTTP download 路由**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 桥接支持导出会话分数csv

EOF
)"
```

---

### Task 7: QD `POST /api/rdagent/import-from-session`

**Files:**
- Create: `backend_api_python/app/services/rdagent_bridge/import_session.py`
- Modify: `backend_api_python/app/routes/rdagent.py`
- Modify: `backend_api_python/app/services/rdagent_bridge/client.py` — `export_session`, `download_export`
- Test: `backend_api_python/tests/test_rdagent_import_session.py`
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue` — 导入表单
- Modify: `QuantDinger-Vue/src/api/rdagent.js` — `importFromSession`

**Interfaces:**
- `import_session_scores(session_id: str, source: str, version: str, universe: str = "csi300") -> dict`  
  流程：`client.export_session` → `client.download_export` → 解析 CSV 行 → `persist_external_alpha_scores(rows)`  
- 路由：`POST /import-from-session` body `{session_id, source?, version?, universe?}`  
  默认 `source=rdagent`，`version=session_<session_id>`（截断至 120 字符）

- [ ] **Step 1: 单测 mock download 返回最小 CSV，断言 persist 被调用且统计正确**

```python
CSV = "as_of,symbol,score\n2026-07-25,600519,1.2\n"

def test_import_session(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: calls.append(rows) or {"upserted": len(rows)},
    )
    # mock client.export + download ...
    from app.services.rdagent_bridge.import_session import import_session_scores
    out = import_session_scores("2026-08-01_17-48-00-363060", source="rdagent", version="v_test")
    assert out["upserted"] == 1
    assert calls[0][0]["symbol"]  # 经 persist 前可已规范化或交由 persist
```

- [ ] **Step 2: 实现 + 路由**

- [ ] **Step 3: Vue：选会话、source/version、导入按钮、展示行数；成功后链到策略中心文案**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 研究工厂支持导出会话并入库分数

EOF
)"
```

---

### Task 8: 运维文档与验收

**Files:**
- Modify: `QuantDinger/docs/RDAGENT_EXTERNAL_ALPHA_CN.md` — 增加「研究工厂模块」与 bridge 启动
- Modify: spec 状态保持「已确认」；计划勾选完成项
- Optional: `docker-compose` 注释 `extra_hosts` / env 示例（若有 compose 文件）

- [x] **Step 1: 按 spec §10 验收标准逐条手测并记录结果到 PR/提交说明**

- [x] **Step 2: 确认 `backend_api_python/Dockerfile` / `requirements.lock` **无** rdagent 依赖新增**

- [x] **Step 3: Commit docs**

```bash
git commit -m "$(cat <<'EOF'
docs: 补充研究工厂与桥接运维说明

EOF
)"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| Bridge HTTP 19901 + token | 1–2 |
| 启停 fin_factor/fin_quant、单任务锁、step_n≤20 | 1–2 |
| 会话列表、日志尾部 | 2、5 |
| server_ui 启停/状态 | 2、5 |
| QD 代理 `/api/rdagent` + admin | 3–4 |
| Vue 研究工厂页 | 5 |
| 导出→导入 external alpha | 6–7 |
| 不进 Docker 镜像 / 密钥不回传 | Global + 8 |
| MCP 二期 | 明确不做于本期 tasks |
| host.docker.internal 文档 | 2、8 |

## Placeholder / Ambiguity Guards

- Bridge 框架固定为 **Flask**（与测试客户端一致），避免「Flask 或 FastAPI」分叉。  
- Vue `request` baseURL 以实现时对照 `api/universe.js` 为准，计划中已要求对齐。  
- `rdagent-workspace` 若不在 git：Task 1/2/6 的 commit 步骤改为「仅落盘 + 在 QD docs 记录路径」，勿强制 `git commit`。
