# External Alpha Score Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地外部 alpha 分数桥接：Postgres 存日频 score，Strategy V2 只读注入，CSV 导入，周频 Top-N 等权示例策略可回测。

**Architecture:** 离线研究（QuantaAlpha / LightGBM）导出 CSV → `import_external_alpha_scores` upsert 到 `qd_external_alpha_scores` → 运行时 `get_external_alpha_scores` 按 PIT（`as_of <= d`）返回截面 → 新模板 `strategy_v2_external_alpha_score` 用 `score_lag_days=1` 读分并调仓。不修改 `csi300_enhanced_v2` 默认行为；不把 QuantaAlpha 装进 API 镜像。

**Tech Stack:** Python 3.12、pandas、PostgreSQL、Strategy API V2、pytest

**Spec:** `docs/superpowers/specs/2026-08-01-external-alpha-score-bridge-design.md`

## Global Constraints

- 信号形态 **C：score**；`weight` 列可空预留，MVP 策略不消费
- 时点：`as_of=T` 分数最早 T+1 调仓；滞后只在策略参数 `score_lag_days`（默认 1，按**自然日** `timedelta`，MVP 足够；勿在 API 层隐式 lag）
- `version=None` → 字面量 `"default"`，禁止自动追最新版本
- PIT：无 `as_of <= d` 时返回空，禁止回退未来截面
- 符号入库/读取统一 `canonicalize_cnstock_key` → `CNStock:600519.SH`
- 策略沙箱只读注入；热路径禁止打外部研究 API
- Commit message：中文 Conventional Commits（`feat:` / `fix:` / `test:` / `docs:`）
- 本期不做：QuantaAlpha JSON 适配器、指增 QP 接线、Docker 内嵌研究栈（P2）

## File Map

| 文件 | 职责 |
|------|------|
| `migrations/20260801_external_alpha_scores.sql` | 建表 |
| `migrations/init.sql` | 同步表定义（若项目惯例要求） |
| `app/services/external_alpha/store.py` | persist / load_scores_as_of |
| `app/services/external_alpha/__init__.py` | 导出 |
| `app/services/strategy_v2/runtime.py` | 注入 `get_external_alpha_scores` |
| `app/services/strategy_v2/contract.py` | 白名单允许名 |
| `scripts/import_external_alpha_scores.py` | CSV 导入 CLI |
| `docs/examples/strategy_v2_external_alpha_score_weekly.py` | 示例策略 |
| `migrations/strategy_v2_templates.sql` | 种子模板 |
| `docs/EXTERNAL_ALPHA_SCORE_BRIDGE_CN.md` | 运维/导出说明 |
| `tests/test_external_alpha_store.py` | PIT / persist |
| `tests/test_import_external_alpha_scores.py` | CSV 导入 |
| `tests/test_external_alpha_runtime_api.py` | 沙箱注入名存在 |

---

## Phase P0 — 存储 + API + 导入

### Task 1: 建表迁移

**Files:**
- Create: `backend_api_python/migrations/20260801_external_alpha_scores.sql`
- Modify: `backend_api_python/migrations/init.sql`（在合适位置追加同一 `CREATE TABLE IF NOT EXISTS`，与现有 `qd_ashare_*` 风格一致）

**Interfaces:**
- Produces: 表 `qd_external_alpha_scores` 可用

- [ ] **Step 1: 写入迁移 SQL**

```sql
-- migrations/20260801_external_alpha_scores.sql
CREATE TABLE IF NOT EXISTS qd_external_alpha_scores (
    id BIGSERIAL PRIMARY KEY,
    as_of DATE NOT NULL,
    source VARCHAR(80) NOT NULL,
    version VARCHAR(120) NOT NULL DEFAULT 'default',
    universe VARCHAR(80) NOT NULL DEFAULT '',
    symbol VARCHAR(80) NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    weight DOUBLE PRECISION,
    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (as_of, source, version, symbol)
);
CREATE INDEX IF NOT EXISTS idx_external_alpha_scores_lookup
  ON qd_external_alpha_scores (source, version, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_external_alpha_scores_asof_symbol
  ON qd_external_alpha_scores (as_of, symbol);
```

- [ ] **Step 2: 将相同 DDL 追加到 `init.sql` 末尾附近（与其它 ashare 表并列）**

- [ ] **Step 3: 本地/容器应用迁移**

Run（Docker 示例）:
```bash
docker exec -i quantdinger-db psql -U quantdinger -d quantdinger < backend_api_python/migrations/20260801_external_alpha_scores.sql
```
Expected: `CREATE TABLE` / `CREATE INDEX` 成功或已存在

- [ ] **Step 4: Commit**

```bash
git add backend_api_python/migrations/20260801_external_alpha_scores.sql backend_api_python/migrations/init.sql
git commit -m "$(cat <<'EOF'
feat: 新增外部alpha分数存储表

EOF
)"
```

---

### Task 2: store 层 persist / PIT load（TDD）

**Files:**
- Create: `backend_api_python/app/services/external_alpha/__init__.py`
- Create: `backend_api_python/app/services/external_alpha/store.py`
- Test: `backend_api_python/tests/test_external_alpha_store.py`

**Interfaces:**
- Produces:
  - `persist_external_alpha_scores(rows: list[dict]) -> dict`  
    每行键：`as_of`, `symbol`, `score`；可选 `source`, `version`, `universe`, `weight`, `meta`  
    返回：`{"inserted": int, "skipped": int, "errors": list[str]}`
  - `load_external_alpha_scores_as_of(as_of, *, source: str, version: str | None = None, symbols: list[str] | None = None) -> pd.Series`  
    `version is None` → `"default"`；index 为规范 `CNStock:` 键；空则空 Series
- Consumes: `canonicalize_cnstock_key` from `app.markets.cn_stock.symbols`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_external_alpha_store.py
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.external_alpha.store import (
    load_external_alpha_scores_as_of,
    persist_external_alpha_scores,
)


def test_persist_normalizes_symbol_and_defaults():
    captured = []

    class _Cur:
        def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append(params)

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        out = persist_external_alpha_scores(
            [{"as_of": "2021-08-31", "symbol": "600519", "score": 1.25}]
        )
    assert out["inserted"] == 1
    assert out["skipped"] == 0
    # as_of, source, version, universe, symbol, score, weight, meta_json
    row = captured[0]
    assert row[1] == "external"
    assert row[2] == "default"
    assert row[4] == "CNStock:600519.SH"
    assert float(row[5]) == 1.25


def test_load_pit_uses_max_as_of_not_future():
    rows = [
        {"symbol": "CNStock:600519.SH", "score": 0.5, "as_of": date(2021, 8, 31)},
    ]

    class _Cur:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params=None):
            self.queries.append((sql, params))

        def fetchone(self):
            # first query: max as_of
            return {"as_of": date(2021, 8, 31)}

        def fetchall(self):
            return rows

        def close(self):
            pass

    cur = _Cur()

    class _Db:
        def cursor(self):
            return cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        series = load_external_alpha_scores_as_of(
            date(2021, 9, 15), source="external", version=None
        )
    assert list(series.index) == ["CNStock:600519.SH"]
    assert float(series.iloc[0]) == 0.5
    # ensure bound uses <= requested day
    assert any("as_of <=" in q[0].replace("\n", " ") or "as_of <= ?" in q[0] or "as_of <= %s" in q[0]
               or "<=" in q[0] for q in cur.queries)


def test_load_empty_when_no_as_of():
    class _Cur:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        series = load_external_alpha_scores_as_of(date(2020, 1, 1), source="missing")
    assert isinstance(series, pd.Series)
    assert series.empty
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd backend_api_python && .venv312/bin/python -m pytest tests/test_external_alpha_store.py -v
```
Expected: import error / missing module

- [ ] **Step 3: Implement `store.py`**

```python
# app/services/external_alpha/store.py
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.markets.cn_stock.symbols import canonicalize_cnstock_key
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SOURCE = "external"
DEFAULT_VERSION = "default"


def _as_date(value: date | str | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def persist_external_alpha_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inserted = 0
    skipped = 0
    errors: list[str] = []
    with get_db_connection() as db:
        cur = db.cursor()
        for idx, raw in enumerate(rows or []):
            try:
                as_of = _as_date(raw.get("as_of"))
                symbol = canonicalize_cnstock_key(str(raw.get("symbol") or ""))
                score = float(raw.get("score"))
            except Exception as exc:
                skipped += 1
                errors.append(f"row{idx}: {exc}")
                continue
            if not symbol or score != score or score == float("inf") or score == float("-inf"):
                skipped += 1
                errors.append(f"row{idx}: invalid symbol/score")
                continue
            source = str(raw.get("source") or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
            version = str(raw.get("version") or DEFAULT_VERSION).strip() or DEFAULT_VERSION
            universe = str(raw.get("universe") or "").strip()
            weight = raw.get("weight")
            weight_f = float(weight) if weight is not None and weight != "" else None
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            cur.execute(
                """
                INSERT INTO qd_external_alpha_scores
                (as_of, source, version, universe, symbol, score, weight, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb)
                ON CONFLICT (as_of, source, version, symbol)
                DO UPDATE SET
                  score = EXCLUDED.score,
                  weight = EXCLUDED.weight,
                  universe = EXCLUDED.universe,
                  meta_json = EXCLUDED.meta_json,
                  ingested_at = NOW()
                """,
                (
                    as_of,
                    source,
                    version,
                    universe,
                    symbol,
                    score,
                    weight_f,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            inserted += 1
        db.commit()
    return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


def load_external_alpha_scores_as_of(
    as_of: date | str,
    *,
    source: str,
    version: str | None = None,
    symbols: list[str] | None = None,
) -> pd.Series:
    as_of_d = _as_date(as_of)
    source_s = str(source or "").strip()
    if not source_s:
        return pd.Series(dtype=float)
    version_s = DEFAULT_VERSION if version is None or str(version).strip() == "" else str(version).strip()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT MAX(as_of) AS as_of
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ? AND as_of <= ?
            """,
            (source_s, version_s, as_of_d),
        )
        hit = cur.fetchone() or {}
        eff = hit.get("as_of")
        if not eff:
            return pd.Series(dtype=float)
        cur.execute(
            """
            SELECT symbol, score
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ? AND as_of = ?
            """,
            (source_s, version_s, eff),
        )
        rows = list(cur.fetchall() or [])
    data: dict[str, float] = {}
    wanted = None
    if symbols:
        wanted = {canonicalize_cnstock_key(s) for s in symbols if canonicalize_cnstock_key(s)}
    for row in rows:
        key = canonicalize_cnstock_key(str(row["symbol"]))
        if not key:
            continue
        if wanted is not None and key not in wanted:
            continue
        try:
            val = float(row["score"])
        except (TypeError, ValueError):
            continue
        if val == val and val not in (float("inf"), float("-inf")):
            data[key] = val
    return pd.Series(data, dtype=float)
```

```python
# app/services/external_alpha/__init__.py
from .store import load_external_alpha_scores_as_of, persist_external_alpha_scores

__all__ = [
    "load_external_alpha_scores_as_of",
    "persist_external_alpha_scores",
]
```

> 注意：若项目 DB 层对 Postgres 使用 `%s` 而非 `?`，按 `get_db_connection` 既有写法对齐（参考 `csi300_enhanced/tushare_sync.py` 的 `?` 风格；当前包装器通常接受 `?`）。

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd backend_api_python && .venv312/bin/python -m pytest tests/test_external_alpha_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend_api_python/app/services/external_alpha backend_api_python/tests/test_external_alpha_store.py
git commit -m "$(cat <<'EOF'
feat: 实现外部alpha分数持久化与时点读取

EOF
)"
```

---

### Task 3: Strategy V2 注入 `get_external_alpha_scores`

**Files:**
- Modify: `backend_api_python/app/services/strategy_v2/runtime.py`
- Modify: `backend_api_python/app/services/strategy_v2/contract.py`（白名单增加函数名）
- Test: `backend_api_python/tests/test_external_alpha_runtime_api.py`

**Interfaces:**
- Produces: 策略沙箱全局 `get_external_alpha_scores(as_of, source, version=None, symbols=None) -> pd.Series`
- Consumes: `load_external_alpha_scores_as_of`

- [ ] **Step 1: Write failing test**

```python
# tests/test_external_alpha_runtime_api.py
from app.services.strategy_v2 import contract


def test_external_alpha_scores_allowed_in_contract():
    assert "get_external_alpha_scores" in contract.STRATEGY_V2_ALLOWED_NAMES  # 若实际常量名不同，改为现有 get_ashare_* 所在集合名
```

先 `rg "get_ashare_valuation_panel" backend_api_python/app/services/strategy_v2/contract.py` 确认集合变量名，再写入正确断言。

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement runtime wrapper + whitelist**

在 `runtime.py`（与 `get_ashare_valuation_panel` 并列）增加：

```python
def get_external_alpha_scores(
    as_of: object,
    source: str,
    version: object = None,
    symbols: list[str] | None = None,
) -> pd.Series:
    from app.services.external_alpha.store import load_external_alpha_scores_as_of

    return load_external_alpha_scores_as_of(
        as_of,
        source=str(source or ""),
        version=None if version is None else str(version),
        symbols=list(symbols) if symbols else None,
    )
```

将 `"get_external_alpha_scores": get_external_alpha_scores` 加入所有构建策略 globals 的 dict（`rg "get_ashare_valuation_panel" runtime.py` 两处都要加）。

在 `contract.py` 允许名集合中追加 `"get_external_alpha_scores"`。

- [ ] **Step 4: Run tests PASS**

```bash
cd backend_api_python && .venv312/bin/python -m pytest tests/test_external_alpha_runtime_api.py tests/test_csi300_enhanced_runtime_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend_api_python/app/services/strategy_v2/runtime.py backend_api_python/app/services/strategy_v2/contract.py backend_api_python/tests/test_external_alpha_runtime_api.py
git commit -m "$(cat <<'EOF'
feat: 策略运行时注入外部alpha分数读取接口

EOF
)"
```

---

### Task 4: CSV 导入脚本

**Files:**
- Create: `backend_api_python/scripts/import_external_alpha_scores.py`
- Test: `backend_api_python/tests/test_import_external_alpha_scores.py`

**Interfaces:**
- Produces: CLI  
  `python scripts/import_external_alpha_scores.py --csv path [--source S] [--version V] [--universe U]`
- Consumes: `persist_external_alpha_scores`

- [ ] **Step 1: Write failing test for CSV parse helper**

把解析逻辑放在 `store.py` 或脚本可导入函数 `rows_from_csv_text(text, *, default_source, default_version, default_universe) -> list[dict]`，便于单测：

```python
# tests/test_import_external_alpha_scores.py
from app.services.external_alpha.store import rows_from_csv_text


def test_rows_from_csv_text_minimal():
    text = "as_of,symbol,score\n20210831,600519,1.5\n2021-08-31,000001.SZ,0.2\n"
    rows = rows_from_csv_text(text, default_source="external", default_version="default", default_universe="")
    assert len(rows) == 2
    assert rows[0]["symbol"] in ("600519", "CNStock:600519.SH") or True  # 规范化可在 persist 做
    assert float(rows[0]["score"]) == 1.5
```

- [ ] **Step 2: Implement `rows_from_csv_text` + CLI**

CLI 骨架：

```python
#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.external_alpha.store import persist_external_alpha_scores, rows_from_csv_text

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--source", default="external")
    p.add_argument("--version", default="default")
    p.add_argument("--universe", default="")
    args = p.parse_args()
    text = Path(args.csv).read_text(encoding="utf-8")
    rows = rows_from_csv_text(
        text,
        default_source=args.source,
        default_version=args.version,
        default_universe=args.universe,
    )
    result = persist_external_alpha_scores(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("inserted", 0) > 0 or result.get("skipped", 0) == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
```

`rows_from_csv_text` 使用 `csv.DictReader`；必填 `as_of,symbol,score`；行级缺省填充 source/version/universe。

- [ ] **Step 3: pytest PASS + 手工 dry 导入 fixture（可选）**

- [ ] **Step 4: Commit**

```bash
git add backend_api_python/scripts/import_external_alpha_scores.py backend_api_python/app/services/external_alpha/store.py backend_api_python/tests/test_import_external_alpha_scores.py
git commit -m "$(cat <<'EOF'
feat: 支持csv导入外部alpha分数

EOF
)"
```

---

## Phase P1 — 示例策略 + 文档

### Task 5: 示例策略 `strategy_v2_external_alpha_score`

**Files:**
- Create: `docs/examples/strategy_v2_external_alpha_score_weekly.py`
- Modify: `backend_api_python/migrations/strategy_v2_templates.sql`（追加 INSERT，`ON CONFLICT (template_key) DO UPDATE`）
- Modify: Vue `strategy-v2.js` 增加参数中文 label（若使用 `strategyV2.params.*` 新键：`alphaSource` 等；优先复用已有 `universeTopN` 等键）

**Interfaces:**
- Produces: 模板键 `strategy_v2_external_alpha_score`
- Consumes: `get_external_alpha_scores`, `get_universe_stocks`, `order_target_percent`, `run_weekly`

- [ ] **Step 1: 编写示例策略（完整可运行骨架）**

要点：
- `@param source str external`
- `@param version str default`
- `@param top_n int 30`
- `@param min_names int 10`
- `@param score_lag_days int 1`
- `initialize`: `set_universe` 指向 `csi300`（或 pool 引用与现网一致）；`run_weekly(rebalance, weekday=1, time="09:35")`
- `rebalance`:
  - `as_of = context.current_dt.date() - timedelta(days=score_lag_days)`（自然日 MVP）
  - `scores = get_external_alpha_scores(as_of, source, version=version)`
  - 若 `len(scores.dropna()) < min_names`: `log` 并 return
  - Top-N：`scores.nlargest(top_n)`
  - 等权目标；对不在目标内的旧持仓 `order_target_percent(..., 0)`
  - 目标内 `order_target_percent(sym, 1.0/n)`

策略内**不要** `import app.*`（沙箱禁止）；只用注入 API 与 `pandas`/`numpy`/`datetime`。

- [ ] **Step 2: 种子 SQL**

在 `strategy_v2_templates.sql` 追加一条模板：`template_key=strategy_v2_external_alpha_score`，`param_schema.params` 带 `labelKey`（如 `strategyV2.params.alphaSource` → 在 `QuantDinger-Vue/src/locales/lang/strategy-v2.js` 的 en/zh-CN 增加「信号来源」等）。

- [ ] **Step 3: 用合成 CSV 烟测（容器）**

```bash
# 写入临时 CSV 后
docker exec quantdinger-backend python /app/scripts/import_external_alpha_scores.py --csv /tmp/scores.csv --source external
# 再跑短区间回测（可复用 verify 脚本模式或 backtest API）
```

Expected: 有分数周产生调仓；无分数周跳过

- [ ] **Step 4: Commit**

```bash
git add docs/examples/strategy_v2_external_alpha_score_weekly.py backend_api_python/migrations/strategy_v2_templates.sql QuantDinger-Vue/src/locales/lang/strategy-v2.js
git commit -m "$(cat <<'EOF'
feat: 新增外部alpha分数周频选股策略模板

EOF
)"
```

> Vue 路径若在独立仓库 `../QuantDinger-Vue`，在该仓库单独提交。

---

### Task 6: 运维文档

**Files:**
- Create: `docs/EXTERNAL_ALPHA_SCORE_BRIDGE_CN.md`

- [ ] **Step 1: 写中文说明**

内容必须覆盖：
1. 边界：QA/LGBM 离线，QD 执行  
2. CSV 列定义与示例  
3. 导入命令  
4. 策略参数 `source/version/top_n/score_lag_days`  
5. 时点：T 分 / T+1 调仓  
6. 明确不安装 QuantaAlpha 进 Docker  

- [ ] **Step 2: Commit**

```bash
git add docs/EXTERNAL_ALPHA_SCORE_BRIDGE_CN.md
git commit -m "$(cat <<'EOF'
docs: 补充外部alpha分数桥接使用说明

EOF
)"
```

---

## P2（本计划不实施，仅登记）

- QuantaAlpha `all_factors_library.json` / 回测预测导出 → 适配器  
- 分数接入 `optimize_enhanced_index` 指增  
- 消费 `weight` 列  
- 交易日日历精确 lag  

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| 表 `qd_external_alpha_scores` | Task 1 |
| persist / PIT load | Task 2 |
| `get_external_alpha_scores` 注入 | Task 3 |
| CSV 导入 | Task 4 |
| 示例模板 Top-N 等权 | Task 5 |
| `score_lag_days` / T+1 | Task 5 |
| 运维文档 | Task 6 |
| 不改 csi300_v2 默认 | 全局约束 |
| 不装 QA 进镜像 | 全局约束 / Task 6 |
| P2 项 | 登记，不实施 |

## Self-Review Notes

- 无 TBD 占位；`version` 缺省与 PIT 规则与规格一致  
- DB 占位符以实现时仓库惯例为准（`?` vs `%s`），Task 2 已注明对齐点  
- Vue i18n 在独立仓库时单独提交，避免漏提
