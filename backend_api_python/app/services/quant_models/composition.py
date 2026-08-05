from __future__ import annotations

from typing import Any, Callable

from app.services.rdagent_bridge.client import RdAgentBridgeClient
from app.services.rdagent_bridge.errors import RdAgentBridgeError


def _empty(kind: str, session_id: str, loop_index: int | None, error: str | None) -> dict[str, Any]:
    return {
        "available": error is None,
        "kind": kind,
        "session_id": session_id,
        "loop_index": loop_index,
        "learner": None,
        "factors": [],
        "bridge_error": error,
    }


def _factor_rows(items: list[Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") and item.get("kind") != "factor":
            continue
        rows.append(
            {
                "name": item.get("name"),
                "formulation": item.get("formulation"),
                "description": item.get("description"),
            }
        )
    return rows


def _parse_loop_index(raw: Any) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _learner_from_artifact(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not artifact:
        return None
    return {
        "name": artifact.get("name"),
        "model_type": artifact.get("model_type"),
        "architecture": artifact.get("architecture"),
        "hyperparameters": artifact.get("hyperparameters")
        if isinstance(artifact.get("hyperparameters"), dict)
        else {},
    }


def build_quant_model_composition(
    model: dict[str, Any],
    *,
    detail_fetcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provenance = model.get("provenance_json") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    session_id = str(provenance.get("session_id") or "").strip()
    loop_raw = provenance.get("loop_index")
    loop_index = int(loop_raw) if loop_raw is not None and str(loop_raw).strip() != "" else None
    kind = str(model.get("kind") or provenance.get("mode") or "factor").strip().lower() or "factor"

    if not session_id or loop_index is None:
        return _empty(kind, session_id, loop_index, "量化模型缺少 session_id/loop_index 溯源")

    fetcher = detail_fetcher
    if fetcher is None:
        client = RdAgentBridgeClient.from_env()

        def fetcher(session_id: str, include: str | None = None):
            return client.session_detail(session_id, include=include)

    try:
        detail = fetcher(session_id, include="summary,loops,factors,sota_library")
    except RdAgentBridgeError as exc:
        return _empty(kind, session_id, loop_index, str(exc) or "无法连接 rdagent bridge")
    except Exception as exc:  # noqa: BLE001 — surface as bridge_error
        return _empty(kind, session_id, loop_index, str(exc)[:240])

    loops = detail.get("loops") if isinstance(detail, dict) else None
    loop = None
    for item in loops or []:
        if not isinstance(item, dict):
            continue
        item_loop_index = _parse_loop_index(item.get("loop_index"))
        if item_loop_index is None or item_loop_index != loop_index:
            continue
        loop = item
        break

    if loop is None:
        return _empty(kind, session_id, loop_index, f"会话中未找到 Loop_{loop_index}")

    # Prefer the loop's own kind: a model may be published with mode=factor by mistake
    # (e.g. Loop_7 is model while model_key ends with _factor), which would otherwise
    # yield an empty factor table.
    loop_kind = str(loop.get("kind") or "").strip().lower()
    effective_kind = loop_kind if loop_kind in ("factor", "model") else kind

    factors: list[dict[str, Any]] = []
    learner = None
    artifacts = loop.get("artifacts") or loop.get("factors") or []
    sota = detail.get("sota_library") if isinstance(detail, dict) else None
    if effective_kind == "factor":
        factors = _factor_rows(artifacts)
        if not factors:
            factors = _factor_rows(sota)
        learner = None
    else:
        model_art = next(
            (a for a in artifacts if isinstance(a, dict) and a.get("kind") == "model"),
            None,
        )
        learner = _learner_from_artifact(model_art)
        factors = _factor_rows(artifacts)
        if not factors:
            factors = _factor_rows(sota)

    return {
        "available": True,
        "kind": effective_kind,
        "session_id": session_id,
        "loop_index": loop_index,
        "learner": learner,
        "factors": factors,
        "bridge_error": None,
    }
