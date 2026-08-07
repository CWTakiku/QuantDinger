# 研究工厂会话续跑（RD-Agent `--path`）设计

> 日期：2026-08-07  
> 状态：已确认；实现计划见 `docs/superpowers/plans/2026-08-07-rdagent-session-resume.md`  
> 范围：rdagent-bridge `POST /v1/jobs` + QD Human API 透传 + 研究工厂会话表「续跑」  
> 相关：`docs/superpowers/specs/2026-08-02-qd-rdagent-bridge-module-design.md`、`docs/RDAGENT_EXTERNAL_ALPHA_CN.md`

## 1. 背景与问题

RD-Agent CLI 支持从已有会话 checkpoint 续跑：

```bash
rdagent fin_factor --path log/<session_id> --loop-n 5
# 或：rdagent fin_quant <session_dir> --loop-n 5
```

`LoopBase.load` 会解析 `log/<id>/__session__` 下最新 pickle，默认 `--checkout`（复用原 log 目录并截断该 checkpoint 之后的半截产物）。

现状：研究工厂「启动」只传 `scenario` + `loop_n`（及数据源/日期/标的池），**从不传 path**；停止后再启一律新会话。已完成 Loop 可导入/发布，但无法在页面上接着训。

已确认交互：**方案 A** — 会话列表每行「续跑」→ 弹窗填写 **再跑轮数**（与启动表单 `loop_n` 解耦）；场景/数据源/标的池仍取当前表单。

## 2. 目标与非目标

### 2.1 目标

1. Bridge `POST /v1/jobs` 支持可选 `resume_session_id`（及可选 `checkout`），校验后把会话目录传给 `rdagent <scenario> --path <dir> --loop-n N`。  
2. QD `/api/rdagent/jobs` 透传上述字段；失败信息可读。  
3. Vue 会话表操作列增加「续跑」：有任务在跑时禁用；成功提示含会话 id 与再跑轮数。  
4. 缺省不传 resume 时行为与今天完全一致（新会话）。

### 2.2 非目标

- 不接受客户端任意绝对/相对 `path`（防路径穿越）；仅 `resume_session_id`。  
- 不指定具体 pickle 文件（由 RD-Agent `load` 选最新）。  
- 不做 `fin_model` 白名单扩展。  
- 不在 UI 暴露 `--no-checkout` 高级开关（API 预留字段即可，默认 `true`）。  
- 不改变 `loop_n` 上限（仍 1…max_loop_n）；续跑时语义为 **再跑 N 轮**。

## 3. 约束

| 项 | 选择 |
|----|------|
| UI | 会话行「续跑」→ 弹窗填再跑轮数 |
| 标识 | `resume_session_id` = 会话列表的 `id` |
| checkout | 默认 `true`（与 CLI 默认一致） |
| 场景 | 使用表单当前 `scenario`（须为 bridge 白名单）；不强制校验与原会话 scenario 一致（由用户负责；可选后续增强） |
| 安全 | session 目录必须落在 `workspace/log/` 下且存在 `__session__` |

## 4. 架构与数据流

```text
Vue 会话行「续跑」
  → POST /api/rdagent/jobs
       { scenario, loop_n, resume_session_id, …现有字段 }
  → Bridge POST /v1/jobs
       resolve_session_dir(workspace, id)
       校验 log/<id>/__session__ 存在
       cmd: … rdagent <scenario> --path <session_dir> --loop-n N
            [--no-checkout 仅当 checkout=false]
  → 子进程续跑；会话仍写入同一 log/<id>/
```

新任务记录可带 `resume_session_id`（及解析后的 `resume_path` 便于日志排查）；`session_hint` / `log_session` 在能推断时填该会话 id。

## 5. API 契约

### 5.1 Bridge `POST /v1/jobs`（增量）

| 字段 | 必需 | 类型 | 说明 |
|------|------|------|------|
| `resume_session_id` | 否 | string | 会话 id；trim 后非空则续跑 |
| `checkout` | 否 | bool | 默认 `true`；`false` 时追加 `--no-checkout` |

错误：

| 条件 | HTTP | error 文案要点 |
|------|------|----------------|
| id 无法 resolve / 目录不在 log 下 | 400 | invalid resume_session_id |
| 无 `__session__` 或无可 load 的 checkpoint | 400 | session has no resumable checkpoint |
| 其它原有校验 | 不变 | |

响应 job 对象增加可选：`resume_session_id`、`checkout`（布尔）。

### 5.2 QD `POST /api/rdagent/jobs`

透传 `resume_session_id` / `resumeSessionId`、`checkout`。不在 QD 侧重做路径解析。

### 5.3 Vue

- 会话表操作列：「续跑」打开弹窗（默认再跑 3 轮，可改 1～20）。  
- 弹窗文案明确：**再跑** N 轮，与上方启动表单循环轮数无关。  
- `disabled`：`bridgeOffline || !!runningJob || resumingSessionId`。  
- 确认后：`startRdagentJob({ …, loop_n: 弹窗值, resume_session_id })`。

## 6. Bridge 实现要点

1. `JobManager.start(..., resume_session_id: str | None = None, checkout: bool = True)`。  
2. 复用 `resolve_session_dir`；`session_dir / "__session__"` 为目录且其下存在至少一个 pickle（或与 `LoopBase.load` 相同的 glob 非空）。  
3. 命令在现有 `--loop-n` 基础上增加：  
   - `--path` + `str(session_dir)`（统一用 flag，避免 positional 在 launcher 脚本中歧义）  
   - `checkout is False` → `--no-checkout`  
4. 单任务锁逻辑不变：已有 running job 时拒绝。  
5. 续跑时 **仍** 应用当前表单的 data_source；**日期窗与数据集划分改为沿用原会话最早一轮 checkpoint**（handler 总区间 + train/valid/test + backtest end + market/benchmark），忽略请求体 `start_date`/`end_date` 与表单标的池。无法解析时回退表单并打日志。

## 7. 测试

| 层 | 用例 |
|----|------|
| Bridge unit | resume id 拼进 cmd；缺 `__session__` 抛错；`checkout=false` 含 `--no-checkout`；无 resume 时 cmd 无 `--path` |
| Bridge HTTP | POST 带 `resume_session_id` 201 且 job 回显该字段；坏 id 400 |
| QD | client / route 透传；mock bridge |
| Vue | 手工：有会话时点续跑；running 时按钮禁用 |

## 8. 文档

- `docs/RDAGENT_EXTERNAL_ALPHA_CN.md` 增一小节：会话续跑（UI + API 字段）。  
- 不强制改 `docs/agent/*`（非 Agent Gateway 面）。

## 9. 验收

1. 对已有含 `__session__` 的会话点「续跑」、`loop_n=1`，任务 running，日志显示加载既有 session / 继续 loop。  
2. 不传 `resume_session_id` 时仍创建新 `log/<新时间戳>/`。  
3. 伪造 id 或空 `__session__` 时 API 400，UI toast，不启动进程。  
4. 已有 running 任务时「续跑」不可点。

## 10. 实现顺序（概要）

1. Bridge：resolve + cmd + 单测  
2. Bridge HTTP + 序列化字段  
3. QD client / route + 单测  
4. Vue 会话表按钮 + 文案  
5. 文档 + 本机冒烟
