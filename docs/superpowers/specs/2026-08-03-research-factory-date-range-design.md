# 研究工厂数据源日期范围（自动切分）设计

日期：2026-08-03  
状态：待用户审阅  
范围：`rdagent-workspace` bridge + QuantDinger 后端代理 + QuantDinger-Vue 研究工厂

## 1. 背景与目标

研究工厂已支持选择 Qlib **数据源**（`default` / `quantmind`），但 RD-Agent 因子场景的训练 / 验证 / 回测日期仍写死在安装包模板（约 train `2008–2014`、valid `2015–2016`、test/backtest `2017–2020`）。与 QuantMInd（日历可至 2026）等新数据源错位时，会出现 Empty data 或无法用近期样本。

**目标（方案 A + 任务启动改写 YAML）**：用户选择**整体起止日期**；bridge 按固定比例自动切分三段，写入本轮任务的 conf 覆盖；留空则保持模板默认（兼容现状）。

**非目标**：

- 不在 UI 上分别手填 train / valid / test（方案 B）。
- 不永久修改 `site-packages` 内模板（方案 2）。
- 不改变数据源白名单机制。

## 2. 用户流程

1. 研究工厂选择场景、数据源、`step_n`。
2. 可选填写 **开始日期 / 结束日期**（默认展示数据源 `calendar_start`～`calendar_end`，可改）。
3. 点击启动 → 任务记录展示解析后的 train / valid / test。
4. 留空起止 → 行为与现网一致（模板默认日期）。

## 3. 切分规则

输入：`start_date`、`end_date`（含首尾，格式 `YYYY-MM-DD`）。

约束：

| 规则 | 值 |
|------|-----|
| 落在所选数据源日历内 | `calendar_start ≤ start ≤ end ≤ calendar_end` |
| 最短跨度 | `end - start ≥ 3 年`（按日历日 1095 天） |
| `start < end` | 必须 |

切分比例（按**日历日**线性切，边界落到交易日由 Qlib 自身对齐）：

| 段 | 占比 | 区间 |
|----|------|------|
| train | 60% | `[start, t1)` |
| valid | 15% | `[t1, t2)` |
| test / backtest | 25% | `[t2, end]` |

其中 `span = end - start`（天数），`t1 = start + 0.60*span`，`t2 = start + 0.75*span`，日期取 `date` 截断。

同步写入模板中出现的：

- `segments.train / valid / test`
- `fit_start_time` / `fit_end_time`（= train）
- `backtest.start_time` / `backtest.end_time`（= test）
- 顶层 / handler 的 `start_time` / `end_time`（覆盖为 `[start, end]`，不少于 test 上界）

## 4. 架构

```
Vue 表单 start_date/end_date
  → QD POST /api/rdagent/jobs
    → Bridge POST /v1/jobs { ..., start_date?, end_date? }
      → activate_data_source
      → compute_segments(start, end)
      → prepare_run_overrides(workspace, job_id, segments)  # YAML 覆盖目录
      → 可选 refresh daily_pv.h5 覆盖 [start, end]
      → 启动 rdagent，环境变量指向覆盖目录
```

## 5. Bridge

### 5.1 API

`POST /v1/jobs` body 新增可选字段：

```json
{
  "scenario": "fin_factor",
  "step_n": 8,
  "data_source": "quantmind",
  "start_date": "2018-01-01",
  "end_date": "2024-12-31"
}
```

- 两者都缺省或都为空字符串 → 不改模板日期。
- 只填一个 → `400`，`error` 说明必须成对。
- 校验失败 → `400` + 明确中文/英文错误信息。

任务对象增加：

```json
{
  "start_date": "2018-01-01",
  "end_date": "2024-12-31",
  "segments": {
    "train": ["2018-01-01", "2022-03-14"],
    "valid": ["2022-03-15", "2023-04-01"],
    "test": ["2023-04-02", "2024-12-31"]
  }
}
```

（无自定义日期时 `segments` 为 `null`。）

### 5.2 YAML 覆盖实现

新建 `rdagent_bridge/date_segments.py`：

- `compute_segments(start: date, end: date) -> dict`
- `validate_against_calendar(start, end, calendar_start, calendar_end) -> None`（抛 `ValueError`）
- `apply_segments_to_yaml_file(path: Path, segments, overall_start, overall_end) -> None`

RD-Agent 的 `QlibFactorExperiment` / `QlibFBWorkspace` 从  
`site-packages/rdagent/scenarios/qlib/experiment/factor_template/`  
复制 `conf_*.yaml` 到每个实验 workspace。因此本规格采用：

**单 job 文件锁下的「备份 → 替换 → 还原」**（现网 `JobManager` 已单并发）：

1. 解析 template 目录（`import rdagent.scenarios.qlib.experiment.factor_template` 的 `__path__`，或等价 `Path(qlib.experiment.__file__).parent / "factor_template"`）。
2. 启动前：将目录内全部 `conf_*.yaml` 备份到 `workspace/git_ignore_folder/job_overrides/<job_id>/backup/`。
3. 按切分结果改写原文件中的 `segments`、`fit_*`、`backtest.*`、`start_time`/`end_time`。
4. 子进程结束（成功 / 失败 / 停止）后在 `finally` 中从 backup **完整还原**；还原失败打 error 日志。
5. `fin_quant` 若另用 `model_template` 下 conf，对 `model_template/conf_*.yaml` 同样处理。

禁止永久改模板且不还原。

### 5.3 `daily_pv.h5`

因子编码依赖 `git_ignore_folder/factor_implementation_source_data/daily_pv.h5`。自定义日期时：

- 调用可参数化的生成逻辑（重构 `scripts/generate_daily_pv.py` 为函数：`build_daily_pv(provider_uri, start, end, out_path, max_symbols=300)`）
- 在 job 启动、激活数据源之后、启动 rdagent 之前执行
- 输出覆盖 `factor_implementation_source_data/daily_pv.h5`（debug 面板可按需同步短区间，非必须）
- 失败则 job 启动失败并返回错误（避免 Silent Empty）

无自定义日期时：**不**强制重建 daily_pv（保持现状）。

## 6. QuantDinger 后端

- `RdAgentBridgeClient.start_job(..., start_date=None, end_date=None)` 写入 JSON body。
- `POST /api/rdagent/jobs` 读取 `start_date` / `startDate`、`end_date` / `endDate` 透传。
- 列表/详情任务字段原样返回 `segments`。

## 7. 前端

研究工厂任务表单：

- 数据源变更时，若用户未手动改过日期，填充该源的 `calendar_start` / `calendar_end`。
- 两个 `a-date-picker`（`valueFormat: YYYY-MM-DD`）。
- 提示文案：「留空则使用 RD 模板默认区间；填写后按 60% / 15% / 25% 自动切分训练、验证、回测。」
- 启动 payload 带上字段；展示校验错误（bridge 400 message）。

## 8. 测试

Bridge：

- `compute_segments` 比例与边界单测。
- 校验：跨度不足、越界日历、只填一端 → ValueError。
- YAML 改写：样例 conf 片段改后 `segments.test` 等于预期。
- JobManager：传入日期时 job dict 含 `segments`；mock 子进程时验证备份/还原被调用（可用 tmp template 目录 monkeypatch）。

QD：

- `start_job` body 含日期字段的 client / route 测试。

Vue：手工 / 组件级确保字段绑定（无强制 E2E）。

## 9. 验收

1. 选 `quantmind`，起止 `2018-01-01`～`2024-12-31`，启动后任务 JSON 含正确三段日期。  
2. 运行中 `conf_baseline.yaml`（或等价）的 test/backtest 落在 2023–2024 一带（按公式）。  
3. 进程结束后 site-packages 模板恢复为改写前内容。  
4. 留空日期启动，行为与改前一致。  
5. 跨度 2 年 → 启动被拒。

## 10. 实现单元

| 单元 | 职责 |
|------|------|
| `rdagent_bridge/date_segments.py` | 校验、切分、YAML 改写 |
| `rdagent_bridge/jobs.py` | 接参、daily_pv、备份替换还原 |
| `scripts/generate_daily_pv.py` 或 `rdagent_bridge/daily_pv.py` | 可参数化生成 |
| QD client / routes | 透传 |
| `QuantDinger-Vue/.../rdagent/index.vue` | 日期控件 |
