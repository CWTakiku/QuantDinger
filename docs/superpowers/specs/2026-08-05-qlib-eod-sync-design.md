# Qlib 收盘同步与选股刷新设计

> 日期：2026-08-05  
> 状态：已实现；计划见 `docs/superpowers/plans/2026-08-05-qlib-eod-sync.md`（Tasks 1–6）  

> 范围：交易日收盘判定、Bridge `qlib/update`、选股「刷新分数」即时同步、Celery Beat 日终同步  
> 相关：`docs/superpowers/specs/2026-08-04-stock-picker-design.md`、`docs/superpowers/specs/2026-08-02-qd-rdagent-bridge-module-design.md`

## 1. 背景与问题

用户心智：A 股当日收盘后，点「刷新分数」或等到日终，本地 Qlib 与分数面板应能跟上最近已收盘交易日。

现状缺口：

| 能力 | 现状 |
|------|------|
| 是否已收盘 | 无；ensure 只看分数面板是否「PIT 覆盖」 |
| Bridge `POST /v1/qlib/update` | QuantDinger 客户端已写，Bridge **未实现**（404） |
| 缺分数时拉行情 | ensure 会调 `qlib_update`，但因 Bridge 缺接口而失败，仅依赖手工脚本 |
| 日终自动同步 | 无；有同类 Celery Beat（如 `csi300_enhanced_daily_sync`）可仿照 |

典型故障：交易所已收盘（如 08-05 15:00 后），Qlib 日历仍停在 08-04，分数只有 08-04；刷新看似成功但无精确当日截面。

## 2. 目标与非目标

### 2.1 目标（本期）

1. **共享收盘门闩**：上海时区 + 交易日历，判断某 `as_of` 是否允许拉日线。  
2. **Bridge 实现** `POST /v1/qlib/update`：复用 `scripts/update_qlib_cn_from_tushare.py`（含日历与 instruments 结束日延长）。  
3. **选股刷新（即时）**：目标日可同步且 Qlib/分数落后时，先 `qlib_update` 再 infer；未收盘则明确提示并对齐最近有分数日。  
4. **Celery Beat 日终**：每个交易日约 **15:30（上海）** 同步 Qlib 到最近已收盘交易日；默认 **不** 全量预热已发布模型分数（可用 env 打开）。

### 2.2 非目标（本期不做）

- 盘中分钟线 / 实时行情。  
- 默认给全部已发布模型每日全量重算分数（过重；仅可选）。  
- 替换研究工厂或回测中心的数据路径。  
- 非 A 股市场的收盘规则。

## 3. 收盘与交易日判定

时区：`Asia/Shanghai`。

| 条件 | 结果 |
|------|------|
| 非交易日（`trade_cal.is_open=0`） | 不可拉该日日线；可 PIT 对齐前一交易日 |
| 交易日且 `as_of < 今天` | 可拉 |
| 交易日且 `as_of == 今天` 且 `now >= 15:05` | 可拉（预留数据源延迟） |
| 交易日且 `as_of == 今天` 且 `now < 15:05` | 不可拉当日；返回 `not_closed` |
| `as_of > 今天` | 不可拉；返回 `future` |

交易日历来源：Tushare `trade_cal`（与现有 Qlib 更新脚本同一 token / `TUSHARE_HTTP_URL`），进程内短缓存（建议 ≥ 1h）。

实现位置建议：

- Bridge：同步接口内自检（避免未收盘误拉）。  
- QuantDinger：`ensure_scores` / 定时任务调用前也可做同一判定，便于前端直接展示原因。

## 4. Bridge：`POST /v1/qlib/update`

### 4.1 请求 / 响应

请求体（与现有 QD 客户端对齐）：

```json
{ "end": "2026-08-05", "force": false }
```

- `end`：可选；默认「最近已收盘交易日」。  
- `force`：为 true 时即使日历已含该日也可重拉（默认 false → 幂等跳过）。

成功响应示例：

```json
{
  "ok": true,
  "provider_uri": ".../cn_data",
  "calendar_before": "2026-08-04",
  "calendar_after": "2026-08-05",
  "fetched_days": ["2026-08-05"],
  "skipped": false
}
```

若未到收盘：`ok=false`，`reason=not_closed`，HTTP 仍可用 200（业务态）或 409（二选一，实现时统一为 **200 + ok/reason**，避免 ensure 把「未收盘」当成硬错误中断预览）。

### 4.2 实现要点

- 包装现有 `update_qlib_cn_from_tushare.py`（子进程或 import `main` 路径）。  
- 鉴权：现有 `X-RDAgent-Bridge-Token`。  
- 超时：QD 侧已放宽至 ≥ 900s；Bridge 侧允许长任务。  
- 幂等：日历 last ≥ end 且 `force=false` → `skipped=true`。

## 5. 选股「刷新分数」（即时路径）

流程：

```
用户选 as_of → ensure
  → 判定 as_of 是否可同步
  → 若分数精确日已存在 → 早退（effective_as_ofs）
  → 若可同步且 Qlib 落后 / 分数缺精确日 → qlib_update(end=as_of) → infer → 写库
  → 若不可同步（未收盘/未来）→ 不硬失败；返回 reason + effective_as_ofs（PIT）
  → 前端：对齐 as_of、提示文案（未收盘 / 已同步到日）
```

与既有逻辑关系：

- 保留「交易日晚于最新分数日 → 视为 missing」；周末 lag 仍可 PIT。  
- `_ensure_qlib_for_as_ofs` 在 Bridge 接口落地后应真正生效；失败时保留 `qlib_update.error` 字段。

前端提示（示例）：

- 未收盘：`当日未收盘，已显示最近分数日 YYYY-MM-DD`  
- 同步成功：`已同步行情并刷新至 YYYY-MM-DD`  
- 同步失败：展示 backend `qlib_update.error` / msg

## 6. Celery Beat 日终路径

仿 `quantdinger.tasks.csi300_enhanced_daily_sync`：

| 项 | 值 |
|----|-----|
| Task | `quantdinger.tasks.qlib_eod_sync` |
| 默认开关 | `ENABLE_QLIB_EOD_SYNC=true` |
| 调度 | 优先 crontab `30 15 * * 1-5`（上海）；若部署无 timezone crontab，可用「每 15–30 分钟轮询 + 收盘门闩 + 当日成功标记」防重 |
| 行为 | 仅 `bridge.qlib_update(end=最近已收盘交易日)` |
| 可选 | `ENABLE_QLIB_EOD_SCORE_WARMUP=false`：为 true 时对已发布模型 ensure 最近已收盘日（限流 / 串行） |

成功标记：Redis 或 DB 键 `qlib_eod_sync:YYYY-MM-DD`，避免同日重复全量 dump。

## 7. 错误与可观测

- Tushare / dump 失败：任务 retry ≤ 2；ensure 返回错误信息不吞掉。  
- Bridge 不可达：选股页提示检查 Bridge；定时任务记日志。  
- 进度：ensure 已有 `updating_qlib` / `inferring_scores` phase，保持兼容。

## 8. 测试计划

| 层 | 用例 |
|----|------|
| 单元 | 收盘门闩：盘中今天 → not_closed；15:05 后今天 → ok；昨天交易日 → ok；周末 → 非交易日 |
| 单元 | ensure：缺精确日且已收盘 → 调用 qlib_update 再 infer；未收盘 → 不 infer、带 reason |
| Bridge | `force=false` 且日历已新 → skipped；缺日 → 调用更新脚本（可 mock） |
| 集成（手工） | 收盘后点刷新：日历与分数到当日；盘中点刷新：提示未收盘并对齐昨日 |

## 9. 实现顺序建议

1. Bridge `qlib/update` + 收盘门闩共享函数  
2. ensure 接好失败/未收盘语义 + 前端文案  
3. Celery task + beat 配置 + env  
4.（可选）分数预热开关  

## 10. 决议记录

- 用户选择 **C**：即时刷新 + 日终定时都要。  
- 日终默认 **只同步 Qlib**，不默认全量预热模型分数。  
- 当日可拉阈值：**15:05** 上海时间。  
- 设计口头确认：2026-08-05（用户「没问题」）。
