"""Canonical CNStock symbol / platform-key helpers."""

from __future__ import annotations

from app.data_sources.tushare_cn import tencent_code_to_ts_code


def canonicalize_cn_symbol(symbol: str) -> str:
    """Normalize to ``600519.SH`` / ``000001.SZ`` / ``300308.SZ`` form."""
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if raw.startswith("CNSTOCK:"):
        raw = raw.split(":", 1)[1]
    return tencent_code_to_ts_code(raw)


def canonicalize_cnstock_key(value: str) -> str:
    """Normalize platform keys to ``CNStock:600519.SH`` form."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if ":" in raw:
        market, symbol = raw.split(":", 1)
        if market.strip().upper() != "CNSTOCK":
            return raw
        ts = canonicalize_cn_symbol(symbol)
        return f"CNStock:{ts}" if ts else raw
    ts = canonicalize_cn_symbol(raw)
    return f"CNStock:{ts}" if ts else raw


def strip_cnstock_prefix(value: str) -> str:
    raw = str(value or "").strip()
    if raw.upper().startswith("CNSTOCK:"):
        return raw.split(":", 1)[1]
    return raw
