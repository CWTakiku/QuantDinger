from __future__ import annotations

import re
from datetime import date

_CLOTH_UP = re.compile(r"上调|上涨|涨价|企稳回升|止跌")
_CLOTH_DOWN = re.compile(r"下调|下跌|降价")
_CLOTH_DOWN_HUILUO = re.compile(r"(?:布价|价格|电子布|(?:玻纤)?纱).*?回落")
_INV_DOWN = re.compile(r"库存.*?(下降|回落|去化|低位)")
_INV_UP = re.compile(r"库存.*?(累积|抬升|上升|高位)")
_CAPACITY = re.compile(r"点火|新窑|新产能")
_CAPACITY_NEG = re.compile(r"(?:暂无|没有|未有|无|未).*?(?:点火|新窑|新产能)")
_PRICE_7628 = re.compile(r"7628.*?(\d+(?:\.\d+)?)\s*元")


def _parse_cloth_trend(text: str, notes: list[str]) -> tuple[int, bool]:
    up = bool(_CLOTH_UP.search(text))
    down = bool(_CLOTH_DOWN.search(text)) or bool(_CLOTH_DOWN_HUILUO.search(text))
    if up and down:
        notes.append("cloth_trend conflict: up and down keywords")
        return 0, True
    if up:
        notes.append("cloth_trend: up")
        return 1, False
    if down:
        notes.append("cloth_trend: down")
        return -1, False
    return 0, False


def _parse_inventory_trend(text: str, notes: list[str]) -> tuple[int, bool]:
    down = bool(_INV_DOWN.search(text))
    up = bool(_INV_UP.search(text))
    if up and down:
        notes.append("inventory_trend conflict: up and down keywords")
        return 0, True
    if down:
        notes.append("inventory_trend: down")
        return -1, False
    if up:
        notes.append("inventory_trend: up")
        return 1, False
    return 0, False


def _parse_new_capacity_flag(text: str, notes: list[str]) -> int:
    if _CAPACITY_NEG.search(text):
        notes.append("new_capacity_flag: negated (暂无/无)")
        return 0
    if _CAPACITY.search(text):
        notes.append("new_capacity_flag: new capacity detected")
        return 1
    return 0


def _parse_cloth_7628_mid(text: str, notes: list[str]) -> float | None:
    match = _PRICE_7628.search(text)
    if not match:
        return None
    price = float(match.group(1))
    notes.append(f"cloth_7628_mid: {price}")
    return price


def _compute_confidence(
    *,
    cloth_trend: int,
    inventory_trend: int,
    new_capacity_flag: int,
    cloth_7628_mid: float | None,
    conflict: bool,
) -> float:
    if conflict:
        return 0.3

    signal_count = sum(
        1
        for value in (cloth_trend, inventory_trend, new_capacity_flag, cloth_7628_mid)
        if value not in (0, None)
    )
    if signal_count == 0:
        return 0.3
    if signal_count >= 2:
        return min(1.0, 0.5 + 0.1 * (signal_count - 2))
    return 0.5


def parse_glass_fiber_news(text: str, *, as_of: date | None = None) -> dict:
    """Parse public glass-fiber news text into weekly trend signals."""
    notes: list[str] = []
    if as_of is not None:
        notes.append(f"as_of: {as_of.isoformat()}")

    cloth_trend, cloth_conflict = _parse_cloth_trend(text, notes)
    inventory_trend, inv_conflict = _parse_inventory_trend(text, notes)
    new_capacity_flag = _parse_new_capacity_flag(text, notes)
    cloth_7628_mid = _parse_cloth_7628_mid(text, notes)

    confidence = _compute_confidence(
        cloth_trend=cloth_trend,
        inventory_trend=inventory_trend,
        new_capacity_flag=new_capacity_flag,
        cloth_7628_mid=cloth_7628_mid,
        conflict=cloth_conflict or inv_conflict,
    )

    return {
        "cloth_trend": cloth_trend,
        "inventory_trend": inventory_trend,
        "new_capacity_flag": new_capacity_flag,
        "cloth_7628_mid": cloth_7628_mid,
        "confidence": confidence,
        "notes": notes,
    }
