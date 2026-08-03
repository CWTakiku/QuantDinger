"""RDAgent import-from-session orchestration tests."""

from __future__ import annotations

CSV = "as_of,symbol,score\n2026-07-25,600519,1.2\n"


class _FakeClient:
    def export_session(self, session_id, source, version, universe, loop_index=None):
        assert session_id == "2026-08-01_17-48-00-363060"
        assert source == "rdagent"
        assert version == "v_test"
        assert universe == "csi300"
        assert loop_index is None
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


def test_default_version_with_loop_index():
    from app.services.rdagent_bridge.import_session import default_session_version

    session_id = "2026-08-01_17-48-00-363060"
    assert default_session_version(session_id, loop_index=7) == f"session_{session_id}_loop7"


def test_import_session_with_loop_index(monkeypatch):
    export_calls = []

    class _LoopClient:
        def export_session(self, session_id, source, version, universe, loop_index=None):
            export_calls.append(
                {
                    "session_id": session_id,
                    "source": source,
                    "version": version,
                    "universe": universe,
                    "loop_index": loop_index,
                }
            )
            return {
                "export_id": "abc123def4567890abcdef1234567890",
                "row_count": 1,
                "source": source,
                "version": version,
            }

        def download_export(self, export_id):
            return CSV

    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: {"upserted": len(rows)},
    )

    from app.services.rdagent_bridge.import_session import import_session_scores

    session_id = "2026-08-01_17-48-00-363060"
    out = import_session_scores(session_id, loop_index=7, client=_LoopClient())
    assert out["upserted"] == 1
    assert export_calls == [
        {
            "session_id": session_id,
            "source": "rdagent",
            "version": f"session_{session_id}_loop7",
            "universe": "csi300",
            "loop_index": 7,
        }
    ]


def test_import_session_without_loop_index_unchanged(monkeypatch):
    export_calls = []

    class _LegacyClient:
        def export_session(self, session_id, source, version, universe, loop_index=None):
            export_calls.append({"loop_index": loop_index})
            return {
                "export_id": "abc123def4567890abcdef1234567890",
                "row_count": 1,
                "source": source,
                "version": version,
            }

        def download_export(self, export_id):
            return CSV

    monkeypatch.setattr(
        "app.services.rdagent_bridge.import_session.persist_external_alpha_scores",
        lambda rows: {"upserted": len(rows)},
    )

    from app.services.rdagent_bridge.import_session import (
        default_session_version,
        import_session_scores,
    )

    session_id = "2026-08-01_17-48-00-363060"
    import_session_scores(session_id, client=_LegacyClient())
    assert export_calls[0]["loop_index"] is None
    assert default_session_version(session_id) == f"session_{session_id}"
