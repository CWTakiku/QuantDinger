"""Quant models human API route tests (mock store)."""


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


def test_publish_requires_login(client):
    resp = client.post("/api/quant-models/publish", json={"display_name": "M"})
    assert resp.status_code == 401


def test_publish_requires_admin(client, monkeypatch):
    resp = client.post(
        "/api/quant-models/publish",
        json={"display_name": "M", "kind": "model", "session_id": "s1", "loop_index": 0},
        headers=_user_auth_headers(monkeypatch),
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == 403


def test_publish_ok(client, monkeypatch):
    calls = []

    def fake_publish(**kwargs):
        calls.append(kwargs)
        return {
            "model_key": kwargs.get("model_key") or "sess-1_loop0_model",
            "display_name": kwargs["display_name"],
            "status": "published",
            "kind": kwargs["kind"],
            "alpha_version": "qm_sess-1_loop0_model",
        }

    monkeypatch.setattr("app.routes.quant_models.publish_quant_model", fake_publish)
    resp = client.post(
        "/api/quant-models/publish",
        json={
            "display_name": "My Model",
            "kind": "model",
            "session_id": "sess-1",
            "loop_index": 0,
            "universe": "csi300",
            "metrics": {"sharpe": 1.2},
        },
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["model_key"] == "sess-1_loop0_model"
    assert calls == [
        {
            "display_name": "My Model",
            "kind": "model",
            "session_id": "sess-1",
            "loop_index": 0,
            "universe": "csi300",
            "owner_user_id": 1,
            "model_key": None,
            "alpha_source": "rdagent",
            "metrics": {"sharpe": 1.2},
        }
    ]


def test_publish_missing_display_name(client, monkeypatch):
    resp = client.post(
        "/api/quant-models/publish",
        json={"kind": "model", "session_id": "s1", "loop_index": 0},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    assert resp.get_json()["msg"] == "quantModels.displayNameRequired"


def test_publish_value_error_maps_400(client, monkeypatch):
    def raise_bad_kind(**kwargs):
        raise ValueError("kind must be one of ['factor', 'model']")

    monkeypatch.setattr("app.routes.quant_models.publish_quant_model", raise_bad_kind)
    resp = client.post(
        "/api/quant-models/publish",
        json={"display_name": "M", "kind": "bad", "session_id": "s1", "loop_index": 0},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    assert "kind" in resp.get_json()["msg"]


def test_publish_duplicate_maps_409(client, monkeypatch):
    class FakeUniqueViolation(Exception):
        pgcode = "23505"

    def raise_dup(**kwargs):
        raise FakeUniqueViolation('duplicate key value violates unique constraint "qd_quant_models_model_key_key"')

    monkeypatch.setattr("app.routes.quant_models.publish_quant_model", raise_dup)
    resp = client.post(
        "/api/quant-models/publish",
        json={"display_name": "M", "kind": "model", "session_id": "s1", "loop_index": 0},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 409
    assert "已存在" in resp.get_json()["msg"]


def test_list_requires_login(client):
    resp = client.get("/api/quant-models/")
    assert resp.status_code == 401


def test_list_ok(client, monkeypatch):
    def fake_list(*, status="published", owner_user_id=None):
        assert status == "published"
        return [{"model_key": "m1", "status": "published"}]

    monkeypatch.setattr("app.routes.quant_models.list_quant_models", fake_list)
    resp = client.get("/api/quant-models/?status=published", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"] == [{"model_key": "m1", "status": "published"}]


def test_archive_ok(client, monkeypatch):
    def fake_archive(model_key):
        assert model_key == "m1"
        return {"model_key": "m1", "status": "archived"}

    monkeypatch.setattr("app.routes.quant_models.archive_quant_model", fake_archive)
    resp = client.post(
        "/api/quant-models/m1/archive",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["status"] == "archived"


def test_patch_model_ok(client, monkeypatch):
    calls = []

    def fake_update(model_key, **kwargs):
        calls.append((model_key, kwargs))
        return {
            "model_key": model_key,
            "display_name": kwargs.get("display_name"),
            "universe": kwargs.get("universe"),
            "status": "published",
        }

    monkeypatch.setattr("app.routes.quant_models.update_quant_model", fake_update)
    resp = client.patch(
        "/api/quant-models/m1",
        json={"display_name": "新名称", "universe": "csi500"},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["display_name"] == "新名称"
    assert body["data"]["universe"] == "csi500"
    assert calls == [("m1", {"display_name": "新名称", "universe": "csi500"})]


def test_delete_model_ok(client, monkeypatch):
    def fake_delete(model_key):
        assert model_key == "m1"
        return {"model_key": "m1", "status": "published"}

    monkeypatch.setattr("app.routes.quant_models.delete_quant_model", fake_delete)
    resp = client.delete(
        "/api/quant-models/m1",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["model_key"] == "m1"


def test_delete_model_not_found(client, monkeypatch):
    def raise_not_found(model_key):
        raise ValueError(f"quant model not found: {model_key}")

    monkeypatch.setattr("app.routes.quant_models.delete_quant_model", raise_not_found)
    resp = client.delete(
        "/api/quant-models/missing",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 404


def test_archive_not_found(client, monkeypatch):
    def raise_not_found(model_key):
        raise ValueError(f"quant model not found: {model_key}")

    monkeypatch.setattr("app.routes.quant_models.archive_quant_model", raise_not_found)
    resp = client.post(
        "/api/quant-models/missing/archive",
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    assert "not found" in resp.get_json()["msg"]


def test_get_model_detail_includes_composition(client, monkeypatch):
    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {
            "model_key": key,
            "display_name": "M1",
            "kind": "factor",
            "status": "published",
            "alpha_source": "rdagent",
            "alpha_version": "qm_m1",
            "universe": "csi300",
            "provenance_json": {"session_id": "s1", "loop_index": 1, "mode": "factor"},
            "metrics_json": {},
        },
    )
    monkeypatch.setattr(
        "app.routes.quant_models.build_quant_model_composition",
        lambda model, detail_fetcher=None: {
            "available": True,
            "kind": "factor",
            "session_id": "s1",
            "loop_index": 1,
            "learner": None,
            "factors": [{"name": "f1", "formulation": "x", "description": "d"}],
            "bridge_error": None,
        },
    )
    resp = client.get("/api/quant-models/m1", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["composition"]["factors"][0]["name"] == "f1"


def test_get_model_detail_404(client, monkeypatch):
    monkeypatch.setattr("app.routes.quant_models.get_quant_model", lambda key: None)
    resp = client.get("/api/quant-models/missing", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 404


def test_ensure_scores_published_only(client, monkeypatch):
    calls = {}

    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {
            "model_key": key,
            "status": "published",
            "kind": "model",
            "alpha_source": "rdagent",
            "alpha_version": "qm_m1",
            "universe": "csi300",
            "provenance_json": {"session_id": "s1", "loop_index": 0, "mode": "model"},
        },
    )

    def fake_ensure(model, as_ofs, on_progress=None):
        calls["as_ofs"] = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in as_ofs]
        return {"missing_before": calls["as_ofs"], "inferred": 1, "still_missing": []}

    monkeypatch.setattr("app.routes.quant_models.ensure_quant_model_scores", fake_ensure)
    resp = client.post(
        "/api/quant-models/m1/ensure-scores",
        json={"as_ofs": ["2026-04-10"]},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert calls["as_ofs"] == ["2026-04-10"]


def test_ensure_scores_rejects_missing_provenance(client, monkeypatch):
    ensure_called = []

    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {
            "model_key": key,
            "status": "published",
            "provenance_json": {},
        },
    )

    def fake_ensure(model, as_ofs, on_progress=None):
        ensure_called.append(True)
        return {}

    monkeypatch.setattr("app.routes.quant_models.ensure_quant_model_scores", fake_ensure)
    resp = client.post(
        "/api/quant-models/m1/ensure-scores",
        json={"as_ofs": ["2026-04-10"]},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
    assert "session_id" in resp.get_json()["msg"]
    assert ensure_called == []


def test_ensure_scores_rejects_archived(client, monkeypatch):
    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {"model_key": key, "status": "archived", "provenance_json": {}},
    )
    resp = client.post(
        "/api/quant-models/m1/ensure-scores",
        json={"as_ofs": ["2026-04-10"]},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
