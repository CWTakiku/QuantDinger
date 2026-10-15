from app.services.symbol_name import names_from_local_rows


def test_history_name_wins_over_the_catalog_spacing():
    names = names_from_local_rows(
        ["000002.SZ", "CNStock:600588.SH", "BTC/USDT"],
        [("000002.SZ", "万科A")],
        [("000002", "万  科Ａ"), ("SH600588", "用友网络"), ("600588", "用友网络")],
    )
    assert names["000002.SZ"] == "万科A"
    assert names["CNStock:600588.SH"] == "用友网络"
    assert "BTC/USDT" not in names


def test_catalog_fills_a_code_missing_from_name_history():
    names = names_from_local_rows(
        ["600588.SH"],
        [],
        [("600588", "用友网络")],
    )
    assert names["600588.SH"] == "用友网络"
