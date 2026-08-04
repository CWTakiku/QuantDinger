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


def cnstock_name_lookup_candidates(value: str) -> list[str]:
    """Candidate keys for ``qd_market_symbols.symbol`` given a CNStock alpha key."""
    key = canonicalize_cnstock_key(value)
    if not key:
        return []
    raw = key.split(":", 1)[-1].strip().upper()
    out: list[str] = []
    seen: set[str] = set()

    def _add(item: str) -> None:
        s = (item or "").strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    _add(raw)
    if raw.endswith(".SH") or raw.endswith(".SZ"):
        code = raw[:-3]
        exch = raw[-2:]
        _add(code)
        _add(f"{exch}{code}")
    elif raw.startswith("SH") or raw.startswith("SZ"):
        _add(raw[2:])
        _add(_to_ts_code(raw))
    elif raw.isdigit() and len(raw) == 6:
        _add(_to_ts_code(raw))
        prefix = "SH" if raw.startswith("6") else "SZ"
        _add(f"{prefix}{raw}")
    return out
