import importlib.util
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.services.ashare_data.minute import compare_minute_to_daily

D1 = date(2015, 1, 5)
D2 = date(2015, 1, 6)


def _daily(rows):
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "close", "volume"])


def test_compare_flags_close_volume_and_coverage():
    daily = _daily(
        [
            ("600000.SH", D1, 15.88, 16.07, 513568709.0),
            ("000001.SZ", D1, 15.99, 16.02, 286043643.0),
            ("600837.SH", D1, 20.0, 21.0, 1000.0),
            ("000002.SZ", D1, 10.0, 10.0, 0.0),
        ]
    )
    minute = pd.DataFrame(
        [
            ("600000.SH", D1, 15.88, 16.07, 513568700.0),
            ("000001.SZ", D1, 16.00, 16.05, 286043600.0),
            ("300001.SZ", D1, 9.0, 9.1, 500.0),
        ],
        columns=["ts_code", "trade_date", "open", "close", "volume"],
    )
    (row,) = compare_minute_to_daily(minute, daily).to_dict("records")
    assert row["matched"] == 2
    assert row["missing"] == 1
    assert row["extra"] == 1
    assert row["close_bad"] == 1
    assert row["volume_bad"] == 0
    assert row["open_bad"] == 1
    assert row["status"] == "mismatch"


def test_compare_ok_when_closes_and_volumes_agree():
    daily = _daily([("600000.SH", D2, 10.0, 10.5, 1_000_000.0)])
    minute = pd.DataFrame(
        [("600000.SH", D2, 10.01, 10.5, 1_000_000.0)],
        columns=["ts_code", "trade_date", "open", "close", "volume"],
    )
    (row,) = compare_minute_to_daily(minute, daily).to_dict("records")
    assert row["status"] == "ok"
    assert row["open_bad"] == 1


@pytest.mark.skipif(importlib.util.find_spec("pyarrow") is None, reason="pyarrow not installed")
def test_import_script_converts_units_and_codes(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "import_ashare_minute", Path(__file__).resolve().parents[1] / "scripts" / "import_ashare_minute.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    src = tmp_path / "src"
    src.mkdir()
    csv = (
        "\ufeff时间,代码,名称,开盘价,收盘价,最高价,最低价,成交量,成交额,涨幅,振幅\n"
        "2015-01-05 09:31:00,sh600000,浦发银行,15.97,15.9,15.98,15.9,85060,135616366,0.13,0.5\n"
        "2015-01-05 09:30:00,sh600000,浦发银行,15.88,15.88,15.88,15.88,52626,83570088,0.0,0.0\n"
    )
    with zipfile.ZipFile(src / "2015_1min.zip", "w") as zf:
        zf.writestr("sh600000_2015.csv", csv.encode("utf-8"))
        zf.writestr("readme.txt", "ignored")
    result = module.convert_year(src, tmp_path / "dst", 2015)
    assert result["status"] == "ok" and result["rows"] == 2 and result["symbols"] == 1
    assert module.convert_year(src, tmp_path / "dst", 2015)["status"] == "skipped"

    import pyarrow.parquet as pq

    table = pq.read_table(tmp_path / "dst" / "year=2015" / "bars.parquet").to_pandas()
    assert table["ts_code"].tolist() == ["600000.SH", "600000.SH"]
    assert table["ts"].dt.strftime("%H:%M").tolist() == ["09:30", "09:31"]
    assert table["volume"].tolist() == [5262600.0, 8506000.0]
    assert str(table["trade_date"].iloc[0]) == "2015-01-05"
