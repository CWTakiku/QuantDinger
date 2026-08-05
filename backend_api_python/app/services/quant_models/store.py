from __future__ import annotations

import json
import re
from typing import Any

from app.utils.db import get_db_connection

_VALID_KINDS = frozenset({"model", "factor"})


def _default_model_key(session_id: str, loop_index: int, kind: str) -> str:
    raw = f"{session_id}_loop{int(loop_index)}_{kind}"
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(raw).strip()).strip("_")
    return slug[:80]


def _serialize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    out = dict(row)
    for key in ("published_at", "created_at", "updated_at"):
        val = out.get(key)
        if val is not None and hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    for key in ("provenance_json", "metrics_json"):
        val = out.get(key)
        if isinstance(val, str):
            try:
                out[key] = json.loads(val)
            except json.JSONDecodeError:
                pass
    return out


def publish_quant_model(
    *,
    display_name: str,
    kind: str,
    session_id: str,
    loop_index: int,
    universe: str,
    owner_user_id: int,
    model_key: str | None = None,
    alpha_source: str = "rdagent",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind_s = str(kind or "").strip().lower()
    if kind_s not in _VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(_VALID_KINDS)}")

    key = str(model_key or "").strip() or _default_model_key(session_id, loop_index, kind_s)
    alpha_version = f"qm_{key}"
    provenance = {
        "session_id": str(session_id),
        "loop_index": int(loop_index),
        "mode": kind_s,
    }
    metrics_json = metrics if isinstance(metrics, dict) else {}

    with get_db_connection() as db:
        cur = db.cursor()
        # Same session/loop/kind re-publish keeps model_key stable (strategies keep working).
        cur.execute(
            """
            INSERT INTO qd_quant_models
            (model_key, display_name, status, kind, alpha_source, alpha_version,
             universe, owner_user_id, provenance_json, metrics_json, published_at)
            VALUES (?, ?, 'published', ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb, NOW())
            ON CONFLICT (model_key) DO UPDATE SET
              display_name = EXCLUDED.display_name,
              status = 'published',
              kind = EXCLUDED.kind,
              alpha_source = EXCLUDED.alpha_source,
              alpha_version = EXCLUDED.alpha_version,
              universe = EXCLUDED.universe,
              owner_user_id = EXCLUDED.owner_user_id,
              provenance_json = EXCLUDED.provenance_json,
              metrics_json = EXCLUDED.metrics_json,
              published_at = NOW(),
              updated_at = NOW()
            RETURNING *
            """,
            (
                key,
                str(display_name or "").strip(),
                kind_s,
                str(alpha_source or "rdagent").strip() or "rdagent",
                alpha_version,
                str(universe or "csi300").strip() or "csi300",
                int(owner_user_id) if owner_user_id is not None else None,
                json.dumps(provenance, ensure_ascii=False),
                json.dumps(metrics_json, ensure_ascii=False),
            ),
        )
        row = cur.fetchone()
        db.commit()
    out = _serialize_row(row)
    if not out:
        raise RuntimeError("publish_quant_model: insert returned no row")
    return out


def list_quant_models(
    *,
    status: str = "published",
    owner_user_id: int | None = None,
) -> list[dict[str, Any]]:
    where = ["status = ?"]
    params: list[Any] = [str(status or "published").strip() or "published"]
    if owner_user_id is not None:
        where.append("owner_user_id = ?")
        params.append(int(owner_user_id))

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT *
            FROM qd_quant_models
            WHERE {' AND '.join(where)}
            ORDER BY published_at DESC NULLS LAST, id DESC
            """,
            tuple(params),
        )
        rows = list(cur.fetchall() or [])
    return [_serialize_row(r) for r in rows if r]


def get_quant_model(model_key: str) -> dict[str, Any] | None:
    key = str(model_key or "").strip()
    if not key:
        return None
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT *
            FROM qd_quant_models
            WHERE model_key = ?
            """,
            (key,),
        )
        row = cur.fetchone()
    return _serialize_row(row)


def archive_quant_model(model_key: str) -> dict[str, Any]:
    key = str(model_key or "").strip()
    if not key:
        raise ValueError("model_key is required")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_quant_models
            SET status = 'archived', updated_at = NOW()
            WHERE model_key = ?
            RETURNING *
            """,
            (key,),
        )
        row = cur.fetchone()
        db.commit()
    out = _serialize_row(row)
    if not out:
        raise ValueError(f"quant model not found: {key}")
    return out


def update_quant_model(
    model_key: str,
    *,
    display_name: str | None = None,
    universe: str | None = None,
) -> dict[str, Any]:
    """Update editable fields only (display_name / universe)."""
    key = str(model_key or "").strip()
    if not key:
        raise ValueError("model_key is required")

    sets: list[str] = []
    params: list[Any] = []
    if display_name is not None:
        name = str(display_name).strip()
        if not name:
            raise ValueError("display_name must not be empty")
        sets.append("display_name = ?")
        params.append(name)
    if universe is not None:
        uni = str(universe).strip() or "csi300"
        sets.append("universe = ?")
        params.append(uni)
    if not sets:
        raise ValueError("no fields to update")

    sets.append("updated_at = NOW()")
    params.append(key)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            UPDATE qd_quant_models
            SET {', '.join(sets)}
            WHERE model_key = ?
            RETURNING *
            """,
            tuple(params),
        )
        row = cur.fetchone()
        db.commit()
    out = _serialize_row(row)
    if not out:
        raise ValueError(f"quant model not found: {key}")
    return out


def delete_quant_model(model_key: str) -> dict[str, Any]:
    """Hard-delete the model row. Does not remove alpha score panels."""
    key = str(model_key or "").strip()
    if not key:
        raise ValueError("model_key is required")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            DELETE FROM qd_quant_models
            WHERE model_key = ?
            RETURNING *
            """,
            (key,),
        )
        row = cur.fetchone()
        db.commit()
    out = _serialize_row(row)
    if not out:
        raise ValueError(f"quant model not found: {key}")
    return out
