"""Periodic A-share Qlib EOD sync (Celery Beat)."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from app.celery_app import celery_app
from app.services.market.ashare_session import decide_ashare_daily_sync
from app.services.rdagent_bridge.client import RdAgentBridgeClient

logger = logging.getLogger(__name__)

SH_TZ = ZoneInfo("Asia/Shanghai")
_REDIS_KEY_PREFIX = "qlib_eod_sync:"
_REDIS_TTL_SEC = 36 * 3600
_redis_client = None


def _enabled(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _today_sh() -> str:
    return datetime.now(SH_TZ).date().isoformat()


def _get_redis():
    """Return a decode_responses Redis client, or None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        from app.config.redis_urls import cache_redis_url

        client = redis.Redis.from_url(
            cache_redis_url(),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
        return _redis_client
    except Exception as exc:
        logger.warning("qlib_eod: Redis unavailable, skip idempotency mark: %s", exc)
        return None


def _already_synced(day: str) -> bool:
    client = _get_redis()
    if client is None:
        return False
    try:
        return bool(client.get(f"{_REDIS_KEY_PREFIX}{day}"))
    except Exception as exc:
        logger.warning("qlib_eod: Redis get failed: %s", exc)
        return False


def _mark_synced(day: str) -> None:
    client = _get_redis()
    if client is None:
        return
    try:
        client.setex(f"{_REDIS_KEY_PREFIX}{day}", _REDIS_TTL_SEC, "1")
    except Exception as exc:
        logger.warning("qlib_eod: Redis setex failed: %s", exc)


def _maybe_warmup(as_of: str) -> int:
    """Optional score warmup for published models (stub when enabled)."""
    if not _enabled("ENABLE_QLIB_EOD_SCORE_WARMUP", "false"):
        return 0
    # Stub: full list+ensure can be wired later; keep branch observable.
    logger.info("qlib_eod: score warmup enabled for %s; warmup_models=0 (stub)", as_of)
    return 0


@celery_app.task(
    bind=True,
    name="quantdinger.tasks.qlib_eod_sync",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def run_qlib_eod_sync_task(self, as_of: str | None = None):
    """Poll-gated daily Qlib update after A-share close.

    Beat entry (see ``app.celery_app``)::

        qlib-eod-sync:
          task: quantdinger.tasks.qlib_eod_sync
          schedule: QLIB_EOD_POLL_INTERVAL_SEC (default 900, min 300)
          env: ENABLE_QLIB_EOD_SYNC (default true)
    """
    del self
    if not _enabled("ENABLE_QLIB_EOD_SYNC"):
        return {"skipped": True, "reason": "disabled"}

    day = (as_of or _today_sh()).strip()[:10]
    decision = decide_ashare_daily_sync(day)
    if not decision.ok:
        return {
            "skipped": True,
            "reason": decision.reason,
            "as_of": decision.as_of or day,
            "trading_day": decision.trading_day,
        }

    if _already_synced(day):
        return {"skipped": True, "reason": "already_synced", "as_of": day}

    client = RdAgentBridgeClient.from_env()
    qlib_meta = client.qlib_update(end=day)
    _mark_synced(day)
    warmup_models = _maybe_warmup(day)
    return {
        "ok": True,
        "as_of": day,
        "qlib_update": qlib_meta,
        "warmup_models": warmup_models,
    }
