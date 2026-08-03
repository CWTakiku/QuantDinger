# 研究工厂标的池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究工厂可选 QD CNStock 标的池，并按规则设置 Qlib market/benchmark（全市场→中证全指，手动/自选→等权）。

**Architecture:** Vue 选 `universe_code` → QD 解析成员并映射基准 → Bridge 写 `instruments/*.txt` + `TemplatePatcher` 改 conf → rdagent 运行。

**Tech Stack:** Python (bridge + QD Flask)、Vue、Qlib instruments 文本格式。

---

## File map

| File | Role |
|------|------|
| `rdagent-workspace/rdagent_bridge/universe_prep.py` | 符号转换、写 instruments、基准推断 |
| `rdagent-workspace/rdagent_bridge/date_segments.py` | TemplatePatcher 支持 market/benchmark |
| `rdagent-workspace/rdagent_bridge/jobs.py` | 接收 universe、落盘、打补丁 |
| `rdagent-workspace/rdagent_bridge/app.py` | `/v1/jobs` body 透传 universe |
| `QuantDinger/.../rdagent_universe.py` | 列 CN 池 + 解析启动载荷 |
| `QuantDinger/.../routes/rdagent.py` | `GET /universes`、`POST /jobs` 组装 |
| `QuantDinger/.../rdagent_bridge/client.py` | start_job 传 universe |
| `QuantDinger-Vue/.../rdagent/index.vue` + `api/rdagent.js` | 下拉 |

---

### Task 1: Bridge universe_prep + tests

**Files:**
- Create: `rdagent-workspace/rdagent_bridge/universe_prep.py`
- Create: `rdagent-workspace/rdagent_bridge/tests/test_universe_prep.py`

- [ ] **Step 1: 失败单测** — `to_qlib_symbol`、`write_instruments_file`、`infer_benchmark`
- [ ] **Step 2: 实现至通过**
- [ ] **Step 3: pytest 绿灯**

### Task 2: TemplatePatcher market/benchmark

**Files:**
- Modify: `date_segments.py`、`tests/test_date_segments.py`

- [ ] **Step 1: 单测 patch market + list benchmark**
- [ ] **Step 2: 实现 `apply(..., market=, benchmark=)`**
- [ ] **Step 3: pytest 绿灯**

### Task 3: JobManager + HTTP

**Files:**
- Modify: `jobs.py`、`app.py`、相关 tests

- [ ] 接收 `universe` dict；非全市场写 instruments；有 universe 或日期时 patch；job 元数据记录 universe_code

### Task 4: QD 侧组装

**Files:**
- Create: `app/services/rdagent_bridge/universe_payload.py`
- Modify: `routes/rdagent.py`、client、tests

- [ ] `GET /api/rdagent/universes`（CN + `__all_market__`）
- [ ] `POST /jobs` 解析成员 → bridge `universe` 载荷

### Task 5: Vue

**Files:**
- Modify: `api/rdagent.js`、`views/rdagent/index.vue`

- [ ] 标的池下拉，默认 `csi300`；启动带 `universe_code`

### Task 6: 文档

- [ ] 更新 `RDAGENT_EXTERNAL_ALPHA_CN.md` 一句说明标的池
