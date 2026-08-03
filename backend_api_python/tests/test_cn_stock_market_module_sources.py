from app.markets.registry import MARKET_MODULES, serialize_market_module


def test_cnstock_lists_tushare_as_primary_recommended_source():
    module = MARKET_MODULES["CNStock"]
    keys = [req.key for req in module.data_requirements]
    assert keys[0] == "tushare"
    assert module.data_requirements[0].recommended is True
    assert "TUSHARE_TOKEN" in module.data_requirements[0].setting_keys

    payload = serialize_market_module(
        module,
        env={"SHOW_CN_STOCK": "true", "TUSHARE_TOKEN": "dummy"},
    )
    assert payload["data_sources"][0]["key"] == "tushare"
    assert payload["data_sources"][0]["recommended"] is True
    assert payload["data_sources"][0]["configured"] is True
    assert payload["status"] == "ready"
