# CSI300 Enhanced Index QP 2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建与 1.0 并存的沪深300指增 2.0：PIT 官方权重基准、行业/市值中性 Alpha、行业+市值+TE 约束 QP、多层因子（含 ICIR/regime），分 C1→C2→C3 交付。

**Architecture:** 扩展 `app/services/csi300_enhanced/`（bench 读取、sync 落库、optimizer 约束、layered alpha）；日终同步写本地表；Strategy V2 新模板 `strategy_v2_csi300_enhanced_v2` 只读本地截面并调用扩展后的 `optimize_enhanced_index`；1.0 默认参数路径行为不变。

**Tech Stack:** Python 3.12、numpy、pandas、PostgreSQL、Tushare、Strategy API V2、pytest

**Spec:** `docs/superpowers/specs/2026-08-01-csi300-enhanced-qp-v2-design.md`

## Global Constraints

- 宇宙 `csi300`，基准 `CNStock:000300.SH`，`long_only`
- **1.0 行为冻结**：未传新参数时 `optimize_enhanced_index` 结果与现网一致
- 策略沙箱禁止在热路径直接打 Tushare；只读平台注入 API / 本地表
- QP 继续 numpy 对角风险 + 投影（不用 cvxpy）
- 策略源码标识符英文；计划/设计中文
- Commit message：中文 Conventional Commits（`feat:` / `fix:` / `test:` / `docs:`）

## File Map

| 文件 | 职责 |
|------|------|
| `app/services/csi300_enhanced/bench.py` | PIT 官方权重读取与降级 |
| `app/services/csi300_enhanced/tushare_sync.py` | 拉取并 **写入** 权重 / daily_basic / 行业 |
| `app/services/csi300_enhanced/layered_alpha.py` | 分层合成、ICIR、regime（C2） |
| `app/services/csi300_enhanced/optimizer.py` | size 带宽 + TE 硬约束 + 降级 status |
| `app/services/csi300_enhanced/__init__.py` | 导出公共 API |
| `migrations/20260801_csi300_enhanced_v2_store.sql` | daily_basic / industry 表 |
| `scripts/sync_csi300_enhanced_data.py` | 手动/日终同步入口 |
| `docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py` | 2.0 示例策略 |
| `migrations/strategy_v2_templates.sql` | 种子 `strategy_v2_csi300_enhanced_v2` |

---

## Phase C1 — 可回测骨架

### Task 1: PIT 基准权重读取

**Files:**
- Create: `backend_api_python/app/services/csi300_enhanced/bench.py`
- Modify: `backend_api_python/app/services/csi300_enhanced/__init__.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_bench.py`

**Interfaces:**
- Produces: `get_csi300_bench_weights(as_of: date | str, *, symbols: list[str] | None = None) -> dict[str, float]`
- Produces: 返回值权重之和为 1.0（支撑集非空时）；附带可选 meta 不进返回值，降级通过 logger.warning

- [ ] **Step 1: Write the failing test**

```python
# tests/test_csi300_enhanced_bench.py
from datetime import date
from unittest.mock import patch

from app.services.csi300_enhanced.bench import get_csi300_bench_weights


def test_bench_weights_from_index_table_normalized():
    rows = [
        {"con_code": "600519.SH", "weight": 3.0},
        {"con_code": "000001.SZ", "weight": 1.0},
    ]
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=rows):
        out = get_csi300_bench_weights(date(2026, 7, 31))
    assert abs(sum(out.values()) - 1.0) < 1e-9
    assert out["CNStock:600519.SH"] == 0.75
    assert out["CNStock:000001.SZ"] == 0.25


def test_bench_weights_fallback_equal_when_empty():
    with patch("app.services.csi300_enhanced.bench._load_index_weights", return_value=[]), \
         patch("app.services.csi300_enhanced.bench._load_universe_member_weights", return_value={}):
        out = get_csi300_bench_weights(date(2026, 7, 31), symbols=["CNStock:A", "CNStock:B"])
    assert out == {"CNStock:A": 0.5, "CNStock:B": 0.5}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend_api_python && python -m pytest tests/test_csi300_enhanced_bench.py -v`  
Expected: FAIL import or missing function

- [ ] **Step 3: Implement `bench.py`**

```python
"""Point-in-time CSI300 benchmark weights with explicit fallbacks."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.services.csi300_enhanced.tushare_sync import weights_to_platform_map
import pandas as pd

logger = get_logger(__name__)


def _as_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _load_index_weights(as_of: date) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT con_code, weight
            FROM qd_csi300_index_weights
            WHERE trade_date = (
              SELECT MAX(trade_date) FROM qd_csi300_index_weights WHERE trade_date <= %s
            )
            """,
            (as_of,),
        )
        return list(cur.fetchall() or [])


def _load_universe_member_weights() -> dict[str, float]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT m.market, m.symbol, m.member_weight
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = 'csi300' AND m.valid_to IS NULL
              AND m.member_weight IS NOT NULL AND m.member_weight > 0
            """
        )
        out: dict[str, float] = {}
        for row in cur.fetchall() or []:
            key = f"{row['market']}:{row['symbol']}"
            # normalize later
            out[key] = float(row["member_weight"])
        return out


def get_csi300_bench_weights(
    as_of: date | str,
    *,
    symbols: list[str] | None = None,
) -> dict[str, float]:
    as_of_d = _as_date(as_of)
    rows = _load_index_weights(as_of_d)
    if rows:
        frame = pd.DataFrame(rows)
        mapped = weights_to_platform_map(frame.assign(trade_date=as_of_d.isoformat()))
        if mapped:
            if symbols:
                mapped = {k: mapped.get(k, 0.0) for k in symbols}
                total = sum(mapped.values())
                if total > 0:
                    return {k: v / total for k, v in mapped.items()}
            else:
                return mapped
    uni = _load_universe_member_weights()
    if uni:
        logger.warning("csi300 bench fallback=universe_member_weight as_of=%s", as_of_d)
        if symbols:
            uni = {k: uni.get(k, 0.0) for k in symbols}
        total = sum(uni.values())
        if total > 0:
            return {k: v / total for k, v in uni.items()}
    support = list(symbols or [])
    if not support:
        logger.warning("csi300 bench fallback=empty as_of=%s", as_of_d)
        return {}
    logger.warning("csi300 bench fallback=equal_weight n=%s as_of=%s", len(support), as_of_d)
    w = 1.0 / len(support)
    return {s: w for s in support}
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd backend_api_python && python -m pytest tests/test_csi300_enhanced_bench.py -v`

- [ ] **Step 5: Export from `__init__.py` and commit**

```bash
git add backend_api_python/app/services/csi300_enhanced/bench.py \
  backend_api_python/app/services/csi300_enhanced/__init__.py \
  backend_api_python/tests/test_csi300_enhanced_bench.py
git commit -m "$(cat <<'EOF'
feat: 新增沪深300基准权重PIT读取与降级

EOF
)"
```

---

### Task 2: 同步落库 — index_weight + daily_basic + industry

**Files:**
- Create: `backend_api_python/migrations/20260801_csi300_enhanced_v2_store.sql`
- Modify: `backend_api_python/app/services/csi300_enhanced/tushare_sync.py`
- Create: `backend_api_python/scripts/sync_csi300_enhanced_data.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_tushare_persist.py`

**Interfaces:**
- Produces: `persist_index_weights(frame) -> int`
- Produces: `fetch_and_persist_index_weights(*, trade_date: str | None = None) -> int`
- Produces: `fetch_and_persist_daily_basic(*, trade_date: str) -> int`
- Produces: `fetch_and_persist_industry_map() -> int`

- [ ] **Step 1: Migration SQL**

```sql
-- migrations/20260801_csi300_enhanced_v2_store.sql
CREATE TABLE IF NOT EXISTS qd_ashare_daily_basic (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    ts_code VARCHAR(32) NOT NULL,
    close DOUBLE PRECISION,
    pe_ttm DOUBLE PRECISION,
    pb DOUBLE PRECISION,
    ps_ttm DOUBLE PRECISION,
    dv_ttm DOUBLE PRECISION,
    total_mv DOUBLE PRECISION,
    circ_mv DOUBLE PRECISION,
    turnover_rate DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, ts_code, source)
);
CREATE INDEX IF NOT EXISTS idx_ashare_daily_basic_date ON qd_ashare_daily_basic (trade_date);

CREATE TABLE IF NOT EXISTS qd_ashare_industry_map (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(32) NOT NULL,
    industry VARCHAR(64) NOT NULL,
    industry_src VARCHAR(40) NOT NULL DEFAULT 'tushare_stock_basic',
    as_of DATE NOT NULL,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ts_code, industry_src, as_of, source)
);
```

Also mirror into `init.sql` if that is the project convention for fresh installs (grep existing `qd_csi300_index_weights` insertion point).

- [ ] **Step 2: Failing persist test**

```python
def test_persist_index_weights_upsert(monkeypatch):
    import pandas as pd
    from app.services.csi300_enhanced import tushare_sync as sync

    frame = pd.DataFrame([
        {"trade_date": "20260731", "con_code": "600519.SH", "weight": 4.2},
        {"trade_date": "20260731", "con_code": "000001.SZ", "weight": 1.1},
    ])
    n = sync.persist_index_weights(frame)
    assert n == 2
    # second call idempotent
    assert sync.persist_index_weights(frame) == 2
```

- [ ] **Step 3: Implement persist + fetch wrappers**

在 `tushare_sync.py` 增加：

```python
def persist_index_weights(frame: pd.DataFrame) -> int:
    df = normalize_index_weight_frame(frame)
    if df.empty:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT INTO qd_csi300_index_weights (trade_date, con_code, weight, source)
                VALUES (%s::date, %s, %s, 'tushare')
                ON CONFLICT (trade_date, con_code, source)
                DO UPDATE SET weight = EXCLUDED.weight, ingested_at = NOW()
                """,
                (str(row["trade_date"]), str(row["con_code"]), float(row["weight"])),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_index_weights(*, trade_date: str | None = None) -> int:
    return persist_index_weights(fetch_index_weights(trade_date=trade_date))
```

对 `daily_basic`：用 `pro.daily_basic(trade_date=...)` 写入 `qd_ashare_daily_basic`。  
对行业：`pro.stock_basic(list_status='L', fields='ts_code,industry')` 写入 `qd_ashare_industry_map`（`as_of=today`）。

- [ ] **Step 4: CLI script**

```python
# scripts/sync_csi300_enhanced_data.py
# argparse: --trade-date YYYYMMDD --skip-industry
# calls fetch_and_persist_* and prints JSON counts
```

- [ ] **Step 5: pytest + 手工 dry sync（有 Token 时）**

```bash
cd backend_api_python && python -m pytest tests/test_csi300_enhanced_tushare_persist.py tests/test_csi300_enhanced_tushare_sync.py -v
# optional:
python scripts/sync_csi300_enhanced_data.py --trade-date 20260731
```

- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 落库沪深300权重与日频基本面行业同步

EOF
)"
```

---

### Task 3: 优化器 — 市值带宽 + TE 硬约束（1.0 兼容）

**Files:**
- Modify: `backend_api_python/app/services/csi300_enhanced/optimizer.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_optimizer.py`（扩展）
- Test: `backend_api_python/tests/test_csi300_enhanced_optimizer_v2_constraints.py`

**Interfaces:**
- Extends: `optimize_enhanced_index(..., size_z=None, size_limit: float | None = None, te_limit: float | None = None) -> dict`
- Produces status values: `optimal` | `degraded_te` | `degraded_size` | `degraded_industry` | `empty`
- When `size_z`/`te_limit` 均为默认 `None`，行为与现有单测逐字一致

- [ ] **Step 1: Regression guard — run existing optimizer tests first**

Run: `python -m pytest tests/test_csi300_enhanced_optimizer.py -v`  
Expected: PASS（改前基线）

- [ ] **Step 2: Write failing TE / size tests**

```python
def test_te_limit_shrinks_active_risk():
    alpha = {f"S{i}": float(i) for i in range(10)}
    w_b = {f"S{i}": 0.1 for i in range(10)}
    idio = {f"S{i}": 0.04 for i in range(10)}  # var
    loose = optimize_enhanced_index(alpha, w_b, idio_var=idio, risk_aversion=0.05, turn_penalty=0.0)
    tight = optimize_enhanced_index(
        alpha, w_b, idio_var=idio, risk_aversion=0.05, turn_penalty=0.0, te_limit=0.01
    )
    assert tight["active_risk_proxy"] <= loose["active_risk_proxy"] + 1e-9
    assert tight["active_risk_proxy"] <= 0.01 + 1e-6


def test_size_limit_bounds_exposure():
    alpha = {"A": 2.0, "B": -2.0, "C": 0.0}
    w_b = {"A": 1/3, "B": 1/3, "C": 1/3}
    size_z = {"A": 2.0, "B": -2.0, "C": 0.0}
    result = optimize_enhanced_index(
        alpha, w_b, size_z=size_z, size_limit=0.1, turn_penalty=0.0, risk_aversion=0.1
    )
    w = result["weights"]
    exposure = sum(w[k] * size_z[k] for k in w) - sum(w_b[k] * size_z[k] for k in w_b)
    assert abs(exposure) <= 0.1 + 1e-6
```

- [ ] **Step 3: Implement projection helpers**

在 `optimizer.py`：

1. 求解现有 active 后，若 `te_limit` 有值且 `active_risk_proxy > te_limit`：对 `(w-w_b)` 乘以 `te_limit / proxy`，再重新 box+simplex（及行业）投影，`status=degraded_te`。  
2. 若传入 `size_z` 与 `size_limit`：计算主动 size exposure，超限则对高/低 size 侧权重做成比例收缩（类似 `_project_industry`），必要时 `status=degraded_size`。  
3. `annualization`：`active_risk_proxy` 已是 √(aᵀ D a)；将 `te_limit` 定义为同一单位（文档写明：与现 proxy 同尺度，默认按年化波动理解，idio_var 传入年化方差）。

- [ ] **Step 4: Run old + new tests**

```bash
python -m pytest tests/test_csi300_enhanced_optimizer.py \
  tests/test_csi300_enhanced_optimizer_v2_constraints.py -v
```

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 指增优化器支持市值带宽与跟踪误差约束

EOF
)"
```

---

### Task 4: 中性化 Alpha 组装（C1 层：动量+低波+估值）

**Files:**
- Create: `backend_api_python/app/services/csi300_enhanced/layered_alpha.py`
- Modify: `backend_api_python/app/services/csi300_enhanced/__init__.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_layered_alpha.py`

**Interfaces:**
- Produces: `load_industry_and_size(symbols, as_of) -> tuple[dict[str,str], pd.Series]`
- Produces: `build_neutralized_factor(raw: pd.Series, industry, log_mcap) -> pd.Series`
- Produces: `combine_layers(layer_scores: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series`  
  缺失层权重再分配到剩余层

- [ ] **Step 1: Failing tests for reweight + neutralize wire-up**

```python
def test_combine_layers_renormalizes_missing():
    from app.services.csi300_enhanced.layered_alpha import combine_layers
    import pandas as pd
    mom = pd.Series({"A": 1.0, "B": -1.0})
    out = combine_layers(
        {"momentum": mom, "flow": pd.Series(dtype=float)},
        {"momentum": 0.2, "flow": 0.25, "value_quality": 0.2, "risk_liq": 0.15, "consensus": 0.2},
    )
    assert set(out.index) == {"A", "B"}
    assert abs(float(out.mean())) < 1e-9  # demeaned z
```

- [ ] **Step 2: Implement helpers using existing preprocess**

```python
def build_neutralized_factor(raw, industry, log_mcap):
    from app.services.csi300_enhanced.preprocess import (
        winsorize_mad, cross_section_zscore, neutralize_industry_size,
    )
    s = cross_section_zscore(winsorize_mad(raw))
    return neutralize_industry_size(s, industry, log_mcap)
```

`load_industry_and_size`：读 `qd_ashare_industry_map` 最新、`qd_ashare_daily_basic.circ_mv` → `log1p(circ_mv)`。

- [ ] **Step 3: pytest PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 新增指增分层中性化Alpha组装

EOF
)"
```

---

### Task 5: Runtime 注入只读 helper（供 2.0 策略）

**Files:**
- Modify: `backend_api_python/app/services/strategy_v2/runtime.py`（globals 注入）
- Modify: `backend_api_python/app/services/strategy_v2/contract.py`（白名单）
- Test: `backend_api_python/tests/test_csi300_enhanced_runtime_api.py`

**Interfaces:**
- Inject: `get_csi300_bench_weights`, `get_ashare_industry_map`, `get_ashare_size_log_mcap`  
  （薄封装，内部转调 `bench` / DB；日期默认用回测当前日 `context.current_dt.date()` 由策略传入 as_of 字符串）

策略侧签名保持纯函数，避免把 Flask 依赖打进沙箱：

```python
# allowed in contract.py API names
"get_csi300_bench_weights",
"get_ashare_industry_map",
"get_ashare_size_log_mcap",
```

- [ ] **Step 1: 扩展 runtime 注入与契约测试**
- [ ] **Step 2: 确认 1.0 示例策略仍 `compile_strategy_v2` 通过**
- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 向策略运行时注入指增基准与行业市值读取

EOF
)"
```

---

### Task 6: 策略 2.0 示例 + 模板种子

**Files:**
- Create: `docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py`
- Modify: `backend_api_python/migrations/strategy_v2_templates.sql`（追加 `strategy_v2_csi300_enhanced_v2`，勿改 1.0 行）
- Test: `backend_api_python/tests/test_strategy_v2_template_seed.py`（断言新 key 存在且 1.0 仍在）
- Test: compile smoke in `test_csi300_enhanced_runtime_api.py`

**Behavior (C1):**
- `set_universe(pool="csi300")`, benchmark `000300.SH`
- Daily: momentum/vol + EP/BP from `get_fundamentals` or size panel；`neutralize` via injected maps；`combine_layers`
- Weekly: `w_bench = get_csi300_bench_weights(as_of, symbols=...)`；`optimize_enhanced_index(..., industry=..., size_z=..., te_limit=..., idio_var=...)`
- Params: `industry_limit`, `size_limit`, `te_limit`, layer weights, plus 1.0 knobs

- [ ] **Step 1: Write example strategy file**（完整可编译源码，英文 docstring 首行名为 `CSI300 Enhanced Index QP 2.0`）
- [ ] **Step 2: Seed template SQL** copy-from example
- [ ] **Step 3: Tests**

```python
assert "strategy_v2_csi300_enhanced_v2" in by_key
assert by_key["strategy_v2_csi300_enhanced"]["name"]  # 1.0 still present
```

- [ ] **Step 4: Compile example**

```bash
python -c "from pathlib import Path; from app.services.strategy_v2 import compile_strategy_v2; \
code=Path('docs/examples/strategy_v2_csi300_enhanced_v2_weekly.py').read_text(); \
compile_strategy_v2(code); print('ok')"
```

（工作目录按仓库实际：`backend_api_python` 与 `docs/` 相对路径以现有 1.0 测试为准。）

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: 新增沪深300指增QP2.0策略模板

EOF
)"
```

- [ ] **Step 6: C1 手工验收**

1. `python scripts/sync_csi300_enhanced_data.py --trade-date <最近交易日>`  
2. 刷新/确认 `csi300` 宇宙成员  
3. 回测中心分别跑 1.0 与 2.0 同区间，确认 2.0 日志含 `bench_source`/`industry`/`te`  

---

## Phase C2 — 另类层 / ICIR / regime

### Task 7: 北向/融资 + 一致预期同步

**Files:**
- Modify: `tushare_sync.py`（写入已有 `qd_ashare_flow_daily` / `qd_ashare_consensus_daily`）
- Modify: `scripts/sync_csi300_enhanced_data.py`
- Test: persist mocks

- [ ] 实现 `fetch_and_persist_flow_daily` / `fetch_and_persist_consensus_daily`
- [ ] 权限失败返回 0 并 warning，不抛
- [ ] Commit: `feat: 同步北向资金流与一致预期截面`

### Task 8: ICIR 加权 + regime

**Files:**
- Modify: `layered_alpha.py`
- Test: `tests/test_csi300_enhanced_icir_regime.py`

**Interfaces:**
- `apply_icir_weights(factor_panel, forward_returns, *, window: int) -> dict[str, float]`
- `apply_regime(layer_weights, *, bench_ret_20: float, threshold: float, mom_scale: float = 0.0) -> dict[str, float]`

- [ ] 单测：样本不足回退等权；regime 触发后 momentum 权重为 0 且其余层归一
- [ ] 策略 2.0 增加 `# @param use_icir` / `regime_enabled`
- [ ] Commit: `feat: 指增Alpha支持ICIR加权与体制过滤`

### Task 9: 将 flow/consensus 接入 2.0 策略层

- [ ] `update_alpha` 读取本地 flow/consensus 列；缺失则 `combine_layers` 自动再分配
- [ ] 回测冒烟 + commit: `feat: 2.0策略接入资金流与一致预期层`

---

## Phase C3 — TE 强化 / 日频局部 QP / 诊断

### Task 10: 日频局部再平衡

**Files:**
- Modify: `optimizer.py` 增加 `optimize_enhanced_index_partial(alpha, w_bench, w_prev, frozen_symbols, **kwargs)`
- Modify: 2.0 策略 `run_daily` 监控分支
- Test: partial freeze keeps non-touch weights within 1e-6

- [ ] Commit: `feat: 指增支持日频局部再平衡`

### Task 11: 回测诊断字段

**Files:**
- Modify: strategy_v2 backtest result enrichment（行业主动偏离、size exposure、te_exante、bench_fallback）
- Test: result dict keys present for v2 runs

- [ ] Commit: `feat: 回测结果输出指增约束诊断字段`
- [ ] 更新 `docs/trading/ASHARE_FACTOR_SIGNAL_CN.md` 或新增 `CSI300_ENHANCED_QP_V2_CN.md` 操作说明

---

## Spec Coverage Checklist

| 规格项 | Task |
|--------|------|
| PIT 官方权重 + 降级 | Task 1–2, 6 |
| daily_basic / 行业落库 | Task 2 |
| 行业+市值中性 Alpha | Task 4, 6 |
| 行业/市值/TE 约束 QP | Task 3, 6 |
| 多层因子含 flow/consensus | Task 7, 9 |
| ICIR + regime | Task 8 |
| 日频局部 QP | Task 10 |
| 1.0 并存不变 | Task 3 回归、Task 6 种子 |
| 热路径不打 Tushare | Task 2 sync / Task 5 只读 API |
| 回测对比诊断 | Task 11 |

---

## 执行说明

完成 C1（Task 1–6）即达到「2.0 可回测对比 1.0」；C2/C3 按序继续。每 Task 结束必须绿测并 commit。
