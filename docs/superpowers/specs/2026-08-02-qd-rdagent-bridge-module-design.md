# QuantDinger ↔ RD-Agent 研究工厂模块设计

> 日期：2026-08-02  
> 状态：已确认；实现计划见 `docs/superpowers/plans/2026-08-02-qd-rdagent-bridge-module.md`  

> 范围：QD 新模块（Vue + Human API）+ 本机 `rdagent-bridge`；一期 HTTP，二期可选 MCP  
> 相关：`docs/RDAGENT_EXTERNAL_ALPHA_CN.md`、`docs/EXTERNAL_ALPHA_SCORE_BRIDGE_CN.md`、`docs/superpowers/specs/2026-08-01-external-alpha-score-bridge-design.md`

## 1. 背景与问题

用户希望在 QuantDinger **页面内**完成 RD-Agent 相关操作（启动挖因子、看状态、导入分数、挂策略），而不是在终端与 19899 之间来回切换。

已确认约束：

1. **功能入口在 QD**：Vue 新菜单模块是唯一产品入口。  
2. **研究进程跑在本机旁路**：调用已有 `~/quant/rdagent-workspace` + micromamba，**不**把 RD-Agent / Qlib / LLM 装进 `quantdinger-backend` Docker。  
3. **调用协议**：一期用 **HTTP**；同一 bridge **可再暴露 MCP tools**（给 Cursor/外部 Agent），与网页共用能力。  
4. 分数消费仍走已有契约：`qd_external_alpha_scores` + `strategy_v2_external_alpha_score`。

现状缺口：

| 能力 | 现状 |
|------|------|
| RD-Agent MCP Server（启停 `fin_factor` 等） | **无**（RD 主要是 MCP 客户端） |
| QuantDinger MCP Server | **有**（约 58 tools，面向 QD 自身） |
| QD ↔ RD 直连 | **无** |
| External Alpha Web 管理面 | **无**（仅 CLI 导入） |

## 2. 目标与非目标

### 2.1 目标（一期 MVP）

1. 本机常驻 **`rdagent-bridge`**（默认端口 **19901**），封装对 RD-Agent 工作区的启停与查询。  
2. QuantDinger Backend 新增 Human API：`/api/rdagent/*`，鉴权后 **代理** 到 bridge（容器不执行挖因子）。  
3. QuantDinger-Vue 新增菜单页「研究工厂 / RD-Agent」，覆盖：  
   - 启动 / 停止 `fin_factor` 或 `fin_quant`（`step_n`、超时）  
   - 任务状态、最近日志尾部、会话列表  
   - 打开 / 指示 `server_ui`（19899）  
   - 选会话 → 导出 CSV → 导入 `qd_external_alpha_scores`  
   - 跳转或预填 External Alpha 策略模板  
4. Bridge 预留与 HTTP 同构的 MCP tools 接口形状（二期打开）。  
5. 文档：运维启动 bridge、环境变量、与 Docker 网络互通方式。

### 2.2 非目标（一期不做）

- 将 RD-Agent / pyqlib / CUDA 装入 `quantdinger-backend` 镜像。  
- 策略热路径或撮合循环内调用 bridge / RD。  
- iframe 完整嵌入 RD `server_ui`（可外链；二期再议）。  
- 多机远程调度、队列、多租户资源隔离。  
- 在 QD 内配置 / 存储研究侧 LLM API Key（密钥仅留在 `rdagent-workspace/.env`）。  
- 替换 Strategy V2 回测为 Qlib 回测。  
- 修改 `strategy_v2_csi300_enhanced_v2` 默认逻辑。

## 3. 架构

```text
QuantDinger-Vue  /rdagent（新模块）
        │  cookie/JWT 鉴权
        ▼
QD Backend  /api/rdagent/*
  （代理、审计、限流；无 RD 依赖）
        │  HTTP  http://host.docker.internal:19901
        │  （可选二期）MCP
        ▼
rdagent-bridge（本机，端口 19901）
        │
        ├─ subprocess: micromamba run -n rdagent rdagent fin_* 
        ├─ 读写 ~/quant/rdagent-workspace/log/
        ├─ 可选管理 server_ui :19899
        └─ 导出 CSV → 调用 QD import（CLI 或内部 HTTP）
                ▼
        qd_external_alpha_scores → Strategy V2 模板
```

### 3.1 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| Vue 研究工厂页 | 操作台 UI、展示状态、引导挂策略 | 直接 spawn 本机进程 |
| QD `/api/rdagent` | 鉴权、代理 bridge、导入落库编排 | 挖因子、加载 LLM |
| `rdagent-bridge` | 启停 RD、列会话、读日志、触发导出 | 持仓、撮合、产品净值 |
| RD-Agent 工作区 | 假设→编码→Qlib 回测进化 | 对用户暴露 HTTP |
| External Alpha 表/策略 | 分数 PIT 与调仓 | 研究循环控制 |

### 3.2 为何不用「纯 MCP 互连」做一期主路径

- QuantDinger 已有 MCP，但是 **QD→Agent** 方向，不是 QD 后端调 RD。  
- RD-Agent **没有** 官方「启停 fin_factor」MCP Server。  
- 浏览器→QD API→HTTP bridge 链路简单、易鉴权、易在 Docker 内用 `host.docker.internal` 访问。  
- MCP 作为 bridge **并行暴露面**，服务 Cursor，不阻塞网页 MVP。

## 4. 本机 Bridge 设计

### 4.1 进程与配置

- 工作目录约定：`RDAGENT_WORKSPACE` 默认 `/Users/<user>/quant/rdagent-workspace`（可用环境变量覆盖）。  
- 监听：`0.0.0.0:19901`（仅建议本机 / Docker 网桥访问；生产应绑定 localhost + 反代或 token）。  
- 鉴权（MVP）：共享密钥头 `X-RDAgent-Bridge-Token`，与 QD 侧 `RDAGENT_BRIDGE_TOKEN` 一致。  
- 依赖：运行在已有 `rdagent` micromamba 环境或轻量 venv；**不**打进 QD 镜像。

### 4.2 HTTP API（MVP）

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 探活；返回 workspace 是否存在 |
| `GET` | `/v1/jobs` | 当前/最近任务列表 |
| `POST` | `/v1/jobs` | 创建任务：`scenario=fin_factor\|fin_quant`，`step_n`，`timeout_h` 可选 |
| `POST` | `/v1/jobs/{id}/stop` | 停止任务（SIGTERM） |
| `GET` | `/v1/jobs/{id}` | 状态：`queued\|running\|succeeded\|failed\|stopped`，pid、log_session、exit_code |
| `GET` | `/v1/jobs/{id}/logs` | 日志尾部（`tail` 行数，默认 200） |
| `GET` | `/v1/sessions` | `log/` 下合法会话列表（时间戳、是否有效） |
| `POST` | `/v1/ui/start` | 确保 `server_ui` 在 19899（若已占用则返回现地址） |
| `GET` | `/v1/ui` | UI 状态与 URL |
| `POST` | `/v1/export` | body：`session`、`source`、`version`；导出 QD CSV 到约定路径并返回 path/摘要 |

并发限制（MVP）：**同时仅允许 1 个** 挖因子/量化任务，避免本机资源打爆。

### 4.3 与现有日志 / UI 的关系

- 挖因子会话仍写入 `log/<timestamp>/`（RD-Agent 原生）。  
- `server_ui` 继续服务 19899；bridge 只负责启停/探活，不重写前端。  
- 已有会话可通过「打开 UI」外链；一期不强制 iframe。

### 4.4 MCP（二期，形状预留）

与 HTTP 同构的 tools 建议名：

- `rdagent_job_start` / `rdagent_job_stop` / `rdagent_job_status`  
- `rdagent_sessions_list` / `rdagent_export_scores`  

一期可只实现 HTTP；目录与 OpenAPI 注释中标注「MCP parity」。

## 5. QuantDinger Backend

### 5.1 配置

| 环境变量 | 说明 | 默认 |
|----------|------|------|
| `RDAGENT_BRIDGE_URL` | Bridge 根 URL | `http://host.docker.internal:19901`（Docker）/ `http://127.0.0.1:19901`（本机） |
| `RDAGENT_BRIDGE_TOKEN` | 与 bridge 共享密钥 | 必填（开发可本地 `.env`） |
| `RDAGENT_BRIDGE_TIMEOUT_S` | 代理超时 | `30`（日志/导出可更长） |

Bridge 不可达时：API 返回明确 `503` + 中文提示「请先在本机启动 rdagent-bridge」，**不**静默吞错。

### 5.2 Human API（代理 + 编排）

前缀：`/api/rdagent`（admin 权限）。

| 方法 | 路径 | 行为 |
|------|------|------|
| `GET` | `/status` | bridge health + 可选当前 job |
| `GET/POST` | `/jobs`… | 透传 bridge `/v1/jobs` |
| `GET` | `/sessions` | 透传 |
| `POST` | `/ui/start` | 透传 |
| `POST` | `/import-from-session` | 编排：bridge `export` → 调用已有 `persist_external_alpha_scores` / 导入逻辑 → 返回行数与 source/version |

导入编排在 **QD 进程内**完成落库（QD 已连 Postgres）；CSV 可由 bridge 写到共享挂载目录，或 bridge 返回 CSV 字节流（MVP 优先：共享目录 `rdagent-workspace/exports/`，Docker volume 只读挂载该目录，或 export 接口直接 POST JSON rows——若行数过大则用文件）。

**MVP 推荐**：bridge `export` 返回 `{ path, row_count, as_of_min, as_of_max }`，QD 在能访问该 path 时读取；若 Docker 无法读宿主机路径，则 bridge 增加 `GET /v1/export/{id}/download` 供 QD 拉取后入库。

### 5.3 安全

- 仅 `admin`（或与现有 `permission: ['admin']` 一致）。  
- 不向前端回传 bridge token、LLM key、完整 `.env`。  
- 审计日志：谁在何时启动/停止/导入（用户 id + job id + session）。  
- 禁止通过 API 指定任意 shell 命令；scenario 枚举白名单。

## 6. QuantDinger-Vue

### 6.1 菜单与路由

- 路由建议：`/rdagent`（或 `/research/rdagent`）。  
- `router.config.js` 增加菜单项，i18n：`研究工厂` / `RD-Agent`。  
- `meta.permission: ['admin']`（与运维敏感操作一致）。

### 6.2 页面区块（一期）

1. **连接状态**：bridge 是否在线、workspace 路径摘要。  
2. **运行控制**：场景选择、`step_n`、启动/停止、状态徽章、日志尾部。  
3. **会话**：历史列表；「打开 UI」→ `http://127.0.0.1:19899`（新标签）。  
4. **导入**：选 session + source/version → 导入结果摘要。  
5. **下一步**：链到策略创建/回测（预填模板与 source/version）。

不在一期做完整 RD 研究循环可视化（研究/开发/反馈三栏）；需要细节时跳转 19899。

## 7. 数据与版本约定

导入时默认：

- `source=rdagent`  
- `version`：用户输入或自动 `session_<timestamp>` / `vYYYYMMDD`  
- 复用现有符号规范化与 PIT 规则（`as_of=T` 最早 T+1 调仓）

导出脚本复用工作区已有 `export_qlib_pred_to_qd_csv.py`（或同等逻辑封装进 bridge）。

## 8. 部署与运维

### 8.1 本机启动 bridge（开发）

```bash
cd ~/quant/rdagent-workspace
# 示例：python -m rdagent_bridge --port 19901
# 需能找到 micromamba、.env、log/
```

建议用 launchd/后台脚本保活（与 streamlit/server_ui 类似）。

### 8.2 Docker 访问宿主机

- macOS Colima/Docker Desktop：`host.docker.internal:19901`。  
- 若不通：文档给出 `extra_hosts` / 宿主机 IP 备选。  
- `exports/` 目录：volume 挂载或改用 download API。

### 8.3 健康检查

- QD：`GET /api/rdagent/status`  
- Bridge：`GET /health`  
- UI：19899 可选，不阻塞挖因子。

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 用户以为 QD Docker 内已含 RD | UI 文案明确「本机研究引擎」；bridge 离线时阻断操作 |
| 长任务拖垮笔记本 | 单任务锁 + `step_n` 上限（如 ≤ 20）+ 超时 |
| Token / 密钥泄漏 | bridge 仅本机监听；token 不进仓库；LLM key 不经 QD |
| 导出日期与真实样本外不一致 | 导入页提示 session 的 as_of 范围；禁止静默日期 remap（烟测脚本不进默认按钮） |
| `rdagent server_ui` CLI 相对路径问题 | bridge 用绝对路径启动 `app.py`（与现运维一致） |

## 10. 验收标准（一期）

1. 本机启动 bridge 后，QD 研究工厂页显示「已连接」。  
2. 管理员可启动 `fin_factor --step-n 1`，页内看到 `running`→终态，日志非空。  
3. 可停止任务。  
4. 会话列表可见已有 `log/` 会话；可打开 19899。  
5. 对某会话执行导入后，`qd_external_alpha_scores` 增加对应 `source/version` 行。  
6. 关闭 bridge 后，QD 返回明确错误，页面不崩溃。  
7. Backend 镜像构建 **不** 新增 rdagent/pyqlib 依赖。

## 11. 实现分期

| 阶段 | 内容 |
|------|------|
| **P0** | bridge HTTP + QD 代理 API + Vue 页（启停/状态/日志/会话/外链 UI） |
| **P1** | 导出会话 → 导入 external alpha + 策略预填 |
| **P2** | bridge MCP tools；可选 iframe / 更细 trace |
| **P3** | cron/调度、多任务队列（若有需求再开） |

## 12. 决策记录

| 项 | 选择 |
|----|------|
| 集成深度 | QD 产品入口 + 本机旁路引擎（非镜像内嵌） |
| 主协议 | HTTP；MCP 二期同构 |
| Bridge 端口 | **19901** |
| RD UI 端口 | **19899**（保持官方/现网） |
| 分数路径 | 既有 External Alpha 桥接，不新建第二套分数表 |
| 默认 scenario | `fin_factor`；可选 `fin_quant` |
