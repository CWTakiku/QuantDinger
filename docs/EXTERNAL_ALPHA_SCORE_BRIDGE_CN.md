# 外部 Alpha 分数桥接 — 运维说明

> 将 QuantaAlpha / LightGBM 等离线研究产出的 **日频 alpha 分数** 导入 QuantDinger，供 Strategy API V2 策略读取并调仓。

## 1. 系统边界

| 环境 | 职责 | 不负责 |
|------|------|--------|
| **离线研究**（**RD-Agent**、QuantaAlpha、Qlib、LightGBM 等） | 挖因子、训练模型、截面打分、导出 CSV | 撮合、持仓账本、产品净值 |
| **QuantDinger** | CSV 校验入库、PIT 读分、组合调仓、回测/模拟/实盘执行 | 模型训练、因子挖掘 |

数据流：

```text
[离线] RD-Agent / QuantaAlpha / LightGBM / 手工导出
        │  CSV
        ▼
import_external_alpha_scores.py  →  qd_external_alpha_scores
        │  get_external_alpha_scores（策略沙箱只读）
        ▼
Strategy V2 模板 strategy_v2_external_alpha_score
        │  Top-N 等权调仓（MVP）
        ▼
组合成交 / 净值
```

**重要：** QuantDinger **不在 Docker 镜像内安装 RD-Agent、QuantaAlpha、Qlib 训练依赖或 LLM 运行时**。研究栈与 QD 生产环境隔离；研究侧 API 密钥不得写入仓库。每日打分可由外部 cron 调用导入脚本完成。

主研究栈推荐：**RD-Agent**（旁路目录 `~/quant/rdagent-workspace`，说明见 `docs/RDAGENT_EXTERNAL_ALPHA_CN.md`）。

## 2. CSV 格式

### 2.1 列定义

| 列 | 必填 | 说明 |
|----|------|------|
| `as_of` | 是 | 信号日期（信息截止日），支持 `YYYY-MM-DD` 或 `YYYYMMDD` |
| `symbol` | 是 | A 股代码，见下方符号规范 |
| `score` | 是 | alpha 分数（有限浮点数，可正可负） |
| `source` | 否 | 来源标识；缺省由 CLI `--source` 填充，默认 `external` |
| `version` | 否 | 模型/因子库版本；缺省由 CLI `--version` 填充，默认 `default` |
| `universe` | 否 | 可选宇宙标签，如 `csi300` |
| `weight` | 否 | 预留列；**本期 MVP 策略不消费**，可留空 |

### 2.2 符号规范

导入时自动规范化，以下写法等价：

- `600519` → 入库为 `CNStock:600519.SH`
- `600519.SH` → `CNStock:600519.SH`
- `CNStock:600519.SH` → 保持不变

无法识别的符号行会被跳过并在导入结果中计数。

### 2.3 示例

**最简（三列）：**

```csv
as_of,symbol,score
2026-07-25,600519,1.25
2026-07-25,000001.SZ,0.85
2026-07-25,600036,0.42
```

**带可选列（行级覆盖 CLI 默认值）：**

```csv
as_of,symbol,score,source,version,universe
2026-07-25,600519,1.25,lightgbm_alpha158,v202607,csi300
2026-07-25,000001.SZ,0.85,lightgbm_alpha158,v202607,csi300
```

**多日截面（同一 `source`/`version` 下按 `as_of` 分行）：**

```csv
as_of,symbol,score
2026-07-18,600519,1.10
2026-07-18,600036,0.55
2026-07-25,600519,1.25
2026-07-25,600036,0.42
```

重复导入同一 `(as_of, source, version, symbol)` 会 **upsert 覆盖** 分数。

## 3. 导入命令

### 3.1 前置：数据库表

首次部署需已执行迁移 `backend_api_python/migrations/20260801_external_alpha_scores.sql`（创建表 `qd_external_alpha_scores`）。按项目惯例通过 migrate 进程或手动 `psql` 应用。

### 3.2 Docker 生产/Compose 环境

将 CSV 拷入容器后执行（容器名以实际 Compose 为准，常见为 `quantdinger-backend`）：

```bash
docker cp scores.csv quantdinger-backend:/tmp/scores.csv

docker exec quantdinger-backend python /app/scripts/import_external_alpha_scores.py \
  --csv /tmp/scores.csv \
  --source external \
  --version default \
  --universe csi300
```

### 3.3 本地开发

```bash
cd backend_api_python
python scripts/import_external_alpha_scores.py \
  --csv /path/to/scores.csv \
  --source external \
  --version default
```

需配置与 API 相同的 PostgreSQL 连接（环境变量 / `.env`）。

### 3.4 输出

脚本打印 JSON 摘要，例如：

```json
{
  "inserted": 300,
  "skipped": 2,
  "errors": ["row15: invalid symbol/score"]
}
```

`skipped` 含符号无法规范化、score 非有限数等行；`errors` 最多返回前 20 条。

## 4. 策略参数

模板键：`strategy_v2_external_alpha_score`  
示例源码：`docs/examples/strategy_v2_external_alpha_score_weekly.py`

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `source` | str | `external` | 须与导入 CSV / CLI `--source` 一致 |
| `version` | str | `default` | 须与导入版本一致；**不自动追最新版本** |
| `top_n` | int | `30` | 取得分 Top-N 等权持有 |
| `min_names` | int | `10` | 有效分数低于此值则跳过当周调仓 |
| `score_lag_days` | int | `1` | 调仓日向前偏移的自然日数，用于计算读分日期 |

策略行为：CSI300 宇宙、**每周一 09:35** 调仓；读取分数 → Top-N 等权 → 平掉不在目标内的持仓。

**`source` / `version` 拼写必须与库中完全一致**，否则 `get_external_alpha_scores` 返回空，策略会跳过调仓。

## 5. 时点与 PIT 规则

### 5.1 T 日分数、T+1 调仓（默认）

业务约定：**`as_of = T` 的分数最早在 T+1 及以后用于调仓**，防止前视。

示例策略默认 `score_lag_days = 1`：

```python
as_of_read = 调仓日.date() - timedelta(days=score_lag_days)
scores = get_external_alpha_scores(as_of_read, source, version=version)
```

若周一调仓，则读取 **上一自然日** 及之前最近一期的分数截面（见下节 PIT）。

### 5.2 PIT 读取（`get_external_alpha_scores`）

对请求日 `d` 与给定 `source`、`version`：

1. 取 `as_of_eff = MAX(as_of) WHERE as_of <= d`（匹配同一 source/version）
2. 返回该 `as_of_eff` 的全部分数
3. 若不存在 `as_of <= d` 的记录：返回空 Series，**不回退到未来日期或全历史最新截面**

API 层 **不再叠加隐式 lag**；滞后仅由策略参数 `score_lag_days` 控制。

### 5.3 当前实现说明

- `score_lag_days` 使用 **自然日**（calendar days），非交易日历精确偏移（P2 增强项）。
- 无分数或有效名不足 `min_names` 时：记录日志并 **跳过调仓**，不会猜测其它 source/version。

## 6. 与指增 2.0 的关系

- 本桥接与 `strategy_v2_csi300_enhanced_v2` **并存**；**不修改** 指增 2.0 默认逻辑。
- MVP 模板为 Top-N 等权；将同一分数接入 `optimize_enhanced_index` 指增 QP 为 **P2**，本期未实施。

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 回测零成交 | 未导入分数或 `source`/`version` 不匹配 | 检查库表 `qd_external_alpha_scores`，对齐参数 |
| 导入 skipped 多 | symbol 格式错误或 score 非有限 | 对照 §2 修正 CSV |
| 某周跳过调仓 | 有效分数 < `min_names` | 扩大导出宇宙或降低 `min_names` |
| 研究机无法连 QD DB | 边界设计如此 | 在研究机导出 CSV，在 QD 侧执行导入 |

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| `backend_api_python/migrations/20260801_external_alpha_scores.sql` | 表结构 |
| `backend_api_python/scripts/import_external_alpha_scores.py` | 导入 CLI |
| `backend_api_python/app/services/external_alpha/store.py` | 持久化与 PIT 加载 |
| `docs/examples/strategy_v2_external_alpha_score_weekly.py` | 示例策略 |
| `docs/superpowers/specs/2026-08-01-external-alpha-score-bridge-design.md` | 设计规格 |
