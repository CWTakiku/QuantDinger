"""RDAgent import-from-session orchestration tests."""

from __future__ import annotations

CSV = "as_of,symbol,score\n2026-07-25,600519,1.2\n"


class _FakeClient:
    def export_session(self, session_id, source, version, universe):
        assert session_id == "2026-08-01_17-48-00-363060"
        assert source == "rdagent"
        assert version == "v_test"
        assert universe == "csi300"
        return {
            "export_id": "abc123def4567890abcdef1234567890",
            "row_count": 1,
            "source": source,
            "version": version,
        }

    def download_export(self, export_id):
        assert export_id == "abc123def4567890abcdef1234567890"
        return CSV


def test_import_session(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: calls.append(rows) or {"upserted": len(rows)},
    )

    from app.services.rdagent_bridge.import_session import import_session_scores

    out = import_session_scores(
        "2026-08-01_17-48-00-363060",
        source="rdagent",
        version="v_test",
        client=_FakeClient(),
    )
    assert out["upserted"] == 1
    assert calls[0][0]["symbol"]
    assert calls[0][0]["source"] == "rdagent"
    assert calls[0][0]["version"] == "v_test"
    assert calls[0][0]["universe"] == "csi300"


def test_default_version_truncated():
    from app.services.rdagent_bridge.import_session import default_session_version

    session_id = "x" * 200
    version = default_session_version(session_id)
    assert version.startswith("session_")
    assert len(version) == 120
