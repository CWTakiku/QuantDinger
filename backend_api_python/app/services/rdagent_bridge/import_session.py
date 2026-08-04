"""Import RDAgent session scores via bridge export → CSV → external alpha store."""

from __future__ import annotations

from typing import Any

from app.services.external_alpha.store import (
    persist_external_alpha_scores,
    preview_external_alpha_scores,
    rows_from_csv_text,
)
from app.services.rdagent_bridge.client import RdAgentBridgeClient


def default_session_version(session_id: str, loop_index: int | None = None) -> str:
    base = f"session_{session_id}"
    if loop_index is not None:
        base = f"{base}_loop{int(loop_index)}"
    return base[:120]


def default_infer_version(
    session_id: str,
    loop_index: int | None = None,
    mode: str = "model",
) -> str:
    base = default_session_version(session_id, loop_index)
    suffix = f"_infer_{mode}"
    return (base[: max(0, 120 - len(suffix))] + suffix)[:120]


def _preview_from_csv_rows(
    rows: list[dict[str, Any]],
    *,
    source: str,
    version: str,
    top_n: int = 50,
) -> dict[str, Any]:
    """Build a latest-as_of ranked preview from in-memory CSV rows (pre-persist)."""
    by_date: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        as_of = str(row.get("as_of") or "")[:10]
        sym = str(row.get("symbol") or "").strip()
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            continue
        if not as_of or not sym or score != score:
            continue
        by_date.setdefault(as_of, []).append((sym, score))
    dates = sorted(by_date.keys(), reverse=True)
    if not dates:
        return {
            "source": source,
            "version": version,
            "as_of": "",
            "as_of_dates": [],
            "rows": [],
            "stats": {},
            "total": 0,
            "limit": top_n,
        }
    latest = dates[0]
    scored = sorted(by_date[latest], key=lambda x: x[1], reverse=True)
    values = [v for _, v in scored]
    stats: dict[str, Any] = {"n": len(values)}
    if values:
        stats["mean"] = float(sum(values) / len(values))
        stats["min"] = float(min(values))
        stats["max"] = float(max(values))
    limit_n = max(1, min(int(top_n), 500))
    preview_rows = [
        {"rank": i + 1, "symbol": sym, "score": score}
        for i, (sym, score) in enumerate(scored[:limit_n])
    ]
    try:
        from app.services.external_alpha.store import _attach_names

        preview_rows = _attach_names(preview_rows)
    except Exception:
        for row in preview_rows:
            row.setdefault("name", "")
    return {
        "source": source,
        "version": version,
        "as_of": latest,
        "as_of_dates": dates,
        "rows": preview_rows,
        "stats": stats,
        "total": len(scored),
        "limit": limit_n,
    }


def import_session_scores(
    session_id: str,
    *,
    source: str = "rdagent",
    version: str | None = None,
    universe: str = "csi300",
    loop_index: int | None = None,
    client: RdAgentBridgeClient | None = None,
) -> dict[str, Any]:
    """Export session scores from bridge, parse CSV, and persist to Postgres."""
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")

    source_s = str(source or "rdagent").strip() or "rdagent"
    version_s = (
        str(version).strip() if version else default_session_version(session_id, loop_index)
    )[:120]
    universe_s = str(universe or "csi300").strip() or "csi300"

    bridge = client or RdAgentBridgeClient.from_env()
    export_meta = bridge.export_session(
        session_id, source_s, version_s, universe_s, loop_index=loop_index
    )
    csv_text = bridge.download_export(str(export_meta["export_id"]))
    rows = rows_from_csv_text(
        csv_text,
        default_source=source_s,
        default_version=version_s,
        default_universe=universe_s,
    )
    persist_result = persist_external_alpha_scores(rows)
    return {
        **export_meta,
        **persist_result,
        "session_id": session_id,
        "universe": universe_s,
        "parsed_rows": len(rows),
    }


def infer_and_import_session_scores(
    session_id: str,
    *,
    source: str = "rdagent",
    version: str | None = None,
    universe: str = "csi300",
    mode: str = "model",
    loop_index: int | None = None,
    start: str | None = None,
    end: str | None = None,
    max_asofs: int | None = None,
    do_import: bool = True,
    client: RdAgentBridgeClient | None = None,
) -> dict[str, Any]:
    """Forward-score on latest Qlib data, optionally persist to External Alpha."""
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")

    mode_s = str(mode or "model").strip().lower() or "model"
    if mode_s not in {"model", "factor"}:
        raise ValueError("mode must be 'model' or 'factor'")

    source_s = str(source or "rdagent").strip() or "rdagent"
    version_s = (
        str(version).strip()
        if version
        else default_infer_version(session_id, loop_index, mode_s)
    )[:120]
    universe_s = str(universe or "csi300").strip() or "csi300"

    bridge = client or RdAgentBridgeClient.from_env()
    export_meta = bridge.infer_session(
        session_id,
        source=source_s,
        version=version_s,
        universe=universe_s,
        mode=mode_s,
        loop_index=loop_index,
        start=start,
        end=end,
        max_asofs=max_asofs,
    )
    result: dict[str, Any] = {
        **export_meta,
        "session_id": session_id,
        "universe": universe_s,
        "imported": False,
    }

    csv_text = bridge.download_export(str(export_meta["export_id"]))
    rows = rows_from_csv_text(
        csv_text,
        default_source=source_s,
        default_version=version_s,
        default_universe=universe_s,
    )
    result["preview"] = _preview_from_csv_rows(
        rows, source=source_s, version=version_s, top_n=50
    )
    result["parsed_rows"] = len(rows)

    if not do_import:
        return result

    persist_result = persist_external_alpha_scores(rows)
    result.update(persist_result)
    result["imported"] = True
    # Prefer DB preview so subsequent date switches hit the same panel.
    try:
        result["preview"] = preview_external_alpha_scores(
            source=source_s,
            version=version_s,
            as_of=result["preview"].get("as_of"),
            limit=50,
        )
    except Exception:
        pass
    return result
