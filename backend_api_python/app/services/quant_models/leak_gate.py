"""Reject factor code that reads the future, and require publish evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

import pandas as pd

_SHIFT_FUTURE = re.compile(r"\.shift\(\s*-\d+")
_REF_FUTURE = re.compile(r"Ref\(\s*[^,]+,\s*-\d+", re.IGNORECASE)
LIVE_SCORE_METHOD = "cs_zmean"


def lookahead_hits(source: str) -> list[str]:
    text = str(source or "")
    hits = []
    if _SHIFT_FUTURE.search(text):
        hits.append("shift(-n)")
    if _REF_FUTURE.search(text):
        hits.append("Ref(field, -n)")
    return hits


def assert_no_lookahead(source: str) -> None:
    hits = lookahead_hits(source)
    if hits:
        raise ValueError("factor reads the future: " + ", ".join(hits))


def truncation_changes_history(compute: Callable[[pd.DataFrame], pd.Series], frame: pd.DataFrame) -> bool:
    """True when dropping the last row changes any earlier factor value."""
    if len(frame) < 3:
        return False
    full = compute(frame)
    trimmed = compute(frame.iloc[:-1])
    shared = full.index.intersection(trimmed.index)
    if len(shared) == 0:
        return False
    return not full.loc[shared].equals(trimmed.loc[shared])


def code_hash(source: str) -> str:
    return hashlib.sha256(str(source or "").encode("utf-8")).hexdigest()


def validate_publish(
    *,
    kind: str,
    metrics: dict[str, Any] | None,
    factor_source: str | None = None,
) -> dict[str, Any]:
    """Return the metrics stored on publish. Raises ValueError when evidence is missing."""
    kind_s = str(kind or "").strip().lower()
    body = dict(metrics or {})
    data_tip = str(body.get("data_tip") or "").strip()[:10]
    digest = str(body.get("code_hash") or "").strip().lower()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data_tip):
        raise ValueError("metrics.data_tip must be YYYY-MM-DD")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("metrics.code_hash must be a sha256 hex digest")
    if not any(body.get(key) is not None for key in ("ic", "rank_ic", "annual_return")):
        raise ValueError("metrics must include ic, rank_ic, or annual_return")
    if kind_s == "factor":
        method = str(body.get("score_method") or "").strip().lower().replace("-", "_")
        if method != LIVE_SCORE_METHOD:
            raise ValueError(f"factor score_method must be {LIVE_SCORE_METHOD}")
        source = factor_source if factor_source is not None else str(body.get("factor_source") or "")
        if source:
            assert_no_lookahead(source)
            expected = code_hash(source)
            if digest != expected:
                raise ValueError("metrics.code_hash does not match factor_source")
    return body
