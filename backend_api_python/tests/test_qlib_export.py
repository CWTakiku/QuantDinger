from app.services.ashare_data.qlib_export import (
    append_symbol_rows,
    qlib_symbol,
    refresh_instrument_end,
    write_symbol_csv,
)


def test_qlib_symbol():
    assert qlib_symbol("600519.SH") == "SH600519"
    assert qlib_symbol("000001.SZ") == "SZ000001"


def test_csv_keeps_raw_price_and_factor(tmp_path):
    path = write_symbol_csv(
        tmp_path,
        "600519.SH",
        [{
            "trade_date": "2024-01-02",
            "open": 27.95,
            "high": 28.1,
            "low": 27.5,
            "close": 28.0,
            "volume": 100,
            "adj_factor": 1.3777,
        }],
    )
    text = path.read_text(encoding="utf-8")
    assert text.startswith("date,open,high,low,close,volume,factor")
    assert "2024-01-02,27.95,28.1,27.5,28.0,100,1.3777" in text


def test_append_does_not_duplicate_a_date(tmp_path):
    row = {
        "trade_date": "2024-01-02",
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "volume": 10,
        "adj_factor": 1,
    }
    append_symbol_rows(tmp_path, "000001.SZ", [row])
    append_symbol_rows(tmp_path, "000001.SZ", [row, {**row, "trade_date": "2024-01-03"}])
    lines = (tmp_path / "SZ000001.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


def test_refresh_instrument_end_keeps_exited_members(tmp_path):
    path = tmp_path / "qd_universe.txt"
    path.write_text(
        "SH600000\t2015-01-05\t2026-10-09\nSH600001\t2015-01-05\t2024-06-01\n",
        encoding="utf-8",
    )
    assert refresh_instrument_end(path, "2026-10-10") == 1
    text = path.read_text(encoding="utf-8")
    assert "SH600000\t2015-01-05\t2026-10-10" in text
    assert "SH600001\t2015-01-05\t2024-06-01" in text
