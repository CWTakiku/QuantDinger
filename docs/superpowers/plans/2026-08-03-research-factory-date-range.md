# 研究工厂数据源日期范围 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究工厂可选手动起止日期；bridge 按 60%/15%/25% 自动切分 train/valid/test，改写本轮 RD-Agent qlib conf，并重建对应区间的 `daily_pv.h5`。

**Architecture:** Vue/QD 透传 `start_date`/`end_date` → `JobManager` 校验并 `compute_segments` → 备份/改写 site-packages 内 `factor_template`（及 `model_template`）`conf_*.yaml` → 生成 daily_pv → 启动子进程 → `finally` 还原模板。

**Tech Stack:** Python 3.10、Flask bridge、PyYAML 或正则改写、qlib `D.features`、Vue 2 + Ant Design Vue、pytest。

**Spec:** `QuantDinger/docs/superpowers/specs/2026-08-03-research-factory-date-range-design.md`

## Global Constraints

- 留空日期 = 不改模板、不重建 daily_pv（兼容现状）。
- 最短跨度 1095 天；须落在数据源 `calendar_start`～`calendar_end`。
- 切分比例固定 60% / 15% / 25%。
- 单并发 job；模板必须备份→替换→还原。
- Commit 中文 Conventional Commits（用户要求提交时）。

## File Map

| File | Responsibility |
|------|----------------|
| `rdagent-workspace/rdagent_bridge/date_segments.py` | 校验、切分、YAML 改写、template 备份还原 |
| `rdagent-workspace/rdagent_bridge/daily_pv.py` | 可参数化生成 daily_pv.h5 |
| `rdagent-workspace/rdagent_bridge/jobs.py` | 接参、串联上述步骤 |
| `rdagent-workspace/rdagent_bridge/app.py` | POST /v1/jobs 解析日期 |
| `rdagent-workspace/rdagent_bridge/tests/test_date_segments.py` | 切分/校验/YAML 单测 |
| `rdagent-workspace/rdagent_bridge/tests/test_jobs_unit.py` | job 带日期字段 |
| QD `client.py` / `routes/rdagent.py` + tests | 透传 |
| `QuantDinger-Vue/src/views/rdagent/index.vue` | 日期控件 |

---

### Task 1: date_segments 核心（切分 + YAML）

**Files:**
- Create: `rdagent-workspace/rdagent_bridge/date_segments.py`
- Create: `rdagent-workspace/rdagent_bridge/tests/test_date_segments.py`

**Interfaces:**
- Produces: `parse_optional_date_pair(start_raw, end_raw) -> tuple[date, date] | None`
- Produces: `validate_against_calendar(start, end, cal_start, cal_end) -> None`
- Produces: `compute_segments(start: date, end: date) -> dict` with keys train/valid/test as `[str, str]`
- Produces: `apply_segments_to_yaml_text(text, segments, overall_start, overall_end) -> str`
- Produces: `TemplatePatcher(template_dirs: list[Path], backup_root: Path)` with `apply(segments,...)` / `restore()`

- [ ] **Step 1: Failing tests**

```python
from datetime import date
import pytest
from rdagent_bridge.date_segments import (
    parse_optional_date_pair,
    validate_against_calendar,
    compute_segments,
    apply_segments_to_yaml_text,
)

def test_parse_both_empty():
    assert parse_optional_date_pair(None, None) is None
    assert parse_optional_date_pair("", "") is None

def test_parse_one_only_raises():
    with pytest.raises(ValueError):
        parse_optional_date_pair("2018-01-01", None)

def test_validate_span_too_short():
    with pytest.raises(ValueError, match="1095|3"):
        validate_against_calendar(
            date(2020, 1, 1), date(2021, 1, 1),
            date(2000, 1, 1), date(2030, 1, 1),
        )

def test_compute_segments_ratios():
    seg = compute_segments(date(2018, 1, 1), date(2024, 12, 31))
    assert seg["train"][0] == "2018-01-01"
    assert seg["test"][1] == "2024-12-31"
    # train end before valid start before test
    assert seg["train"][1] < seg["valid"][0] or seg["train"][1] <= seg["valid"][0]

def test_apply_yaml_segments():
    sample = """
            segments:
                train: [2008-01-01, 2014-12-31]
                valid: [2015-01-01, 2016-12-31]
                test: [2017-01-01, 2020-08-01]
    backtest:
        start_time: 2017-01-01
        end_time: 2020-08-01
"""
    seg = {
        "train": ["2018-01-01", "2022-03-14"],
        "valid": ["2022-03-15", "2023-04-01"],
        "test": ["2023-04-02", "2024-12-31"],
    }
    out = apply_segments_to_yaml_text(sample, seg, "2018-01-01", "2024-12-31")
    assert "2018-01-01" in out and "2024-12-31" in out
    assert "2008-01-01" not in out
```

- [ ] **Step 2: RED** — `pytest rdagent_bridge/tests/test_date_segments.py -q`
- [ ] **Step 3: Implement `date_segments.py`**（含 TemplatePatcher 用 shutil copy2 备份/还原整个 conf 文件列表）
- [ ] **Step 4: GREEN**
- [ ] **Step 5: Commit** `feat: 实现日期切分与conf改写`

---

### Task 2: daily_pv 可参数化生成

**Files:**
- Create: `rdagent-workspace/rdagent_bridge/daily_pv.py`
- Modify: `rdagent-workspace/scripts/generate_daily_pv.py`（改为调用库函数，保留 CLI）
- Test: `rdagent_bridge/tests/test_daily_pv.py`（mock `qlib`/`D.features`，断言调用参数与写路径）

**Interfaces:**
- Produces: `build_daily_pv(provider_uri: str, start: str, end: str, out_path: Path, max_symbols: int = 300) -> Path`

- [ ] **Step 1–4: TDD** mock features 返回小 DataFrame，断言 `to_hdf` 目标为 `git_ignore_folder/factor_implementation_source_data/daily_pv.h5`
- [ ] **Step 5: Commit** `feat: 支持按区间生成daily_pv`

---

### Task 3: JobManager + HTTP 接日期

**Files:**
- Modify: `rdagent_bridge/jobs.py`
- Modify: `rdagent_bridge/app.py`
- Modify: `rdagent_bridge/tests/test_jobs_unit.py`, `test_app_http.py`

**Interfaces:**
- `JobManager.start(..., start_date=None, end_date=None)`
- 使用 `data_sources.list_data_sources` / calendar 校验
- 有日期时：TemplatePatcher.apply → build_daily_pv → Popen；在 `_finalize_job` / `stop` 路径 `restore()`
- 在 job dict 写入 `start_date`/`end_date`/`segments`

实现要点：

```python
# jobs.py start()
pair = parse_optional_date_pair(start_date, end_date)
segments = None
patcher = None
if pair:
    start_d, end_d = pair
    # resolve calendar from activated source
    validate_against_calendar(start_d, end_d, cal_s, cal_e)
    segments = compute_segments(start_d, end_d)
    patcher = TemplatePatcher(template_dirs, backup_root)
    patcher.apply(segments, start_d.isoformat(), end_d.isoformat())
    build_daily_pv(provider_uri, start_d.isoformat(), end_d.isoformat(), out_path)
# store patcher on self._patchers[job_id]
# finalize/stop: self._patchers.pop(job_id).restore()
```

解析 template 目录：

```python
import rdagent.scenarios.qlib.experiment as qexp
base = Path(qexp.__file__).parent
dirs = [base / "factor_template", base / "model_template"]
```

- [ ] **Step 1–4: 单测** monkeypatch TemplatePatcher/build_daily_pv；断言 job 含 segments；finalize 调用 restore
- [ ] **Step 5: Commit** `feat: 任务启动支持自定义日期区间`
- [ ] **Step 6: 重启 bridge**

---

### Task 4: QD 代理透传

**Files:**
- Modify: `QuantDinger/backend_api_python/app/services/rdagent_bridge/client.py`
- Modify: `QuantDinger/backend_api_python/app/routes/rdagent.py`
- Modify/Create tests accordingly

- [ ] `start_job(..., start_date=None, end_date=None)` 写入 body（非空才带字段）
- [ ] route 读 `start_date`/`startDate`、`end_date`/`endDate`
- [ ] 测试断言 POST JSON 含日期
- [ ] Commit `feat: 透传研究任务日期区间`
- [ ] docker cp + restart backend

---

### Task 5: Vue 日期控件 + 部署

**Files:**
- Modify: `QuantDinger-Vue/src/views/rdagent/index.vue`

- [ ] `form.start_date` / `form.end_date`
- [ ] 两个 `a-date-picker` value-format `YYYY-MM-DD`
- [ ] 数据源切换：若 `_datesTouched` 为 false，填入 `calendar_start`/`calendar_end`
- [ ] 用户改日期设 `_datesTouched = true`
- [ ] 提示文案按 spec
- [ ] `handleStart` payload 带上（空字符串则不传或传 null）
- [ ] Commit `feat: 研究工厂支持选择数据日期范围`
- [ ] Rebuild frontend:  
  `FRONTEND_SRC_PATH=/Users/taki/quant/QuantDinger-Vue docker compose -f docker-compose.yml -f docker-compose.build.yml build frontend && ... up -d frontend`

---

## Spec Coverage

| Spec | Task |
|------|------|
| 切分 60/15/25 + 校验 | 1 |
| YAML 备份改写还原 | 1, 3 |
| daily_pv | 2, 3 |
| jobs/HTTP | 3 |
| QD | 4 |
| Vue | 5 |
| 留空兼容 | 3–5 |

## Placeholder Scan

无 TBD；template 路径与还原时机已写明。
