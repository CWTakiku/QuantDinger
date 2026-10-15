import pandas as pd
import pytest

from app.services.quant_models.leak_gate import (
    assert_no_lookahead,
    code_hash,
    truncation_changes_history,
    validate_publish,
)


def test_shift_minus_one_is_rejected():
    with pytest.raises(ValueError, match="future"):
        assert_no_lookahead("out = close.shift(-1)")


def test_past_shift_is_allowed():
    assert_no_lookahead("out = close.shift(1) / close") is None


def test_truncation_detects_a_future_shift():
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0]})

    def leaky(data):
        return data["close"].shift(-1)

    def clean(data):
        return data["close"].shift(1)

    assert truncation_changes_history(leaky, frame) is True
    assert truncation_changes_history(clean, frame) is False


def test_publish_requires_tip_hash_and_a_metric():
    with pytest.raises(ValueError, match="data_tip"):
        validate_publish(kind="model", metrics={"code_hash": "a" * 64, "ic": 0.1})


def test_factor_publish_rejects_a_different_score_method():
    source = "alpha = close.shift(1)"
    with pytest.raises(ValueError, match="score_method"):
        validate_publish(
            kind="factor",
            factor_source=source,
            metrics={
                "data_tip": "2026-10-09",
                "code_hash": code_hash(source),
                "ic": 0.02,
                "score_method": "lgb",
            },
        )


def test_factor_publish_rejects_mismatched_hash():
    source = "alpha = close.shift(1)"
    with pytest.raises(ValueError, match="code_hash"):
        validate_publish(
            kind="factor",
            factor_source=source,
            metrics={
                "data_tip": "2026-10-09",
                "code_hash": "b" * 64,
                "rank_ic": 0.03,
                "score_method": "cs_zmean",
            },
        )
