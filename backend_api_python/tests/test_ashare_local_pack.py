from datetime import date

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

from app.services.ashare_data.local_pack import PackFetcher, load_api_range  # noqa: E402


def _write(root, api, day, rows):
    data = root / api / "data"
    data.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(data / f"{api}__trade_date={day}.parquet")


def test_pack_fetcher_mixes_pack_and_remote(tmp_path):
    _write(tmp_path, "daily", "20200102", [{"ts_code": "600000.SH", "trade_date": "20200102", "close": 12.0}])
    _write(tmp_path, "daily", "20200103", [{"ts_code": "600000.SH", "trade_date": "20200103", "close": 12.1}])
    _write(tmp_path, "daily", "20210104", [{"ts_code": "600000.SH", "trade_date": "20210104", "close": 10.0}])
    calls = []

    def remote(api, day):
        calls.append((api, day))
        return pd.DataFrame([{"ts_code": "600000.SH", "up_limit": 13.2}])

    fetcher = PackFetcher(tmp_path, date(2020, 1, 1), date(2020, 12, 31), apis=["daily", "stk_limit"], remote=remote)
    assert fetcher.remote_apis == ["stk_limit"]
    assert fetcher.trade_dates() == [date(2020, 1, 2), date(2020, 1, 3)]
    frames = fetcher(date(2020, 1, 3))
    assert frames["daily"]["close"].tolist() == [12.1]
    assert frames["stk_limit"]["up_limit"].tolist() == [13.2]
    assert calls == [("stk_limit", date(2020, 1, 3))]
    assert fetcher(date(2020, 1, 6))["daily"].empty


def test_pack_fetcher_requires_remote_for_missing_api(tmp_path):
    _write(tmp_path, "daily", "20200102", [{"ts_code": "600000.SH", "trade_date": "20200102"}])
    with pytest.raises(FileNotFoundError):
        PackFetcher(tmp_path, date(2020, 1, 1), date(2020, 1, 31), apis=["daily", "stk_limit"])


def test_load_api_range_filters_by_file_date(tmp_path):
    _write(tmp_path, "adj_factor", "20191231", [{"ts_code": "A", "trade_date": "20191231", "adj_factor": 1.0}])
    _write(tmp_path, "adj_factor", "20200102", [{"ts_code": "A", "trade_date": "20200102", "adj_factor": 1.1}])
    frame = load_api_range(tmp_path, "adj_factor", date(2020, 1, 1), date(2020, 1, 31))
    assert frame["trade_date"].tolist() == ["20200102"]
