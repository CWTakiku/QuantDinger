"""Tests for infer-and-import orchestration."""

from __future__ import annotations


class _FakeInferClient:
    def infer_session(self, session_id, **kwargs):
        assert session_id == "2026-08-04_04-24-44-347073"
        assert kwargs.get("mode") == "model"
        assert kwargs.get("loop_index") == 7
        return {
            "export_id": "abc123def4567890abcdef1234567890",
            "row_count": 2,
            "as_of_min": "2026-07-01",
            "as_of_max": "2026-08-03",
            "source": kwargs.get("source"),
            "version": kwargs.get("version"),
            "mode": "model",
        }

    def download_export(self, export_id):
        return "as_of,symbol,score\n2026-08-03,600000.SH,0.1\n2026-08-03,000001.SZ,-0.2\n"


def test_infer_and_import(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: calls.append(rows) or {"inserted": len(rows), "upserted": len(rows)},
    )

    from app.services.rdagent_bridge.import_session import (
        default_infer_version,
        infer_and_import_session_scores,
    )

    out = infer_and_import_session_scores(
        "2026-08-04_04-24-44-347073",
        mode="model",
        loop_index=7,
        client=_FakeInferClient(),
    )
    assert out["imported"] is True
    assert out["inserted"] == 2
    assert out["as_of_max"] == "2026-08-03"
    assert len(calls[0]) == 2
    assert default_infer_version("abc", 3, "factor").endswith("_infer_factor")


def test_infer_without_import(monkeypatch):
    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: (_ for _ in ()).throw(AssertionError("should not persist")),
    )
    from app.services.rdagent_bridge.import_session import infer_and_import_session_scores

    out = infer_and_import_session_scores(
        "2026-08-04_04-24-44-347073",
        mode="model",
        loop_index=7,
        do_import=False,
        client=_FakeInferClient(),
    )
    assert out["imported"] is False
    assert out["row_count"] == 2
