"""Named Custom (OpenAI-compatible) LLM profiles stored in .env."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple


PROFILES_KEY = "CUSTOM_LLM_PROFILES"
ACTIVE_KEY = "CUSTOM_ACTIVE_PROFILE"
URL_KEY = "CUSTOM_API_URL"
KEY_KEY = "CUSTOM_API_KEY"
MODEL_KEY = "CUSTOM_MODEL"

_PROFILE_FIELDS = ("id", "name", "url", "key", "model")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def suggest_profile_name(url: str, model: str = "") -> str:
    u = (url or "").lower()
    if "bigmodel.cn" in u:
        return "智谱 GLM"
    if "volces.com" in u or "ark.cn" in u:
        return "火山方舟"
    if "openai.com" in u:
        return "OpenAI Compatible"
    if model:
        return str(model)
    return "Custom API"


def parse_profiles(raw: Any) -> List[Dict[str, str]]:
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("profiles"), list):
        items = data["profiles"]
    elif isinstance(data, list):
        items = data
    else:
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip() or _new_id()
        if pid in seen:
            pid = _new_id()
        seen.add(pid)
        out.append(
            {
                "id": pid,
                "name": str(item.get("name") or "").strip() or suggest_profile_name(
                    str(item.get("url") or ""), str(item.get("model") or "")
                ),
                "url": str(item.get("url") or "").strip(),
                "key": str(item.get("key") or "").strip(),
                "model": str(item.get("model") or "").strip(),
            }
        )
    return out


def serialize_profiles(profiles: List[Dict[str, str]]) -> str:
    clean = []
    for p in profiles:
        clean.append({k: str(p.get(k) or "") for k in _PROFILE_FIELDS})
    return json.dumps({"profiles": clean}, ensure_ascii=False, separators=(",", ":"))


def profiles_for_api(profiles: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Mask secrets for settings /values response."""
    result = []
    for p in profiles:
        key = str(p.get("key") or "")
        result.append(
            {
                "id": p.get("id") or "",
                "name": p.get("name") or "",
                "url": p.get("url") or "",
                "model": p.get("model") or "",
                "key": "",
                "key_configured": bool(key),
            }
        )
    return result


def ensure_profiles_from_env(env: Dict[str, str]) -> Tuple[List[Dict[str, str]], str, bool]:
    """
    Return (profiles, active_id, mutated).
    If no profiles JSON yet, seed one from current CUSTOM_* fields.
    """
    profiles = parse_profiles(env.get(PROFILES_KEY))
    mutated = False
    url = str(env.get(URL_KEY) or "").strip()
    key = str(env.get(KEY_KEY) or "").strip()
    model = str(env.get(MODEL_KEY) or "").strip()
    active = str(env.get(ACTIVE_KEY) or "").strip()

    if not profiles:
        if url or model or key:
            pid = _new_id()
            profiles = [
                {
                    "id": pid,
                    "name": suggest_profile_name(url, model),
                    "url": url,
                    "key": key,
                    "model": model,
                }
            ]
            active = pid
            mutated = True
        else:
            return [], "", False

    ids = {p["id"] for p in profiles}
    if not active or active not in ids:
        active = profiles[0]["id"]
        mutated = True

    return profiles, active, mutated


def materialize_active(profiles: List[Dict[str, str]], active_id: str) -> Dict[str, str]:
    active = next((p for p in profiles if p["id"] == active_id), None)
    if active is None and profiles:
        active = profiles[0]
        active_id = active["id"]
    if active is None:
        return {
            PROFILES_KEY: serialize_profiles([]),
            ACTIVE_KEY: "",
            URL_KEY: "",
            KEY_KEY: "",
            MODEL_KEY: "",
        }
    return {
        PROFILES_KEY: serialize_profiles(profiles),
        ACTIVE_KEY: active_id,
        URL_KEY: active.get("url") or "",
        KEY_KEY: active.get("key") or "",
        MODEL_KEY: active.get("model") or "",
    }


def reconcile_on_save(
    current_env: Dict[str, str],
    updates: Dict[str, str],
) -> Dict[str, str]:
    """
    Merge incoming settings updates for custom LLM profiles.
    Empty profile keys / CUSTOM_API_KEY mean "keep existing secret".
    Always materialize the active profile into CUSTOM_API_*.
    """
    touched = any(
        k in updates
        for k in (PROFILES_KEY, ACTIVE_KEY, URL_KEY, KEY_KEY, MODEL_KEY)
    )
    if not touched and PROFILES_KEY not in current_env:
        return updates

    merged_env = dict(current_env)
    merged_env.update({k: str(v) for k, v in updates.items()})

    old_profiles, old_active, _ = ensure_profiles_from_env(current_env)
    old_by_id = {p["id"]: p for p in old_profiles}

    if PROFILES_KEY in updates:
        new_profiles = parse_profiles(updates.get(PROFILES_KEY))
    else:
        new_profiles = [dict(p) for p in old_profiles]

    if not new_profiles:
        # Fall back: build from CUSTOM_* in the update/env
        url = str(updates.get(URL_KEY, merged_env.get(URL_KEY) or "")).strip()
        model = str(updates.get(MODEL_KEY, merged_env.get(MODEL_KEY) or "")).strip()
        key = str(updates.get(KEY_KEY, "")).strip() or str(merged_env.get(KEY_KEY) or "").strip()
        if url or model or key:
            pid = old_active or _new_id()
            new_profiles = [
                {
                    "id": pid,
                    "name": suggest_profile_name(url, model),
                    "url": url,
                    "key": key,
                    "model": model,
                }
            ]

    # Restore blank keys from previous store
    for p in new_profiles:
        if not p.get("key"):
            prev = old_by_id.get(p["id"]) or {}
            p["key"] = str(prev.get("key") or "")

    active_id = str(updates.get(ACTIVE_KEY, old_active) or "").strip()
    ids = {p["id"] for p in new_profiles}
    if not active_id or active_id not in ids:
        active_id = new_profiles[0]["id"] if new_profiles else ""

    # Apply form CUSTOM_* onto the active profile (partial updates OK)
    for p in new_profiles:
        if p["id"] != active_id:
            continue
        if URL_KEY in updates:
            p["url"] = str(updates.get(URL_KEY) or "").strip()
        if MODEL_KEY in updates:
            p["model"] = str(updates.get(MODEL_KEY) or "").strip()
        if KEY_KEY in updates:
            new_key = str(updates.get(KEY_KEY) or "").strip()
            if new_key:
                p["key"] = new_key
        if not (p.get("name") or "").strip():
            p["name"] = suggest_profile_name(p.get("url") or "", p.get("model") or "")
        break

    materialized = materialize_active(new_profiles, active_id)
    out = dict(updates)
    out.update(materialized)
    return out


def sanitize_profile_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip())
    return text[:64] if text else "Custom API"
