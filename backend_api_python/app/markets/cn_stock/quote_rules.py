"""Pre-trade A-share checks against a live quote (limit band and suspension)."""

from __future__ import annotations

from typing import Any, Mapping


def _board_limit_pct(code: str, *, st: bool) -> float:
    if st:
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def ashare_quote_block_reason(
    quote: Mapping[str, Any] | None,
    symbol: str,
    *,
    buying: bool,
    st: bool = False,
) -> str:
    """Return ``no_quote`` / ``suspended`` / ``limit_up`` / ``limit_down`` when the order cannot fill, else ''."""
    if not quote:
        return "no_quote"
    try:
        last = float(quote.get("last") or 0.0)
        previous = float(quote.get("previousClose") or quote.get("prev_close") or 0.0)
        high = float(quote.get("high") or 0.0)
        low = float(quote.get("low") or 0.0)
    except (TypeError, ValueError):
        return "no_quote"
    # Tencent quotes a suspended stock with zero high/low for the session.
    if high <= 0 and low <= 0:
        return "suspended"
    if last <= 0:
        return "no_quote"
    if previous <= 0:
        return ""
    text = str(symbol or "").upper()
    code = text.split(":", 1)[-1].split("@", 1)[0].split(".", 1)[0]
    code = code[2:] if code[:2] in {"SH", "SZ", "BJ"} else code
    pct = _board_limit_pct(code, st=st)
    up = round(previous * (1.0 + pct) + 1e-8, 2)
    down = round(previous * (1.0 - pct) + 1e-8, 2)
    tick = 0.005
    if buying and last >= up - tick:
        return "limit_up"
    if not buying and last <= down + tick:
        return "limit_down"
    return ""
