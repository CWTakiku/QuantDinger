"""Periodic CSI300 enhanced-index data sync (Celery Beat)."""

from __future__ import annotations

import os
from datetime import date

from app.celery_app import celery_app


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@celery_app.task(
    bind=True,
    name="quantdinger.tasks.csi300_enhanced_daily_sync",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def run_csi300_enhanced_daily_sync_task(self, trade_date: str | None = None):
    """Day-end sync of CSI300 weights / daily_basic / industry / flow / consensus.

    Returns zero counts when Tushare is missing; does not break worker startup.
    Beat entry (see ``app.celery_app``)::

        csi300-enhanced-daily-sync:
          task: quantdinger.tasks.csi300_enhanced_daily_sync
          schedule: CSI300_ENHANCED_SYNC_INTERVAL_SEC (default 86400)
          env: ENABLE_CSI300_ENHANCED_DAILY_SYNC (default true)
    """
    del self
    if not _enabled("ENABLE_CSI300_ENHANCED_DAILY_SYNC"):
        return {"skipped": True}
    from app.services.csi300_enhanced.tushare_sync import run_csi300_enhanced_daily_sync

    day = trade_date or date.today().strftime("%Y%m%d")
    return run_csi300_enhanced_daily_sync(
        trade_date=day,
        skip_industry=_enabled("CSI300_ENHANCED_SYNC_SKIP_INDUSTRY", "false"),
        skip_flow=_enabled("CSI300_ENHANCED_SYNC_SKIP_FLOW", "false"),
        skip_consensus=_enabled("CSI300_ENHANCED_SYNC_SKIP_CONSENSUS", "false"),
    )
