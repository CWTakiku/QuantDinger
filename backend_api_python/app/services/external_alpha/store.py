from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from typing import Any

import pandas as pd

from app.markets.cn_stock.symbols import canonicalize_cnstock_key
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


def persist_external_alpha_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inserted = 0
    skipped = 0
    errors: list[str] = []
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
            cur.execute(
                """
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
                """,
                (
                    as_of,
                    source,
                    version,
                    universe,
                    symbol,
                    score,
                    weight_f,
                    json.dumps(meta, ensure_ascii=False),
                ),
            )
            inserted += 1
        db.commit()
    return {"inserted": inserted, "skipped": skipped, "errors": errors[:20]}


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
