"""Tests for QD → rdagent-bridge LLM mirror push helper."""

from app.services.rdagent_bridge.llm_sync import filter_qd_llm_env, push_llm_settings_to_bridge


def test_filter_qd_llm_env_keeps_provider_slice():
    out = filter_qd_llm_env(
        {
            "LLM_PROVIDER": "custom",
            "CUSTOM_MODEL": "m",
            "DATABASE_URL": "secret",
            "OPENAI_MODEL": "gpt",
        }
    )
    assert out == {
        "LLM_PROVIDER": "custom",
        "CUSTOM_MODEL": "m",
        "OPENAI_MODEL": "gpt",
    }


def test_push_llm_settings_to_bridge(monkeypatch):
    captured = {}

    class Fake:
        @classmethod
        def from_env(cls):
            return cls()

        def push_llm_sync(self, env):
            captured["env"] = env
            return {"applied": True, "provider": "custom", "chat_model": "openai/m"}

    monkeypatch.setattr(
        "app.services.rdagent_bridge.llm_sync.RdAgentBridgeClient",
        Fake,
    )
    status = push_llm_settings_to_bridge(
        {"LLM_PROVIDER": "custom", "CUSTOM_MODEL": "m", "FOO": "1"}
    )
    assert status["applied"] is True
    assert captured["env"] == {"LLM_PROVIDER": "custom", "CUSTOM_MODEL": "m"}
