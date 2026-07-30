"""A-share board-lot rules (手数).

Mainland cash equities trade in lots of 100 shares.
STAR Market (科创板, 688xxx) requires a minimum opening size of 2 lots (200 shares).
Full closes may sell any remaining shares (including odd lots).
"""

from __future__ import annotations

import math
import re
from typing import Tuple

_CN_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def extract_cn_stock_code(symbol: str | None) -> str | None:
    raw = str(symbol or "").strip()
    if not raw:
        return None
    # Prefer market-qualified symbols.
    if raw.upper().startswith("CNSTOCK:"):
        body = raw.split(":", 1)[1]
        # CNStock:600519 / CNStock:600519.SH / CNStock:SH600519
        match = _CN_CODE_RE.search(body)
        return match.group(1) if match else None
    match = _CN_CODE_RE.fullmatch(raw) or _CN_CODE_RE.search(raw)
    return match.group(1) if match else None


def is_star_market(symbol: str | None, *, market_tag: str | None = None) -> bool:
    tag = str(market_tag or "").strip()
    if "科创" in tag or tag.upper() in {"STAR", "STAR_MARKET", "KCB"}:
        return True
    code = extract_cn_stock_code(symbol)
    return bool(code and code.startswith("688"))


def cn_stock_lot_spec(
    symbol: str | None,
    *,
    market_tag: str | None = None,
) -> Tuple[float, float] | None:
    """Return ``(lot_size, min_open_quantity)`` for CNStock symbols, else ``None``."""
    code = extract_cn_stock_code(symbol)
    if code is None and not str(symbol or "").upper().startswith("CNSTOCK:"):
        return None
    if code is None:
        # Qualified CNStock without parseable code — still apply standard board lot.
        return 100.0, 100.0
    if is_star_market(symbol, market_tag=market_tag) or code.startswith("688"):
        return 100.0, 200.0
    return 100.0, 100.0


def cn_stock_lot_size(symbol: str | None, *, market_tag: str | None = None) -> float | None:
    spec = cn_stock_lot_spec(symbol, market_tag=market_tag)
    return None if spec is None else spec[0]


def cn_stock_min_open(symbol: str | None, *, market_tag: str | None = None) -> float | None:
    spec = cn_stock_lot_spec(symbol, market_tag=market_tag)
    return None if spec is None else spec[1]


def round_to_lot(value: float, lot_size: float) -> float:
    if lot_size <= 0:
        return float(value)
    units = math.floor(abs(value) / lot_size + 1e-8)
    return math.copysign(units * lot_size, value) if units else 0.0


def adjust_cn_stock_target(
    symbol: str | None,
    current: float,
    target: float,
    *,
    market_tag: str | None = None,
) -> float:
    """Snap a desired target quantity onto A-share board-lot constraints.

    - Full close (target ~= 0) keeps target at 0 (odd-lot exit allowed).
    - Opening from flat requires ``min_open``; otherwise target stays flat.
    - Other deltas are floored to ``lot_size`` multiples.
    """
    spec = cn_stock_lot_spec(symbol, market_tag=market_tag)
    if spec is None:
        return float(target)
    lot_size, min_open = spec
    current = float(current or 0.0)
    target = float(target or 0.0)

    if abs(target) <= 1e-12:
        return 0.0

    delta = target - current
    if abs(delta) <= 1e-12:
        return current

    # Opening / flipping through flat is handled by callers as separate legs.
    if abs(current) <= 1e-12:
        rounded = round_to_lot(delta, lot_size)
        if abs(rounded) + 1e-12 < min_open:
            return 0.0
        return rounded

    rounded_delta = round_to_lot(delta, lot_size)
    if abs(rounded_delta) <= 1e-12:
        return current

    new_target = current + rounded_delta
    # Do not reverse side via partial lot rounding.
    if current > 0 and new_target < -1e-12:
        return 0.0
    if current < 0 and new_target > 1e-12:
        return 0.0
    return new_target
