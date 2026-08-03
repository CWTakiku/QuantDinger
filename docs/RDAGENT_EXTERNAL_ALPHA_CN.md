# RD-Agent → QuantDinger 外部 Alpha 分数

研究侧使用 [Microsoft RD-Agent](https://github.com/microsoft/RD-Agent)（`fin_quant` / `fin_factor`）在 **独立环境** 挖因子与训练模型；QuantDinger 只消费日频 `score` CSV。

## 研究工厂模块（QD 产品入口）

管理员在 Vue **研究工厂** 页（`/rdagent`）通过 QD 后端代理管理本机 RD-Agent，无需把 RD 装进 Docker 镜像。

| 能力 | QD API（admin） | 说明 |
|------|-----------------|------|
| 桥接状态 | `GET /api/rdagent/status` | 页内「已连接 / 未连接」 |
| 启停任务 | `POST /api/rdagent/jobs`、`POST .../stop` | `fin_factor` / `fin_quant`，`step_n` ≤ 20 |
| 日志 / 会话 | `GET .../logs`、`GET /api/rdagent/sessions` | 会话来自工作区 `log/` |
| RD UI | `GET/POST /api/rdagent/ui*` | 外链 Streamlit **19899** |
| 分数导入 | `POST /api/rdagent/import-from-session` | 导出会话 → `qd_external_alpha_scores` |

OpenAPI 契约见 `docs/api/openapi.yaml`（RDAgent 标签，含 `import-from-session`）。

### 启动 Bridge（19901）

Bridge 运行在 **宿主机**，不在 `quantdinger-backend` 容器内。详细接口与鉴权见工作区文档：

**`~/quant/rdagent-workspace/docs/BRIDGE_CN.md`**

```bash
cd ~/quant/rdagent-workspace
cp .env.example .env   # 设置 RDAGENT_BRIDGE_TOKEN
chmod +x scripts/run_rdagent_bridge.sh
./scripts/run_rdagent_bridge.sh
# 健康检查: curl -s http://127.0.0.1:19901/health
```

### QD 侧环境变量

在 `backend_api_python/.env`（或 Docker 挂载的同一文件）配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `RDAGENT_BRIDGE_URL` | Bridge 根 URL | 本机 backend：`http://127.0.0.1:19901`；容器内：`http://host.docker.internal:19901` |
| `RDAGENT_BRIDGE_TOKEN` | 与 bridge `.env` 相同 | 必填 |
| `RDAGENT_BRIDGE_TIMEOUT_S` | 代理超时（秒） | 默认 `30` |

Docker Compose 联调时，backend 服务需能解析 `host.docker.internal`（Linux 可在 `docker-compose.yml` 的 backend 下加 `extra_hosts: ["host.docker.internal:host-gateway"]`）。Bridge 与 LLM 密钥 **不** 进入 QD 镜像或 git。

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
export PATH="$HOME/quant/rdagent-workspace/bin:$PATH"   # conda→micromamba shim
cd ~/quant/rdagent-workspace
set -a; source .env; set +a
micromamba run -n rdagent rdagent fin_factor --step-n 1   # 或 fin_quant

# 导出 → 导入 → 回测模板 strategy_v2_external_alpha_score
# source/version 与导入一致
```

Colima 已配 Docker Hub 镜像加速（daoCloud / 1ms / xuanyuan）。挖因子默认 `MODEL_CoSTEER_env_type=conda`（环境 `rdagent4qlib`），避免拉 CUDA 镜像。

依赖钉扎：工作区 `requirements-pin.txt`（`rdagent==0.8.0` + `pydantic-ai==1.107.1` + `pyqlib`）。
