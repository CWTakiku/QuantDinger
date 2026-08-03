"""A-share board-lot helpers for Strategy V2 execution."""

from __future__ import annotations


def cn_stock_board_lot(symbol: str) -> float | None:
    """Return A-share board lot when ``symbol`` is a CNStock instrument.

    - STAR Market (``688`` / ``689``): 200 shares
    - Other A-shares (main board / ChiNext / BSE, etc.): 100 shares
    - Non-CNStock symbols: ``None`` (caller keeps its own default)
    """
    text = str(symbol or "").strip()
    if not text:
        return None
    upper = text.upper()
    if not upper.startswith("CNSTOCK:"):
        return None
    code_part = upper.split(":", 1)[1].split("@", 1)[0]
    digits = "".join(ch for ch in code_part.split(".", 1)[0] if ch.isdigit())
    if len(digits) < 6:
        # Keep suffix-stripped codes like 600519
        digits = "".join(ch for ch in code_part if ch.isdigit())
    if not digits:
        return 100.0
    if digits.startswith(("688", "689")):
        return 200.0
    return 100.0
