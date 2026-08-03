#!/usr/bin/env python3
"""Backfill CSI300 index weights and rebuild csi300 universe membership.

Usage (inside backend container or host with DB + Tushare):

    SKIP_STARTUP_HOOKS=1 python scripts/backfill_csi300_index_weights.py \\
        --start 20150101 --end 20210830

Requires TUSHARE_TOKEN (and optional TUSHARE_HTTP_URL) in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from app.utils.db import get_db_connection  # noqa: E402


CSI300_INDEX = "000300.SH"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _build_pro():
    import tushare as ts

    token = str(os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is not set")
    pro = ts.pro_api(token)
    http_url = str(os.environ.get("TUSHARE_HTTP_URL") or "").strip()
    if http_url:
        pro._DataApi__http_url = http_url
    return pro


def _canonicalize_cn_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if raw.startswith("CNSTOCK:"):
        raw = raw.split(":", 1)[1]
    if raw.endswith(".SH") or raw.endswith(".SZ"):
        return raw
    if raw.isdigit() and len(raw) == 6:
        return f"{raw}.SH" if raw.startswith("6") else f"{raw}.SZ"
    return raw


def _month_windows(start: str, end: str) -> list[tuple[str, str]]:
    """Yield overlapping month windows [YYYYMMDD, YYYYMMDD] covering [start, end]."""
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    windows: list[tuple[str, str]] = []
    y, m = s.year, s.month
    while date(y, m, 1) <= e:
        if m == 12:
            nxt = date(y + 1, 1, 1)
        else:
            nxt = date(y, m + 1, 1)
        w_start = max(s, date(y, m, 1))
        w_end = min(e, nxt.fromordinal(nxt.toordinal() - 1))
        windows.append((w_start.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")))
        y, m = nxt.year, nxt.month
    return windows


def fetch_index_weights(pro: Any, *, start_date: str, end_date: str) -> pd.DataFrame:
    try:
        frame = pro.index_weight(index_code=CSI300_INDEX, start_date=start_date, end_date=end_date)
    except Exception as exc:
        print(f"WARN index_weight {start_date}-{end_date}: {exc}", flush=True)
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    df = frame.copy()
    if "con_code" not in df.columns and "con_ts_code" in df.columns:
        df = df.rename(columns={"con_ts_code": "con_code"})
    keep = [c for c in ("trade_date", "con_code", "weight") if c in df.columns]
    if len(keep) < 3:
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    out = df[keep].copy()
    out["trade_date"] = out["trade_date"].astype(str).str.replace("-", "", regex=False)
    out["con_code"] = out["con_code"].astype(str)
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce")
    return out.dropna(subset=["weight"]).reset_index(drop=True)


def persist_index_weights(frame: pd.DataFrame, *, source: str = "tushare") -> int:
    if frame is None or frame.empty:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in frame.iterrows():
            td = str(row["trade_date"]).replace("-", "")[:8]
            cur.execute(
                """
                INSERT INTO qd_csi300_index_weights (trade_date, con_code, weight, source)
                VALUES (TO_DATE(%s, 'YYYYMMDD'), %s, %s, %s)
                ON CONFLICT (trade_date, con_code, source)
                DO UPDATE SET weight = EXCLUDED.weight, ingested_at = NOW()
                """,
                (td, str(row["con_code"]), float(row["weight"]), source),
            )
        db.commit()
    return int(len(frame))


def _bao_code_to_ts(code: str) -> str:
    raw = str(code or "").strip().lower()
    if raw.startswith("sh.") and len(raw) >= 9:
        return f"{raw[3:]}.SH"
    if raw.startswith("sz.") and len(raw) >= 9:
        return f"{raw[3:]}.SZ"
    return _canonicalize_cn_symbol(raw)


def fetch_baostock_hs300_board(as_of: str) -> pd.DataFrame:
    """Membership-only board via Baostock (equal weight percent). as_of=YYYY-MM-DD."""
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        print(f"WARN baostock login: {lg.error_msg}", flush=True)
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    try:
        rs = bs.query_hs300_stocks(date=as_of)
        rows: list[dict[str, Any]] = []
        while rs.error_code == "0" and rs.next():
            item = dict(zip(rs.fields, rs.get_row_data()))
            con = _bao_code_to_ts(str(item.get("code") or ""))
            update = str(item.get("updateDate") or as_of).replace("-", "")[:8]
            if not con:
                continue
            rows.append({"trade_date": update, "con_code": con, "weight": None})
    finally:
        bs.logout()

    if not rows:
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    # Group by updateDate; equal-weight within each board.
    out_rows: list[dict[str, Any]] = []
    by_td: dict[str, list[str]] = {}
    for row in rows:
        by_td.setdefault(str(row["trade_date"]), []).append(str(row["con_code"]))
    for td, codes in by_td.items():
        uniq = sorted(set(codes))
        w = 100.0 / max(len(uniq), 1)
        for code in uniq:
            out_rows.append({"trade_date": td, "con_code": code, "weight": w})
    return pd.DataFrame(out_rows)


def existing_weight_dates() -> set[str]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT DISTINCT trade_date FROM qd_csi300_index_weights")
        out: set[str] = set()
        for row in cur.fetchall() or []:
            td = row["trade_date"]
            if isinstance(td, datetime):
                td = td.date()
            if isinstance(td, date):
                out.add(td.strftime("%Y%m%d"))
            else:
                out.add(str(td).replace("-", "")[:8])
        return out


def rebuild_membership(*, universe_code: str = "csi300") -> dict[str, Any]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_universes WHERE code = ?", (universe_code,))
        uni = cur.fetchone()
        if not uni:
            raise ValueError(f"universe not found: {universe_code}")
        universe_id = int(uni["id"])

        cur.execute(
            """
            SELECT trade_date, con_code, weight
            FROM qd_csi300_index_weights
            ORDER BY trade_date ASC, con_code ASC
            """
        )
        raw_rows = list(cur.fetchall() or [])
        if not raw_rows:
            return {
                "universe_id": universe_id,
                "boards": 0,
                "members_inserted": 0,
            }

        # Prefer higher weight when the same symbol appears from multiple sources.
        by_date: dict[date, dict[str, float]] = {}
        for row in raw_rows:
            td = row["trade_date"]
            if isinstance(td, datetime):
                td = td.date()
            elif not isinstance(td, date):
                td = date.fromisoformat(str(td)[:10])
            code = _canonicalize_cn_symbol(str(row["con_code"] or ""))
            if not code:
                continue
            try:
                weight_pct = float(row["weight"])
            except (TypeError, ValueError):
                continue
            if weight_pct <= 0 or weight_pct != weight_pct:
                continue
            board = by_date.setdefault(td, {})
            prev = board.get(code)
            if prev is None or weight_pct > prev:
                board[code] = weight_pct

        dates = sorted(by_date)
        cur.execute("DELETE FROM qd_universe_members WHERE universe_id = ?", (universe_id,))
        inserted = 0
        version = f"index_weight_pit:{dates[0].isoformat()}:{dates[-1].isoformat()}"
        for idx, td in enumerate(dates):
            next_td = dates[idx + 1] if idx + 1 < len(dates) else None
            board = sorted(by_date[td].items(), key=lambda item: (-item[1], item[0]))
            for rank, (code, weight_pct) in enumerate(board, start=1):
                cur.execute(
                    """
                    INSERT INTO qd_universe_members (
                        universe_id, market, symbol, name, market_type,
                        valid_from, valid_to, member_weight, member_rank,
                        source_version, metadata_json
                    )
                    VALUES (
                        ?, 'CNStock', ?, '', 'spot',
                        ?, ?, ?, ?,
                        ?, '{}'::jsonb
                    )
                    """,
                    (
                        universe_id,
                        code,
                        td,
                        next_td,
                        float(weight_pct) / 100.0,
                        int(rank),
                        version,
                    ),
                )
                inserted += 1
        db.commit()

    return {
        "universe_id": universe_id,
        "universe_code": universe_code,
        "boards": len(dates),
        "members_inserted": inserted,
        "first_trade_date": dates[0].isoformat(),
        "last_trade_date": dates[-1].isoformat(),
    }


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20150101", help="YYYYMMDD inclusive")
    parser.add_argument("--end", default="20210830", help="YYYYMMDD inclusive")
    parser.add_argument("--sleep", type=float, default=0.35, help="Seconds between API calls")
    parser.add_argument("--skip-fetch", action="store_true", help="Only rebuild membership")
    parser.add_argument("--skip-rebuild", action="store_true", help="Only fetch weights")
    parser.add_argument(
        "--baostock-until",
        default="20151231",
        help="Also fill membership-only boards via Baostock up to YYYYMMDD "
        "(for months missing on Tushare mirror, typically 2015)",
    )
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "start": args.start,
        "end": args.end,
        "windows": 0,
        "rows_upserted": 0,
        "empty_windows": 0,
        "baostock_rows": 0,
        "baostock_boards": 0,
    }

    def _maybe_rewrite_db_host() -> None:
        """Rewrite docker DNS host only when running on the host machine."""
        db_url = str(os.environ.get("DATABASE_URL") or "")
        if "@postgres:" not in db_url:
            return
        if Path("/.dockerenv").exists():
            return
        os.environ["DATABASE_URL"] = db_url.replace("@postgres:", "@127.0.0.1:")

    _maybe_rewrite_db_host()

    if not args.skip_fetch:
        pro = _build_pro()
        windows = _month_windows(args.start, args.end)
        summary["windows"] = len(windows)
        for i, (w_start, w_end) in enumerate(windows, start=1):
            frame = fetch_index_weights(pro, start_date=w_start, end_date=w_end)
            n = persist_index_weights(frame, source="tushare")
            summary["rows_upserted"] += n
            if n == 0:
                summary["empty_windows"] += 1
            dates = sorted(set(frame["trade_date"].tolist())) if n else []
            print(
                f"[tushare {i}/{len(windows)}] {w_start}-{w_end}: rows={n} boards={dates}",
                flush=True,
            )
            time.sleep(max(0.0, float(args.sleep)))

        # Fill pre-Tushare gap (e.g. 2015) with Baostock membership snapshots.
        if args.baostock_until:
            known = existing_weight_dates()
            bao_windows = _month_windows(args.start, args.baostock_until)
            for i, (w_start, w_end) in enumerate(bao_windows, start=1):
                as_of = f"{w_end[:4]}-{w_end[4:6]}-{w_end[6:8]}"
                frame = fetch_baostock_hs300_board(as_of)
                if frame.empty:
                    print(f"[baostock {i}/{len(bao_windows)}] {as_of}: empty", flush=True)
                    continue
                # Skip boards whose trade_date already has Tushare weights.
                keep_dates = [d for d in sorted(set(frame["trade_date"].tolist())) if d not in known]
                if not keep_dates:
                    print(
                        f"[baostock {i}/{len(bao_windows)}] {as_of}: skipped (tushare exists)",
                        flush=True,
                    )
                    continue
                frame = frame[frame["trade_date"].isin(keep_dates)]
                n = persist_index_weights(frame, source="baostock")
                summary["baostock_rows"] += n
                summary["baostock_boards"] += len(keep_dates)
                known.update(keep_dates)
                print(
                    f"[baostock {i}/{len(bao_windows)}] {as_of}: rows={n} boards={keep_dates}",
                    flush=True,
                )
                time.sleep(max(0.0, float(args.sleep)))

    if not args.skip_rebuild:
        rebuilt = rebuild_membership()
        summary["rebuild"] = rebuilt

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    rebuild = summary.get("rebuild") or {}
    if args.skip_rebuild:
        return 0 if summary["rows_upserted"] > 0 or args.skip_fetch else 1
    return 0 if int(rebuild.get("members_inserted") or 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
