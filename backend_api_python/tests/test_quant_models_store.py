from unittest.mock import patch

import pytest

from app.services.quant_models.store import publish_quant_model


def test_publish_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        publish_quant_model(
            display_name="Test Model",
            kind="invalid",
            session_id="sess-1",
            loop_index=0,
            universe="csi300",
            owner_user_id=1,
        )


def test_publish_sets_alpha_version():
    captured = []

    class _Cur:
        def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append((sql, params))

        def fetchone(self):
            return {
                "id": 1,
                "model_key": "sess-1_loop0_model",
                "display_name": "My Model",
                "status": "published",
                "kind": "model",
                "alpha_source": "rdagent",
                "alpha_version": "qm_sess-1_loop0_model",
                "universe": "csi300",
                "owner_user_id": 42,
                "provenance_json": {
                    "session_id": "sess-1",
                    "loop_index": 0,
                    "mode": "model",
                },
                "metrics_json": {},
                "published_at": "2026-08-04T10:00:00+00:00",
                "created_at": "2026-08-04T10:00:00+00:00",
                "updated_at": "2026-08-04T10:00:00+00:00",
            }

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.quant_models.store.get_db_connection", return_value=_Db()):
        out = publish_quant_model(
            display_name="My Model",
            kind="model",
            session_id="sess-1",
            loop_index=0,
            universe="csi300",
            owner_user_id=42,
        )

    assert out["model_key"] == "sess-1_loop0_model"
    assert out["alpha_version"] == "qm_sess-1_loop0_model"
    assert out["status"] == "published"
    assert out["provenance_json"]["session_id"] == "sess-1"
    assert out["provenance_json"]["loop_index"] == 0
    assert out["provenance_json"]["mode"] == "model"
    assert len(captured) == 1
    _sql, params = captured[0]
    # model_key, display_name, kind, alpha_source, alpha_version, universe,
    # owner_user_id, provenance_json, metrics_json
    assert params[4] == "qm_sess-1_loop0_model"


def test_list_quant_models_filters_status_and_owner():
    class _Cur:
        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return None

        def fetchall(self):
            return [
                {
                    "id": 1,
                    "model_key": "m1",
                    "display_name": "M1",
                    "status": "published",
                    "kind": "model",
                    "alpha_source": "rdagent",
                    "alpha_version": "qm_m1",
                    "universe": "csi300",
                    "owner_user_id": 7,
                    "provenance_json": {},
                    "metrics_json": {},
                    "published_at": None,
                    "created_at": None,
                    "updated_at": None,
                }
            ]

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.quant_models.store.get_db_connection", return_value=_Db()):
        from app.services.quant_models.store import list_quant_models

        rows = list_quant_models(status="published", owner_user_id=7)
    assert len(rows) == 1
    assert rows[0]["model_key"] == "m1"


def test_archive_quant_model_sets_archived():
    class _Cur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchone(self):
            return {
                "id": 1,
                "model_key": "m1",
                "display_name": "M1",
                "status": "archived",
                "kind": "factor",
                "alpha_source": "rdagent",
                "alpha_version": "qm_m1",
                "universe": "csi300",
                "owner_user_id": None,
                "provenance_json": {},
                "metrics_json": {},
                "published_at": None,
                "created_at": None,
                "updated_at": None,
            }

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.quant_models.store.get_db_connection", return_value=_Db()):
        from app.services.quant_models.store import archive_quant_model

        out = archive_quant_model("m1")
    assert out["status"] == "archived"
    assert out["model_key"] == "m1"
