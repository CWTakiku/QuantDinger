from app.services.settings.custom_llm_profiles import (
    ACTIVE_KEY,
    KEY_KEY,
    MODEL_KEY,
    PROFILES_KEY,
    URL_KEY,
    ensure_profiles_from_env,
    parse_profiles,
    profiles_for_api,
    reconcile_on_save,
    serialize_profiles,
    suggest_profile_name,
)


def test_suggest_profile_name():
    assert "智谱" in suggest_profile_name("https://open.bigmodel.cn/api/coding/paas/v4", "glm-5.2")
    assert "方舟" in suggest_profile_name("https://ark.cn-beijing.volces.com/api/v3", "deepseek")


def test_ensure_profiles_seeds_from_custom_fields():
    env = {
        URL_KEY: "https://ark.cn-beijing.volces.com/api/v3",
        KEY_KEY: "ark-secret",
        MODEL_KEY: "deepseek-v4-flash-260425",
    }
    profiles, active, mutated = ensure_profiles_from_env(env)
    assert mutated is True
    assert len(profiles) == 1
    assert profiles[0]["model"] == "deepseek-v4-flash-260425"
    assert active == profiles[0]["id"]


def test_profiles_for_api_masks_keys():
    masked = profiles_for_api(
        [{"id": "a", "name": "A", "url": "u", "key": "secret", "model": "m"}]
    )
    assert masked[0]["key"] == ""
    assert masked[0]["key_configured"] is True


def test_reconcile_keeps_secret_when_blank_and_materializes_active():
    profiles = [
        {
            "id": "zhipu",
            "name": "智谱 GLM",
            "url": "https://open.bigmodel.cn/api/coding/paas/v4",
            "key": "zhipu-key",
            "model": "glm-5.2",
        },
        {
            "id": "ark",
            "name": "火山方舟",
            "url": "https://ark.cn-beijing.volces.com/api/v3",
            "key": "ark-key",
            "model": "deepseek-v4-flash-260425",
        },
    ]
    current = {
        PROFILES_KEY: serialize_profiles(profiles),
        ACTIVE_KEY: "ark",
        URL_KEY: profiles[1]["url"],
        KEY_KEY: profiles[1]["key"],
        MODEL_KEY: profiles[1]["model"],
    }
    # Switch to zhipu without resending keys
    updates = {
        PROFILES_KEY: serialize_profiles(
            [
                {**profiles[0], "key": ""},
                {**profiles[1], "key": ""},
            ]
        ),
        ACTIVE_KEY: "zhipu",
        URL_KEY: profiles[0]["url"],
        MODEL_KEY: profiles[0]["model"],
        KEY_KEY: "",
    }
    out = reconcile_on_save(current, updates)
    assert out[ACTIVE_KEY] == "zhipu"
    assert out[URL_KEY] == profiles[0]["url"]
    assert out[MODEL_KEY] == "glm-5.2"
    assert out[KEY_KEY] == "zhipu-key"
    stored = parse_profiles(out[PROFILES_KEY])
    assert {p["id"]: p["key"] for p in stored} == {
        "zhipu": "zhipu-key",
        "ark": "ark-key",
    }
