"""Minimal CNStock key normalization for external alpha CSV import."""

from __future__ import annotations


def _to_ts_code(code: str) -> str:
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


def canonicalize_cnstock_key(value: str) -> str:
    """Normalize platform keys to ``CNStock:600519.SH`` form."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if ":" in raw:
        market, symbol = raw.split(":", 1)
        if market.strip().upper() != "CNSTOCK":
            return raw
        ts = _to_ts_code(symbol)
        return f"CNStock:{ts}" if ts else raw
    ts = _to_ts_code(raw)
    return f"CNStock:{ts}" if ts else raw
