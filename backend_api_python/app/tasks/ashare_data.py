"""Periodic A-share data platform sync (Celery Beat)."""

from __future__ import annotations

import os

from app.celery_app import celery_app


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@celery_app.task(bind=True, name="quantdinger.tasks.ashare_data_daily_sync")
def run_ashare_data_daily_sync_task(self):
    """Catch up recent trade dates into ``qd_ashare_daily_bar``.

    Runs every ``ASHARE_DATA_SYNC_INTERVAL_SEC`` (default 1800); it is a no-op
    until 17:30 Shanghai and once the recent days are all ``ok``, and e-mails
    one alert per day if data is still incomplete after 20:00.
    """
    del self
    if not _enabled("ENABLE_ASHARE_DATA_SYNC"):
        return {"skipped": True}
    from app.services.ashare_data import run_daily_sync

    return run_daily_sync()
