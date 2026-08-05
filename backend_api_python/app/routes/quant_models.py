"""Quant model publish/archive APIs (admin only)."""

from datetime import date, datetime, timedelta

from flask import g, jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.quant_models import (
    archive_quant_model,
    get_quant_model,
    list_quant_models,
    publish_quant_model,
)
from app.services.quant_models.composition import build_quant_model_composition
from app.services.quant_models.ensure_scores import ensure_quant_model_scores
from app.services.rdagent_bridge.errors import RdAgentBridgeError
from app.utils.auth import admin_required, login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)
quant_models_blp = Blueprint("quant_models", __name__)


def _success(data=None, *, status: int = 200):
    return jsonify({"code": 1, "msg": "success", "data": data}), status


def _parse_day(value: object) -> date:
    text = str(value or "").strip()[:10]
    return datetime.strptime(text, "%Y-%m-%d").date()


def _as_ofs_from_body(payload: dict) -> list[date]:
    raw_list = payload.get("as_ofs")
    if isinstance(raw_list, list) and raw_list:
        return sorted({_parse_day(item) for item in raw_list})
    start = payload.get("start")
    end = payload.get("end")
    if start and end:
        a = _parse_day(start)
        b = _parse_day(end)
        if b < a:
            raise ValueError("end must be >= start")
        out: list[date] = []
        cur = a
        for _ in range(400):
            out.append(cur)
            if cur >= b:
                break
            cur = cur + timedelta(days=1)
        return out
    raise ValueError("as_ofs or start/end required")


def _provenance_session_loop(model: dict) -> tuple[str | None, int | None]:
    provenance = model.get("provenance_json") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    session_id = str(provenance.get("session_id") or "").strip()
    loop_raw = provenance.get("loop_index")
    loop_index: int | None = None
    if loop_raw is not None and str(loop_raw).strip() != "":
        try:
            loop_index = int(loop_raw)
        except (TypeError, ValueError):
            loop_index = None
    return (session_id or None, loop_index)


def _is_unique_violation(exc: BaseException) -> bool:
    if getattr(exc, "pgcode", None) == "23505":
        return True
    msg = str(exc).lower()
    return "unique constraint" in msg or "duplicate key" in msg


@quant_models_blp.route("/publish", methods=["POST"])
@login_required
@admin_required
def publish_model():
    try:
        payload = request.get_json(silent=True) or {}
        display_name = str(payload.get("display_name") or payload.get("displayName") or "").strip()
        if not display_name:
            return jsonify({"code": 0, "msg": "quantModels.displayNameRequired", "data": None}), 400

        kind = str(payload.get("kind") or "").strip()
        if not kind:
            return jsonify({"code": 0, "msg": "quantModels.kindRequired", "data": None}), 400

        session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        if not session_id:
            return jsonify({"code": 0, "msg": "quantModels.sessionIdRequired", "data": None}), 400

        loop_index_raw = payload.get("loop_index", payload.get("loopIndex"))
        if loop_index_raw is None or str(loop_index_raw).strip() == "":
            return jsonify({"code": 0, "msg": "quantModels.loopIndexRequired", "data": None}), 400
        loop_index = int(loop_index_raw)

        universe = str(payload.get("universe") or "csi300").strip() or "csi300"
        model_key_raw = payload.get("model_key", payload.get("modelKey"))
        model_key = str(model_key_raw).strip() if model_key_raw is not None and str(model_key_raw).strip() else None
        alpha_source = str(payload.get("alpha_source") or payload.get("alphaSource") or "rdagent").strip() or "rdagent"
        metrics = payload.get("metrics")
        if metrics is not None and not isinstance(metrics, dict):
            return jsonify({"code": 0, "msg": "quantModels.invalidMetrics", "data": None}), 400

        result = publish_quant_model(
            display_name=display_name,
            kind=kind,
            session_id=session_id,
            loop_index=loop_index,
            universe=universe,
            owner_user_id=int(g.user_id),
            model_key=model_key,
            alpha_source=alpha_source,
            metrics=metrics,
        )
        logger.info(
            "quant model published user=%s model_key=%s session=%s loop=%s",
            getattr(g, "user_id", None),
            result.get("model_key"),
            session_id,
            loop_index,
        )
        return _success(result, status=201)
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception as exc:
        if _is_unique_violation(exc):
            return jsonify({"code": 0, "msg": str(exc), "data": None}), 409
        logger.exception("publish quant model failed")
        return jsonify({"code": 0, "msg": "quantModels.publishFailed", "data": None}), 500


@quant_models_blp.route("/", methods=["GET"], strict_slashes=False)
@login_required
@admin_required
def list_models():
    try:
        status = str(request.args.get("status") or "published").strip() or "published"
        return _success(list_quant_models(status=status))
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception:
        logger.exception("list quant models failed")
        return jsonify({"code": 0, "msg": "quantModels.listFailed", "data": None}), 500


@quant_models_blp.route("/<string:model_key>/ensure-scores", methods=["POST"])
@login_required
@admin_required
def ensure_model_scores(model_key: str):
    model = get_quant_model(model_key)
    if not model:
        return jsonify({"code": 0, "msg": "quantModels.notFound", "data": None}), 404
    if str(model.get("status") or "").strip() != "published":
        return jsonify({"code": 0, "msg": "quantModels.notPublished", "data": None}), 400
    payload = request.get_json(silent=True) or {}
    try:
        as_ofs = _as_ofs_from_body(payload)
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    session_id, loop_index = _provenance_session_loop(model)
    if not session_id or loop_index is None:
        return jsonify(
            {"code": 0, "msg": "量化模型缺少 session_id/loop_index 溯源", "data": None}
        ), 400
    try:
        result = ensure_quant_model_scores(model, as_ofs)
    except RdAgentBridgeError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), int(getattr(exc, "status_code", None) or 503)
    except Exception as exc:
        logger.exception("ensure quant model scores failed")
        return jsonify({"code": 0, "msg": str(exc)[:240] or "quantModels.ensureFailed", "data": None}), 500
    return _success(result)


@quant_models_blp.route("/<string:model_key>", methods=["GET"], strict_slashes=False)
@login_required
@admin_required
def get_model(model_key: str):
    model = get_quant_model(model_key)
    if not model:
        return jsonify({"code": 0, "msg": "quantModels.notFound", "data": None}), 404
    composition = build_quant_model_composition(model)
    data = dict(model)
    data["composition"] = composition
    return _success(data)


@quant_models_blp.route("/<string:model_key>/archive", methods=["POST"])
@login_required
@admin_required
def archive_model(model_key: str):
    try:
        key = str(model_key or "").strip()
        if not key:
            return jsonify({"code": 0, "msg": "quantModels.modelKeyRequired", "data": None}), 400
        return _success(archive_quant_model(key))
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception:
        logger.exception("archive quant model failed")
        return jsonify({"code": 0, "msg": "quantModels.archiveFailed", "data": None}), 500
