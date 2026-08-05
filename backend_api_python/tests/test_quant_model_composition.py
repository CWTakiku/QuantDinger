from app.services.quant_models.composition import build_quant_model_composition


def test_build_composition_factor_loop():
    model = {
        "kind": "factor",
        "provenance_json": {"session_id": "sess-1", "loop_index": 2, "mode": "factor"},
    }

    def fake_detail(session_id, include=None):
        assert session_id == "sess-1"
        return {
            "loops": [
                {
                    "loop_index": 2,
                    "kind": "factor",
                    "artifacts": [
                        {
                            "kind": "factor",
                            "name": "mom20",
                            "formulation": "close/close_20-1",
                            "description": "momentum",
                        }
                    ],
                    "factors": [
                        {
                            "kind": "factor",
                            "name": "mom20",
                            "formulation": "close/close_20-1",
                            "description": "momentum",
                        }
                    ],
                }
            ],
            "sota_library": [],
        }

    out = build_quant_model_composition(model, detail_fetcher=fake_detail)
    assert out["available"] is True
    assert out["kind"] == "factor"
    assert out["learner"] is None
    assert out["factors"][0]["name"] == "mom20"
    assert out["bridge_error"] is None


def test_build_composition_model_loop_with_sota_factors():
    model = {
        "kind": "model",
        "provenance_json": {"session_id": "sess-1", "loop_index": 5, "mode": "model"},
    }

    def fake_detail(session_id, include=None):
        return {
            "loops": [
                {
                    "loop_index": 5,
                    "kind": "model",
                    "artifacts": [
                        {
                            "kind": "model",
                            "name": "lstm_v2",
                            "model_type": "pytorch",
                            "architecture": "LSTM",
                            "hyperparameters": {"lr": 0.01},
                        }
                    ],
                }
            ],
            "sota_library": [
                {"name": "f1", "formulation": "a", "description": "d1"},
            ],
            "sota_model": {
                "name": "lstm_v2",
                "architecture": "LSTM",
                "model_type": "pytorch",
                "loop_index": 5,
            },
        }

    out = build_quant_model_composition(model, detail_fetcher=fake_detail)
    assert out["learner"]["model_type"] == "pytorch"
    assert out["learner"]["architecture"] == "LSTM"
    assert out["factors"][0]["name"] == "f1"


def test_build_composition_loop_not_found():
    model = {
        "kind": "factor",
        "provenance_json": {"session_id": "sess-1", "loop_index": 3, "mode": "factor"},
    }

    def fake_detail(session_id, include=None):
        assert session_id == "sess-1"
        return {
            "loops": [
                {"loop_index": 1, "artifacts": []},
                {"loop_index": 2, "artifacts": []},
                {"loop_index": None, "artifacts": []},
                {"loop_index": "bad", "artifacts": []},
            ],
            "sota_library": [],
        }

    out = build_quant_model_composition(model, detail_fetcher=fake_detail)
    assert out["available"] is False
    assert out["kind"] == "factor"
    assert out["session_id"] == "sess-1"
    assert out["loop_index"] == 3
    assert out["learner"] is None
    assert out["factors"] == []
    assert out["bridge_error"] == "会话中未找到 Loop_3"


def test_build_composition_bridge_down():
    from app.services.rdagent_bridge.errors import RdAgentBridgeError

    model = {
        "kind": "model",
        "provenance_json": {"session_id": "sess-1", "loop_index": 0, "mode": "model"},
    }

    def boom(session_id, include=None):
        raise RdAgentBridgeError(503, "rdagent_bridge_unreachable", "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge")

    out = build_quant_model_composition(model, detail_fetcher=boom)
    assert out["available"] is False
    assert out["learner"] is None
    assert out["factors"] == []
    assert "bridge" in (out["bridge_error"] or "").lower() or "rdagent" in (out["bridge_error"] or "").lower()
