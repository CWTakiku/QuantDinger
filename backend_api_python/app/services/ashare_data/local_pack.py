"""Offline Tushare per-API archive (``<api>/<api>.tar.gz`` of per-date Parquet).

Feeds :func:`sync_trade_date` with the same frames the live API returns, so
validation and upserts are shared with the online sync.
"""

from __future__ import annotations

import re
import tarfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from app.services.ashare_data.rows import parse_ymd
from app.utils.logger import get_logger

logger = get_logger(__name__)

PACK_APIS = ("daily", "stk_limit", "suspend_d", "daily_basic", "stock_st", "adj_factor")
_FILE_DATE = re.compile(r"trade_date=(\d{8})")


def extract_pack(archive_root: Path, dest: Path, apis: Iterable[str] = PACK_APIS) -> list[str]:
    """Extract ``<api>.tar.gz`` into ``dest/<api>/data`` unless already present."""
    extracted = []
    for api in apis:
        if (dest / api / "data").is_dir():
            continue
        archive = archive_root / api / f"{api}.tar.gz"
        if not archive.is_file():
            raise FileNotFoundError(archive)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")
        extracted.append(api)
    return extracted


def _files_in_range(api_dir: Path, start: date, end: date) -> list[Path]:
    files = sorted((api_dir / "data").glob("*.parquet"))
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    selected = []
    for path in files:
        match = _FILE_DATE.search(path.name)
        if match is None or lo <= match.group(1) <= hi:
            selected.append(path)
    return selected


def load_api_range(pack_root: Path, api: str, start: date, end: date) -> pd.DataFrame:
    files = _files_in_range(pack_root / api, start, end)
    if not files:
        return pd.DataFrame()
    frame = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    if frame.empty or "trade_date" not in frame.columns:
        return pd.DataFrame()
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace("-", "").str[:8]
    lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    return frame[(frame["trade_date"] >= lo) & (frame["trade_date"] <= hi)]


def pack_has(pack_root: Path, api: str) -> bool:
    return (pack_root / api / "data").is_dir()


class PackFetcher:
    """Per-day frames for one date window, loaded once per window.

    APIs absent from the pack are pulled per day through ``remote`` (the
    Tushare client), so a partial download still yields complete days.
    """

    def __init__(
        self,
        pack_root: Path,
        start: date,
        end: date,
        apis: Iterable[str] = PACK_APIS,
        remote: Callable[[str, date], pd.DataFrame] | None = None,
    ) -> None:
        self._by_api: dict[str, dict[str, pd.DataFrame]] = {}
        self._remote_apis: list[str] = []
        self._remote = remote
        for api in apis:
            if not pack_has(pack_root, api):
                if remote is None:
                    raise FileNotFoundError(f"{api} missing from pack and no remote fetcher")
                self._remote_apis.append(api)
                continue
            frame = load_api_range(pack_root, api, start, end)
            self._by_api[api] = (
                {key: group for key, group in frame.groupby("trade_date")} if not frame.empty else {}
            )

    @property
    def remote_apis(self) -> list[str]:
        return list(self._remote_apis)

    def trade_dates(self) -> list[date]:
        return sorted(d for d in (parse_ymd(k) for k in self._by_api.get("daily", {})) if d is not None)

    def __call__(self, day: date) -> dict[str, pd.DataFrame]:
        key = day.strftime("%Y%m%d")
        frames = {api: groups.get(key, pd.DataFrame()) for api, groups in self._by_api.items()}
        if self._remote_apis:
            with ThreadPoolExecutor(max_workers=len(self._remote_apis)) as pool:
                futures = {api: pool.submit(self._remote, api, day) for api in self._remote_apis}
                frames.update({api: future.result() for api, future in futures.items()})
        return frames


def import_local_pack(
    pack_root: Path,
    start: date,
    end: date,
    *,
    resume: bool = True,
    rebuild_adjustment: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Load ``qd_ashare_daily_bar`` from an extracted pack, one calendar year at a time.

    Pass ``rebuild_adjustment=False`` when several imports run concurrently and
    rebuild once afterwards; concurrent full-table rebuilds contend for locks.
    """
    from app.services.ashare_data.sync import (
        BARS_DATASET,
        _days_with_status,
        _fetch_dataset,
        _pro,
        load_st_intervals,
        rebuild_adj_factors,
        sync_trade_date,
    )

    pro = None
    if not all(pack_has(pack_root, api) for api in PACK_APIS if api != "adj_factor"):
        pro = _pro()
    remote = (lambda api, day: _fetch_dataset(pro, api, day)) if pro is not None else None
    intervals = load_st_intervals()
    done = _days_with_status(start, end, ("ok",)) if resume else set()
    summary: dict[str, Any] = {"days": 0, "ok": 0, "partial": [], "failed": [], "skipped": 0}
    for year in range(start.year, end.year + 1):
        window_start = max(start, date(year, 1, 1))
        window_end = min(end, date(year, 12, 31))
        fetcher = PackFetcher(
            pack_root,
            window_start,
            window_end,
            apis=[a for a in PACK_APIS if a != "adj_factor"],
            remote=remote,
        )
        summary["remote_apis"] = fetcher.remote_apis
        days = fetcher.trade_dates()
        summary["days"] += len(days)
        for day in days:
            if day in done:
                summary["skipped"] += 1
                continue
            result = sync_trade_date(
                day,
                st_intervals=intervals,
                max_attempts=2 if remote is not None else 1,
                retry_sleep=10.0,
                fetcher=fetcher,
            )
            if result["status"] == "ok":
                summary["ok"] += 1
            else:
                summary[result["status"]].append(day.isoformat())
            if progress is not None:
                progress(result)
    if rebuild_adjustment:
        summary["adj_updated"] = rebuild_adj_factors()
    summary["dataset"] = BARS_DATASET
    return summary


def compare_adj_factors(pack_root: Path, start: date, end: date) -> dict[str, Any]:
    """Check derived factors against Tushare's: per stock the ratio must be constant."""
    from app.utils.db import get_db_connection

    official = load_api_range(pack_root, "adj_factor", start, end)[["ts_code", "trade_date", "adj_factor"]]
    official = official.rename(columns={"adj_factor": "official"})
    official["trade_date"] = pd.to_datetime(official["trade_date"]).dt.date
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT ts_code, trade_date, adj_factor FROM qd_ashare_daily_bar WHERE trade_date BETWEEN ? AND ?",
            (start, end),
        )
        ours = pd.DataFrame(cur.fetchall() or [], columns=["ts_code", "trade_date", "adj_factor"])
        cur.close()
    merged = ours.merge(official, on=["ts_code", "trade_date"], how="inner").dropna()
    merged = merged[merged["official"] > 0]
    merged["ratio"] = merged["adj_factor"] / merged["official"]
    spread = merged.groupby("ts_code")["ratio"].agg(lambda s: s.max() / s.min() - 1.0)
    return {
        "rows": int(len(merged)),
        "stocks": int(spread.size),
        "stocks_drift_gt_0.5pct": int((spread > 0.005).sum()),
        "stocks_drift_gt_2pct": int((spread > 0.02).sum()),
        "worst": spread.sort_values(ascending=False).head(10).round(4).to_dict(),
    }
