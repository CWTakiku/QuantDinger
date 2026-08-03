"""RDAgent session detail proxy route tests."""


def _user_auth_headers(monkeypatch):
    from app.utils import auth

    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {
            "sub": "tester",
            "user_id": 1,
            "role": "user",
            "token_version": 1,
            "_verified_username": "tester",
            "_verified_user_role": "user",
        },
    )
    return {"Authorization": "Bearer test-token"}


def _admin_auth_headers(monkeypatch):
    from app.utils import auth

    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {
            "sub": "admin",
            "user_id": 1,
            "role": "admin",
            "token_version": 1,
            "_verified_username": "admin",
            "_verified_user_role": "admin",
        },
    )
    return {"Authorization": "Bearer admin-token"}


def test_session_detail_requires_admin(client, monkeypatch):
    resp = client.get(
        "/api/rdagent/sessions/sess-1/detail",
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == 403


def test_session_detail_requires_login(client):
    resp = client.get("/api/rdagent/sessions/sess-1/detail")
    assert resp.status_code == 401


def test_session_detail_ok(client, monkeypatch):
    calls = []

    class Fake:
        def session_detail(self, session_id, include=None):
            calls.append({"session_id": session_id, "include": include})
            return {"session_id": session_id, "loops": []}

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get(
        "/api/rdagent/sessions/sess-1/detail?include=summary,loops",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["session_id"] == "sess-1"
    assert calls == [{"session_id": "sess-1", "include": "summary,loops"}]


def test_session_detail_without_include(client, monkeypatch):
    calls = []

    class Fake:
        def session_detail(self, session_id, include=None):
            calls.append({"session_id": session_id, "include": include})
            return {"session_id": session_id}

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get(
        "/api/rdagent/sessions/sess-2/detail",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["session_id"] == "sess-2"
    assert calls == [{"session_id": "sess-2", "include": None}]


def test_session_detail_bridge_error(client, monkeypatch):
    from app.services.rdagent_bridge import RdAgentBridgeError

    class Fake:
        def session_detail(self, session_id, include=None):
            raise RdAgentBridgeError(404, "rdagent_bridge_bad_request", "session not found")

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get(
        "/api/rdagent/sessions/missing/detail",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == 0
    assert body["data"]["error_code"] == "rdagent_bridge_bad_request"


def test_session_metrics_csv_requires_admin(client, monkeypatch):
    resp = client.get(
        "/api/rdagent/sessions/sess-1/metrics.csv",
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 403


def test_session_metrics_csv_ok(client, monkeypatch):
    calls = []

    class Fake:
        def session_metrics_csv(self, session_id):
            calls.append(session_id)
            return b"loop,metric\n1,0.5\n", "text/csv"

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get(
        "/api/rdagent/sessions/sess-1/metrics.csv",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert resp.data == b"loop,metric\n1,0.5\n"
    assert "text/csv" in resp.content_type
    assert 'filename="session_sess-1_metrics.csv"' in resp.headers.get("Content-Disposition", "")
    assert calls == ["sess-1"]


def test_session_metrics_csv_bridge_error(client, monkeypatch):
    from app.services.rdagent_bridge import RdAgentBridgeError

    class Fake:
        def session_metrics_csv(self, session_id):
            raise RdAgentBridgeError(503, "rdagent_bridge_unreachable", "bridge down")

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get(
        "/api/rdagent/sessions/sess-1/metrics.csv",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == 0
    assert body["data"]["error_code"] == "rdagent_bridge_unreachable"
