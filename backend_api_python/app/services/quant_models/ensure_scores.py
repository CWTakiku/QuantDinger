"""Ensure external alpha score panels exist for quant model as_of dates."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

from app.services.external_alpha.store import (
    list_external_alpha_as_ofs,
    load_external_alpha_scores_as_of,
)
from app.services.rdagent_bridge.client import RdAgentBridgeClient
from app.services.rdagent_bridge.errors import RdAgentBridgeError
from app.services.rdagent_bridge.import_session import infer_and_import_session_scores

logger = logging.getLogger(__name__)


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


def _as_of_covered(
    as_of: str,
    *,
    source: str,
    version: str,
    min_names: int = 1,
) -> bool:
    """True when PIT load yields at least *min_names* scores on/before *as_of*.

    Weekly ``score_lag_days`` often lands on weekends; scores are stored on
    trading days only. Exact ``as_of`` membership is the wrong coverage test —
    the strategy runtime uses the same PIT ``as_of <=`` semantics.
    """
    series = load_external_alpha_scores_as_of(
        as_of, source=source, version=version
    )
    return series is not None and len(series) >= max(1, int(min_names))


def _missing_as_ofs(
    requested: list[str],
    *,
    source: str,
    version: str,
    min_names: int = 1,
) -> list[str]:
    return [
        d
        for d in requested
        if not _as_of_covered(d, source=source, version=version, min_names=min_names)
    ]


def _ensure_qlib_for_as_ofs(
    as_ofs: list[str],
    *,
    client: RdAgentBridgeClient | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Pull Qlib day bars through the latest missing as_of before infer."""
    if not as_ofs:
        return None
    target = as_ofs[-1]
    if on_progress:
        on_progress(
            {
                "phase": "updating_qlib",
                "end": target,
                "missing_before": list(as_ofs),
            }
        )
    bridge = client or RdAgentBridgeClient.from_env()
    try:
        return bridge.qlib_update(end=target)
    except RdAgentBridgeError as exc:
        # Infer still runs and may snap to the last available bar; surface the
        # update failure in export_meta so the UI can explain still_missing.
        logger.warning("qlib update before ensure-scores failed: %s", exc)
        return {"error": str(exc), "end": target}


def ensure_quant_model_scores(
    model: dict,
    as_ofs: list[date],
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    client: RdAgentBridgeClient | None = None,
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

    try:
        min_names = int((model.get("params") or {}).get("min_names") or 1)
    except (TypeError, ValueError, AttributeError):
        min_names = 1
    min_names = max(1, min_names)

    requested = _normalize_as_ofs(as_ofs)
    missing = _missing_as_ofs(
        requested, source=source, version=version, min_names=min_names
    )

    if not missing:
        return {
            "missing_before": [],
            "inferred": 0,
            "still_missing": [],
        }

    qlib_meta = _ensure_qlib_for_as_ofs(missing, client=client, on_progress=on_progress)

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
        client=client,
    )
    if isinstance(export_meta, dict) and qlib_meta is not None:
        export_meta = {**export_meta, "qlib_update": qlib_meta}

    still_missing = _missing_as_ofs(
        missing, source=source, version=version, min_names=min_names
    )
    # Bridge may snap beyond-calendar as_of onto the last bar; credit those.
    effective_as_ofs: list[str] = []
    panel = list_external_alpha_as_ofs(source=source, version=version)
    if isinstance(export_meta, dict):
        for key in ("as_of_max", "as_of_min", "infer_end", "infer_start"):
            val = str(export_meta.get(key) or "").strip()[:10]
            if val and val in panel and val not in effective_as_ofs:
                effective_as_ofs.append(val)
        if still_missing and (effective_as_ofs or panel):
            snap_max = max(effective_as_ofs or panel)
            # Dates strictly after the latest available bar are considered covered by snap.
            still_missing = [d for d in still_missing if d <= snap_max]
            still_missing = _missing_as_ofs(
                still_missing, source=source, version=version, min_names=min_names
            )
    inferred = len(missing) - len(still_missing)

    result: dict[str, Any] = {
        "missing_before": missing,
        "inferred": inferred,
        "still_missing": still_missing,
        "export_meta": export_meta,
        "effective_as_ofs": sorted(effective_as_ofs),
        "qlib_update": qlib_meta,
    }
    return result
