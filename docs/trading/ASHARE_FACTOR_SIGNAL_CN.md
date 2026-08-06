# A 股因子信号：Tushare、回测与邮件部署

更新日期：2026-07-30

本文说明如何在 QuantDinger 上配置 A 股（`CNStock`）数据源、运行示例周频动量策略、对比沪深300/中证500，并以 **signal 模式 + 邮件** 部署（不下单）。

> **安全提示**：`TUSHARE_TOKEN`、SMTP 密码等敏感信息仅写入运行环境（如 `backend_api_python/.env`），**切勿提交到 Git 或粘贴到工单/日志**。

---

## 1. 前置条件

- QuantDinger 后端与数据库已正常运行。
- 已从运营商或 Tushare 服务方获取 **Token** 与 **HTTP API 基址**（自定义网关 URL）。
- 具备可用的 SMTP 发信账号（应用专用密码，非登录密码）。

---

## 2. 配置 Tushare（A 股日线优先数据源）

在 `backend_api_python/.env`（可从 `backend_api_python/env.example` 复制）中设置：

```bash
# A-share Tushare (optional; preferred daily source for CNStock when set)
TUSHARE_TOKEN=your_token_here
# Custom HTTP API base for Tushare-compatible gateways (optional)
TUSHARE_HTTP_URL=https://your-operator.example.com/api
```

说明：

| 变量 | 含义 |
|---|---|
| `TUSHARE_TOKEN` | Tushare 或兼容网关颁发的 Token |
| `TUSHARE_HTTP_URL` | 运营商提供的 API 基址（末尾通常为 `/api`） |

- **HTTP 基址请向运营商索取**，不同部署环境 URL 不同。
- 配置后重启后端；`CNStock` 日线会优先走 Tushare，失败时回退 Twelve Data / 腾讯 / yfinance / AkShare 等既有链路。
- 未配置时 A 股仍可用，但日线质量与稳定性可能不如 Tushare 方案。

---

## 3. 让 A 股在 UI 中可见

市场可见性由 `backend_api_python/app/utils/market_visibility.py` 统一解析，与 `env.example` 保持一致。

**推荐（白名单）：**

```bash
ENABLED_MARKETS=Crypto,USStock,CNStock
```

**或沿用旧开关（未设置 `ENABLED_MARKETS` 时生效）：**

```bash
SHOW_CN_STOCK=true
```

注意：

- 若 `ENABLED_MARKETS` **非空**，则仅白名单内市场可见；此时须显式包含 `CNStock`，单独设置 `SHOW_CN_STOCK=true` 无效。
- 修改后重启后端；自选/Agent 市场列表与首页雷达会同步过滤。

---

## 4. 配置 SMTP（signal 邮件）

在同一 `.env` 中填写：

```bash
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=notify@example.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=notify@example.com
SMTP_USE_TLS=true
SMTP_USE_SSL=false
```

字段含义见 `env.example` 中 **Email / SMTP** 小节。更详细的提供商示例见 [NOTIFICATION_EMAIL_CONFIG_EN.md](../deployment/NOTIFICATION_EMAIL_CONFIG_EN.md)。

配置完成后重启后端。

---

## 5. 确认股票池快照（csi300 / csi500）

示例策略依赖平台公开股票池 `csi300`（沪深300）与 `csi500`（中证500）。首次部署或长期未更新时，请刷新快照：

```bash
cd backend_api_python
python scripts/refresh_public_universe_snapshots.py \
  --universes csi300,csi500 \
  --as-of YYYY-MM-DD
```

建议先加 `--dry-run` 检查成员数量（csi300 约 300、csi500 约 500）。脚本内置完整性门槛，数量异常时拒绝写库。

公开池为**时点快照**，非完整官方历史成分；存活偏差与调仓时点偏差见 [PUBLIC_UNIVERSE_AND_FUNDAMENTALS_CN.md](./PUBLIC_UNIVERSE_AND_FUNDAMENTALS_CN.md)。

---

## 6. 导入示例策略并回测

示例源码：`docs/examples/strategy_v2_cn_momentum_weekly.py`

策略要点：

- 仅做多（`long_only`），每周一 09:35 调仓（`run_weekly(..., weekday=1)`）。
- 默认股票池 **csi300**，20 日动量 Top-N（默认 N=10）。
- 适合 signal 模式：产生调仓意图，不向券商下单。

**操作步骤：**

1. 在策略 IDE 新建 Strategy API V2 源码，粘贴示例文件全文。
2. 点击 **验证/编译**，确认通过（`initialize`、`set_universe`、周调度等契约正确）。
3. 发起 **回测**，建议先用 **短区间**（如 3–6 个月）确认每周持仓变化与净值曲线合理。
4. 检查回测输出：`rebalanceRecords` / `holdingSnapshots` 应仅在周一（或平台约定的周调仓日）变化；`orderLedger` 中 reason 含 `weekly_select` / `weekly_remove`。

可调参数（策略内 `# @param` 注释）：

| 参数 | 默认 | 说明 |
|---|---:|---|
| `holdings` | 10 | 持仓只数 |
| `momentum_period` | 20 | 动量回看天数 |
| `max_weight` | 0.12 | 单票权重上限 |

---

## 7. 对比 csi300 与 csi500

示例策略在 `initialize` 中**写死** `pool="csi300"` 与基准 `CNStock:000300.SH`，**不支持**在部署面板通过 `pool_name` 运行时切换。

| 池 | 操作 |
|---|---|
| **csi300** | 直接使用示例源码 |
| **csi500** | **复制一份策略文件**，修改 `initialize` 内两处：<br>• `context.set_universe(pool="csi500")`<br>• `context.set_benchmark("CNStock:000905.SH")` |

分别保存为两个策略源、各自回测，对比净值、换手与持仓差异。signal 部署时建议各建一套部署（或同名加后缀区分），便于邮件侧对比。

---

## 8. Signal 部署（邮件通知）

回测满意后，创建 **stopped** 部署并配置：

| 字段 | 值 |
|---|---|
| `executionMode` | `signal` |
| `notificationChannels` | 含 `"email"` |
| `notificationTargets.email` | 收件邮箱（多个可用英文逗号分隔） |

**UI 路径（概要）：** 策略页 → 从已保存源码创建部署 → 执行模式选 **信号（signal）** → 通知渠道勾选 **邮件** → 填写收件地址 → 保存后 **启动**。

**API 示例（Agent Gateway）：**

```json
{
  "name": "cn-momentum-csi300-weekly",
  "sourceId": 123,
  "initialCapital": 1000000,
  "executionMode": "signal",
  "notificationChannels": ["email"],
  "notificationTargets": {
    "email": "you@example.com"
  },
  "params": {
    "holdings": 10,
    "momentum_period": 20,
    "max_weight": 0.12
  }
}
```

signal 模式 **不需要** 券商凭证；不会向交易所下单。

---

## 9. 验收清单

| 项 | 预期 |
|---|---|
| Tushare | 配置 Token/基址后，CNStock 日线可拉取；日志无认证错误 |
| 市场可见 | UI 自选可添加 A 股、`csi300`/`csi500` 池可选 |
| 回测 | 短区间内有周频调仓记录；无未来函数类异常 |
| 非调仓日 | signal 运行中 **不应** 按日刷邮件或重复推送相同意图 |
| 调仓日 | 周一（策略 `weekday=1`）调仓后收到 **至少一封** 调仓邮件，正文含策略名、标的、动作/权重等 |
| 双池 | csi300 与 csi500 各一套源码/部署均可独立跑通 |

邮件发送失败时：查看后端日志与 SMTP 配置；**信号意图仍会生成**，策略循环不因邮件失败中断（见设计文档错误处理约定）。

---

## 10. 相关文档

- 设计规格：[2026-07-30-ashare-factor-signal-design.md](../superpowers/specs/2026-07-30-ashare-factor-signal-design.md)
- 策略 API V2：[STRATEGY_DEV_GUIDE_CN.md](./STRATEGY_DEV_GUIDE_CN.md)
- 公开股票池：[PUBLIC_UNIVERSE_AND_FUNDAMENTALS_CN.md](./PUBLIC_UNIVERSE_AND_FUNDAMENTALS_CN.md)
- SMTP 详解：[NOTIFICATION_EMAIL_CONFIG_EN.md](../deployment/NOTIFICATION_EMAIL_CONFIG_EN.md)

---

## 11. 玻纤行业周信号 / 巨石 CTA（扩展示例）

除周频动量示例外，仓库提供 **中国巨石卫星仓 CTA**，消费周度玻纤行业信号（7628 电子布趋势、库存、新产能）作为开仓门闩；无有效行业行时 **不开新卫星仓**。A 股仍建议 **signal 模式 + 邮件** 部署。

| 资源 | 路径 |
|---|---|
| 设计规格 | [2026-08-06-jushi-cta-industry-signal-design.md](../superpowers/specs/2026-08-06-jushi-cta-industry-signal-design.md) |
| 实现计划 | [2026-08-06-jushi-cta-industry-signal.md](../superpowers/plans/2026-08-06-jushi-cta-industry-signal.md) |
| 示例策略 | [`strategy_v2_jushi_satellite_cta.py`](../examples/strategy_v2_jushi_satellite_cta.py) |
| 表迁移 | `backend_api_python/migrations/20260806_industry_glass_fiber_weekly.sql` |
| manual 覆盖 CLI | `backend_api_python/scripts/upsert_glass_fiber_industry_week.py` |

**人工覆盖周信号（示例）：**

```bash
cd backend_api_python
python scripts/upsert_glass_fiber_industry_week.py \
  --as-of 2026-08-01 --cloth-trend 1 --inventory-trend -1 --source manual
```

策略沙箱内调用 `get_glass_fiber_industry_week()` 读取合并后的有效行（优先级：`manual` > 付费源预留 > `public_news`）。公开快讯由 Celery Beat 任务 `quantdinger.tasks.glass_fiber_industry_sync` 写入；`GLASS_FIBER_NEWS_URLS` 为空时任务 **skip**，不伪造行业中性信号。

**日内 T 语义（MVP）：** `enable_intraday_t=1` 时策略 **仅记录日志**，表示允许外部执行器在日内做 T；**不会**在 14:55 单独下平仓单。外部执行器须在收盘前自行平掉 T 敞口；策略日频 `order_target_percent` 始终是 **卫星仓目标权重**，不含 T 往返。

公开解析 **非官方点价**，仅供研究；付费卓创/隆众源本期未接，仅 `source` 预留。
