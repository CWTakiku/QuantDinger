# A 股技术因子周频信号邮件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运营商 Tushare 接入 QuantDinger 作为 A 股主行情源，并交付可周频调仓的沪深300/中证500 动量 Top-N 示例策略，以 `signal` + 邮件推送、不下单。

**Architecture:** 新增独立 Tushare 客户端（Token + 自定义 HTTP，仅环境变量）；`CNStockDataSource.get_kline` 优先走 Tushare 日线，失败再回退现有 Twelve/腾讯/yfinance/AkShare。策略层复用 Strategy API V2：`pool=csi300|csi500` + `run_weekly` + `get_factors(..., "momentum", period=20)`，部署 `executionMode=signal` 与 SMTP 通知。

**Tech Stack:** Python 3.12、Flask 后端、`tushare` SDK、pandas、现有 Strategy V2 / SignalNotifier / pytest

**Spec:** `docs/superpowers/specs/2026-07-30-ashare-factor-signal-design.md`

## Global Constraints

- 不做 A 股实盘下单；终点是 `executionMode=signal` + 邮件。
- 真实 `TUSHARE_TOKEN` 禁止写入仓库、测试夹具、日志或文档正文；仅用环境变量 / 本地未跟踪配置。
- 第一阶段只用技术因子（默认 20 日动量）；基本面多因子不实现。
- 调仓为周频；策略示例默认 Top-N=10、仅做多。
- `docs/agent/*` 若改动须英文；本功能操作说明可用中文放在 `docs/trading/` 或 `docs/examples/`。
- Commit message 使用 Conventional Commits 中文：`feat:` / `test:` / `docs:` 等。

---

## File Structure

| 路径 | 职责 |
|---|---|
| `backend_api_python/app/data_sources/tushare_cn.py` | Tushare 客户端：ts_code 转换、日线拉取、归一化为平台 K 线 dict |
| `backend_api_python/app/data_sources/cn_stock.py` | 在 `get_kline` 最前插入 Tushare 日线优先层 |
| `backend_api_python/requirements.txt` | 增加 `tushare` 依赖 |
| `backend_api_python/env.example` | 增加 `TUSHARE_TOKEN` / `TUSHARE_HTTP_URL` 占位 |
| `backend_api_python/tests/test_tushare_cn.py` | 客户端单测（mock，不打真实网） |
| `backend_api_python/tests/test_cn_stock_tushare_priority.py` | CNStock 优先调用 Tushare 的单测 |
| `docs/examples/strategy_v2_cn_momentum_weekly.py` | 周频动量 Top-N 示例（参数切池） |
| `docs/trading/ASHARE_FACTOR_SIGNAL_CN.md` | 本地配置 Tushare、回测、signal 邮件操作说明 |

---

### Task 1: Tushare 日线客户端（可单测）

**Files:**
- Create: `backend_api_python/app/data_sources/tushare_cn.py`
- Create: `backend_api_python/tests/test_tushare_cn.py`
- Modify: `backend_api_python/requirements.txt`（增加一行 `tushare>=1.4.0`）

**Interfaces:**
- Consumes: 环境变量 `TUSHARE_TOKEN`、`TUSHARE_HTTP_URL`；腾讯风格代码如 `SH600519` / `600519.SH`
- Produces:
  - `tencent_code_to_ts_code(code: str) -> str` → 如 `600519.SH`
  - `is_tushare_configured() -> bool`
  - `fetch_tushare_daily_klines(*, tencent_code: str, limit: int, before_time: int | None = None) -> list[dict]`  
    每项：`{"time": <unix_sec>, "open", "high", "low", "close", "volume"}`（与 `tencent_kline_rows_to_dicts` 同形）

- [ ] **Step 1: Write the failing test**

```python
# backend_api_python/tests/test_tushare_cn.py
from app.data_sources.tushare_cn import tencent_code_to_ts_code


def test_tencent_code_to_ts_code():
    assert tencent_code_to_ts_code("SH600519") == "600519.SH"
    assert tencent_code_to_ts_code("SZ000001") == "000001.SZ"
    assert tencent_code_to_ts_code("600519.SH") == "600519.SH"
    assert tencent_code_to_ts_code("000001") == "000001.SZ"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend_api_python && python -m pytest tests/test_tushare_cn.py::test_tencent_code_to_ts_code -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `tushare_cn`

- [ ] **Step 3: Write minimal symbol conversion + configured gate**

```python
# backend_api_python/app/data_sources/tushare_cn.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def is_tushare_configured() -> bool:
    return bool(str(os.environ.get("TUSHARE_TOKEN") or "").strip())


def tencent_code_to_ts_code(code: str) -> str:
    s = (code or "").strip().upper()
    if not s:
        return s
    if s.endswith(".SH") or s.endswith(".SZ"):
        return s
    if s.startswith("SH") and len(s) >= 8 and s[2:].isdigit():
        return f"{s[2:]}.SH"
    if s.startswith("SZ") and len(s) >= 8 and s[2:].isdigit():
        return f"{s[2:]}.SZ"
    if s.isdigit() and len(s) == 6:
        return f"{s}.SH" if s.startswith("6") else f"{s}.SZ"
    return s
```

- [ ] **Step 4: Re-run symbol test**

Run: `cd backend_api_python && python -m pytest tests/test_tushare_cn.py::test_tencent_code_to_ts_code -v`

Expected: PASS

- [ ] **Step 5: Write failing test for daily fetch (mocked pro)**

```python
# append to test_tushare_cn.py
from unittest.mock import MagicMock, patch
import pandas as pd

from app.data_sources.tushare_cn import fetch_tushare_daily_klines


def test_fetch_tushare_daily_klines_maps_rows(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://example.test/api")

    frame = pd.DataFrame(
        [
            {"trade_date": "20260105", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "vol": 1000.0},
            {"trade_date": "20260106", "open": 10.5, "high": 11.2, "low": 10.0, "close": 11.0, "vol": 1200.0},
        ]
    )
    fake_pro = MagicMock()
    fake_pro.daily.return_value = frame

    with patch("app.data_sources.tushare_cn._build_pro", return_value=fake_pro):
        rows = fetch_tushare_daily_klines(tencent_code="SH600519", limit=2)

    assert len(rows) == 2
    assert rows[0]["open"] == 10.0
    assert rows[-1]["close"] == 11.0
    assert "time" in rows[0]
    fake_pro.daily.assert_called()
    kwargs = fake_pro.daily.call_args.kwargs
    assert kwargs["ts_code"] == "600519.SH"
```

- [ ] **Step 6: Run fetch test to verify it fails**

Run: `cd backend_api_python && python -m pytest tests/test_tushare_cn.py::test_fetch_tushare_daily_klines_maps_rows -v`

Expected: FAIL (`fetch_tushare_daily_klines` 未定义)

- [ ] **Step 7: Implement fetch + pro builder**

在 `tushare_cn.py` 追加（要点）：

```python
def _build_pro():
    import tushare as ts
    token = str(os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        return None
    ts.set_token(token)
    pro = ts.pro_api()
    http_url = str(os.environ.get("TUSHARE_HTTP_URL") or "").strip()
    if http_url:
        # 运营商自定义基址（与官方 pro 兼容的 DataApi）
        pro._DataApi__http_url = http_url
    return pro


def _trade_date_to_unix(trade_date: str) -> int:
    dt = datetime.strptime(str(trade_date), "%Y%m%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_tushare_daily_klines(
    *,
    tencent_code: str,
    limit: int,
    before_time: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not is_tushare_configured():
        return []
    pro = _build_pro()
    if pro is None:
        return []
    ts_code = tencent_code_to_ts_code(tencent_code)
    lim = max(int(limit or 1), 1)
    end_date = None
    if before_time:
        end_date = datetime.fromtimestamp(int(before_time), tz=timezone.utc).strftime("%Y%m%d")
    try:
        # 多取一点再截断，兼容 end_date 过滤
        df = pro.daily(ts_code=ts_code, end_date=end_date)
    except Exception as exc:
        logger.warning("Tushare daily failed ts_code=%s: %s", ts_code, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    df = df.sort_values("trade_date")
    if before_time:
        df = df[df["trade_date"].map(_trade_date_to_unix) < int(before_time)]
    df = df.tail(lim)
    out: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            out.append(
                {
                    "time": _trade_date_to_unix(row["trade_date"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    # Tushare vol 单位为手；保持数值即可，与现网一致即可用
                    "volume": float(row.get("vol") or row.get("volume") or 0.0),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return out
```

并在 `requirements.txt` 增加：`tushare>=1.4.0`

- [ ] **Step 8: Run all tushare_cn tests**

Run: `cd backend_api_python && python -m pytest tests/test_tushare_cn.py -v`

Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend_api_python/app/data_sources/tushare_cn.py \
  backend_api_python/tests/test_tushare_cn.py \
  backend_api_python/requirements.txt
git commit -m "$(cat <<'EOF'
feat: 接入tushare日线客户端

EOF
)"
```

---

### Task 2: CNStock 优先使用 Tushare 日线

**Files:**
- Modify: `backend_api_python/app/data_sources/cn_stock.py`
- Create: `backend_api_python/tests/test_cn_stock_tushare_priority.py`
- Modify: `backend_api_python/env.example`（在数据源段落增加 `TUSHARE_TOKEN=` / `TUSHARE_HTTP_URL=` 注释占位）

**Interfaces:**
- Consumes: `is_tushare_configured`, `fetch_tushare_daily_klines`
- Produces: `CNStockDataSource.get_kline` 在 `tf in ("1D",)`（及平台归一后的日线）且已配置 Token 时，先返回 Tushare 结果；空则走原 fallback

- [ ] **Step 1: Write the failing test**

```python
# backend_api_python/tests/test_cn_stock_tushare_priority.py
from unittest.mock import patch
from app.data_sources.cn_stock import CNStockDataSource


def test_get_kline_prefers_tushare_for_daily(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "dummy")
    rows = [{"time": 1736035200, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    src = CNStockDataSource()
    with patch("app.data_sources.cn_stock.fetch_tushare_daily_klines", return_value=rows) as mocked, \
         patch("app.data_sources.cn_stock.fetch_twelvedata_klines", return_value=[]) as twelve:
        out = src.get_kline("600519.SH", "1d", 10)
    assert out == rows or out[0]["close"] == 1.5
    mocked.assert_called()
    twelve.assert_not_called()
```

（若 `filter_and_limit` 改变 list 身份，断言字段即可；关键是 **未调用** Twelve。）

- [ ] **Step 2: Run test to verify it fails or shows Twelve still first**

Run: `cd backend_api_python && python -m pytest tests/test_cn_stock_tushare_priority.py -v`

Expected: FAIL（未导入 / 仍先调 Twelve）

- [ ] **Step 3: Wire Tushare at the top of `get_kline` for daily**

在 `cn_stock.py`：

```python
from app.data_sources.tushare_cn import fetch_tushare_daily_klines, is_tushare_configured
```

在 `get_kline` 内、Twelve Data 之前：

```python
        # Tier 0: Tushare daily (operator token + optional custom HTTP URL)
        if tf in ("1D",) and is_tushare_configured():
            rows = fetch_tushare_daily_klines(
                tencent_code=code, limit=lim, before_time=before_time
            )
            if rows:
                return self.filter_and_limit(
                    rows,
                    limit=lim,
                    before_time=before_time,
                    after_time=after_time,
                    truncate=(after_time is None),
                )
```

更新文件头注释说明 Tushare 优先。

在 `env.example` 增加（无真实值）：

```bash
# A-share Tushare (optional; preferred daily source for CNStock when set)
TUSHARE_TOKEN=
# Custom HTTP API base for Tushare-compatible gateways (optional)
TUSHARE_HTTP_URL=
```

- [ ] **Step 4: Run priority test**

Run: `cd backend_api_python && python -m pytest tests/test_cn_stock_tushare_priority.py tests/test_tushare_cn.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend_api_python/app/data_sources/cn_stock.py \
  backend_api_python/tests/test_cn_stock_tushare_priority.py \
  backend_api_python/env.example
git commit -m "$(cat <<'EOF'
feat: 中国A股日线优先使用tushare

EOF
)"
```

---

### Task 3: 周频动量 Top-N 示例策略

**Files:**
- Create: `docs/examples/strategy_v2_cn_momentum_weekly.py`

**Interfaces:**
- Consumes: Strategy V2 API（`set_universe(pool=...)`, `run_weekly`, `get_universe_stocks`, `get_factors`, `order_target_percent`）
- Produces: 可粘贴到策略 IDE 的源码；参数 `pool_name`、`holdings`、`momentum_period`、`max_weight`

说明：运行时因子 ID 使用 registry 的 `momentum`，并通过 `period=` 传 20；**不要**依赖未注册的字面量 `momentum_20`（那是研究引擎面板名）。

- [ ] **Step 1: Add example strategy file**

```python
"""CN CSI Momentum Weekly
Long-only weekly Top-N by 20-day momentum on csi300 or csi500.
Signal-mode friendly: no short, rebalance only on weekly schedule.
"""

# @param pool_name str csi300 Universe pool: csi300 or csi500
# @param holdings int 10 Number of holdings range=3:30:1
# @param momentum_period int 20 Momentum lookback days range=5:60:1
# @param max_weight float 0.12 Max weight per name range=0.05:0.25:0.01


def initialize(context):
    pool_name = str(context.params.get("pool_name", "csi300")).strip().lower()
    if pool_name not in ("csi300", "csi500"):
        pool_name = "csi300"
    g.pool_name = pool_name
    context.set_universe(pool=pool_name)
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(80)
    if pool_name == "csi500":
        context.set_benchmark("CNStock:000905.SH")
    else:
        context.set_benchmark("CNStock:000300.SH")
    # weekday: 1=Monday
    run_weekly(rebalance, weekday=1, time="09:35")


def rebalance(context, data):
    holdings = int(context.params.get("holdings", 10))
    period = int(context.params.get("momentum_period", 20))
    max_weight = float(context.params.get("max_weight", 0.12))
    symbols = get_universe_stocks()
    if len(symbols) < holdings:
        return

    scores = get_factors(symbols, "momentum", period=period)
    if scores is None or getattr(scores, "empty", True) or "momentum" not in scores.columns:
        return

    ranked = scores["momentum"].dropna().sort_values(ascending=False)
    selected = list(ranked.head(holdings).index)
    if not selected:
        return

    target_weight = min(max_weight, 0.95 / len(selected))
    current = get_positions()

    for symbol in current:
        if symbol not in selected:
            order_target_percent(symbol, 0.0, reason="weekly_remove")

    for symbol in selected:
        order_target_percent(symbol, target_weight, reason="weekly_select")
```

- [ ] **Step 2: Sanity-check compile if catalog validator is available**

Run（若环境缺依赖可跳过，改为在 IDE「验证」）：

```bash
cd backend_api_python && python - <<'PY'
from pathlib import Path
from app.services.strategy_v2.contract import compile_strategy_source
src = Path("../docs/examples/strategy_v2_cn_momentum_weekly.py").read_text(encoding="utf-8")
manifest = compile_strategy_source(src)
print(manifest.metadata().get("universe") or manifest.universe)
print("ok")
PY
```

Expected: 打印含 `csi300` 的 universe 信息且无异常。若函数名不同，以仓库内 `compile_strategy` / `validate` 实际导出为准，目标是「源码通过契约编译」。

- [ ] **Step 3: Commit**

```bash
git add docs/examples/strategy_v2_cn_momentum_weekly.py
git commit -m "$(cat <<'EOF'
feat: 新增A股周频动量选股示例策略

EOF
)"
```

---

### Task 4: 操作说明（Tushare + 回测 + signal 邮件）

**Files:**
- Create: `docs/trading/ASHARE_FACTOR_SIGNAL_CN.md`
- Modify: `docs/superpowers/specs/2026-07-30-ashare-factor-signal-design.md`（仅在「交付物」处加指向本说明的链接，若尚未有）

**Interfaces:**
- Consumes: 用户本地 `TUSHARE_*`、`SMTP_*`、`SHOW_CN_STOCK` / 市场可见性配置
- Produces: 可按步骤操作的中文说明（不含真实 Token）

- [ ] **Step 1: Write the operator doc**

文档必须覆盖：

1. 在运行环境设置 `TUSHARE_TOKEN`、`TUSHARE_HTTP_URL`（写明「向运营商索取基址，勿提交 Token」）。
2. 确保 A 股市场对 UI 可见（`SHOW_CN_STOCK=true` 或 `VISIBLE_MARKETS` 含 `CNStock`，以 `env.example` / `market_visibility.py` 为准）。
3. 配置 `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM`。
4. 刷新或确认 `csi300`/`csi500` 股票池快照存在（引用 `scripts/refresh_public_universe_snapshots.py`）。
5. 将 `docs/examples/strategy_v2_cn_momentum_weekly.py` 粘贴到策略 IDE → 验证 → 回测（建议先短区间）。
6. 分别用 `pool_name=csi300` 与 `csi500` 对比。
7. 部署：`executionMode=signal`，`notificationChannels` 含 `email`，`notificationTargets.email` 为收件人。
8. 验收：非调仓日不应刷屏；周一调仓后收到邮件。

- [ ] **Step 2: Commit**

```bash
git add docs/trading/ASHARE_FACTOR_SIGNAL_CN.md docs/superpowers/specs/2026-07-30-ashare-factor-signal-design.md
git commit -m "$(cat <<'EOF'
docs: 补充A股因子信号与tushare配置说明

EOF
)"
```

---

### Task 5: 本地联调检查清单（手工，不提交密钥）

**Files:** 无代码变更（除非联调发现缺口再开修复任务）

- [ ] **Step 1: 在 shell 中导出变量（勿写入仓库文件）**

```bash
export TUSHARE_TOKEN='…'          # 仅本机会话
export TUSHARE_HTTP_URL='https://ts.gyzcloud.top/api'
```

- [ ] **Step 2: 冒烟拉一根日线**

```bash
cd backend_api_python && python - <<'PY'
from app.data_sources.tushare_cn import fetch_tushare_daily_klines
rows = fetch_tushare_daily_klines(tencent_code="SH600519", limit=5)
print(len(rows), rows[-1] if rows else None)
PY
```

Expected: `len(rows) > 0` 且最后一根有 `close`

- [ ] **Step 3: 跑相关单测**

```bash
cd backend_api_python && python -m pytest tests/test_tushare_cn.py tests/test_cn_stock_tushare_priority.py -v
```

Expected: PASS

- [ ] **Step 4: 若冒烟失败**

按错误分流：Token/URL → 检查环境变量；限频 → 降低调用；字段缺失 → 调整 `daily` 字段映射。修复后回到对应 Task 补测试，**不要**把 Token 写进测试。

---

## Spec coverage (self-review)

| Spec 要求 | Task |
|---|---|
| Tushare 主行情、环境变量、禁止提交 Token | 1, 2, 4, 5 |
| csi300 + csi500、周频、Top-N、仅做多、20 日动量 | 3 |
| signal + 邮件、操作说明 | 4 |
| 回测验证路径 | 4（手工）+ 3（示例） |
| 基本面 / 实盘 | 明确不在本计划 |
| 错误处理（单票失败跳过） | 策略示例自然跳过空因子；数据层返回空走 fallback（Task 2） |
| 邮件去重 / 非调仓日静默 | 依赖平台 `run_weekly` + 现有 SignalNotifier；文档说明验收点（Task 4） |

无 TBD 占位。因子名统一为 registry `momentum` + `period`，与研究引擎字面量 `momentum_20` 区分已在 Task 3 写明。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-30-ashare-factor-signal.md`.

**两种执行方式：**

1. **Subagent-Driven（推荐）** — 每个 Task 派独立子代理，Task 间复查  
2. **Inline Execution** — 本会话按 executing-plans 连续执行并设检查点  

选哪一种？
