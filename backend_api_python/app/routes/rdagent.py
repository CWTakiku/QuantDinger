"""RDAgent research factory proxy APIs (admin only)."""

from flask import jsonify, request

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.rdagent_bridge import RdAgentBridgeClient, RdAgentBridgeError
from app.services.rdagent_bridge.import_session import default_session_version, import_session_scores
from app.utils.auth import admin_required, login_required
from app.utils.logger import get_logger


logger = get_logger(__name__)
rdagent_blp = Blueprint("rdagent", __name__)


def get_bridge_client() -> RdAgentBridgeClient:
    return RdAgentBridgeClient.from_env()


def _success(data=None, *, status: int = 200):
    return jsonify({"code": 1, "msg": "success", "data": data}), status


def _failure(exc: RdAgentBridgeError):
    return jsonify({"code": 0, "msg": exc.code, "data": None}), exc.status_code


def _running_jobs(client: RdAgentBridgeClient) -> list[dict]:
    jobs = client.list_jobs()
    return [
        job for job in jobs
        if isinstance(job, dict) and str(job.get("status") or "").lower() == "running"
    ]


@rdagent_blp.route("/status", methods=["GET"])
@login_required
@admin_required
def rdagent_status():
    try:
        client = get_bridge_client()
        health = client.health()
        data = dict(health) if isinstance(health, dict) else {"health": health}
        data["running_jobs"] = _running_jobs(client)
        return _success(data)
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent status failed")
        return jsonify({"code": 0, "msg": "rdagent.statusFailed", "data": None}), 500


@rdagent_blp.route("/jobs", methods=["GET"])
@login_required
@admin_required
def list_rdagent_jobs():
    try:
        return _success(get_bridge_client().list_jobs())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("list rdagent jobs failed")
        return jsonify({"code": 0, "msg": "rdagent.jobsListFailed", "data": None}), 500


@rdagent_blp.route("/jobs", methods=["POST"])
@login_required
@admin_required
def start_rdagent_job():
    try:
        payload = request.get_json(silent=True) or {}
        scenario = str(payload.get("scenario") or "").strip()
        if not scenario:
            return jsonify({"code": 0, "msg": "rdagent.scenarioRequired", "data": None}), 400
        step_n = payload.get("step_n", payload.get("stepN"))
        if step_n is None:
            return jsonify({"code": 0, "msg": "rdagent.stepNRequired", "data": None}), 400
        timeout_h = payload.get("timeout_h", payload.get("timeoutH"))
        timeout = float(timeout_h) if timeout_h is not None else None
        data_source = str(payload.get("data_source") or payload.get("dataSource") or "default").strip()
        return _success(
            get_bridge_client().start_job(
                scenario,
                int(step_n),
                timeout_h=timeout,
                data_source=data_source or "default",
            ),
            status=201,
        )
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except (TypeError, ValueError):
        return jsonify({"code": 0, "msg": "rdagent.invalidPayload", "data": None}), 400
    except Exception:
        logger.exception("start rdagent job failed")
        return jsonify({"code": 0, "msg": "rdagent.jobStartFailed", "data": None}), 500


@rdagent_blp.route("/jobs/<string:job_id>", methods=["GET"])
@login_required
@admin_required
def get_rdagent_job(job_id: str):
    try:
        return _success(get_bridge_client().get_job(job_id))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("get rdagent job failed")
        return jsonify({"code": 0, "msg": "rdagent.jobGetFailed", "data": None}), 500


@rdagent_blp.route("/jobs/<string:job_id>/stop", methods=["POST"])
@login_required
@admin_required
def stop_rdagent_job(job_id: str):
    try:
        return _success(get_bridge_client().stop_job(job_id))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("stop rdagent job failed")
        return jsonify({"code": 0, "msg": "rdagent.jobStopFailed", "data": None}), 500


@rdagent_blp.route("/jobs/<string:job_id>/logs", methods=["GET"])
@login_required
@admin_required
def rdagent_job_logs(job_id: str):
    try:
        tail_raw = request.args.get("tail", "200")
        tail = int(tail_raw)
        return _success(get_bridge_client().job_logs(job_id, tail=tail))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except (TypeError, ValueError):
        return jsonify({"code": 0, "msg": "rdagent.invalidTail", "data": None}), 400
    except Exception:
        logger.exception("rdagent job logs failed")
        return jsonify({"code": 0, "msg": "rdagent.jobLogsFailed", "data": None}), 500


@rdagent_blp.route("/sessions", methods=["GET"])
@login_required
@admin_required
def list_rdagent_sessions():
    try:
        return _success(get_bridge_client().list_sessions())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("list rdagent sessions failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionsListFailed", "data": None}), 500


@rdagent_blp.route("/ui", methods=["GET"])
@login_required
@admin_required
def rdagent_ui_status():
    try:
        return _success(get_bridge_client().ui_status())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent ui status failed")
        return jsonify({"code": 0, "msg": "rdagent.uiStatusFailed", "data": None}), 500


@rdagent_blp.route("/ui/start", methods=["POST"])
@login_required
@admin_required
def rdagent_ui_start():
    try:
        return _success(get_bridge_client().ui_start())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent ui start failed")
        return jsonify({"code": 0, "msg": "rdagent.uiStartFailed", "data": None}), 500


@rdagent_blp.route("/data-sources", methods=["GET"])
@login_required
@admin_required
def list_rdagent_data_sources():
    try:
        return _success(get_bridge_client().list_data_sources())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("list rdagent data sources failed")
        return jsonify({"code": 0, "msg": "rdagent.dataSourcesListFailed", "data": None}), 500


@rdagent_blp.route("/import-from-session", methods=["POST"])
@login_required
@admin_required
def rdagent_import_from_session():
    try:
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        if not session_id:
            return jsonify({"code": 0, "msg": "rdagent.sessionIdRequired", "data": None}), 400

        source = str(payload.get("source") or "rdagent").strip() or "rdagent"
        version_raw = payload.get("version")
        if version_raw is not None and str(version_raw).strip():
            version = str(version_raw).strip()[:120]
        else:
            version = default_session_version(session_id)
        universe = str(payload.get("universe") or "csi300").strip() or "csi300"

        result = import_session_scores(
            session_id,
            source=source,
            version=version,
            universe=universe,
        )
        logger.info(
            "rdagent import-from-session user=%s session=%s source=%s version=%s inserted=%s",
            getattr(request, "user_id", None),
            session_id,
            source,
            version,
            result.get("inserted"),
        )
        return _success(result)
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    except Exception:
        logger.exception("rdagent import-from-session failed")
        return jsonify({"code": 0, "msg": "rdagent.importFromSessionFailed", "data": None}), 500
