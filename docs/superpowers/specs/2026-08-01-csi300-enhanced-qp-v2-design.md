# 沪深300指增 QP 2.0 设计

> 日期：2026-08-01  
> 状态：已确认（待实现）  
> 数据源：Tushare（含自定义 HTTP 网关）  
> 交付形态：新建策略/模板 **CSI300 Enhanced Index QP 2.0**，与 1.0 并存  
> 前置规格：[2026-07-31-csi300-enhanced-qp-design.md](./2026-07-31-csi300-enhanced-qp-design.md)

## 1. 背景与问题

1.0（MVP）已落地并可回测，但存在结构性局限：

| 问题 | 1.0 现状 |
|------|----------|
| Alpha 同质化 | 仅动量 + 波动率线性合成，无基本面/资金流/一致预期 |
| 基准简化 | `w_b = 1/n` 等权，非官方自由流通权重 |
| 无行业约束 | 优化器支持 `industry=` 但策略未传入 |
| 无市值中性 | 预处理有 `neutralize_industry_size`，策略未调用 |
| 动量通病 | 无反转/体制过滤，急跌行情易持续回撤 |

平台已有可复用积木：`fetch_index_weights`、`neutralize_industry_size`、`optimize_enhanced_index(..., industry=...)`、权重/资金流/一致预期占位表。2.0 目标是把这些接成可复现的商用级流水线。

## 2. 目标与非目标

### 2.1 目标

- 官方自由流通权重基准（PIT `index_weight`）
- 多层 Alpha：量价 + 估值质量 + 资金流/北向 + 一致预期（权限不足则该层降权/跳过）
- 因子层：行业中性 + 市值中性
- 组合层：单票主动、行业主动、市值风格带宽、TE 硬约束、换手惩罚/阈值
- 反转体制：熊市急跌时降动量权重或切防守模式
- 新建策略与模板种子：`CSI300 Enhanced Index QP 2.0` / `strategy_v2_csi300_enhanced_v2`
- **1.0 策略、模板与默认优化器行为保持不变**

### 2.2 非目标（后续版本）

- 商用 Barra 授权模型替换
- 实盘 SOR / 算法拆单 / 冲击成本模型
- 用 LightGBM/FFN 完全替代线性 Alpha

## 3. 架构总览

```text
日终同步 (Celery/scheduler)
  Tushare → 本地权重/行业/daily_basic/财务/北向/一致预期表

每个交易日
  Step1 池过滤 → Step2 因子预处理(中性化) → Step3 分层α(+ICIR)
  监控偏离 / TE / 流动性
  if 触发微调: 局部 QP（冻结多数权重）

每周主窗口（默认周一）
  w_b = PIT 官方权重
  全量 QP（行业/市值/TE）→ w*
  if 换手 ≥ 阈值: 调仓；else 跳过
```

策略热路径 **只读本地截面**，禁止在调仓循环内打满 Tushare。

## 4. 数据与落库

### 4.1 接口映射（以实现时账号权限为准）

| 用途 | Tushare 接口 | 落库 |
|------|--------------|------|
| 成分与权重 | `index_weight` | `qd_csi300_index_weights`（已有，补写入 + PIT 读取） |
| 估值/流通市值/换手 | `daily_basic` | 新建 `qd_ashare_daily_basic`（或等价日频截面表） |
| 行业 | `stock_basic` / 申万行业接口 | 新建 `qd_ashare_industry_map` |
| 财务质量 | `fina_indicator` | 新建 `qd_ashare_fina_snapshot`（财报日更新，其余沿用） |
| 北向 | `hk_hold` 等 | `qd_ashare_flow_daily`（已有占位，补写入） |
| 一致预期 | `forecast` / `report_rc` 等 | `qd_ashare_consensus_daily`（已有占位，补写入） |
| 行情 | `daily`（现有 CNStock 链路） | 沿用现有 K 线缓存 |

### 4.2 读取 API（供策略/服务）

| API | 职责 |
|-----|------|
| `get_csi300_bench_weights(as_of) -> dict[str,float]` | PIT 官方权重；缺失则宇宙 `member_weight`；再缺失则 `1/n` + warning |
| `get_industry_map(symbols) -> dict[str,str]` | 行业代码 |
| `get_size_zscore(symbols, as_of) -> Series` | 流通市值对数截面 zscore |
| `get_factor_panel(symbols, as_of, factor_ids) -> DataFrame` | 原始/中性化因子列 |
| `build_layered_alpha(panel, layer_weights, *, icir=None, regime=None) -> Series` | 分层合成 |

同步任务失败时：该数据日对应层标记 `unavailable`，Alpha 层权重自动再分配到可用层；整条流水线不得硬失败。

## 5. Alpha（日频）

### 5.1 池过滤（相对 1.0 增强）

在 `pool=csi300` 与 `min_history_bars` 之外增加（可配开关，默认开）：

- 停牌 / 涨跌停（不可买入侧）尽力而为（数据可得时）
- 近 20 日日均成交额下限
- ST / 上市不足 60 交易日（数据可得时）

### 5.2 因子预处理

对每个因子截面：

1. MAD Winsor（默认 5×MAD）  
2. 截面 Z-score  
3. **行业中性 + 市值中性**（调用/扩展 `neutralize_industry_size`）  
4. 缺失：层内截面中位数填充；缺失率过高则当日该因子失效  

### 5.3 分层与默认权重

| 层 | 默认权重 | 优先因子 |
|----|----------|----------|
| 资金流 | 25% | 北向 1D/5D/20D、融资变动（可得则用） |
| 一致预期 | 20% | EPS/NP 上修、Forward EP 等（按权限） |
| 估值质量 | 20% | EP/BP、ROE/ROA、成长同比 |
| 动量 | 20% | mom_60/120；短动量默认关闭 |
| 风险流动性 | 15% | 低 Vol_20、换手/成交额 |

层权重通过策略 `# @param` 暴露，和必须为 1（归一）。

### 5.4 ICIR（2.0 内做）

- `# @param use_icir bool true`  
- 滚动窗口估计层内/因子 ICIR，作非负权重再归一  
- ICIR 样本不足时回退等权层内合成  

### 5.5 体制过滤（反转保护）

触发条件（可配，默认）：

- 基准 `000300` 近 20 日收益 < 阈值（默认 -8%），或  
- 近 20 日已实现波动显著抬升  

动作：

- 动量层权重 → 0（或乘 `regime_mom_scale`，默认 0）  
- 提高风险流动性 + 估值质量层相对权重  
- 可选：提高 `risk_aversion` / 降低 `active_limit`（防守）  

## 6. 优化器（周频主 + 日频局部）

### 6.1 扩展现有对角 QP（保持 1.0 兼容）

扩展 `optimize_enhanced_index`（或新增 `optimize_enhanced_index_v2` 并让 v2 策略调用）：

| 约束 | 默认 | 说明 |
|------|------|------|
| 满仓、禁止做空 | Σw=1, w≥0 | 已有 |
| 单票主动 | ≤2.5% | 已有 `active_limit` |
| 行业主动 | ≤5% | 已有投影；2.0 必须传入 `industry` |
| 市值风格带宽 | 0.4σ（可配） | **新增**相对基准的 size exposure 投影 |
| TE 硬约束 | 年化 4% | **新增** ex-ante TE（对角/简化风格 Σ）；超限则收缩主动权重 |
| 换手惩罚 | `turn_penalty` | 已有 |
| 特异风险 | `idio_var` | 2.0 传入 realized vol²，替代全 1 |

不可行时降级顺序：

1. 放宽市值带宽 → 2. 放宽行业限额 → 3. 降低 `active_limit` → 4. 接近 `w_b` 并记录 `status=degraded_*`

### 6.2 日频局部 QP

- 默认不跑全量优化  
- 触发：主动偏离超阈值、ex-ante TE 破位、成分大幅调出  
- 冻结未触发的大部分权重，仅对子集再投影  

### 6.3 基准权重

```text
w_b = get_csi300_bench_weights(as_of)
  1) qd_csi300_index_weights PIT
  2) qd_universe_members.member_weight（csi300 快照）
  3) equal weight + structured warning
```

## 7. 策略契约（2.0）

### 7.1 产物

| 产物 | 路径/标识 |
|------|-----------|
| 示例源码 | `docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py` |
| 模板种子 | `strategy_v2_csi300_enhanced_v2`（SQL/seed，与 1.0 并存） |
| 用户可见名 | `CSI300 Enhanced Index QP 2.0` |

### 7.2 调度

- `pool=csi300`，benchmark `CNStock:000300.SH`  
- `run_daily(update_alpha_and_monitor)`  
- `run_weekly(rebalance, weekday=1)`  

### 7.3 关键 `# @param`（初值）

- 层权重：`w_flow`, `w_consensus`, `w_value_quality`, `w_momentum`, `w_risk_liq`  
- `use_icir`, `regime_enabled`, `regime_ret_threshold`  
- `industry_limit`, `size_limit`, `te_limit`  
- 保留 1.0 参数：`active_limit`, `risk_aversion`, `turn_penalty`, `min_turnover`, `mom_*`, `vol_period`, `min_history_bars`, `universe_top_n`

`initialize` 内仍禁止读取 `context.params`（平台契约不变）；参数仅在 handler 中读取。

## 8. QuantDinger 落点

| 模块 | 职责 |
|------|------|
| `csi300_enhanced/tushare_sync.py` | 扩展写入权重/daily_basic/行业/财务/北向/一致预期 |
| 同步命令 + Celery 任务 | 日终拉取；支持手动 `scripts/sync_csi300_enhanced_data.py` |
| `csi300_enhanced/preprocess.py` | 复用并文档化中性化；分层合成 helper |
| `csi300_enhanced/optimizer.py` | size 带宽 + TE 硬约束 + 降级 |
| `csi300_enhanced/bench.py`（新建） | PIT 权重读取与降级 |
| Strategy runtime 注入 | 向策略暴露 `optimize_enhanced_index`（v2 行为经参数启用）及必要只读 helper |
| 回测中心 | 2.0 结果可看超额、TE、行业/市值偏离、换手（尽量复用现有诊断字段） |

## 9. 分期落地

| 阶段 | 内容 | 验收焦点 |
|------|------|----------|
| **C1** | 权重落库+PIT、`daily_basic`/行业、中性化 Alpha（动量+低波+估值质量）、行业+市值约束 QP、2.0 策略骨架可回测 | 不再默认 1/n；有行业/市值约束日志 |
| **C2** | 北向/融资、一致预期层、ICIR、regime | 层缺失自动降权；regime 可单测 |
| **C3** | TE 硬约束强化、日频局部 QP、回测诊断补齐 | TE 超限有收缩/降级；1.0 vs 2.0 同区间可对比 |

实现顺序严格按 C1 → C2 → C3；每阶段保持 1.0 绿测。

## 10. 验收标准

1. 日终同步后，权重/关键因子/α 截面可查，覆盖率与缺失率可观测  
2. 同一回测区间可并行跑 1.0 与 2.0，对比超额、TE、行业/市值偏离、换手  
3. 存在官方权重数据时，2.0 **不得**静默使用纯 1/n（降级必须打日志）  
4. TE / 行业 / 市值约束在优化 `status` 与日志中可见  
5. Tushare 权限缺失层自动降权，流水线不整体失败  
6. 单测覆盖：PIT 权重、中性化、size/TE 投影、regime、模板种子、1.0 回归  
7. 低于换手阈值的周次不产生无效调仓（与 1.0 一致）

## 11. 风险与依赖

- Tushare 套餐字段不足 → 对应层 `unavailable` 并再分配权重  
- 简化 Σ 与真实 Barra 有差距 → TE 为模型内 ex-ante，不是交易所真 TE  
- 日频全宇宙算力 → 批量向量化 + 本地缓存，禁止策略内重拉全历史 API  
- 公开宇宙快照与官方权重日期可能不一致 → 以权重表 PIT 为主，成分以池与权重支撑集交集为准  

## 12. 已确认决策摘要

- 范围：方案 C（完整商用级：约束 + 多层 Alpha + ICIR + regime + TE）  
- 交付：新建 2.0，与 1.0 并存对比  
- 数据：Tushare 网关；热路径只读本地  
- 分期：C1 骨架可回测 → C2 另类层/ICIR/regime → C3 TE/局部 QP/诊断  
- 1.0 行为冻结，避免回归  
