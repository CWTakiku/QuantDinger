# 外部 Alpha 分数桥接设计（QuantaAlpha / LightGBM → QuantDinger）

> 日期：2026-08-01  
> 状态：已确认；实现计划见 `docs/superpowers/plans/2026-08-01-external-alpha-score-bridge.md`  
> 范围：方案一 + 信号形态 C（分数）  
> 相关：CSI300 指增 2.0、Strategy API V2、离线研究栈（QuantaAlpha / Qlib / LightGBM）

## 1. 背景与问题

QuantDinger 已具备事件驱动组合回测与 A 股 PIT 面板，但缺少与外部研究工厂（QuantaAlpha 挖因子、LightGBM 打分）之间的稳定契约。若把研究回测与产品回测混在同一引擎，会出现双真相源与执行语义漂移。

用户确认边界：

- **离线研究**：QuantaAlpha（可叠加 LightGBM 等模型）负责因子/打分  
- **产品引擎**：QuantDinger Strategy V2 负责读分数、组合、撮合、净值  
- **信号形态 C**：日频 **score**（不是最终权重、不是纯名单）

## 2. 目标与非目标

### 2.1 目标

1. 新增 Postgres 表存储外部日频 alpha 分数（PIT 可读）。  
2. Strategy V2 沙箱注入只读 API：`get_external_alpha_scores(...)`。  
3. 提供 CSV 导入脚本，使 QuantaAlpha / LightGBM 结果可落库，无需把研究栈装进 API 镜像。  
4. 提供示例策略模板：周频读分数 →（可选）中性化 → Top-N 等权调仓（MVP）。  
5. 固定时点：**`as_of=T` 的分数最早在 T+1 用于调仓**，防止前视。

### 2.2 非目标（本期不做）

- 在 QuantDinger Docker 镜像内安装 QuantaAlpha、Qlib 训练依赖或 LLM 运行时。  
- 用 QuantaAlpha / Qlib 回测替换 Strategy V2。  
- 自动每日调度「挖因子 / 训练模型」（可由外部 cron 调用导入脚本）。  
- 强制研究侧产出目标权重（形态 B）；本期契约以 score 为主。  
- 修改现有 `strategy_v2_csi300_enhanced_v2` 默认行为。

## 3. 架构

```text
[离线] QuantaAlpha / LightGBM / 手工导出
        │  CSV（或后续 JSON 适配器）
        ▼
scripts/import_external_alpha_scores.py
        │  upsert
        ▼
qd_external_alpha_scores
  (as_of, source, version, universe, symbol, score, meta)
        │  get_external_alpha_scores(as_of, source, ...)
        ▼
Strategy API V2（示例模板）
  score → 可选行业/市值中性 → Top-N 等权（MVP）
        │  可后续接 CSI300 指增 QP
        ▼
组合调仓 / 模拟成交 / 净值
```

### 3.1 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| QuantaAlpha / LightGBM | 挖因子、训练、截面打分、导出 CSV | 撮合、持仓账本、产品净值 |
| 导入脚本 | 校验、符号规范化、批量 upsert | 模型训练 |
| `qd_external_alpha_scores` | PIT 分数真相源 | 权重优化 |
| `get_external_alpha_scores` | 策略侧只读、失败封闭 | 写库、补全未来数据 |
| Strategy V2 模板 | 消费分数并调仓 | 研究侧 IC/挖因子 |

### 3.2 分数 vs 权重（契约选择）

| | score（本期） | weight（后续可选） |
|--|---------------|-------------------|
| 含义 | 相对强弱，可正可负 | 资金占比，通常归一 |
| 产出方 | 模型/因子合成 | 组合优化器 |
| QD 用法 | 排序/筛选后再优化 | 可直接目标仓位 |

本期只强制 `score`；表结构可预留 `weight` 可空列以便后续扩展，但 MVP 策略不依赖 weight。

## 4. 数据契约

### 4.1 表 `qd_external_alpha_scores`

| 列 | 类型 | 说明 |
|----|------|------|
| `as_of` | DATE | 信号日期（研究可知信息截止日） |
| `source` | TEXT | 来源，如 `quantaalpha`、`lightgbm_alpha158` |
| `version` | TEXT | 模型/因子库版本或哈希，缺省 `default` |
| `universe` | TEXT | 可选，如 `csi300`；空表示未标注 |
| `symbol` | TEXT | 平台规范键，`CNStock:600519.SH` |
| `score` | DOUBLE | alpha 分数 |
| `weight` | DOUBLE NULL | 预留；本期可空 |
| `meta_json` | JSONB | 可选诊断字段 |
| `ingested_at` | TIMESTAMPTZ | 入库时间 |

唯一约束：`(as_of, source, version, symbol)`。

索引：`(source, version, as_of DESC)`；`(as_of, symbol)`。

### 4.2 CSV 导入格式（MVP）

必填列：

```text
as_of,symbol,score
```

可选列：`source,version,universe,weight`

- `as_of`：`YYYY-MM-DD` 或 `YYYYMMDD`  
- `symbol`：接受 `600519` / `600519.SH` / `CNStock:600519.SH`，入库前规范化  
- 默认 `source=external`，`version=default`（可由 CLI 覆盖）

### 4.3 PIT 读取规则

对请求日 `d`、给定 `source`（及可选 `version`）：

1. 取 `as_of_eff = MAX(as_of) WHERE as_of <= d`（且匹配 source/version）。  
2. 返回该 `as_of_eff` 的全部分数截面（或按 `symbols` 过滤）。  
3. 若无任何 `as_of <= d`：返回空，**禁止**回退到未来日期或全历史最新截面。

滞后由策略参数 `score_lag_days` 在调用前计算读取日，再传入本 API 的 `as_of`；API 本身不做隐式 lag。

## 5. Strategy V2 API

### 5.1 注入函数

```python
get_external_alpha_scores(
    as_of,                 # date | str
    source,                # str, required
    version=None,          # None → 固定字面量 "default"（不自动追最新版本）
    symbols=None,          # optional list[str]
) -> pd.Series  # index=CNStock:..., dtype=float
```

约定：`version=None` 时使用字面量 `"default"`，不自动挑选「最新版本」，以保证回测可复现。

策略模板通过参数 `score_lag_days`（默认 1）把调仓日映射为读取日：`as_of_read = 调仓日 − score_lag_days`（按交易日或自然日在实现计划中选定；推荐交易日）。不在 API 层再增加隐式 `lag` 参数，避免双重滞后。

### 5.2 示例模板（MVP）

- `template_key`：`strategy_v2_external_alpha_score`  
- 行为：周频调仓；读取分数；取 Top-N；等权；`order_target_percent`  
- 参数建议：`source`、`version`、`top_n`、`min_names`、`score_lag_days`（默认 1）  
- 宇宙：可用公开 `csi300` 或模板静态池；分数缺失的成分自然不会进 Top-N  

后续可选：同一分数接入 `optimize_enhanced_index`（指增），另开任务，不阻塞 MVP。

## 6. 错误与失败封闭

| 情况 | 行为 |
|------|------|
| 指定日无分数 | 空 Series；策略跳过调仓并 log |
| 符号无法规范化 | 导入时跳过该行并计数告警 |
| score 非有限数 | 导入拒绝该行 |
| source/version 拼写错误 | 读取为空（不猜测其它 source） |

## 7. 测试计划

1. **单元**：符号规范化；PIT `as_of <= d`；未来数据不可见。  
2. **导入**：CSV round-trip；重复 upsert 覆盖。  
3. **策略**：有分数时产生调仓；无分数时零成交/跳过；`score_lag_days=1` 不用当日未实现收益。  

## 8. 文档与运维

- `docs/` 下简短中文说明：如何从 QuantaAlpha / LightGBM 导出 CSV 并导入。  
- 研究环境与 QD 生产镜像隔离；密钥（LLM API 等）不得写入仓库。  

## 9. 实现分期

| 期 | 交付 |
|----|------|
| P0 | 表 + persist/load + 导入脚本 + 沙箱 API + 单元测试 |
| P1 | 示例策略模板 + 回测可跑通（合成 CSV fixture） |
| P2 | QuantaAlpha JSON 适配器、指增 QP 接线、可选 weight 列消费 |

## 10. 已确认决策

| 项 | 选择 |
|----|------|
| 总体方案 | 方案一：离线研究 + QD 执行 |
| 研究栈 | QuantaAlpha 可接；LightGBM 等同属离线打分 |
| 信号形态 | C：score |
| 时点 | T 分数，T+1 及以后调仓 |
| version 缺省 | 固定 `default`，不自动追新 |
| 与 2.0 指增 | 并存；不修改 2.0 默认逻辑 |
