"""RDAgent research factory proxy APIs (admin only)."""

from flask import Response, g, jsonify, request

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
    detail = (exc.message or "").strip()
    msg = f"{exc.code}: {detail}" if detail and detail != exc.code else exc.code
    return jsonify({"code": 0, "msg": msg, "data": {"error_code": exc.code}}), exc.status_code


def _running_jobs(jobs: list) -> list[dict]:
    return [
        job for job in jobs
        if isinstance(job, dict) and str(job.get("status") or "").lower() == "running"
    ]


def _last_job(jobs: list):
    finished = [
        job for job in jobs
        if isinstance(job, dict) and str(job.get("status") or "").lower() != "running"
    ]
    if not finished:
        # Prefer newest overall job if only running / empty
        candidates = [j for j in jobs if isinstance(j, dict)]
    else:
        candidates = finished
    if not candidates:
        return None

    def _sort_key(job: dict):
        return str(job.get("finished_at") or job.get("started_at") or "")

    return max(candidates, key=_sort_key)


@rdagent_blp.route("/status", methods=["GET"])
@login_required
@admin_required
def rdagent_status():
    try:
        client = get_bridge_client()
        health = client.health()
        data = dict(health) if isinstance(health, dict) else {"health": health}
        jobs = client.list_jobs()
        data["running_jobs"] = _running_jobs(jobs)
        data["last_job"] = _last_job(jobs)
        data["jobs"] = jobs
        try:
            data["llm_sync"] = client.llm_sync_status()
        except RdAgentBridgeError:
            data["llm_sync"] = {"enabled": False, "applied": False, "error": "bridge_llm_sync_unavailable"}
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


@rdagent_blp.route("/universes", methods=["GET"])
@login_required
@admin_required
def list_rdagent_universes():
    try:
        from app.services.rdagent_bridge.universe_payload import list_rdagent_universes as _list
        from app.services.universe import get_universe_service

        return _success(_list(get_universe_service(), g.user_id))
    except Exception:
        logger.exception("list rdagent universes failed")
        return jsonify({"code": 0, "msg": "rdagent.universesListFailed", "data": None}), 500


@rdagent_blp.route("/jobs", methods=["POST"])
@login_required
@admin_required
def start_rdagent_job():
    try:
        from app.services.rdagent_bridge.universe_payload import build_universe_job_payload
        from app.services.universe import get_universe_service

        payload = request.get_json(silent=True) or {}
        scenario = str(payload.get("scenario") or "").strip()
        if not scenario:
            return jsonify({"code": 0, "msg": "rdagent.scenarioRequired", "data": None}), 400
        loop_n = payload.get("loop_n", payload.get("loopN"))
        if loop_n is None:
            return jsonify({"code": 0, "msg": "rdagent.loopNRequired", "data": None}), 400
        timeout_h = payload.get("timeout_h", payload.get("timeoutH"))
        timeout = float(timeout_h) if timeout_h is not None else None
        data_source = str(payload.get("data_source") or payload.get("dataSource") or "default").strip()
        start_date_raw = payload.get("start_date", payload.get("startDate"))
        end_date_raw = payload.get("end_date", payload.get("endDate"))
        start_date = str(start_date_raw).strip() if start_date_raw is not None and str(start_date_raw).strip() else None
        end_date = str(end_date_raw).strip() if end_date_raw is not None and str(end_date_raw).strip() else None
        universe_code = payload.get("universe_code", payload.get("universeCode"))
        try:
            universe_body = build_universe_job_payload(
                get_universe_service(),
                g.user_id,
                str(universe_code).strip() if universe_code is not None else None,
                end_date=end_date,
            )
        except ValueError as exc:
            return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
        return _success(
            get_bridge_client().start_job(
                scenario,
                int(loop_n),
                timeout_h=timeout,
                data_source=data_source or "default",
                start_date=start_date,
                end_date=end_date,
                universe=universe_body,
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


@rdagent_blp.route("/sessions/<string:session_id>/detail", methods=["GET"])
@login_required
@admin_required
def rdagent_session_detail(session_id: str):
    try:
        include = request.args.get("include")
        return _success(get_bridge_client().session_detail(session_id, include=include))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent session detail failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionDetailFailed", "data": None}), 500


@rdagent_blp.route("/sessions/<string:session_id>/factor-matrix", methods=["GET"])
@login_required
@admin_required
def rdagent_session_factor_matrix(session_id: str):
    try:
        loop_index = request.args.get("loop_index", type=int)
        sample_dates = request.args.get("sample_dates", type=int)
        max_symbols = request.args.get("max_symbols", type=int)
        columns = request.args.get("columns")
        params: dict = {}
        if loop_index is not None:
            params["loop_index"] = loop_index
        if sample_dates is not None:
            params["sample_dates"] = sample_dates
        if max_symbols is not None:
            params["max_symbols"] = max_symbols
        if columns:
            params["columns"] = columns
        return _success(get_bridge_client().factor_matrix(session_id, **params))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent session factor matrix failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionFactorMatrixFailed", "data": None}), 500


@rdagent_blp.route("/sessions/<string:session_id>/factor-matrix.csv", methods=["GET"])
@login_required
@admin_required
def rdagent_session_factor_matrix_csv(session_id: str):
    try:
        loop_index = request.args.get("loop_index", type=int)
        max_rows = request.args.get("max_rows", type=int)
        columns = request.args.get("columns")
        params: dict = {}
        if loop_index is not None:
            params["loop_index"] = loop_index
        if max_rows is not None:
            params["max_rows"] = max_rows
        if columns:
            params["columns"] = columns
        body, ctype = get_bridge_client().factor_matrix_csv(session_id, **params)
        return Response(
            body,
            mimetype=ctype or "text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="session_{session_id}_factor_matrix.csv"'
                ),
            },
        )
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent session factor matrix csv failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionFactorMatrixCsvFailed", "data": None}), 500


@rdagent_blp.route("/sessions/<string:session_id>/metrics.csv", methods=["GET"])
@login_required
@admin_required
def rdagent_session_metrics_csv(session_id: str):
    try:
        body, ctype = get_bridge_client().session_metrics_csv(session_id)
        return Response(
            body,
            mimetype=ctype or "text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="session_{session_id}_metrics.csv"'
                ),
            },
        )
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent session metrics csv failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionMetricsCsvFailed", "data": None}), 500


@rdagent_blp.route("/sessions/<string:session_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_rdagent_session(session_id: str):
    try:
        sid = str(session_id or "").strip()
        if not sid:
            return jsonify({"code": 0, "msg": "rdagent.sessionIdRequired", "data": None}), 400
        return _success(get_bridge_client().delete_session(sid))
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("delete rdagent session failed")
        return jsonify({"code": 0, "msg": "rdagent.sessionDeleteFailed", "data": None}), 500


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


@rdagent_blp.route("/llm-sync", methods=["GET"])
@login_required
@admin_required
def rdagent_llm_sync_status():
    try:
        return _success(get_bridge_client().llm_sync_status())
    except RdAgentBridgeError as exc:
        return _failure(exc)
    except Exception:
        logger.exception("rdagent llm-sync status failed")
        return jsonify({"code": 0, "msg": "rdagent.llmSyncFailed", "data": None}), 500


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
        loop_index_raw = payload.get("loop_index", payload.get("loopIndex"))
        loop_index = int(loop_index_raw) if loop_index_raw is not None else None
        version_raw = payload.get("version")
        if version_raw is not None and str(version_raw).strip():
            version = str(version_raw).strip()[:120]
        else:
            version = default_session_version(session_id, loop_index)
        universe = str(payload.get("universe") or "csi300").strip() or "csi300"

        result = import_session_scores(
            session_id,
            source=source,
            version=version,
            universe=universe,
            loop_index=loop_index,
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
