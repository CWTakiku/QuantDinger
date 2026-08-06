"""Periodic glass-fiber industry news sync (Celery Beat)."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.celery_app import celery_app
from app.services.industry_glass_fiber.fetch import aggregate_week_from_texts, fetch_url
from app.services.industry_glass_fiber.store import upsert_glass_fiber_week

logger = logging.getLogger(__name__)

SH_TZ = ZoneInfo("Asia/Shanghai")


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _news_urls() -> list[str]:
    raw = os.getenv("GLASS_FIBER_NEWS_URLS", "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _week_as_of(day: date | None = None) -> date:
    """Friday of the ISO week containing ``day`` (Asia/Shanghai calendar date)."""
    ref = day or datetime.now(SH_TZ).date()
    return ref + timedelta(days=4 - ref.weekday())


def run_glass_fiber_industry_sync() -> dict:
    """Fetch whitelisted news URLs, aggregate weekly signals, and upsert."""
    if not _enabled("ENABLE_GLASS_FIBER_INDUSTRY_SYNC"):
        return {"ok": True, "skipped": True, "reason": "disabled"}

    urls = _news_urls()
    if not urls:
        return {"ok": True, "skipped": True, "reason": "no_urls"}

    as_of = _week_as_of()
    texts: list[str] = []
    fetched_urls: list[str] = []
    errors: list[dict[str, str]] = []

    for url in urls:
        try:
            text = fetch_url(url)
            if text.strip():
                texts.append(text)
                fetched_urls.append(url)
        except Exception as exc:
            logger.warning("glass_fiber_industry: fetch failed for %s: %s", url, exc)
            errors.append({"url": url, "error": str(exc)})

    if not texts:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_text",
            "as_of": as_of.isoformat(),
            "errors": errors,
        }

    aggregated = aggregate_week_from_texts(texts, as_of=as_of)
    row = {
        "as_of": as_of,
        "cloth_trend": aggregated["cloth_trend"],
        "inventory_trend": aggregated["inventory_trend"],
        "new_capacity_flag": aggregated["new_capacity_flag"],
        "cloth_7628_mid": aggregated.get("cloth_7628_mid"),
        "source": "public_news",
        "confidence": aggregated["confidence"],
        "raw_refs": {"urls": fetched_urls, "errors": errors},
    }
    saved = upsert_glass_fiber_week(row)
    return {
        "ok": True,
        "as_of": as_of.isoformat(),
        "source": "public_news",
        "urls": fetched_urls,
        "saved": saved,
        "errors": errors,
    }


@celery_app.task(
    bind=True,
    name="quantdinger.tasks.glass_fiber_industry_sync",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def run_glass_fiber_industry_sync_task(self):
    """Beat entry (see ``app.celery_app``).

    Env:
      ENABLE_GLASS_FIBER_INDUSTRY_SYNC (default true)
      GLASS_FIBER_NEWS_URLS comma-separated whitelist
    """
    del self
    return run_glass_fiber_industry_sync()
