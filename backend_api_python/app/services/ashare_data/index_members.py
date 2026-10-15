"""Turn index weight boards into point-in-time universe membership."""

from __future__ import annotations

from datetime import date, datetime
from typing import Mapping


def canonicalize_cn_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if raw.startswith("CNSTOCK:"):
        raw = raw.split(":", 1)[1]
    if raw.endswith((".SH", ".SZ", ".BJ")):
        return raw
    if raw.isdigit() and len(raw) == 6:
        if raw.startswith(("8", "4", "9")):
            return f"{raw}.BJ"
        if raw.startswith("6"):
            return f"{raw}.SH"
        return f"{raw}.SZ"
    return raw


def boards_to_member_rows(boards: Mapping[date, Mapping[str, float]]) -> list[dict]:
    """Each board stays in force until the next published weight date.

    The newest board stays open (``valid_to`` is None). Weight is stored as a
    fraction, matching ``qd_universe_members.member_weight``.
    """
    dates = sorted(boards)
    rows: list[dict] = []
    for index, day in enumerate(dates):
        nxt = dates[index + 1] if index + 1 < len(dates) else None
        ranked = sorted(boards[day].items(), key=lambda item: (-item[1], item[0]))
        for rank, (symbol, weight_pct) in enumerate(ranked, start=1):
            code = canonicalize_cn_symbol(symbol)
            if not code or weight_pct <= 0 or weight_pct != weight_pct:
                continue
            rows.append(
                {
                    "symbol": code,
                    "valid_from": day,
                    "valid_to": nxt,
                    "weight": float(weight_pct) / 100.0,
                    "rank": rank,
                }
            )
    return rows


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).replace("-", "")[:8]
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
