"""Tushare-compatible REST gateway client.

The gateway exposes ``GET {base}/{api_name}?<params>`` with an ``X-API-Key``
header and answers ``{"code": 0, "data": {"fields": [...], "items": [...]}}``.
``TushareRestPro`` mimics the ``tushare.pro_api()`` call style
(``pro.daily(ts_code=...)`` / ``pro.query("daily", ...)``) and returns
DataFrames, so existing callers keep working unchanged.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Iterable

import pandas as pd
import requests

from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_REST_URL = "https://tl.kaixin8.top/tushare/pro"
DEFAULT_BACKUP_URL = "http://datahubco.com/app-api/openapi/v1/tushare"
_MAX_PAGES = 50


class TushareRestError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class TushareRestPro:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_REST_URL,
        backup_url: str = DEFAULT_BACKUP_URL,
        timeout: float = 30.0,
        retries: int = 2,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.backup_url = str(backup_url or "").strip().rstrip("/")
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self._session = session or requests.Session()

    def _endpoints(self, api_name: str) -> Iterable[tuple[str, str]]:
        if self.base_url:
            yield self.base_url, api_name
        if self.backup_url and self.backup_url != self.base_url:
            # The backup gateway spells API names with dashes (trade-cal).
            yield self.backup_url, api_name.replace("_", "-")

    def _request(self, base: str, path: str, params: dict[str, Any]) -> pd.DataFrame:
        paginate = "offset" not in params and "limit" not in params
        frames: list[pd.DataFrame] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            page_params = dict(params, offset=offset) if offset else params
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
            raise TushareRestError(f"HTTP {resp.status_code} {message}", transient=True)
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
        for base, path in self._endpoints(api_name):
            for attempt in range(self.retries):
                try:
                    return self._request(base, path, clean)
                except TushareRestError as exc:
                    errors.append(f"{base}/{path}: {exc}")
                    if not exc.transient:
                        break
                    if attempt + 1 < self.retries:
                        time.sleep(0.5 * (attempt + 1))
            logger.warning("Tushare REST %s failed on %s; trying next endpoint", api_name, base)
        raise TushareRestError(f"tushare {api_name} failed: " + " | ".join(errors))

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return functools.partial(self.query, name)
