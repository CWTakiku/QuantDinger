"""Route tests for the quant-model-driven backtest endpoints.

The job worker is mocked so these tests stay fast and DB-free; they focus on
auth, payload validation, and the shape of the response envelope.
"""
from __future__ import annotations

import pytest


def _user_auth_headers(monkeypatch) -> dict:
    from app.utils import auth

    monkeypatch.setattr(
        auth,
        "verify_token",
        lambda token: {
            "sub": "tester",
            "user_id": 7,
            "role": "user",
            "token_version": 1,
            "_verified_username": "tester",
            "_verified_user_role": "user",
        },
    )
    return {"Authorization": "Bearer test-token"}


def _admin_auth_headers(monkeypatch) -> dict:
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


@pytest.fixture(autouse=True)
def _isolated_jobs():
    from app.services.quant_models import jobs

    jobs._reset_for_tests()
    yield
    jobs._reset_for_tests()


def test_run_with_model_requires_login(client):
    resp = client.post("/api/backtest/run-with-model", json={"model_key": "m1"})
    assert resp.status_code == 401


def test_run_with_model_requires_model_key(client, monkeypatch):
    resp = client.post(
        "/api/backtest/run-with-model",
        json={"sourceId": 5, "startDate": "2026-01-05", "endDate": "2026-01-25"},
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["msg"] == "quantModels.modelKeyRequired"


def test_run_with_model_accepts_camel_case_key(client, monkeypatch):
    """``modelKey`` (camelCase from Vue) must be accepted alongside ``model_key``."""
    submitted = {}

    def fake_submit(*, payload, user_id):
        submitted["payload"] = dict(payload)
        submitted["user_id"] = user_id
        return {
            "job_id": "job-123",
            "status": "queued",
            "phase": "queued",
            "progress": None,
            "result": None,
            "error": None,
            "created_at": "2026-08-04T12:00:00.000000Z",
            "started_at": None,
            "finished_at": None,
        }

    monkeypatch.setattr(
        "app.services.quant_models.jobs.submit_prepare_and_backtest",
        fake_submit,
    )

    resp = client.post(
        "/api/backtest/run-with-model",
        json={
            "modelKey": "sess-1_loop0_model",
            "sourceId": 5,
            "startDate": "2026-01-05",
            "endDate": "2026-01-25",
        },
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["code"] == 1
    assert body["msg"] == "queued"
    assert body["data"]["job_id"] == "job-123"
    # The route forwards the authenticated user_id; __user_id injection is the
    # job layer's responsibility (covered by worker unit tests).
    assert submitted["user_id"] == 7
    assert submitted["payload"]["modelKey"] == "sess-1_loop0_model"
    assert "__user_id" not in submitted["payload"]


def test_run_with_model_dispatches_to_submitter(client, monkeypatch):
    submit_calls = []

    def fake_submit(*, payload, user_id):
        submit_calls.append((payload, user_id))
        return {
            "job_id": "job-abc",
            "status": "queued",
            "phase": "queued",
            "progress": None,
            "result": None,
            "error": None,
            "created_at": "2026-08-04T12:00:00.000000Z",
            "started_at": None,
            "finished_at": None,
        }

    monkeypatch.setattr(
        "app.services.quant_models.jobs.submit_prepare_and_backtest",
        fake_submit,
    )

    resp = client.post(
        "/api/backtest/run-with-model",
        json={
            "model_key": "m1",
            "code": "run_weekly(rebalance)",
            "startDate": "2026-01-05",
            "endDate": "2026-01-25",
            "params": {"score_lag_days": 1},
        },
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["data"]["job_id"] == "job-abc"
    payload, user_id = submit_calls[0]
    assert user_id == 1
    assert payload["model_key"] == "m1"
    assert payload["params"]["score_lag_days"] == 1


def test_get_model_job_requires_login(client):
    resp = client.get("/api/backtest/model-jobs/anything")
    assert resp.status_code == 401


def test_get_model_job_not_found(client, monkeypatch):
    resp = client.get(
        "/api/backtest/model-jobs/missing",
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 404
    assert resp.get_json()["msg"] == "strategyV2.backtest.modelJobNotFound"


def test_get_model_job_returns_snapshot(client, monkeypatch):
    from app.services.quant_models import jobs

    # Seed an in-memory job owned by user 7.
    snap = jobs.create_job(
        payload={"model_key": "m1", "__user_id": 7},
        user_id=7,
    )
    jobs.mark_succeeded(snap["job_id"], {"run_id": 11, "metrics": {"sharpe": 1.2}})

    resp = client.get(
        f"/api/backtest/model-jobs/{snap['job_id']}",
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["job_id"] == snap["job_id"]
    assert body["data"]["status"] == "succeeded"
    assert body["data"]["result"]["run_id"] == 11
    # The raw request payload (containing code) must not leak.
    assert "request" not in body["data"]


def test_get_model_job_rejects_other_tenant(client, monkeypatch):
    from app.services.quant_models import jobs

    # Job owned by user 7; caller is user 1 (admin headers).
    snap = jobs.create_job(
        payload={"model_key": "m1", "__user_id": 7},
        user_id=7,
    )
    resp = client.get(
        f"/api/backtest/model-jobs/{snap['job_id']}",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 404


def test_list_model_jobs(client, monkeypatch):
    from app.services.quant_models import jobs

    jobs.create_job(payload={"model_key": "m1", "__user_id": 7}, user_id=7)
    jobs.create_job(payload={"model_key": "m2", "__user_id": 7}, user_id=7)
    # Different tenant — must be filtered out.
    jobs.create_job(payload={"model_key": "m3", "__user_id": 1}, user_id=1)

    resp = client.get(
        "/api/backtest/model-jobs?limit=10",
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    job_ids = [row["job_id"] for row in body["data"]]
    assert len(job_ids) == 2
