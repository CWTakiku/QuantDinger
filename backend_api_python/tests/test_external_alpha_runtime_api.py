from app.services.strategy_v2 import contract


def test_external_alpha_scores_allowed_in_contract():
    assert "get_external_alpha_scores" in contract._RUNTIME_GLOBAL_CALL_NAMES
