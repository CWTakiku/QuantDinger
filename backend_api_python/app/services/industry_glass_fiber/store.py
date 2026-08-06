from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app.utils.db import get_db_connection

SOURCE_PRIORITY = ("manual", "zhuochuang", "oilchem", "public_news")
MIN_CONFIDENCE = 0.5

_UPSERT_SQL = """
INSERT INTO qd_industry_glass_fiber_weekly
(as_of, cloth_7628_mid, yarn_2400_mid, cloth_trend, inventory_trend,
 new_capacity_flag, source, confidence, raw_refs)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
ON CONFLICT (as_of, source) DO UPDATE SET
  cloth_7628_mid = EXCLUDED.cloth_7628_mid,
  yarn_2400_mid = EXCLUDED.yarn_2400_mid,
  cloth_trend = EXCLUDED.cloth_trend,
  inventory_trend = EXCLUDED.inventory_trend,
  new_capacity_flag = EXCLUDED.new_capacity_flag,
  confidence = EXCLUDED.confidence,
  raw_refs = EXCLUDED.raw_refs,
  updated_at = NOW()
RETURNING *
"""


def _as_date(value: date | str | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def _parse_json_field(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_db_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    as_of_val = out.get("as_of")
    if hasattr(as_of_val, "isoformat"):
        out["as_of"] = as_of_val.isoformat()[:10]
    else:
        out["as_of"] = str(as_of_val or "")[:10]
    out["cloth_7628_mid"] = out.get("cloth_7628_mid")
    out["yarn_2400_mid"] = out.get("yarn_2400_mid")
    out["cloth_trend"] = int(out.get("cloth_trend") or 0)
    out["inventory_trend"] = int(out.get("inventory_trend") or 0)
    out["new_capacity_flag"] = int(out.get("new_capacity_flag") or 0)
    out["source"] = str(out.get("source") or "")
    out["confidence"] = float(out.get("confidence") or 0.0)
    out["raw_refs"] = _parse_json_field(out.get("raw_refs"))
    return out


def _format_resolve_result(row: dict[str, Any]) -> dict[str, Any]:
    out = _normalize_db_row(row)
    out["industry_available"] = True
    return out


def _priority_index(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def _updated_at_sort_key(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _pick_best_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [r for r in rows if float(r.get("confidence") or 0.0) >= MIN_CONFIDENCE]
    if not valid:
        return None
    valid.sort(
        key=lambda r: (
            _priority_index(str(r.get("source") or "")),
            -_updated_at_sort_key(r.get("updated_at")),
        )
    )
    return valid[0]


def _as_of_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _as_date(str(value))


def _list_as_ofs_on_or_before(requested: date) -> list[date]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT DISTINCT as_of
            FROM qd_industry_glass_fiber_weekly
            WHERE as_of <= ?
            ORDER BY as_of DESC
            """,
            (requested,),
        )
        hits = cur.fetchall() or []
    return [_as_of_date(hit["as_of"]) for hit in hits if hit.get("as_of")]


def _find_effective_as_of(requested: date) -> date | None:
    """Latest as_of <= requested that has at least one confidence-qualified row."""
    for as_of in _list_as_ofs_on_or_before(requested):
        if _pick_best_row(_load_rows_for_as_of(as_of)):
            return as_of
    return None


def _load_rows_for_as_of(as_of: date) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT as_of, cloth_7628_mid, yarn_2400_mid, cloth_trend, inventory_trend,
                   new_capacity_flag, source, confidence, raw_refs, updated_at
            FROM qd_industry_glass_fiber_weekly
            WHERE as_of = ?
            """,
            (as_of,),
        )
        return list(cur.fetchall() or [])


def _upsert_row(row: dict[str, Any]) -> dict[str, Any]:
    as_of = _as_date(row["as_of"])
    cloth_7628 = row.get("cloth_7628_mid")
    cloth_7628_f = float(cloth_7628) if cloth_7628 is not None and cloth_7628 != "" else None
    yarn_2400 = row.get("yarn_2400_mid")
    yarn_2400_f = float(yarn_2400) if yarn_2400 is not None and yarn_2400 != "" else None
    cloth_trend = int(row["cloth_trend"])
    inventory_trend = int(row["inventory_trend"])
    new_capacity_flag = int(row.get("new_capacity_flag") or 0)
    source = str(row["source"]).strip()
    confidence = float(row.get("confidence", MIN_CONFIDENCE))
    raw_refs = row.get("raw_refs") if isinstance(row.get("raw_refs"), dict) else {}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            _UPSERT_SQL,
            (
                as_of,
                cloth_7628_f,
                yarn_2400_f,
                cloth_trend,
                inventory_trend,
                new_capacity_flag,
                source,
                confidence,
                json.dumps(raw_refs, ensure_ascii=False),
            ),
        )
        saved = cur.fetchone()
        db.commit()
    if not saved:
        raise RuntimeError("upsert_glass_fiber_week: insert returned no row")
    return _normalize_db_row(saved)


def upsert_glass_fiber_week(row: dict[str, Any]) -> dict[str, Any]:
    return _upsert_row(row)


def resolve_glass_fiber_week(as_of: date | str) -> dict[str, Any] | None:
    requested = _as_date(as_of)
    effective = _find_effective_as_of(requested)
    if not effective:
        return None
    rows = _load_rows_for_as_of(effective)
    best = _pick_best_row(rows)
    if not best:
        return None
    return _format_resolve_result(best)
