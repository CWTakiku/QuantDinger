from datetime import date

from app.services.ashare_data.index_members import boards_to_member_rows, canonicalize_cn_symbol


def test_symbol_suffix():
    assert canonicalize_cn_symbol("600519") == "600519.SH"
    assert canonicalize_cn_symbol("000001") == "000001.SZ"
    assert canonicalize_cn_symbol("830001") == "830001.BJ"


def test_boards_close_when_the_next_list_is_published():
    rows = boards_to_member_rows(
        {
            date(2021, 1, 29): {"600000.SH": 0.4, "000001.SZ": 0.6},
            date(2021, 6, 30): {"600000.SH": 0.2, "300001.SZ": 0.8},
        }
    )
    by_key = {(row["symbol"], row["valid_from"]): row for row in rows}
    first = by_key[("000001.SZ", date(2021, 1, 29))]
    assert first["valid_to"] == date(2021, 6, 30)
    assert first["rank"] == 1
    assert first["weight"] == 0.006
    assert by_key[("600000.SH", date(2021, 6, 30))]["valid_to"] is None
    assert ("000001.SZ", date(2021, 6, 30)) not in by_key
