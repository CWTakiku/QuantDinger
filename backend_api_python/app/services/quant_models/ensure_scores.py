"""Ensure external alpha score panels exist for quant model as_of dates."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app.services.external_alpha.store import list_external_alpha_as_ofs
from app.services.rdagent_bridge.import_session import infer_and_import_session_scores


def _normalize_as_of(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()[:10]


def _normalize_as_ofs(as_ofs: list[date]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in as_ofs:
        normalized = _normalize_as_of(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return sorted(out)


def ensure_quant_model_scores(
    model: dict,
    as_ofs: list[date],
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Return ``{missing_before, inferred, still_missing, export_meta?}``."""
    source = str(model.get("alpha_source") or "rdagent").strip() or "rdagent"
    version = str(model.get("alpha_version") or "").strip()
    universe = str(model.get("universe") or "csi300").strip() or "csi300"
    kind = str(model.get("kind") or "model").strip().lower() or "model"

    provenance = model.get("provenance_json") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    session_id = str(provenance.get("session_id") or "").strip()
    loop_index = provenance.get("loop_index")
    if loop_index is not None:
        loop_index = int(loop_index)

    requested = _normalize_as_ofs(as_ofs)
    present = set(list_external_alpha_as_ofs(source=source, version=version))
    missing = sorted(set(requested) - present)

    if not missing:
        return {
            "missing_before": [],
            "inferred": 0,
            "still_missing": [],
        }

    if on_progress:
        on_progress(
            {
                "phase": "inferring_scores",
                "missing_before": missing,
                "count": len(missing),
            }
        )

    export_meta = infer_and_import_session_scores(
        session_id,
        source=source,
        version=version,
        universe=universe,
        mode=kind,
        loop_index=loop_index,
        start=missing[0],
        end=missing[-1],
        do_import=True,
    )

    new_present = set(list_external_alpha_as_ofs(source=source, version=version))
    still_missing = sorted(set(missing) - new_present)
    inferred = len(missing) - len(still_missing)

    result: dict[str, Any] = {
        "missing_before": missing,
        "inferred": inferred,
        "still_missing": still_missing,
        "export_meta": export_meta,
    }
    return result
