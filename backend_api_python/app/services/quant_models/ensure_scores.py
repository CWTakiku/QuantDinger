"""Ensure external alpha score panels exist for quant model as_of dates."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Callable

from app.services.external_alpha.store import (
    list_external_alpha_as_ofs,
    load_external_alpha_scores_as_of,
)
from app.services.market.ashare_session import decide_ashare_daily_sync
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
    """True when PIT load yields at least *min_names* scores on/before *as_of*."""
    series = load_external_alpha_scores_as_of(
        as_of, source=source, version=version
    )
    return series is not None and len(series) >= max(1, int(min_names))


def _iter_weekdays(start: date, end: date) -> list[str]:
    """Inclusive Mon–Fri ISO dates from *start* through *end*."""
    if end < start:
        return []
    out: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def _candidate_as_ofs(requested: list[str], panel: list[str]) -> list[str]:
    """Expand *requested* so tip→target gaps and interior panel holes are included.

    Stock-picker / ensure often asks for a single tip day. Without expansion,
    jumping from 08-05 to 08-07 leaves 08-06 permanently missing (PIT then
    hides the hole). Always fill weekday spans that belong to the request.
    """
    if not requested:
        return []
    want_hi = max(requested)
    want_hi_d = date.fromisoformat(want_hi)
    candidates: set[str] = set(requested)
    panel_set = {d for d in panel if d}
    max_panel = max(panel_set) if panel_set else ""

    if max_panel:
        tip = date.fromisoformat(max_panel)
        if tip < want_hi_d:
            candidates.update(_iter_weekdays(tip + timedelta(days=1), want_hi_d))
    else:
        want_lo_d = date.fromisoformat(min(requested))
        candidates.update(_iter_weekdays(want_lo_d, want_hi_d))

    # Interior holes: weekdays between consecutive exact panel days that sit
    # at or before the requested tip (e.g. panel has 08-05 & 08-07).
    sorted_panel = sorted(panel_set)
    for left, right in zip(sorted_panel, sorted_panel[1:]):
        left_d = date.fromisoformat(left)
        right_d = date.fromisoformat(right)
        if left_d >= want_hi_d:
            break
        gap_hi = min(right_d - timedelta(days=1), want_hi_d)
        if gap_hi <= left_d:
            continue
        candidates.update(_iter_weekdays(left_d + timedelta(days=1), gap_hi))

    return sorted(candidates)


def _missing_as_ofs(
    requested: list[str],
    *,
    source: str,
    version: str,
    min_names: int = 1,
) -> list[str]:
    """Dates that still need infer / import.

    - Exact panel weekday with enough names → covered.
    - Sat/Sun → PIT cover from prior trading-day scores (strategy lag).
    - Weekday without an exact panel row → missing, even if PIT would fall
      back to an older day (prevents permanent interior holes).
    - Requesting a tip beyond the panel expands all intermediate weekdays.
    """
    panel = list_external_alpha_as_ofs(source=source, version=version)
    panel_set = set(panel)
    max_panel = max(panel) if panel else ""
    min_n = max(1, int(min_names))
    missing: list[str] = []
    for d in _candidate_as_ofs(requested, panel):
        try:
            weekday = date.fromisoformat(d).weekday()
        except ValueError:
            weekday = -1

        if d in panel_set and _as_of_covered(
            d, source=source, version=version, min_names=min_n
        ):
            continue

        if weekday >= 5:
            # Weekend lag dates stay PIT-covered when prior scores exist.
            if max_panel and _as_of_covered(
                d, source=source, version=version, min_names=min_n
            ):
                continue
            missing.append(d)
            continue

        # Weekday: require an exact panel cross-section (no PIT hide).
        missing.append(d)
    return missing


def _pit_as_of(as_of: str, panel: list[str]) -> str | None:
    """Newest panel date on/before *as_of* (same semantics as strategy PIT)."""
    candidates = [d for d in panel if d and d <= as_of]
    return max(candidates) if candidates else None


def _effective_as_ofs_for_requested(requested: list[str], panel: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for d in requested:
        pit = _pit_as_of(d, panel)
        if pit and pit not in seen:
            seen.add(pit)
            out.append(pit)
    return sorted(out)


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
        # Exact panel covers request — still tell UI which exact panel days apply
        # so stock-picker can snap away from dates with no exact cross-section.
        panel = list_external_alpha_as_ofs(source=source, version=version)
        return {
            "missing_before": [],
            "inferred": 0,
            "still_missing": [],
            "effective_as_ofs": _effective_as_ofs_for_requested(requested, panel),
            "sync_reason": None,
            "qlib_update": None,
        }

    syncable: list[str] = []
    block_reason: str | None = None
    for d in missing:
        decision = decide_ashare_daily_sync(d)
        if decision.ok:
            syncable.append(d)
        else:
            block_reason = block_reason or decision.reason

    if not syncable:
        panel = list_external_alpha_as_ofs(source=source, version=version)
        reason = block_reason or "not_syncable"
        hint = None
        if reason == "future":
            tip_day = max(panel) if panel else None
            hint = (
                f"所选交易日尚未到来，不会生成未来日真实分数"
                + (f"，将使用最近已有分数日 {tip_day}" if tip_day else "")
            )
        return {
            "missing_before": missing,
            "inferred": 0,
            "still_missing": missing,
            "effective_as_ofs": _effective_as_ofs_for_requested(requested, panel),
            "sync_reason": reason,
            "user_hint": hint,
            "qlib_update": None,
        }

    qlib_meta = _ensure_qlib_for_as_ofs(
        syncable, client=client, on_progress=on_progress
    )
    sync_reason = block_reason
    if isinstance(qlib_meta, dict) and qlib_meta.get("ok") is False:
        # Bridge soft-fail (HTTP 200 + ok:false) — do not raise; surface reason.
        sync_reason = sync_reason or str(qlib_meta.get("reason") or "qlib_update_failed")

    if on_progress:
        on_progress(
            {
                "phase": "inferring_scores",
                "missing_before": missing,
                "count": len(syncable),
            }
        )

    export_meta = infer_and_import_session_scores(
        session_id,
        source=source,
        version=version,
        universe=universe,
        mode=kind,
        loop_index=loop_index,
        start=syncable[0],
        end=syncable[-1],
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
        "sync_reason": sync_reason,
    }
    return result
