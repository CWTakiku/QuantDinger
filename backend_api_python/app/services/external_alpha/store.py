from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.services.external_alpha.symbols import canonicalize_cnstock_key, cnstock_name_lookup_candidates
from app.utils.db import get_db_connection

DEFAULT_SOURCE = "external"
DEFAULT_VERSION = "default"


def _as_date(value: date | str | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def resolve_cnstock_names(symbols: list[str]) -> dict[str, str]:
    """Map canonical / raw CNStock keys to display names via ``qd_market_symbols``."""
    if not symbols:
        return {}
    candidate_to_keys: dict[str, list[str]] = {}
    for raw in symbols:
        key = str(raw or "").strip()
        if not key:
            continue
        for cand in cnstock_name_lookup_candidates(key):
            candidate_to_keys.setdefault(cand, []).append(key)

    if not candidate_to_keys:
        return {}

    cand_list = sorted(candidate_to_keys.keys())
    placeholders = ",".join(["?"] * len(cand_list))
    name_by_cand: dict[str, str] = {}
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                f"""
                SELECT symbol, name
                FROM qd_market_symbols
                WHERE market = 'CNStock'
                  AND UPPER(symbol) IN ({placeholders})
                  AND name IS NOT NULL AND name <> ''
                """,
                tuple(cand_list),
            )
            for row in cur.fetchall() or []:
                sym = str(row.get("symbol") or "").strip().upper()
                name = str(row.get("name") or "").strip()
                if sym and name and sym not in name_by_cand:
                    name_by_cand[sym] = name
    except Exception:
        return {}

    out: dict[str, str] = {}
    for cand, keys in candidate_to_keys.items():
        name = name_by_cand.get(cand)
        if not name:
            continue
        for key in keys:
            out.setdefault(key, name)
            canon = canonicalize_cnstock_key(key)
            if canon:
                out.setdefault(canon, name)
    return out


def _attach_names(preview_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = resolve_cnstock_names([str(r.get("symbol") or "") for r in preview_rows])
    for row in preview_rows:
        sym = str(row.get("symbol") or "")
        row["name"] = names.get(sym) or names.get(canonicalize_cnstock_key(sym)) or ""
    return preview_rows


_REQUIRED_CSV_FIELDS = ("as_of", "symbol", "score")


def rows_from_csv_text(
    text: str,
    *,
    default_source: str = DEFAULT_SOURCE,
    default_version: str = DEFAULT_VERSION,
    default_universe: str = "",
) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV must include header row with as_of,symbol,score")
    headers = {h.strip().lower() for h in reader.fieldnames if h}
    missing = [f for f in _REQUIRED_CSV_FIELDS if f not in headers]
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(missing)}")

    def _col(row: dict[str, str], name: str) -> str:
        for key, val in row.items():
            if key and key.strip().lower() == name:
                return str(val or "").strip()
        return ""

    rows: list[dict[str, Any]] = []
    for raw in reader:
        if not any(str(v or "").strip() for v in raw.values()):
            continue
        row: dict[str, Any] = {
            "as_of": _col(raw, "as_of"),
            "symbol": _col(raw, "symbol"),
            "score": _col(raw, "score"),
            "source": _col(raw, "source") or default_source,
            "version": _col(raw, "version") or default_version,
            "universe": _col(raw, "universe") or default_universe,
        }
        weight = _col(raw, "weight")
        if weight:
            row["weight"] = weight
        rows.append(row)
    return rows


_UPSERT_SQL = """
INSERT INTO qd_external_alpha_scores
(as_of, source, version, universe, symbol, score, weight, meta_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb)
ON CONFLICT (as_of, source, version, symbol)
DO UPDATE SET
  score = EXCLUDED.score,
  weight = EXCLUDED.weight,
  universe = EXCLUDED.universe,
  meta_json = EXCLUDED.meta_json,
  ingested_at = NOW()
"""

_PERSIST_BATCH_SIZE = 2000


def _flush_score_rows(cur: Any, params_list: list[tuple[Any, ...]]) -> None:
    """Batch upsert; prefer raw executemany to avoid per-row RETURNING/savepoint overhead."""
    if not params_list:
        return
    raw = getattr(cur, "_cursor", None)
    if raw is not None and hasattr(raw, "executemany"):
        sql_pg = _UPSERT_SQL.replace("?", "%s")
        for i in range(0, len(params_list), _PERSIST_BATCH_SIZE):
            raw.executemany(sql_pg, params_list[i : i + _PERSIST_BATCH_SIZE])
        return
    for params in params_list:
        cur.execute(_UPSERT_SQL, params)


def persist_external_alpha_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inserted = 0
    skipped = 0
    errors: list[str] = []
    params_list: list[tuple[Any, ...]] = []
    with get_db_connection() as db:
        cur = db.cursor()
        for idx, raw in enumerate(rows or []):
            try:
                as_of = _as_date(raw.get("as_of"))
                symbol = canonicalize_cnstock_key(str(raw.get("symbol") or ""))
                score = float(raw.get("score"))
                weight = raw.get("weight")
                weight_f = float(weight) if weight is not None and weight != "" else None
            except Exception as exc:
                skipped += 1
                errors.append(f"row{idx}: {exc}")
                continue
            if not symbol or score != score or score == float("inf") or score == float("-inf"):
                skipped += 1
                errors.append(f"row{idx}: invalid symbol/score")
                continue
            if weight_f is not None and (
                weight_f != weight_f or weight_f == float("inf") or weight_f == float("-inf")
            ):
                skipped += 1
                errors.append(f"row{idx}: invalid weight")
                continue
            source = str(raw.get("source") or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
            version = str(raw.get("version") or DEFAULT_VERSION).strip() or DEFAULT_VERSION
            universe = str(raw.get("universe") or "").strip()
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            params_list.append(
                (
                    as_of,
                    source,
                    version,
                    universe,
                    symbol,
                    score,
                    weight_f,
                    json.dumps(meta, ensure_ascii=False),
                )
            )
            inserted += 1
        _flush_score_rows(cur, params_list)
        db.commit()
    return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


def list_external_alpha_panels(*, source: str | None = None) -> list[dict[str, Any]]:
    """List distinct (source, version) panels with coverage stats for UI selects."""
    source_filter = str(source or "").strip()
    with get_db_connection() as db:
        cur = db.cursor()
        if source_filter:
            cur.execute(
                """
                SELECT source, version,
                       MIN(as_of) AS as_of_min,
                       MAX(as_of) AS as_of_max,
                       COUNT(*) AS row_count
                FROM qd_external_alpha_scores
                WHERE source = ?
                GROUP BY source, version
                ORDER BY MAX(as_of) DESC, version DESC
                """,
                (source_filter,),
            )
        else:
            cur.execute(
                """
                SELECT source, version,
                       MIN(as_of) AS as_of_min,
                       MAX(as_of) AS as_of_max,
                       COUNT(*) AS row_count
                FROM qd_external_alpha_scores
                GROUP BY source, version
                ORDER BY MAX(as_of) DESC, source ASC, version DESC
                """
            )
        rows = list(cur.fetchall() or [])
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "source": str(row.get("source") or ""),
                "version": str(row.get("version") or ""),
                "as_of_min": str(row.get("as_of_min") or ""),
                "as_of_max": str(row.get("as_of_max") or ""),
                "row_count": int(row.get("row_count") or 0),
            }
        )
    return out


def list_external_alpha_as_ofs(*, source: str, version: str) -> list[str]:
    """Distinct as_of dates for a panel, newest first."""
    source_s = str(source or "").strip()
    version_s = DEFAULT_VERSION if version is None or str(version).strip() == "" else str(version).strip()
    if not source_s:
        return []
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT DISTINCT as_of
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ?
            ORDER BY as_of DESC
            """,
            (source_s, version_s),
        )
        rows = list(cur.fetchall() or [])
    return [str(r.get("as_of") or "")[:10] for r in rows if r.get("as_of")]


def preview_external_alpha_scores(
    *,
    source: str,
    version: str,
    as_of: date | str | None = None,
    limit: int = 50,
    order: str = "desc",
) -> dict[str, Any]:
    """Return ranked score rows for one as_of (default = latest), plus panel dates."""
    source_s = str(source or "").strip()
    version_s = DEFAULT_VERSION if version is None or str(version).strip() == "" else str(version).strip()
    limit_n = max(1, min(int(limit or 50), 500))
    order_s = "ASC" if str(order or "").strip().lower() in {"asc", "bottom", "low"} else "DESC"
    if not source_s:
        return {"as_of": "", "as_of_dates": [], "rows": [], "stats": {}, "total": 0, "order": "desc"}

    as_of_dates = list_external_alpha_as_ofs(source=source_s, version=version_s)
    if not as_of_dates:
        return {"as_of": "", "as_of_dates": [], "rows": [], "stats": {}, "total": 0, "order": "desc"}

    requested_as_of = ""
    if as_of:
        requested_as_of = str(_as_date(as_of))
        eff = requested_as_of
        # Exact panel day preferred; otherwise PIT-snap to newest as_of <= request
        # (weekends / beyond-calendar dates share strategy runtime semantics).
        if eff not in as_of_dates:
            candidates = [d for d in as_of_dates if d and d <= eff]
            if candidates:
                eff = max(candidates)
    else:
        eff = as_of_dates[0]

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT symbol, score
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ? AND as_of = ?
            ORDER BY score {order_s}
            """,
            (source_s, version_s, eff),
        )
        rows = list(cur.fetchall() or [])

    scored: list[tuple[str, float]] = []
    for row in rows:
        key = canonicalize_cnstock_key(str(row.get("symbol") or ""))
        if not key:
            continue
        try:
            val = float(row["score"])
        except (TypeError, ValueError):
            continue
        if val != val or val in (float("inf"), float("-inf")):
            continue
        scored.append((key, val))

    # Stats always over full cross-section (desc order).
    all_desc = sorted(scored, key=lambda x: x[1], reverse=True)
    values = [v for _, v in all_desc]
    stats: dict[str, Any] = {"n": len(values)}
    if values:
        stats["mean"] = float(sum(values) / len(values))
        stats["min"] = float(min(values))
        stats["max"] = float(max(values))
        mid = len(values) // 2
        stats["median"] = float(values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2)

    if order_s == "ASC":
        scored_view = list(reversed(all_desc))  # low → high
        preview_slice = scored_view[:limit_n]
        preview_rows = [
            {"rank": len(all_desc) - i, "symbol": sym, "score": score}
            for i, (sym, score) in enumerate(preview_slice)
        ]
    else:
        preview_slice = all_desc[:limit_n]
        preview_rows = [
            {"rank": i + 1, "symbol": sym, "score": score}
            for i, (sym, score) in enumerate(preview_slice)
        ]

    preview_rows = _attach_names(preview_rows)

    result: dict[str, Any] = {
        "source": source_s,
        "version": version_s,
        "as_of": str(eff)[:10],
        "as_of_dates": as_of_dates,
        "rows": preview_rows,
        "stats": stats,
        "total": len(all_desc),
        "limit": limit_n,
        "order": "asc" if order_s == "ASC" else "desc",
    }
    if requested_as_of and requested_as_of != str(eff)[:10]:
        result["requested_as_of"] = requested_as_of
        result["pit_snapped"] = True
    return result


def load_external_alpha_scores_as_of(
    as_of: date | str,
    *,
    source: str,
    version: str | None = None,
    symbols: list[str] | None = None,
) -> pd.Series:
    as_of_d = _as_date(as_of)
    source_s = str(source or "").strip()
    if not source_s:
        return pd.Series(dtype=float)
    version_s = DEFAULT_VERSION if version is None or str(version).strip() == "" else str(version).strip()
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT MAX(as_of) AS as_of
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ? AND as_of <= ?
            """,
            (source_s, version_s, as_of_d),
        )
        hit = cur.fetchone() or {}
        eff = hit.get("as_of")
        if not eff:
            return pd.Series(dtype=float)
        cur.execute(
            """
            SELECT symbol, score
            FROM qd_external_alpha_scores
            WHERE source = ? AND version = ? AND as_of = ?
            """,
            (source_s, version_s, eff),
        )
        rows = list(cur.fetchall() or [])
    data: dict[str, float] = {}
    wanted = None
    if symbols:
        wanted = {canonicalize_cnstock_key(s) for s in symbols if canonicalize_cnstock_key(s)}
    for row in rows:
        key = canonicalize_cnstock_key(str(row["symbol"]))
        if not key:
            continue
        if wanted is not None and key not in wanted:
            continue
        try:
            val = float(row["score"])
        except (TypeError, ValueError):
            continue
        if val == val and val not in (float("inf"), float("-inf")):
            data[key] = val
    return pd.Series(data, dtype=float)
