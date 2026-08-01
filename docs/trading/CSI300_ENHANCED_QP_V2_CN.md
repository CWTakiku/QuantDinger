# 沪深300指增 QP 2.0：同步、回测与诊断

更新日期：2026-08-01

本文说明 **CSI300 Enhanced Index QP 2.0**（`strategy_v2_csi300_enhanced_v2`）的数据同步、与 1.0 的差异、关键参数，以及回测结果中的指增约束诊断字段。

> **安全提示**：`TUSHARE_TOKEN` 等敏感信息仅写入 `backend_api_python/.env`，勿提交 Git。

---

## 1. 日终数据同步

在 `backend_api_python` 目录执行：

```bash
python scripts/sync_csi300_enhanced_data.py --trade-date 20260731
```

常用选项：

| 选项 | 说明 |
|------|------|
| `--trade-date YYYYMMDD` | 同步指定交易日（默认当天） |
| `--skip-industry` | 跳过申万/行业映射 |
| `--skip-flow` | 跳过北向/融资截面 |

同步写入本地表（策略热路径只读 DB，不打 Tushare）：

- `qd_csi300_index_weights` — 官方 PIT 权重
- `qd_ashare_daily_basic` — 流通市值等
- `qd_ashare_industry_map` — 行业
- `qd_ashare_flow_daily` / `qd_ashare_consensus_daily` — 资金流 / 一致预期（权限不足则跳过）

### Celery Beat（可选自动化）

| 项 | 值 |
|----|----|
| Beat key | `csi300-enhanced-daily-sync` |
| Task | `quantdinger.tasks.csi300_enhanced_daily_sync` |
| 间隔 | `CSI300_ENHANCED_SYNC_INTERVAL_SEC`（默认 `86400`） |
| 开关 | `ENABLE_CSI300_ENHANCED_DAILY_SYNC`（默认开启） |

未配置 `TUSHARE_TOKEN` 时各面板返回 0，不阻断 worker / beat 启动。也可手动调用同一编排函数 `run_csi300_enhanced_daily_sync`。

同步后刷新 `csi300` 宇宙成员，再在回测中心选择 2.0 模板。

---

## 2. 1.0 vs 2.0

| 项 | 1.0 (`strategy_v2_csi300_enhanced`) | 2.0 (`strategy_v2_csi300_enhanced_v2`) |
|----|-------------------------------------|----------------------------------------|
| 基准权重 | 宇宙等权 `1/n` | PIT 官方权重，缺失时等权降级 |
| Alpha | 动量 + 波动率 | 多层（动量/低波/估值质量/资金流/一致预期）+ 行业市值中性 |
| 约束 | 单票主动 | + 行业主动、市值带宽、TE 硬约束 |
| 调度 | 周频调仓 | 日频 alpha/监控 + 周频主调仓 + 可选日频局部 QP |
| 回测诊断 | 无结构化字段 | `diagnostics.enhancedIndex`（见下节） |

1.0 模板与默认行为**不变**；对比时建议使用相同区间与 `universe_top_n`。

---

## 3. 关键 `# @param`

### 层权重（和为 1，运行时归一）

- `w_momentum`, `w_risk_liq`, `w_value_quality`, `w_flow`, `w_consensus`

### 约束

| 参数 | 默认 | 说明 |
|------|------|------|
| `active_limit` | 0.025 | 单票主动上限 |
| `industry_limit` | 0.05 | 行业主动上限 |
| `size_limit` | 0.30 | 相对基准的 size exposure 带宽（z 空间） |
| `te_limit` | 0.08 | ex-ante TE 上限（与 `active_risk_proxy` 同尺度；`idio_var` 为年化方差 `(vol·√252)²`） |
| `min_turnover` | 0.02 | 周频最低换手，低于则跳过调仓 |

### 体制 / ICIR

- `use_icir`, `regime_enabled`, `regime_ret_threshold`

### 日频局部再平衡

- `partial_rebalance_enabled`, `partial_active_dev_trigger`, `partial_te_trigger`, `partial_min_turnover`, `partial_max_touch`

示例源码：`docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py`

---

## 4. 回测诊断字段

2.0 策略在每次成功周频/局部调仓后调用 `record_enhanced_index_diagnostics`，回测结果写入：

```json
{
  "diagnostics": {
    "enhancedIndex": {
      "rebalanceCount": 12,
      "benchFallbackRate": 0.0,
      "avgTeExante": 0.045,
      "maxTeExante": 0.072,
      "avgSizeExposure": 0.08,
      "maxIndustryActiveDeviation": 0.04,
      "last": { "...": "..." },
      "rebalances": [ "..."]
    }
  }
}
```

单次调仓记录（`last` / `rebalances[]`）主要键：

| 键 | 含义 |
|----|------|
| `benchSource` | `csi300_pit` 或 `equal_fallback` |
| `benchFallback` | 是否降级为等权基准 |
| `teExante` / `activeRiskProxy` | 优化器 ex-ante TE 代理 |
| `sizeExposure` | 主动权重对 size z 的暴露 |
| `industryActiveDeviation.maxAbs` | 最大行业主动偏离 |
| `industryActiveDeviation.byIndustry` | 分行业主动权重 |
| `optimizerStatus` | 如 `optimal`、`degraded_te` |
| `kind` | `weekly` 或 `partial` |

1.0 回测**不包含** `diagnostics.enhancedIndex`，向后兼容。

策略日志仍含 `bench_source` / `te` / `industry` 行，便于与结构化字段交叉核对。

---

## 5. 建议验收流程

1. 同步最近交易日数据（见第 1 节）
2. 回测中心：同区间分别跑 1.0 与 2.0
3. 检查 2.0 日志含 `bench_source`、`te`、`industry`
4. 检查回测 JSON 含 `diagnostics.enhancedIndex.last.benchSource` 等键
5. 有官方权重时，`benchFallback` 应为 `false`；若长期为 `true`，检查权重表同步
