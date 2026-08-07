"""Push QuantDinger LLM settings into the host rdagent-bridge mirror."""

from __future__ import annotations

from typing import Any

from app.services.rdagent_bridge.client import RdAgentBridgeClient
from app.services.rdagent_bridge.errors import RdAgentBridgeError
from app.utils.logger import get_logger

logger = get_logger(__name__)

_QD_LLM_EXACT_KEYS = frozenset({"LLM_PROVIDER"})
_QD_LLM_PREFIXES = (
    "CUSTOM_",
    "OPENAI_",
    "DEEPSEEK_",
    "OPENROUTER_",
    "ATLASCLOUD_",
    "GROK_",
    "ANTHROPIC_",
    "AZURE_",
)


def is_qd_llm_key(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return False
    if k in _QD_LLM_EXACT_KEYS:
        return True
    return any(k.startswith(p) for p in _QD_LLM_PREFIXES)


def filter_qd_llm_env(env: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (env or {}).items():
        if not is_qd_llm_key(str(key)):
            continue
        out[str(key).strip()] = "" if value is None else str(value)
    return out


def push_llm_settings_to_bridge(env: dict[str, Any]) -> dict[str, Any] | None:
    """
    Best-effort push of LLM slice to rdagent-bridge.
    Returns bridge status dict on success, None when skipped / unreachable.
    """
    payload = filter_qd_llm_env(env)
    if not payload:
        return None
    try:
        client = RdAgentBridgeClient.from_env()
        status = client.push_llm_sync(payload)
        logger.info(
            "Pushed LLM settings to rdagent-bridge: provider=%s chat_model=%s",
            status.get("provider"),
            status.get("chat_model"),
        )
        return status if isinstance(status, dict) else {"ok": True}
    except RdAgentBridgeError as exc:
        logger.warning("rdagent-bridge LLM sync skipped: %s", exc.message or exc.code)
        return None
    except Exception:
        logger.exception("rdagent-bridge LLM sync failed")
        return None
