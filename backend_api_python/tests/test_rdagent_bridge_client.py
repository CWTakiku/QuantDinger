"""RDAgent bridge HTTP client tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.services.rdagent_bridge.client import RdAgentBridgeClient
from app.services.rdagent_bridge.errors import RdAgentBridgeError


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("RDAGENT_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("RDAGENT_BRIDGE_URL", "http://127.0.0.1:19901")
    with pytest.raises(RdAgentBridgeError) as exc_info:
        RdAgentBridgeClient.from_env()
    assert exc_info.value.status_code == 500


def test_health_maps_connection_error(monkeypatch):
    monkeypatch.setenv("RDAGENT_BRIDGE_TOKEN", "t")
    monkeypatch.setenv("RDAGENT_BRIDGE_URL", "http://127.0.0.1:1")
    client = RdAgentBridgeClient.from_env()
    with pytest.raises(RdAgentBridgeError) as exc_info:
        client.health()
    err = exc_info.value
    assert err.status_code == 503
    assert "bridge" in err.code.lower() or "rdagent" in err.code.lower()


def test_from_env_uses_timeout(monkeypatch):
    monkeypatch.setenv("RDAGENT_BRIDGE_TOKEN", "t")
    monkeypatch.setenv("RDAGENT_BRIDGE_URL", "http://127.0.0.1:19901")
    monkeypatch.setenv("RDAGENT_BRIDGE_TIMEOUT_S", "12.5")
    client = RdAgentBridgeClient.from_env()
    assert client.timeout_s == 12.5


def test_health_returns_payload(monkeypatch):
    monkeypatch.setattr(
        "app.services.rdagent_bridge.client.requests.request",
        lambda *args, **kwargs: MagicMock(
            status_code=200,
            json=lambda: {"ok": True, "workspace_exists": True},
        ),
    )
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    assert client.health() == {"ok": True, "workspace_exists": True}


def test_list_jobs_extracts_jobs(monkeypatch):
    monkeypatch.setattr(
        "app.services.rdagent_bridge.client.requests.request",
        lambda *args, **kwargs: MagicMock(
            status_code=200,
            json=lambda: {"jobs": [{"id": "j1", "status": "running"}]},
        ),
    )
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    assert client.list_jobs() == [{"id": "j1", "status": "running"}]


def test_start_job_posts_payload(monkeypatch):
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(status_code=201, json=lambda: {"id": "j2", "status": "running"})

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    out = client.start_job(
        "fin_factor",
        loop_n=3,
        timeout_h=1.5,
        start_date="2018-01-01",
        end_date="2024-12-31",
    )
    assert out["id"] == "j2"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:19901/v1/jobs"
    assert captured["kwargs"]["json"] == {
        "scenario": "fin_factor",
        "loop_n": 3,
        "data_source": "default",
        "timeout_h": 1.5,
        "start_date": "2018-01-01",
        "end_date": "2024-12-31",
    }
    assert captured["kwargs"]["headers"]["X-RDAgent-Bridge-Token"] == "token"


def test_unauthorized_maps_to_bridge_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.rdagent_bridge.client.requests.request",
        lambda *args, **kwargs: MagicMock(
            status_code=401,
            json=lambda: {"error": "unauthorized"},
        ),
    )
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "bad")
    with pytest.raises(RdAgentBridgeError) as exc_info:
        client.list_jobs()
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "rdagent_bridge_unauthorized"


def test_server_error_maps_to_bridge_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.rdagent_bridge.client.requests.request",
        lambda *args, **kwargs: MagicMock(
            status_code=500,
            json=lambda: {"error": "ui app not found"},
        ),
    )
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    with pytest.raises(RdAgentBridgeError) as exc_info:
        client.ui_start()
    err = exc_info.value
    assert err.status_code == 500
    assert err.code == "rdagent_bridge_error"
    assert "ui app not found" in err.message


def test_timeout_maps_to_unreachable(monkeypatch):
    def fake_request(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    with pytest.raises(RdAgentBridgeError) as exc_info:
        client.get_job("j1")
    assert exc_info.value.status_code == 503


def test_export_session_without_loop_index(monkeypatch):
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(status_code=201, json=lambda: {"export_id": "e1"})

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    out = client.export_session("sess-1", "rdagent", "v1", "csi300")
    assert out["export_id"] == "e1"
    assert captured["kwargs"]["json"] == {
        "session": "sess-1",
        "source": "rdagent",
        "version": "v1",
        "universe": "csi300",
    }


def test_export_session_with_loop_index(monkeypatch):
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["kwargs"] = kwargs
        return MagicMock(status_code=201, json=lambda: {"export_id": "e2"})

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    client.export_session("sess-1", "rdagent", "v_loop7", "csi300", loop_index=7)
    assert captured["kwargs"]["json"]["loop_index"] == 7


def test_qlib_update_posts_end(monkeypatch):
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(
            status_code=200,
            json=lambda: {"updated": True, "calendar_end": "2026-08-04"},
        )

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    out = client.qlib_update(end="2026-08-04")
    assert out["updated"] is True
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:19901/v1/qlib/update"
    assert captured["kwargs"]["json"] == {"force": False, "end": "2026-08-04"}
    assert captured["kwargs"]["timeout"] >= 900


def test_factor_matrix_gets_json(monkeypatch):
    captured: dict = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(
            status_code=200,
            json=lambda: {"session_id": "sess-1", "loop_index": 0, "columns": ["f1"]},
        )

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.request", fake_request)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    out = client.factor_matrix("sess-1", loop_index=0, sample_dates=3, max_symbols=10)
    assert out["session_id"] == "sess-1"
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:19901/v1/sessions/sess-1/factor-matrix"
    assert captured["kwargs"]["params"] == {
        "loop_index": 0,
        "sample_dates": 3,
        "max_symbols": 10,
    }


def test_factor_matrix_csv_returns_bytes(monkeypatch):
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock(
            status_code=200,
            content=b"date,symbol,f1\n",
            headers={"Content-Type": "text/csv"},
        )

    monkeypatch.setattr("app.services.rdagent_bridge.client.requests.get", fake_get)
    client = RdAgentBridgeClient("http://127.0.0.1:19901", "token")
    body, ctype = client.factor_matrix_csv("sess-1", loop_index=1, max_rows=1000)
    assert body == b"date,symbol,f1\n"
    assert ctype == "text/csv"
    assert captured["url"] == "http://127.0.0.1:19901/v1/sessions/sess-1/factor-matrix.csv"
    assert captured["kwargs"]["params"] == {"loop_index": 1, "max_rows": 1000}
