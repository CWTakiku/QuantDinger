"""RDAgent human API route tests (mock bridge client)."""


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


def test_status_requires_admin(client, monkeypatch):
    resp = client.get("/api/rdagent/status", headers=_user_auth_headers(monkeypatch))
    assert resp.status_code == 403
    assert resp.get_json()["code"] == 403


def test_status_requires_login(client):
    resp = client.get("/api/rdagent/status")
    assert resp.status_code == 401


def test_status_ok(client, monkeypatch):
    class Fake:
        def health(self):
            return {"ok": True, "workspace": "/tmp"}

        def list_jobs(self):
            return [
                {"id": "j1", "status": "running", "started_at": "2026-08-02T10:00:00+00:00"},
                {
                    "id": "j2",
                    "status": "succeeded",
                    "started_at": "2026-08-02T09:00:00+00:00",
                    "finished_at": "2026-08-02T09:30:00+00:00",
                    "exit_code": 0,
                },
            ]

        def llm_sync_status(self):
            return {"enabled": True, "applied": True, "chat_model": "openai/glm-5.2"}

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get("/api/rdagent/status", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["ok"] is True
    assert body["data"]["running_jobs"] == [
        {"id": "j1", "status": "running", "started_at": "2026-08-02T10:00:00+00:00"}
    ]
    assert body["data"]["last_job"]["id"] == "j2"
    assert body["data"]["last_job"]["status"] == "succeeded"


def test_list_jobs_ok(client, monkeypatch):
    class Fake:
        def list_jobs(self):
            return [{"id": "j1", "status": "queued"}]

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.get("/api/rdagent/jobs", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 200
    assert resp.get_json()["data"] == [{"id": "j1", "status": "queued"}]


def test_start_job_ok(client, monkeypatch):
    calls = []

    class Fake:
        def start_job(
            self,
            scenario,
            step_n,
            timeout_h=None,
            data_source="default",
            start_date=None,
            end_date=None,
        ):
            calls.append(
                {
                    "scenario": scenario,
                    "step_n": step_n,
                    "timeout_h": timeout_h,
                    "data_source": data_source,
                    "start_date": start_date,
                    "end_date": end_date,
                }
            )
            return {"id": "j-new", "status": "queued"}

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", lambda: Fake())
    resp = client.post(
        "/api/rdagent/jobs",
        json={
            "scenario": "fin_factor",
            "step_n": 2,
            "timeout_h": 1.5,
            "data_source": "quantmind",
            "start_date": "2018-01-01",
            "end_date": "2024-12-31",
        },
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 201
    assert resp.get_json()["data"]["id"] == "j-new"
    assert calls == [
        {
            "scenario": "fin_factor",
            "step_n": 2,
            "timeout_h": 1.5,
            "data_source": "quantmind",
            "start_date": "2018-01-01",
            "end_date": "2024-12-31",
        }
    ]


def test_bridge_error_maps_status_code(client, monkeypatch):
    from app.services.rdagent_bridge import RdAgentBridgeError

    def raise_unreachable():
        raise RdAgentBridgeError(503, "rdagent_bridge_unreachable", "bridge down")

    monkeypatch.setattr("app.routes.rdagent.get_bridge_client", raise_unreachable)
    resp = client.get("/api/rdagent/jobs", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["code"] == 0
    assert body["msg"] == "rdagent_bridge_unreachable: bridge down"
    assert body["data"]["error_code"] == "rdagent_bridge_unreachable"


def test_import_from_session_ok(client, monkeypatch):
    calls = []

    def fake_import(session_id, *, source, version, universe):
        calls.append(
            {"session_id": session_id, "source": source, "version": version, "universe": universe}
        )
        return {
            "export_id": "e1",
            "inserted": 2,
            "source": source,
            "version": version,
            "parsed_rows": 2,
        }

    monkeypatch.setattr("app.routes.rdagent.import_session_scores", fake_import)
    resp = client.post(
        "/api/rdagent/import-from-session",
        json={"session_id": "sess-1", "source": "rdagent", "version": "v1"},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["inserted"] == 2
    assert calls == [
        {
            "session_id": "sess-1",
            "source": "rdagent",
            "version": "v1",
            "universe": "csi300",
        }
    ]


def test_import_from_session_requires_session_id(client, monkeypatch):
    resp = client.post(
        "/api/rdagent/import-from-session",
        json={},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    assert resp.get_json()["msg"] == "rdagent.sessionIdRequired"
