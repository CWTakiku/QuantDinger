"""Import RDAgent session scores via bridge export → CSV → external alpha store."""

from __future__ import annotations

from typing import Any

from app.services.external_alpha.store import persist_external_alpha_scores, rows_from_csv_text
from app.services.rdagent_bridge.client import RdAgentBridgeClient


def default_session_version(session_id: str) -> str:
    return f"session_{session_id}"[:120]


def import_session_scores(
    session_id: str,
    *,
    source: str = "rdagent",
    version: str | None = None,
    universe: str = "csi300",
    client: RdAgentBridgeClient | None = None,
) -> dict[str, Any]:
    """Export session scores from bridge, parse CSV, and persist to Postgres."""
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")

    source_s = str(source or "rdagent").strip() or "rdagent"
    version_s = (str(version).strip() if version else default_session_version(session_id))[:120]
    universe_s = str(universe or "csi300").strip() or "csi300"

    bridge = client or RdAgentBridgeClient.from_env()
    export_meta = bridge.export_session(session_id, source_s, version_s, universe_s)
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
