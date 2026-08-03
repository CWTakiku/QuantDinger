# 研究工厂会话详情（对齐 RD UI）设计

日期：2026-08-03  
状态：待用户审阅  
范围：`rdagent-workspace` bridge + QuantDinger 后端代理 + QuantDinger-Vue 研究工厂

## 1. 背景与目标

QuantDinger「研究工厂」目前只能启停任务、列会话、导入 External Alpha，**不能**查看因子研究指标与过程。指标与过程已落在本机 `log/<session_id>/` 的 pickle 中，现由 RD-Agent Streamlit UI（`:19899`）展示。

**目标（方案 2）**：在研究工厂内**原生复刻** RD UI 核心能力，用户无需离开 QD 即可完成查看。P0（总览指标）+ P1（代码/反馈/净值）+ **P2（编码进化回放、指标 CSV 下载）一次交付**。

**非目标**：

- 不在 Docker 内跑挖因子；研究仍在宿主机 bridge / RD-Agent。
- 不替换 RD-Agent 挖因子工作流；本功能只读会话产物。
- 「打开 RD UI」按钮**保留**作兜底（深度调试 / 新字段兼容）。

## 2. 用户流程

1. 打开研究工厂 → 刷新历史会话。
2. 点击某一会话行（或「查看详情」）→ 主区进入「会话详情」。
3. 默认落在 **总览**：Baseline + 各 Loop 指标表与折线；假设列表可筛选「只看成功」。
4. 切换 **研发循环** → 选 Loop → 研究 / 开发 / 反馈 / **编码进化** Tab。
5. 可选：下载指标 CSV；导入 External Alpha；打开 RD UI。

## 3. 架构

```
Vue 研究工厂
  → QD GET /api/rdagent/sessions/<id>/detail[?include=...]
    → Bridge GET /v1/sessions/<id>/detail[?include=...]
      → 只读解析 workspace/log/<id>/Loop_* 目标 pickle
```

原则：

- Bridge 负责 pickle → JSON（需 rdagent conda 环境中的类定义）。
- QD 仅鉴权代理 + 错误码映射，不落库会话详情。
- 前端按 `include` 懒加载大字段（代码、净值、进化历史），避免首屏过大。

## 4. Bridge API

### 4.1 `GET /v1/sessions/<session_id>/detail`

Query：

| 参数 | 默认 | 说明 |
|------|------|------|
| `include` | `summary,loops,factors` | 逗号分隔；允许：`summary`,`loops`,`factors`,`code`,`equity`,`evolution`,`all` |

`all` = 全部块。

响应（示意）：

```json
{
  "session_id": "2026-08-02_11-48-06-768124",
  "scenario": "qlib_factor",
  "baseline": {
    "label": "Alpha Base",
    "ic": 0.0276,
    "icir": 0.2204,
    "rank_ic": 0.0388,
    "rank_icir": 0.3130,
    "annualized_return": 0.0147,
    "max_drawdown": -0.1207,
    "information_ratio": 0.12
  },
  "metric_series": [
    {"label": "Alpha Base", "ic": 0.0276, "...": "..."},
    {"label": "Loop_0", "loop_index": 0, "ic": 0.0297, "decision": true}
  ],
  "loops": [
    {
      "loop_index": 0,
      "hypothesis": {
        "hypothesis": "...",
        "reason": "...",
        "concise_reason": null,
        "concise_observation": null,
        "concise_justification": null,
        "concise_knowledge": null
      },
      "metrics": {
        "ic": 0.0297,
        "icir": 0.2513,
        "rank_ic": 0.0307,
        "rank_icir": 0.2525,
        "annualized_return": 0.0341,
        "max_drawdown": -0.1566,
        "information_ratio": null
      },
      "feedback": {
        "decision": true,
        "observations": "...",
        "hypothesis_evaluation": "...",
        "new_hypothesis": "...",
        "reason": "...",
        "exception": null
      },
      "factors": [
        {
          "name": "Momentum_10d",
          "description": "...",
          "formulation": "...",
          "coding_success": true,
          "final_feedback": "...",
          "code": null,
          "evolution": null
        }
      ],
      "equity_curve": null
    }
  ]
}
```

字段映射（`Experiment.result` / `Series` index）：

| JSON | Series key |
|------|------------|
| `ic` | `IC` |
| `icir` | `ICIR` |
| `rank_ic` | `Rank IC` |
| `rank_icir` | `Rank ICIR` |
| `annualized_return` | `1day.excess_return_with_cost.annualized_return` |
| `max_drawdown` | `1day.excess_return_with_cost.max_drawdown` |
| `information_ratio` | `1day.excess_return_with_cost.information_ratio` |

### 4.2 解析规则

每个 `Loop_N` 目录（N 为 `loop_index`）：

| 数据 | Glob（取 mtime 最新） |
|------|----------------------|
| 指标 / baseline 引用 | `running/runner result/**/*.pkl` → `QlibFactorExperiment` |
| 假设 | `direct_exp_gen/hypothesis generation/**/*.pkl` |
| 决策反馈 | `feedback/feedback/**/*.pkl`（排除路径含 `evolving feedback`） |
| 因子工作区 | runner 的 `sub_workspace_list`；若无 runner，回退 `coding/coder result/**/*.pkl` |
| 编码成败 | 最新 `coding/**/evolving feedback/**/*.pkl` 中与因子对齐的 `final_decision` |
| 代码 | `include=code` 时读 workspace `file_dict['factor.py']` 或磁盘 `factor.py` |
| 净值 | `include=equity` 时读 `running/Quantitative Backtesting Chart/**/*.pkl`；列保留 `account`,`bench`,`return`（及原 index 日期）；点数 &gt; 800 时按交易日保留，必要时每周一点 |
| 进化回放 P2 | `include=evolution`：按 `coding/evo_loop_*` 顺序收集每轮 code + feedback（`final_decision` / `final_feedback`） |

边界：

- 无 `runner result`：`metrics` / `equity_curve` / `feedback.decision` 可为 `null`，仍返回假设与已有编码信息。
- pickle 损坏：该块 `error` 字符串，其余块继续。
- 非法 `session_id`：400；目录不存在：404。

### 4.3 `GET /v1/sessions/<session_id>/metrics.csv`（P2）

导出总览用扁平 CSV：`label,loop_index,decision,ic,icir,rank_ic,rank_icir,annualized_return,max_drawdown,information_ratio`。  
含 baseline 行（`loop_index` 空）。鉴权与其它 `/v1` 相同。

## 5. QuantDinger 后端

- `GET /api/rdagent/sessions/<session_id>/detail`：`login_required` + `admin_required`；透传 `include`；bridge 错误映射沿用 `RdAgentBridgeError`（`msg` 含 detail）。
- `GET /api/rdagent/sessions/<session_id>/metrics.csv`：代理下载，`Content-Disposition` 附件。
- Client 增加对应方法；**超时**：详情默认 60s，CSV 60s（大会话 evolution+code 可能偏慢）。

## 6. 前端（研究工厂）

### 6.1 信息架构

在现有「历史会话」表增加操作「查看详情」。点击后在同页展开 **会话详情 section**（不用 Drawer），位于会话表与导入区之间：

1. **总览**
   - 指标表（Baseline + Loop）
   - 折线：IC、年化收益、最大回撤（复用项目已有图表组件；若无则用轻量 ECharts/Ant 折线）
   - 假设列表 +「只看成功」开关
   - 按钮：下载指标 CSV

2. **研发循环**
   - Loop 选择（Select / Tabs）
   - Tab **研究**：hypothesis / reason
   - Tab **开发**：因子子 Tab（名 + ✔️/❌）；描述、公式；代码折叠（需 `include=code`）
   - Tab **反馈**：本轮指标 vs baseline；observations / evaluation / decision；净值图 account vs bench（需 `include=equity`）
   - Tab **编码进化**（P2）：按 evo_loop 时间线展示每轮反馈与代码 diff/全文（无 diff 库则并列「上一轮 / 本轮」代码折叠）

3. **底部**：导入 External Alpha（自动填当前 session）、删除、打开 RD UI。

### 6.2 加载策略

- 进入详情：`include=summary,loops,factors`
- 打开「开发」且展开代码：再请求 `include=code`（或带 factors+code）合并
- 打开「反馈」净值：`include=equity`
- 打开「编码进化」：`include=evolution`（可与 code 合并）

### 6.3 空态与错误

- 会话无任何 Loop：提示「尚无研究循环」
- 仅有假设无回测：指标列显示「—」
- bridge 不可达：沿用现有 offline Alert

## 7. 测试

Bridge：

- 用样本会话 `2026-08-02_11-48-06-768124`：Loop_0 有完整 metrics/decision/factors；Loop_1 无 runner 时 metrics 为 null。
- 单元测试：metric Series → JSON 映射；非法 session_id；缺失目录。
- evolution：存在 `evo_loop_*` 时返回有序列表。

QD：

- 路由鉴权与代理 mock。
- Vue：详情区渲染指标表；只看成功过滤；Tab 懒加载请求参数。

## 8. 性能与运维

- 目标：默认 `include` 详情 &lt; 2s（本机 SSD、单会话）。
- 禁止 `FileStorage.iter_msg()` 全量扫 200+ pkl。
- Bridge 热更新后需重启；QD 后端镜像需 rebuild 或 docker cp 部署新 client/routes；前端需 rebuild 或本地 Vite。

## 9. 验收标准

1. 不打开 19899，仅在研究工厂可看到样本会话 Loop_0 的 IC/年化/回撤，且与 runner pickle 一致（误差 &lt; 1e-6）。
2. 可见假设原文、因子列表成败、`factor.py`、反馈 decision。
3. 可见净值 account vs bench 曲线。
4. 编码进化 Tab 能按 evo_loop 回放。
5. 可下载 metrics.csv：固定表头；第 1 行为 baseline；随后 **每个 Loop 一行**（无回测的 Loop 指标列为空，`decision` 可空）。
6. 「打开 RD UI」仍可用。

## 10. 实现单元划分

| 单元 | 职责 |
|------|------|
| `rdagent_bridge/session_detail.py` | pickle 抽取与 JSON 规范化 |
| `rdagent_bridge/app.py` | `/detail`、`/metrics.csv` 路由 |
| `RdAgentBridgeClient` | HTTP 方法 |
| `routes/rdagent.py` | QD 代理 |
| `views/rdagent/SessionDetail.vue` | 总览 + 循环 UI（由 `index.vue` 引入） |
| `api/rdagent.js` | `fetchSessionDetail` / `downloadSessionMetricsCsv` |
