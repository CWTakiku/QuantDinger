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
    covered = {"2026-07-01", "2026-07-08", "2026-07-15"}
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


def test_missing_dates_triggers_qlib_then_infer(monkeypatch):
    model = _sample_model()
    requested = [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15)]
    qlib_calls: list = []
    _patch_qlib(monkeypatch, qlib_calls)

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
        covered.update(["2026-07-08", "2026-07-15"])
        return {
            "export_id": "abc123",
            "row_count": 10,
            "as_of_min": "2026-07-08",
            "as_of_max": "2026-07-15",
            "imported": True,
        }

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        fake_infer,
    )

    out = ensure_quant_model_scores(model, requested)

    assert out["missing_before"] == ["2026-07-08", "2026-07-15"]
    assert out["inferred"] == 2
    assert out["still_missing"] == []
    assert out["export_meta"]["export_id"] == "abc123"
    assert out["qlib_update"]["target"] == "2026-07-15"
    assert qlib_calls == [["2026-07-08", "2026-07-15"]]
    assert infer_calls[0][0] == "2026-08-04_04-24-44-347073"
    assert infer_calls[0][1]["start"] == "2026-07-08"
    assert infer_calls[0][1]["end"] == "2026-07-15"


def test_partial_infer_leaves_still_missing(monkeypatch):
    _patch_qlib(monkeypatch)
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

    assert out["missing_before"] == ["2026-07-08", "2026-07-15"]
    assert out["inferred"] == 1
    assert out["still_missing"] == ["2026-07-15"]


def test_on_progress_called_when_missing(monkeypatch):
    _patch_qlib(monkeypatch)
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
