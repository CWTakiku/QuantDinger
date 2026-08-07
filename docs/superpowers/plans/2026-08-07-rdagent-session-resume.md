# 研究工厂会话续跑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究工厂会话表「续跑」可从既有 `log/<session_id>` checkpoint 再跑 `loop_n` 轮。

**Architecture:** Vue 传 `resume_session_id` → QD `/api/rdagent/jobs` 透传 → Bridge `JobManager.start` 解析 `workspace/log/<id>`、校验 `__session__`，CLI 追加 `--path <dir>`（可选 `--no-checkout`）。

**Tech Stack:** rdagent-bridge Flask、QD Flask Human API、Vue 2、pytest

**Spec:** `docs/superpowers/specs/2026-08-07-rdagent-session-resume-design.md`

## Global Constraints

- 仅接受 `resume_session_id`，禁止客户端任意 path
- `loop_n` 语义：再跑 N 轮；上限不变（1…max_loop_n）
- `checkout` 默认 `true`；UI 不暴露开关
- scenario 白名单仍为 `fin_factor` | `fin_quant`
- Commit message：中文 Conventional Commits（本会话默认不提交，除非用户要求）

## File Map

| 文件 | 职责 |
|------|------|
| `/home/igrs/data/RD-Agent/rdagent_bridge/sessions.py` | `resolve_session_dir`（已有）；可增 `assert_resumable` |
| `/home/igrs/data/RD-Agent/rdagent_bridge/jobs.py` | `start(..., resume_session_id, checkout)` + cmd |
| `/home/igrs/data/RD-Agent/rdagent_bridge/app.py` | POST body 解析与序列化 |
| `/home/igrs/data/RD-Agent/rdagent_bridge/tests/test_jobs_unit.py` | resume cmd / 校验单测 |
| `/home/igrs/data/QuantDinger/backend_api_python/app/services/rdagent_bridge/client.py` | `start_job` 透传 |
| `/home/igrs/data/QuantDinger/backend_api_python/app/routes/rdagent.py` | 读 body 透传 |
| `/home/igrs/data/QuantDinger/backend_api_python/tests/test_rdagent_routes.py` | 透传断言 |
| `/home/igrs/data/QuantDinger/QuantDinger-Vue/src/views/rdagent/index.vue` | 会话行「续跑」 |
| `/home/igrs/data/QuantDinger/docs/RDAGENT_EXTERNAL_ALPHA_CN.md` | 文档小节 |

---

### Task 1: Bridge 校验 + JobManager 拼 `--path`

**Files:**
- Modify: `RD-Agent/rdagent_bridge/sessions.py`
- Modify: `RD-Agent/rdagent_bridge/jobs.py`
- Modify: `RD-Agent/rdagent_bridge/tests/test_jobs_unit.py`

**Interfaces:**
- Produces: `assert_session_resumable(workspace: Path, session_id: str) -> Path`（返回 session_dir）
- Produces: `JobManager.start(..., resume_session_id: str | None = None, checkout: bool = True)`
- Consumes: 现有 `resolve_session_dir`

- [ ] **Step 1: 写失败单测**

```python
def test_resume_appends_path(tmp_path, monkeypatch):
    # create log/sid/__session__/0/0_propose pickle-like file
    ...
    captured = {}
    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        class P:
            pid = 1
            def poll(self): return None
        return P()
    ...
    jm.start("fin_factor", loop_n=2, resume_session_id=sid)
    assert "--path" in captured["cmd"]
    assert str(session_dir) in captured["cmd"]

def test_resume_missing_checkpoint_raises(tmp_path):
    ...
```

- [ ] **Step 2: 实现 `assert_session_resumable` + `start` 参数与 cmd**
- [ ] **Step 3: 跑单测通过**

---

### Task 2: Bridge HTTP + job 序列化字段

**Files:**
- Modify: `RD-Agent/rdagent_bridge/app.py`
- Modify: `RD-Agent/rdagent_bridge/tests/test_app_http.py`（若有）或扩展 unit

- [ ] **Step 1: POST 解析 `resume_session_id` / `checkout`，写入 job 与 `_serialize_job`**
- [ ] **Step 2: 坏 id → 400；测 HTTP 或 unit 覆盖**

---

### Task 3: QD 透传

**Files:**
- Modify: `backend_api_python/app/services/rdagent_bridge/client.py`
- Modify: `backend_api_python/app/routes/rdagent.py`
- Modify: `backend_api_python/tests/test_rdagent_routes.py`
- Modify: `backend_api_python/tests/test_rdagent_bridge_client.py`（若需）

- [ ] **Step 1: `start_job(..., resume_session_id=None, checkout=True)` body 字段**
- [ ] **Step 2: route 读 `resume_session_id`/`resumeSessionId`、`checkout`**
- [ ] **Step 3: 单测断言透传**

---

### Task 4: Vue 会话表「续跑」

**Files:**
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue`
- Modify: `QuantDinger-Vue/src/api/rdagent.js`（若 start 已透传任意 payload 则可不改）

- [ ] **Step 1: 操作列按钮 + `handleResume(record)`**
- [ ] **Step 2: 本地/容器 rebuild 前端（按现网 compose）**

---

### Task 5: 文档 + 冒烟

**Files:**
- Modify: `docs/RDAGENT_EXTERNAL_ALPHA_CN.md`
- Modify: spec 状态 → 已确认/已实现

- [ ] **Step 1: 文档小节**
- [ ] **Step 2: 重启 bridge；curl POST 带 resume（可用真实会话 id）冒烟**

---

## Spec coverage

| Spec 项 | Task |
|---------|------|
| resume_session_id + checkout | 1–2 |
| 校验 __session__ / 400 | 1–2 |
| QD 透传 | 3 |
| 会话行续跑 UI | 4 |
| 文档 | 5 |
| 无 resume 行为不变 | 1 单测 |
