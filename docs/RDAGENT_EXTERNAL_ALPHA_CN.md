# RD-Agent → QuantDinger 外部 Alpha 分数

研究侧使用 [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent)（`fin_quant` / `fin_factor`）在 **独立环境** 挖因子与训练模型；QuantDinger 只消费日频 `score` CSV。

## 边界

| 侧 | 职责 |
|----|------|
| RD-Agent + Qlib | 假设→实现→回测进化；导出预测分数 |
| QuantDinger | `qd_external_alpha_scores` PIT 读分、Strategy V2 执行 |

**不要**把 RD-Agent 装进 `quantdinger-backend` 镜像。

## 本机位置

- Micromamba 环境：`rdagent`（Python 3.10）
- 工作区：`/Users/taki/quant/rdagent-workspace`
- 导出脚本：`rdagent-workspace/scripts/export_qlib_pred_to_qd_csv.py`
- 通用导入：`backend_api_python/scripts/import_external_alpha_scores.py`
- CSV 契约：见 `docs/EXTERNAL_ALPHA_SCORE_BRIDGE_CN.md`

## 日常命令摘要

```bash
export MAMBA_ROOT_PREFIX=$HOME/miniforge3
cd ~/quant/rdagent-workspace
micromamba run -n rdagent dotenv run -- rdagent fin_quant

# 导出 → 导入 → 回测模板 strategy_v2_external_alpha_score
# source/version 与导入一致
```

依赖钉扎：工作区 `requirements-pin.txt`（`rdagent==0.8.0` + `pydantic-ai==1.107.1`）。
