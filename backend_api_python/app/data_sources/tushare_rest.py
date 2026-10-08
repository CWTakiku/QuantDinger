"""Tushare-compatible REST gateway client.

The gateway exposes ``GET {base}/{api_name}?<params>`` with an ``X-API-Key``
header and answers ``{"code": 0, "data": {"fields": [...], "items": [...]}}``.
``TushareRestPro`` mimics the ``tushare.pro_api()`` call style
(``pro.daily(ts_code=...)`` / ``pro.query("daily", ...)``) and returns
DataFrames, so existing callers keep working unchanged.
"""

from __future__ import annotations

import functools
import os
import threading
import time
from typing import Any, Iterable

import pandas as pd
import requests

from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_REST_URL = "https://tl.kaixin8.top/tushare/pro"
# Documented backup is plain HTTP. It is not used unless a caller opts in.
DEFAULT_BACKUP_URL = ""
_PAGE_LIMIT = 5000
_MAX_PAGES = 50


class _RateLimiter:
    """Process-wide spacing so concurrent fetches stay under the per-IP quota."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self, per_minute: float) -> None:
        if per_minute <= 0:
            return
        interval = 60.0 / per_minute
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_at)
            self._next_at = start + interval
        delay = start - now
        if delay > 0:
            time.sleep(delay)


_rate_limiter = _RateLimiter()


def _max_per_minute() -> float:
    try:
        return float(os.getenv("TUSHARE_REST_MAX_PER_MIN", "180") or 180)
    except ValueError:
        return 180.0


def _insecure_allowed(explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return os.getenv("TUSHARE_REST_ALLOW_INSECURE", "").strip().lower() in {"1", "true", "yes"}


class TushareRestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transient: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class TushareRestPro:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_REST_URL,
        backup_url: str = DEFAULT_BACKUP_URL,
        timeout: float = 30.0,
        retries: int = 3,
        session: requests.Session | None = None,
        allow_insecure: bool | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.backup_url = str(backup_url or "").strip().rstrip("/")
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.allow_insecure = _insecure_allowed(allow_insecure)
        self.max_per_minute = 0.0 if session is not None else _max_per_minute()
        self._session = session or requests.Session()

    def _url_allowed(self, url: str) -> bool:
        if str(url).lower().startswith("https://"):
            return True
        if self.allow_insecure:
            logger.warning("Tushare REST sending the API key to non-HTTPS endpoint %s", url)
            return True
        logger.warning(
            "Tushare REST skipped non-HTTPS endpoint %s; set TUSHARE_REST_ALLOW_INSECURE=1 to allow it",
            url,
        )
        return False

    def _endpoints(self, api_name: str) -> Iterable[tuple[str, str]]:
        if self.base_url and self._url_allowed(self.base_url):
            yield self.base_url, api_name
        if self.backup_url and self.backup_url != self.base_url and self._url_allowed(self.backup_url):
            # The backup gateway spells API names with dashes (trade-cal).
            yield self.backup_url, api_name.replace("_", "-")

    def _request(self, base: str, path: str, params: dict[str, Any]) -> pd.DataFrame:
        paginate = "offset" not in params and "limit" not in params
        frames: list[pd.DataFrame] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            page_params = dict(params)
            if offset:
                # Both gateways require limit whenever offset is set.
                page_params["offset"] = offset
                page_params.setdefault("limit", _PAGE_LIMIT)
            frame, has_more = self._request_page(base, path, page_params)
            frames.append(frame)
            if not (paginate and has_more and len(frame)):
                break
            offset += len(frame)
        else:
            raise TushareRestError(f"{path}: more than {_MAX_PAGES} pages")
        if len(frames) == 1:
            return frames[0]
        return pd.concat(frames, ignore_index=True)

    def _request_page(
        self, base: str, path: str, params: dict[str, Any]
    ) -> tuple[pd.DataFrame, bool]:
        _rate_limiter.wait(self.max_per_minute)
        try:
            resp = self._session.get(
                f"{base}/{path}",
                params=params,
                headers={"X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TushareRestError(f"{type(exc).__name__}: {exc}", transient=True) from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        message = str(payload.get("msg") or payload.get("error") or resp.reason or "").strip()
        if resp.status_code >= 500 or resp.status_code == 429:
            retry_after = _retry_after_seconds(getattr(resp, "headers", None))
            raise TushareRestError(
                f"HTTP {resp.status_code} {message}",
                transient=True,
                retry_after=retry_after,
            )
        if resp.status_code >= 400:
            raise TushareRestError(f"HTTP {resp.status_code} {message}")
        code = payload.get("code")
        if payload.get("ok") is False or code not in (0, "0", None):
            raise TushareRestError(f"code={code} {message}")

        data = payload.get("data") or {}
        fields = list(data.get("fields") or [])
        items = data.get("items") or []
        return pd.DataFrame(items, columns=fields or None), bool(data.get("has_more"))

    def query(self, api_name: str, fields: str = "", **params: Any) -> pd.DataFrame:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if fields:
            clean["fields"] = fields
        errors: list[str] = []
        endpoints = list(self._endpoints(api_name))
        if not endpoints:
            raise TushareRestError(f"tushare {api_name} failed: no HTTPS endpoint")
        for base, path in endpoints:
            for attempt in range(self.retries):
                try:
                    return self._request(base, path, clean)
                except TushareRestError as exc:
                    errors.append(f"{base}/{path}: {exc}")
                    if not exc.transient:
                        break
                    if attempt + 1 < self.retries:
                        time.sleep(_retry_delay(exc, attempt))
            logger.warning("Tushare REST %s failed on %s; trying next endpoint", api_name, base)
        raise TushareRestError(f"tushare {api_name} failed: " + " | ".join(errors))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return functools.partial(self.query, name)


def _retry_after_seconds(headers: Any) -> float | None:
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _retry_delay(exc: TushareRestError, attempt: int) -> float:
    # Relay guidance: exponential backoff of 1, 2, 4 seconds on 429/502/503/504.
    delay = exc.retry_after if exc.retry_after is not None else float(2 ** attempt)
    return min(max(float(delay), 0.0), 8.0)
