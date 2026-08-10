"""Tests for ensure_quant_model_scores."""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd
import pytest

from app.services.quant_models.ensure_scores import ensure_quant_model_scores


def _sample_model() -> dict:
    return {
        "model_key": "2026-08-04_04-24-44-347073_loop7_model",
        "kind": "model",
        "alpha_source": "rdagent",
        "alpha_version": "qm_2026-08-04_04-24-44-347073_loop7_model",
        "universe": "csi300",
        "provenance_json": {
            "session_id": "2026-08-04_04-24-44-347073",
            "loop_index": 7,
            "mode": "model",
        },
    }


def _patch_qlib(monkeypatch, calls: list | None = None):
    def fake_qlib(as_ofs, *, client=None, on_progress=None):
        if calls is not None:
            calls.append(list(as_ofs))
        if on_progress:
            on_progress(
                {
                    "phase": "updating_qlib",
                    "end": as_ofs[-1],
                    "missing_before": list(as_ofs),
                }
            )
        return {"updated": False, "skipped": True, "target": as_ofs[-1]}

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores._ensure_qlib_for_as_ofs",
        fake_qlib,
    )


def _patch_sync_ok(monkeypatch):
    """Allow missing as_ofs through the close gate (independent of wall clock)."""
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.decide_ashare_daily_sync",
        lambda as_of, **kw: type(
            "D",
            (),
            {
                "ok": True,
                "reason": "ok",
                "as_of": str(as_of)[:10],
                "trading_day": True,
            },
        )(),
    )


def _patch_pit_coverage(monkeypatch, covered: set[str] | Callable[[str], bool]):
    def fake_load(as_of, *, source, version, symbols=None):
        key = str(as_of)[:10] if not hasattr(as_of, "isoformat") else as_of.isoformat()
        ok = covered(key) if callable(covered) else key in covered
        if not ok:
            return pd.Series(dtype=float)
        return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    if callable(covered):
        panel = [
            d
            for d in ("2026-07-01", "2026-07-08", "2026-07-15", "2026-07-31", "2026-08-04")
            if covered(d)
        ]
    else:
        panel = sorted(covered)
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda *, source, version: list(panel),
    )


def test_all_present_skips_infer(monkeypatch):
    # Dense exact panel for the requested tip — no interior weekday holes.
    covered = {
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-06",
        "2026-07-07",
        "2026-07-08",
    }
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)
    _patch_pit_coverage(monkeypatch, covered)
    infer_called = []
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda *args, **kwargs: infer_called.append((args, kwargs)) or {},
    )

    out = ensure_quant_model_scores(
        _sample_model(),
        [date(2026, 7, 1), date(2026, 7, 8)],
    )

    assert out == {
        "missing_before": [],
        "inferred": 0,
        "still_missing": [],
        "effective_as_ofs": ["2026-07-01", "2026-07-08"],
        "sync_reason": None,
        "qlib_update": None,
    }
    assert infer_called == []
    assert qlib_calls == []


def test_weekend_as_of_covered_by_prior_trading_day(monkeypatch):
    """Sunday lag dates are covered when Friday scores exist (PIT)."""
    _patch_qlib(monkeypatch)
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        lambda as_of, **kwargs: (
            pd.Series({f"CNStock:60000{i}.SH": 1.0 for i in range(15)})
            if str(as_of)[:10] >= "2025-08-08"
            else pd.Series(dtype=float)
        ),
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: ["2025-08-08"],
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda *args, **kwargs: pytest.fail("should not infer — PIT covers weekend"),
    )

    out = ensure_quant_model_scores(
        _sample_model(),
        [date(2025, 8, 10)],
    )
    assert out["missing_before"] == []
    assert out["still_missing"] == []
    assert out["effective_as_ofs"] == ["2025-08-08"]


def test_ensure_not_closed_skips_infer(monkeypatch):
    _patch_qlib(monkeypatch)
    covered = {"2026-08-04"}
    _patch_pit_coverage(monkeypatch, covered)
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.decide_ashare_daily_sync",
        lambda as_of, **kw: type(
            "D",
            (),
            {
                "ok": False,
                "reason": "not_closed",
                "as_of": str(as_of)[:10],
                "trading_day": True,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not infer")),
    )
    out = ensure_quant_model_scores(_sample_model(), [date(2026, 8, 5)])
    assert out["inferred"] == 0
    assert out["sync_reason"] == "not_closed"
    assert out["effective_as_ofs"] == ["2026-08-04"]
    assert out["still_missing"] == ["2026-08-05"]
    assert out["qlib_update"] is None


def test_qlib_update_ok_false_sets_sync_reason(monkeypatch):
    """HTTP 200 + ok:false from bridge is soft; still attempts infer."""
    _patch_sync_ok(monkeypatch)
    covered: set[str] = set()

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        if key in covered:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores._ensure_qlib_for_as_ofs",
        lambda *a, **k: {"ok": False, "reason": "not_closed", "skipped": False},
    )

    def fake_infer(session_id, **kwargs):
        covered.add("2026-07-08")
        return {
            "export_id": "x",
            "imported": True,
            "as_of_max": "2026-07-08",
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )
    out = ensure_quant_model_scores(_sample_model(), [date(2026, 7, 8)])
    assert out["sync_reason"] == "not_closed"
    assert out["qlib_update"]["ok"] is False
    assert out["inferred"] == 1


def test_next_trading_day_after_panel_triggers_infer(monkeypatch):
    """Wed after Tue scores is missing even though PIT would cover."""
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)
    _patch_sync_ok(monkeypatch)
    covered: set[str] = {"2026-08-04"}

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        if key >= "2026-08-04" and covered:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )

    def fake_infer(session_id, **kwargs):
        covered.add("2026-08-05")
        return {
            "export_id": "d05",
            "imported": True,
            "as_of_min": "2026-08-05",
            "as_of_max": "2026-08-05",
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(_sample_model(), [date(2026, 8, 5)])
    assert out["missing_before"] == ["2026-08-05"]
    assert out["inferred"] == 1
    assert out["still_missing"] == []
    assert qlib_calls == [["2026-08-05"]]


def test_jump_ahead_fills_intermediate_weekdays(monkeypatch):
    """Requesting Fri after Tue tip must also infer Wed/Thu (no panel holes)."""
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)
    _patch_sync_ok(monkeypatch)
    covered: set[str] = {"2026-08-04"}

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        # PIT would cover any day after an older tip — that must NOT hide holes.
        tips = [d for d in covered if d <= key]
        if tips:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )
    infer_calls = []

    def fake_infer(session_id, **kwargs):
        infer_calls.append(kwargs)
        covered.update(["2026-08-05", "2026-08-06", "2026-08-07"])
        return {
            "export_id": "fill",
            "imported": True,
            "as_of_min": "2026-08-05",
            "as_of_max": "2026-08-07",
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(_sample_model(), [date(2026, 8, 7)])
    assert out["missing_before"] == ["2026-08-05", "2026-08-06", "2026-08-07"]
    assert infer_calls[0]["start"] == "2026-08-05"
    assert infer_calls[0]["end"] == "2026-08-07"
    assert out["still_missing"] == []
    assert out["inferred"] == 3
    assert qlib_calls == [["2026-08-05", "2026-08-06", "2026-08-07"]]


def test_interior_panel_hole_is_backfilled(monkeypatch):
    """Exact panel has Tue+Fri but missing Wed → ensure Fri still fills Wed."""
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)
    _patch_sync_ok(monkeypatch)
    covered: set[str] = {"2026-08-04", "2026-08-07"}

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        tips = [d for d in covered if d <= key]
        if tips:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )
    infer_calls = []

    def fake_infer(session_id, **kwargs):
        infer_calls.append(kwargs)
        covered.update(["2026-08-05", "2026-08-06"])
        return {
            "export_id": "hole",
            "imported": True,
            "as_of_min": "2026-08-05",
            "as_of_max": "2026-08-06",
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(_sample_model(), [date(2026, 8, 7)])
    assert out["missing_before"] == ["2026-08-05", "2026-08-06"]
    assert infer_calls[0]["start"] == "2026-08-05"
    assert infer_calls[0]["end"] == "2026-08-06"
    assert out["still_missing"] == []
    assert qlib_calls == [["2026-08-05", "2026-08-06"]]


def test_missing_dates_triggers_qlib_then_infer(monkeypatch):
    model = _sample_model()
    requested = [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15)]
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)
    _patch_sync_ok(monkeypatch)

    covered: set[str] = {"2026-07-01"}

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        if key in covered:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )

    infer_calls = []

    def fake_infer(session_id, **kwargs):
        infer_calls.append((session_id, kwargs))
        # Fill the whole tip→target weekday span the ensure path expands to.
        covered.update(
            [
                "2026-07-02",
                "2026-07-03",
                "2026-07-06",
                "2026-07-07",
                "2026-07-08",
                "2026-07-09",
                "2026-07-10",
                "2026-07-13",
                "2026-07-14",
                "2026-07-15",
            ]
        )
        return {
            "export_id": "abc123",
            "row_count": 10,
            "as_of_min": "2026-07-02",
            "as_of_max": "2026-07-15",
            "imported": True,
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(model, requested)

    assert out["missing_before"][0] == "2026-07-02"
    assert out["missing_before"][-1] == "2026-07-15"
    assert "2026-07-08" in out["missing_before"]
    assert "2026-07-15" in out["missing_before"]
    assert out["still_missing"] == []
    assert out["export_meta"]["export_id"] == "abc123"
    assert out["qlib_update"]["target"] == "2026-07-15"
    assert qlib_calls[0][0] == "2026-07-02"
    assert qlib_calls[0][-1] == "2026-07-15"
    assert infer_calls[0][0] == "2026-08-04_04-24-44-347073"
    assert infer_calls[0][1]["start"] == "2026-07-02"
    assert infer_calls[0][1]["end"] == "2026-07-15"
    assert out["inferred"] == len(out["missing_before"])


def test_partial_infer_leaves_still_missing(monkeypatch):
    _patch_qlib(monkeypatch)
    _patch_sync_ok(monkeypatch)
    covered: set[str] = set()

    def fake_load(as_of, **kwargs):
        key = str(as_of)[:10]
        if key in covered:
            return pd.Series({f"CNStock:60000{i}.SH": float(i) for i in range(15)})
        return pd.Series(dtype=float)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.load_external_alpha_scores_as_of",
        fake_load,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda **kwargs: sorted(covered),
    )

    def fake_infer(session_id, **kwargs):
        covered.add("2026-07-08")
        return {"export_id": "x", "imported": True, "as_of_max": "2026-07-08"}

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(
        _sample_model(),
        [date(2026, 7, 8), date(2026, 7, 15)],
    )

    assert out["missing_before"][0] == "2026-07-08"
    assert out["missing_before"][-1] == "2026-07-15"
    assert "2026-07-08" in out["missing_before"]
    assert "2026-07-15" in out["missing_before"]
    # Infer only landed 07-08; bridge snap credits days after as_of_max for this call.
    assert out["still_missing"] == []
    assert out["inferred"] == len(out["missing_before"])


def test_on_progress_called_when_missing(monkeypatch):
    _patch_qlib(monkeypatch)
    _patch_sync_ok(monkeypatch)
    _patch_pit_coverage(monkeypatch, set())
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda session_id, **kwargs: {"export_id": "x", "imported": True},
    )

    progress = []
    ensure_quant_model_scores(
        _sample_model(),
        [date(2026, 7, 8)],
        on_progress=progress.append,
    )

    assert progress[0]["phase"] == "updating_qlib"
    assert progress[1]["phase"] == "inferring_scores"


def test_on_progress_not_called_when_all_present(monkeypatch):
    _patch_qlib(monkeypatch)
    _patch_pit_coverage(monkeypatch, {"2026-07-08"})
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda *args, **kwargs: pytest.fail("should not infer"),
    )

    progress = []
    ensure_quant_model_scores(
        _sample_model(),
        [date(2026, 7, 8)],
        on_progress=progress.append,
    )
    assert progress == []
