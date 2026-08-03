from app.markets.cn_stock.symbols import canonicalize_cn_symbol, canonicalize_cnstock_key


def test_canonicalize_cn_symbol_adds_exchange_suffix():
    assert canonicalize_cn_symbol("300308") == "300308.SZ"
    assert canonicalize_cn_symbol("600519") == "600519.SH"
    assert canonicalize_cn_symbol("000001.SZ") == "000001.SZ"


def test_canonicalize_cnstock_key():
    assert canonicalize_cnstock_key("CNStock:300308") == "CNStock:300308.SZ"
    assert canonicalize_cnstock_key("300308.SZ") == "CNStock:300308.SZ"
