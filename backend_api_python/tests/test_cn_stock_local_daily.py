from pathlib import Path

from app.data_sources import cn_stock_local_daily as local_daily
from app.data_sources.tencent import parse_tencent_kline_time


def test_fetch_local_daily_klines_reads_csv(tmp_path: Path, monkeypatch):
    csv_path = tmp_path / "600519.csv"
    csv_path.write_text(
        "日期,开盘价,最高价,最低价,收盘价,成交量（股）\n"
        "2018-08-01,100,110,90,105,1000\n"
        "2018-08-02,105,112,100,108,1200\n"
        "2018-08-03,108,115,107,114,1300\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CNSTOCK_LOCAL_DAILY_DIR", str(tmp_path))
    local_daily._load_csv_rows.cache_clear()

    rows = local_daily.fetch_local_daily_klines(
        symbol="600519.SH",
        limit=10,
        after_time=parse_tencent_kline_time("2018-08-02"),
    )
    assert len(rows) == 2
    assert rows[0]["close"] == 108.0
    assert rows[-1]["close"] == 114.0
