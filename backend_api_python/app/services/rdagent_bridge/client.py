"""HTTP client for the host-side RDAgent bridge."""

from __future__ import annotations

import os
from typing import Any

import requests

from app.services.rdagent_bridge.errors import RdAgentBridgeError

_DEFAULT_URL = "http://127.0.0.1:19901"
_DEFAULT_TIMEOUT_S = 180.0
_TOKEN_HEADER = "X-RDAgent-Bridge-Token"


class RdAgentBridgeClient:
    def __init__(self, base_url: str, token: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout_s = float(timeout_s)

    @classmethod
    def from_env(cls) -> RdAgentBridgeClient:
        url = (os.environ.get("RDAGENT_BRIDGE_URL") or _DEFAULT_URL).strip()
        token = (os.environ.get("RDAGENT_BRIDGE_TOKEN") or "").strip()
        if not token:
            raise RdAgentBridgeError(
                500,
                "rdagent_bridge_config",
                "RDAGENT_BRIDGE_TOKEN is required",
            )
        timeout_raw = (os.environ.get("RDAGENT_BRIDGE_TIMEOUT_S") or "").strip()
        timeout_s = float(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT_S
        return cls(base_url=url, token=token, timeout_s=timeout_s)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", auth=False)

    def list_jobs(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v1/jobs")
        jobs = payload.get("jobs")
        return list(jobs) if isinstance(jobs, list) else []

    def start_job(
        self,
        scenario: str,
        loop_n: int,
        timeout_h: float | None = None,
        data_source: str = "default",
        start_date: str | None = None,
        end_date: str | None = None,
        universe: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "scenario": scenario,
            "loop_n": int(loop_n),
            "data_source": (data_source or "default").strip() or "default",
        }
        if timeout_h is not None:
            body["timeout_h"] = float(timeout_h)
        start_s = (start_date or "").strip()
        end_s = (end_date or "").strip()
        if start_s:
            body["start_date"] = start_s
        if end_s:
            body["end_date"] = end_s
        if universe:
            body["universe"] = universe
        return self._request("POST", "/v1/jobs", json_body=body)

    def list_data_sources(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v1/data-sources")
        items = payload.get("data_sources")
        return list(items) if isinstance(items, list) else []

    def llm_sync_status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/llm-sync")

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}")

    def stop_job(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/jobs/{job_id}/stop")

    def job_logs(self, job_id: str, tail: int = 200) -> dict[str, Any]:
        return self._request("GET", f"/v1/jobs/{job_id}/logs", params={"tail": int(tail)})

    def list_sessions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v1/sessions")
        sessions = payload.get("sessions")
        return list(sessions) if isinstance(sessions, list) else []

    def delete_session(self, session_id: str) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise RdAgentBridgeError(400, "rdagent_bridge_bad_request", "session_id is required")
        return self._request("DELETE", f"/v1/sessions/{sid}")

    def ui_status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/ui")

    def ui_start(self) -> dict[str, Any]:
        return self._request("POST", "/v1/ui/start")

    def export_session(
        self,
        session_id: str,
        source: str = "rdagent",
        version: str = "default",
        universe: str = "csi300",
        loop_index: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "session": session_id,
            "source": source,
            "version": version,
            "universe": universe,
        }
        if loop_index is not None:
            body["loop_index"] = int(loop_index)
        return self._request("POST", "/v1/export", json_body=body)

    def factor_matrix(self, session_id: str, **params: Any) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise RdAgentBridgeError(400, "rdagent_bridge_bad_request", "session_id is required")
        query: dict[str, Any] = {}
        for key in ("loop_index", "sample_dates", "max_symbols", "columns"):
            if key in params and params[key] is not None:
                query[key] = params[key]
        return self._request(
            "GET",
            f"/v1/sessions/{sid}/factor-matrix",
            params=query or None,
            timeout_s=max(self.timeout_s, 60),
        )

    def factor_matrix_csv(self, session_id: str, **params: Any) -> tuple[bytes, str]:
        sid = str(session_id or "").strip()
        if not sid:
            raise RdAgentBridgeError(400, "rdagent_bridge_bad_request", "session_id is required")
        query: dict[str, Any] = {}
        for key in ("loop_index", "max_rows", "columns"):
            if key in params and params[key] is not None:
                query[key] = params[key]
        headers = self._headers()
        timeout = max(self.timeout_s, 60)
        try:
            response = requests.get(
                self._url(f"/v1/sessions/{sid}/factor-matrix.csv"),
                headers=headers,
                params=query or None,
                timeout=timeout,
                proxies={"http": None, "https": None},
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise RdAgentBridgeError(
                503,
                "rdagent_bridge_unreachable",
                "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge",
            ) from exc

        status = int(response.status_code)
        if status == 401:
            payload = self._decode_json(response)
            message = self._error_message(payload, "rdagent bridge unauthorized")
            raise RdAgentBridgeError(401, "rdagent_bridge_unauthorized", message)

        if status >= 500:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge error ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_error", message)

        if status >= 400:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge request failed ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_bad_request", message)

        content_type = response.headers.get("Content-Type") or "text/csv"
        return response.content, content_type

    def session_detail(self, session_id: str, include: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if include:
            params["include"] = include
        return self._request(
            "GET",
            f"/v1/sessions/{session_id}/detail",
            params=params or None,
            timeout_s=max(self.timeout_s, 60),
        )

    def session_metrics_csv(self, session_id: str) -> tuple[bytes, str]:
        sid = str(session_id or "").strip()
        if not sid:
            raise RdAgentBridgeError(400, "rdagent_bridge_bad_request", "session_id is required")
        headers = self._headers()
        timeout = max(self.timeout_s, 60)
        try:
            response = requests.get(
                self._url(f"/v1/sessions/{sid}/metrics.csv"),
                headers=headers,
                timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise RdAgentBridgeError(
                503,
                "rdagent_bridge_unreachable",
                "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge",
            ) from exc

        status = int(response.status_code)
        if status == 401:
            payload = self._decode_json(response)
            message = self._error_message(payload, "rdagent bridge unauthorized")
            raise RdAgentBridgeError(401, "rdagent_bridge_unauthorized", message)

        if status >= 500:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge error ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_error", message)

        if status >= 400:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge request failed ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_bad_request", message)

        content_type = response.headers.get("Content-Type") or "text/csv"
        return response.content, content_type

    def download_export(self, export_id: str) -> str:
        export_id = str(export_id or "").strip()
        if not export_id:
            raise RdAgentBridgeError(400, "rdagent_bridge_bad_request", "export_id is required")
        headers = self._headers()
        try:
            response = requests.get(
                self._url(f"/v1/export/{export_id}/download"),
                headers=headers,
                timeout=self.timeout_s,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise RdAgentBridgeError(
                503,
                "rdagent_bridge_unreachable",
                "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge",
            ) from exc

        status = int(response.status_code)
        if status == 401:
            payload = self._decode_json(response)
            message = self._error_message(payload, "rdagent bridge unauthorized")
            raise RdAgentBridgeError(401, "rdagent_bridge_unauthorized", message)

        if status >= 500:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge error ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_error", message)

        if status >= 400:
            payload = self._decode_json(response)
            message = self._error_message(payload, f"rdagent bridge request failed ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_bad_request", message)

        return response.text

    def _headers(self) -> dict[str, str]:
        return {_TOKEN_HEADER: self.token}

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        headers = self._headers() if auth else {}
        try:
            # Bypass HTTP(S)_PROXY — Colima/Clash proxies break host bridge access.
            response = requests.request(
                method,
                self._url(path),
                headers=headers,
                json=json_body,
                params=params,
                timeout=self.timeout_s if timeout_s is None else timeout_s,
                proxies={"http": None, "https": None},
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise RdAgentBridgeError(
                503,
                "rdagent_bridge_unreachable",
                "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge",
            ) from exc

        return self._parse_response(response)

    def _parse_response(self, response: requests.Response) -> dict[str, Any]:
        status = int(response.status_code)
        payload = self._decode_json(response)

        if status == 401:
            message = self._error_message(payload, "rdagent bridge unauthorized")
            raise RdAgentBridgeError(401, "rdagent_bridge_unauthorized", message)

        if status >= 500:
            message = self._error_message(payload, f"rdagent bridge error ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_error", message)

        if status >= 400:
            message = self._error_message(payload, f"rdagent bridge request failed ({status})")
            raise RdAgentBridgeError(status, "rdagent_bridge_bad_request", message)

        if not isinstance(payload, dict):
            raise RdAgentBridgeError(
                502,
                "rdagent_bridge_invalid_response",
                "rdagent bridge returned non-object JSON",
            )
        return payload

    @staticmethod
    def _decode_json(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            text = (response.text or "").strip()
            return {"error": text} if text else {}

    @staticmethod
    def _error_message(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            for key in ("error", "message", "msg"):
                value = payload.get(key)
                if value:
                    return str(value)
        return fallback
