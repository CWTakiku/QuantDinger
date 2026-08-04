"""Tests for ensure_quant_model_scores."""

from __future__ import annotations

from datetime import date

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


def test_all_present_skips_infer(monkeypatch):
    listed = ["2026-07-01", "2026-07-08", "2026-07-15"]

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda *, source, version: list(listed),
    )
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


def test_missing_dates_triggers_infer_and_recheck(monkeypatch):
    model = _sample_model()
    requested = [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15)]
    list_calls: list[tuple[str, str]] = []

    def fake_list(*, source, version):
        list_calls.append((source, version))
        if len(list_calls) == 1:
            return ["2026-07-01"]
        return ["2026-07-01", "2026-07-08", "2026-07-15"]

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        fake_list,
    )

    infer_calls = []

    def fake_infer(session_id, **kwargs):
        infer_calls.append((session_id, kwargs))
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
    assert len(list_calls) == 2
    assert list_calls[0] == ("rdagent", model["alpha_version"])
    assert infer_calls == [
        (
            "2026-08-04_04-24-44-347073",
            {
                "source": "rdagent",
                "version": model["alpha_version"],
                "universe": "csi300",
                "mode": "model",
                "loop_index": 7,
                "start": "2026-07-08",
                "end": "2026-07-15",
                "do_import": True,
            },
        )
    ]


def test_partial_infer_leaves_still_missing(monkeypatch):
    list_calls = 0

    def fake_list(*, source, version):
        nonlocal list_calls
        list_calls += 1
        if list_calls == 1:
            return []
        return ["2026-07-08"]

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        fake_list,
    )
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.infer_and_import_session_scores",
        lambda session_id, **kwargs: {"export_id": "x", "imported": True},
    )

    out = ensure_quant_model_scores(
        _sample_model(),
        [date(2026, 7, 8), date(2026, 7, 15)],
    )

    assert out["missing_before"] == ["2026-07-08", "2026-07-15"]
    assert out["inferred"] == 1
    assert out["still_missing"] == ["2026-07-15"]


def test_on_progress_called_when_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda *, source, version: [],
    )
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

    assert progress == [
        {
            "phase": "inferring_scores",
            "missing_before": ["2026-07-08"],
            "count": 1,
        }
    ]


def test_on_progress_not_called_when_all_present(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.list_external_alpha_as_ofs",
        lambda *, source, version: ["2026-07-08"],
    )
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
