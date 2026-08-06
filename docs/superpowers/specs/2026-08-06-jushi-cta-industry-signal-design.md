# 中国巨石卫星仓 CTA + 玻纤行业信号（公开抓取 MVP）设计

> 日期：2026-08-06  
> 状态：**计划已就绪；MVP 实现已完成**（Task 1–5，公开抓取 + manual + CTA 示例）。  
> 实现计划：[`2026-08-06-jushi-cta-industry-signal.md`](../plans/2026-08-06-jushi-cta-industry-signal.md)  
> 标的：`CNStock:600176.SH`（中国巨石）  
> 相关：`docs/trading/STRATEGY_DEV_GUIDE_CN.md`、`docs/examples/strategy_v2_jushi_satellite_cta.py`、`docs/trading/ASHARE_FACTOR_SIGNAL_CN.md`

## 1. 背景与问题

用户已有组合底仓，希望对中国巨石做 **卫星仓 CTA**（总仓 ≤20%），并叠加：

1. **行业外部信号**（电子布 7628、粗纱、库存、新增产能）作为周期命门；  
2. **公司基本面 / 估值 / 流动性** 过滤；  
3. **硬性离场与日内 T 子逻辑**（T 仓 ≤ 巨石仓 25%，14:55 前了结，不隔夜）。

行业价与库存主源（卓创 / 隆众）多为付费终端，仓库内无现成接入。用户选择：

- **先做公开资讯抓取 MVP**；  
- **表结构预留付费源与人工覆盖**；  
- 是否有付费权限 **暂不确定**。

A 股在 QuantDinger 上 **无券商 live 通道**，CTA 以 **回测 + signal 模式** 落地。

## 2. 目标与非目标

### 2.1 目标（本期）

1. **周度行业信号表**（标准化字段，多源共存，可读优先级明确）。  
2. **公开资讯采集任务**：定时抓取可公开访问的玻纤/电子布相关快讯，解析为 trend / flag / 可选价格，写入表（`source=public_news`）。  
3. **人工覆盖**：同周可写入 `source=manual`，策略优先读覆盖。  
4. **巨石 CTA 示例策略**：消费行业表 + 量价/估值/仓位/离场/日内 T 开关；无有效行业信号时 **不开新卫星仓**。  
5. **信号模式友好**：日志写明开平仓与关 T 原因，便于邮件/通知跟踪。

### 2.2 非目标（本期不做）

- 破解或绕过卓创 / 隆众登录与付费墙。  
- 付费 API 实装（仅预留 `source` 枚举与 writer 接口形状）。  
- A 股券商自动下单 / 真实账户 T+1 股数账本对接。  
- 完整还原历史点价序列用于多年精确回测（公开源噪声大；回测可用 manual 历史或缺失则跳过开仓）。  
- 把组合底仓周频调仓与巨石 CTA 揉成单一策略。

## 3. 架构

```
公开快讯源（财联社等可公开 URL）
        │
        ▼
  采集 + 解析（Celery Beat / 管理脚本）
        │
        ▼
  qd_industry_glass_fiber_weekly
        │
        ├── public_news（自动）
        ├── manual（人工覆盖）
        └── zhuochuang | oilchem（预留，本期不写）
        │
        ▼
  策略沙箱只读 API（如 get_glass_fiber_industry_week(as_of)）
        │
        ▼
  巨石卫星仓 CTA（Strategy API V2，signal）
```

原则：

- 策略进程 **不直接 HTTP 抓网页**（超时、反爬、不确定性）；只读库内标准化行。  
- 采集与交易解耦；采集失败不得伪装成「行业中性允许开仓」。

## 4. 数据模型

### 4.1 表：`qd_industry_glass_fiber_weekly`

建议字段：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | bigserial | PK |
| `as_of` | date | 周观察截止日（建议周五或当周最后一个交易日） |
| `cloth_7628_mid` | numeric null | 7628 电子布中间价（元/米），可空 |
| `yarn_2400_mid` | numeric null | 2400tex 粗纱中间价，可空 |
| `cloth_trend` | smallint | −1 / 0 / +1（相对上周或文本推断） |
| `inventory_trend` | smallint | −1 / 0 / +1（库存下降为 −1，累积为 +1） |
| `new_capacity_flag` | smallint | 0 / 1（大规模新增点火/集中投放） |
| `source` | varchar(32) | `public_news` \| `manual` \| `zhuochuang` \| `oilchem` |
| `confidence` | real | 0–1；&lt; 0.5 视为对该源无效 |
| `raw_refs` | jsonb | 链接、摘要、解析痕迹（审计） |
| `created_at` / `updated_at` | timestamptz | |

约束：

- `UNIQUE (as_of, source)`  
- `cloth_trend` / `inventory_trend` ∈ {−1, 0, 1}  
- `new_capacity_flag` ∈ {0, 1}  
- `confidence` ∈ [0, 1]

### 4.2 读取优先级

同周多源时，策略取 **一条有效行**：

1. `manual` 且 `confidence ≥ 0.5`  
2. `zhuochuang` / `oilchem`（并列时取 `updated_at` 更新；本期无数据则跳过）  
3. `public_news` 且 `confidence ≥ 0.5`

若无有效行 → `industry_available=false` → **禁止新开卫星仓**；已有持仓的离场规则仍可依据量价/止损触发；日内 T **关闭**。

### 4.3 连续两周规则

- **卖出预警 / 硬离场（行业）**：最近两周有效行均满足 `cloth_trend < 0` 且 `inventory_trend > 0`（或等价「价格下行 + 库存抬升」）。  
- **开仓行业侧**：最近一周 `cloth_trend ≥ 0` 且 `inventory_trend ≤ 0` 且 `new_capacity_flag = 0`。

## 5. 公开采集 MVP

### 5.1 源与频率

- 初始源：可公开访问的玻纤 / 电子布 / 电子纱快讯列表或详情页（实现时固定白名单 URL；优先财联社等公开稿，避免付费墙）。  
- Beat：每个交易日 1–2 次；**按周聚合** 为最多一行 `public_news`。  
- User-Agent / 限速 / 失败重试；禁止并发打爆目标站。

### 5.2 解析

从标题与正文抽取：

- 价格数字（7628、电子布 元/米、电子纱 元/吨）→ 可选填 `cloth_7628_mid` 等；  
- 涨跌语义 → `cloth_trend`；  
- 库存松紧 / 累积 → `inventory_trend`；  
- 点火、新产能、织机放量 → `new_capacity_flag`。

规则：

- 无可靠数字时价格可空，但应尽量给出 trend；  
- 冲突语义时降低 `confidence`；  
- 当周无任何可解析稿件 → 不覆盖为「假 0」；可保留上周行或标记缺失并由策略视为 unavailable。

### 5.3 合规与安全

- 只抓公开页；不存储付费 cookie；不提交密钥到 Git。  
- `raw_refs` 仅存 URL 与短摘要，控制体积。  
- 文档与日志标明：公开解析 **非官方点价**，仅供研究信号。

## 6. 策略沙箱 API

建议只读函数（名称实现时可微调）：

```text
get_glass_fiber_industry_week(as_of=None) -> dict | None
```

返回合并后的有效行字段 + `industry_available`；`as_of` 默认策略当前日，按 PIT：`as_of ≤ 当前日` 的最近周。

注入位置：与现有 `get_fundamentals` / `get_external_alpha_scores` 同类的策略 globals。

## 7. 巨石 CTA 行为（Strategy API V2）

### 7.1 形态

- 静态单标的：`CNStock:600176.SH` → **CTA**。  
- `direction_mode=long_only`；禁止杠杆。  
- 订阅：波段用 `1d`；日内 T 子逻辑可用 `5m`/`15m`（若数据质量不足，MVP 可先用日线模拟「当日平仓」语义 + 收盘前强制目标回底仓，并在文档标明限制）。  
- 部署：`executionMode=signal`。

### 7.2 仓位

| 规则 | 参数默认 |
|------|----------|
| 卫星仓上限（占组合） | `satellite_max_pct=0.20` |
| 日内 T 机动仓 | ≤ 巨石持仓的 `t_max_frac=0.25` |
| 硬止损 | `hard_stop_pct=0.12`（浮亏触及减仓/清卫星） |
| PE 分位止盈 | `pe_exit_percentile=0.90`（有估值数据时） |
| 流动性门槛 | 近 N 日均成交额 &gt; `min_amount`（默认 40 亿；可配置） |
| 高换手预警 | 换手 &gt; `turnover_warn`（默认 0.08）→ 关 T 或降 T |

核心底仓不在本策略账本内；本策略目标权重仅表示 **卫星增强仓**。

### 7.3 开仓（多数条件，行业为门闩）

同时满足（行业门闩为硬条件）：

1. `industry_available` 且开仓行业侧通过；  
2. 基本面：MVP 可用参数 `gm_qoq` / `np_qoq` / `cash_profit_ratio`（季报手填或后续接 fundamentals）；净现比持续 &lt; 0.8 时拒绝新开；  
3. PE-TTM 未处历史极高分位（有数据时）；  
4. 流动性满足；  
5. 目标仓位 ≤ `satellite_max_pct`。

### 7.4 硬离场（任一）

- 行业连续两周转空（§4.3）；  
- 基本面恶化（毛利环比明显下滑且净现比 &lt; 0.8，参数或 fundamentals）；  
- 浮亏 ≥ `hard_stop_pct`；  
- PE 分位 ≥ 止盈阈值；  
- 参数 `market_stress=1`（大盘/板块极端，关仓或仅关 T）。

### 7.5 日内 T 子逻辑

- 仅当卫星仓 &gt; 0、行业未转空、未 `market_stress`、流动性 OK、当日 T 亏损未触达限额；  
- 机动仓 ≤ 持仓 × `t_max_frac`；  
- **收盘前（如 14:55 上海）强制** 将 T 临时仓目标了结，使标的策略仓回到「底仓语义」——MVP 在 signal 下表达为收盘目标权重回到卫星目标、不隔夜放大；  
- 正 T / 反 T 具体微观信号可参数化（均线/冲高回落等），本期以框架 + 一种简单默认信号即可。

## 8. 评价指标（日志 / 回测后处理）

- 卫星持有期相对基准（如沪深300）超额；  
- 日内 T 贡献（若有独立 reason 标签可分拆）；  
- 最大回撤；  
- 周期捕捉：开仓是否落在行业 `cloth_trend≥0` 周、离场是否落在双周转空附近（研究型统计，非交易硬条件）。

## 9. 实现锚点（建议）

| 区域 | 路径建议 |
|------|----------|
| 迁移 / DDL | `backend_api_python` 既有 migration 习惯 |
| Store | `app/services/industry_glass_fiber/store.py` |
| 采集 | `app/tasks/` 或 `scripts/` + Celery Beat 条目 |
| 沙箱注入 | `app/services/strategy_v2/runtime.py` |
| 示例策略 | `docs/examples/strategy_v2_jushi_satellite_cta.py` |
| 单测 | 解析 fixture、优先级合并、策略无行业拒开仓 |

## 10. 验收标准

1. 迁移后表存在；可插入 `public_news` / `manual` 行。  
2. 同周 `manual` 覆盖后，只读 API 返回 manual 字段。  
3. 采集任务在 fixture HTML/文本上解析出 trend，并 upsert 周行。  
4. CTA：无有效行业行时不开新仓；行业双周转空时发出减仓/清仓意图；日志含 reason。  
5. 仓位目标不超过 `satellite_max_pct`；T 相关参数在 schema 中可配。  
6. 文档写明：公开源非官方点价；A 股为 signal；付费源预留未接。

## 11. 风险与限制（须在报告/说明中保留）

- 周期下行与供给刚性、产能 2–3 年释放、需求（AI/风电/地产）、成本、天量换手滑点。  
- 公开抓取漏报/误报 → 依赖 `confidence` 与 manual 覆盖。  
- T+1 与真实可卖股数由执行端保证；平台 signal 不代替券商持仓账本。

## 12. 后续（非本期）

- 接入卓创/隆众官方或代理 API（同一表、`source` 切换）。  
- 基本面字段自动从 `get_fundamentals` / Tushare 财务接口填充。  
- 独立分钟 T 子策略进程（若日线框架不够用）。
