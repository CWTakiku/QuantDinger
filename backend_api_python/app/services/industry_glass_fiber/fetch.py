from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date
from urllib.request import Request, urlopen

from app.services.industry_glass_fiber.parse import parse_glass_fiber_news

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    cleaned = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def fetch_url(url: str, timeout: float = 15.0) -> str:
    req = Request(url, headers={"User-Agent": "QuantDingerGlassFiberBot/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    if "<" in text and ">" in text:
        return _strip_html(text)
    return text


def _majority_vote(values: list[int]) -> int:
    if not values:
        return 0
    counts = Counter(values)
    top = counts.most_common(2)
    if len(top) == 1:
        return top[0][0]
    if top[0][1] > top[1][1]:
        return top[0][0]
    return 0


def aggregate_week_from_texts(texts: list[str], as_of: date) -> dict:
    """Parse multiple news snippets and merge signals by majority vote."""
    parsed = [parse_glass_fiber_news(text, as_of=as_of) for text in texts if text and text.strip()]
    if not parsed:
        return parse_glass_fiber_news("", as_of=as_of)

    cloth_trend = _majority_vote([p["cloth_trend"] for p in parsed])
    inventory_trend = _majority_vote([p["inventory_trend"] for p in parsed])
    new_capacity_flag = _majority_vote([p["new_capacity_flag"] for p in parsed])

    priced = [p for p in parsed if p.get("cloth_7628_mid") is not None]
    cloth_7628_mid = None
    if priced:
        best = max(priced, key=lambda p: p.get("confidence", 0.0))
        cloth_7628_mid = best.get("cloth_7628_mid")

    confidence = max(p.get("confidence", 0.0) for p in parsed)

    return {
        "cloth_trend": cloth_trend,
        "inventory_trend": inventory_trend,
        "new_capacity_flag": new_capacity_flag,
        "cloth_7628_mid": cloth_7628_mid,
        "confidence": confidence,
    }
