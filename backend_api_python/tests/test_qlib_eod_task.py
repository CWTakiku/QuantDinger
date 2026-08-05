"""Tests for Celery Qlib EOD sync task."""

from __future__ import annotations


def test_qlib_eod_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_QLIB_EOD_SYNC", "false")
    from app.tasks.qlib_eod import run_qlib_eod_sync_task

    assert run_qlib_eod_sync_task.run() == {"skipped": True, "reason": "disabled"}


def test_qlib_eod_skips_when_not_closed(monkeypatch):
    monkeypatch.setenv("ENABLE_QLIB_EOD_SYNC", "true")
    monkeypatch.setattr(
        "app.tasks.qlib_eod.decide_ashare_daily_sync",
        lambda as_of, **kw: type(
            "D",
            (),
            {
                "ok": False,
                "reason": "not_closed",
                "as_of": "2026-08-05",
                "trading_day": True,
            },
        )(),
    )
    from app.tasks.qlib_eod import run_qlib_eod_sync_task

    out = run_qlib_eod_sync_task.run()
    assert out["skipped"] is True
    assert out["reason"] == "not_closed"


def test_qlib_eod_skips_when_already_synced(monkeypatch):
    monkeypatch.setenv("ENABLE_QLIB_EOD_SYNC", "true")
    monkeypatch.setattr(
        "app.tasks.qlib_eod.decide_ashare_daily_sync",
        lambda as_of, **kw: type(
            "D",
            (),
            {"ok": True, "reason": "ok", "as_of": "2026-08-05", "trading_day": True},
        )(),
    )
    monkeypatch.setattr("app.tasks.qlib_eod._already_synced", lambda day: True)
    from app.tasks.qlib_eod import run_qlib_eod_sync_task

    out = run_qlib_eod_sync_task.run(as_of="2026-08-05")
    assert out == {"skipped": True, "reason": "already_synced", "as_of": "2026-08-05"}


def test_qlib_eod_calls_qlib_update_when_ok(monkeypatch):
    monkeypatch.setenv("ENABLE_QLIB_EOD_SYNC", "true")
    monkeypatch.setenv("ENABLE_QLIB_EOD_SCORE_WARMUP", "false")
    monkeypatch.setattr(
        "app.tasks.qlib_eod.decide_ashare_daily_sync",
        lambda as_of, **kw: type(
            "D",
            (),
            {"ok": True, "reason": "ok", "as_of": as_of, "trading_day": True},
        )(),
    )
    monkeypatch.setattr("app.tasks.qlib_eod._already_synced", lambda day: False)
    marked = []
    monkeypatch.setattr("app.tasks.qlib_eod._mark_synced", lambda day: marked.append(day))

    class FakeClient:
        @classmethod
        def from_env(cls):
            return cls()

        def qlib_update(self, *, end=None, force=False):
            return {"ok": True, "end": end, "force": force}

    monkeypatch.setattr("app.tasks.qlib_eod.RdAgentBridgeClient", FakeClient)
    from app.tasks.qlib_eod import run_qlib_eod_sync_task

    out = run_qlib_eod_sync_task.run(as_of="2026-08-05")
    assert out["ok"] is True
    assert out["as_of"] == "2026-08-05"
    assert out["qlib_update"]["end"] == "2026-08-05"
    assert out["warmup_models"] == 0
    assert marked == ["2026-08-05"]
