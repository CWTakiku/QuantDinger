# 研究工厂标的池（来自 QD）设计

## 目标

研究工厂启动 `fin_factor` / `fin_quant` 时可选择 **CNStock 标的池**（来自 QuantDinger `qd_universes`），并自动设置 Qlib `market` + `benchmark`，不再写死 `csi300` / `SH000300`。

## 范围

- **池来源**：`GET /api/universes` 中 `market=CNStock` 的系统池 + 用户手动池 + `watchlist`；另加特殊项 **全市场**。
- **非 CN / 美港股池**：不下发。
- **数据源**：仍用现有 `data_source`（default / quantmind）；池只决定 instruments 子集与基准。

## 基准规则（按池类型推断）

| 选择 | Qlib `market` | `benchmark` |
|------|---------------|-------------|
| 特殊项「全市场」 | `all`（provider 自带 `instruments/all.txt`） | `SZ000985`（中证全指；QuantMind 已有） |
| `csi300` | 由 QD 成员导出的 instruments 文件 | `SH000300` |
| `csi500` | 同上 | `SH000905` |
| 其它系统指数池（若有 CN） | 同上 | 有 `source_ref` 映射则用对应指数，否则等权 |
| `manual` / `watchlist` | 同上 | **成分等权**：YAML `benchmark` 为成员代码列表（Qlib 对多代码取收益均值） |

## 符号

- QD：`600519.SH` → Qlib：`SH600519`
- 成员 as_of：任务 `end_date`（若有），否则当天 UTC 日期
- 导出后与 provider 无行情的代码可保留在 instruments 中（Qlib 会跳过缺失）；若有效成员 &lt; 10 则拒绝启动

## 调用链

```
Vue 研究工厂
  → GET /api/rdagent/universes          # QD 过滤 CN + 注入全市场
  → POST /api/rdagent/jobs { universe_code }
  → QD resolve_members → bridge POST /v1/jobs
       { universe: { code, kind, market_id, members_qlib[], benchmark } }
  → Bridge: 写 instruments/<market_id>.txt（全市场跳过）
  → TemplatePatcher: 改 conf_*.yaml 的 market / benchmark
  → rdagent 运行
```

## 非目标

- 不在研究工厂内编辑池成员（仍用 QD 标的池管理）
- 不自动把非 A 股池接到 Qlib
- 不改变分数导入 `source/version` 契约

## 验收

1. 下拉可见：全市场、csi300、csi500、当前用户 CN 手动池。
2. 选 csi300 启动后模板 `market`/`benchmark` 为导出池与 `SH000300`；回测不再因缺基准失败（quantmind 已有别名）。
3. 选手动池时 `benchmark` 为成员列表；组合回测能算出超额（相对等权）。
4. 选全市场时 `market=all`，`benchmark=SZ000985`。
