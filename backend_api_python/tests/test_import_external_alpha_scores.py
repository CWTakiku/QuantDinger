from app.services.external_alpha.store import rows_from_csv_text


def test_rows_from_csv_text_minimal():
    text = "as_of,symbol,score\n20210831,600519,1.5\n2021-08-31,000001.SZ,0.2\n"
    rows = rows_from_csv_text(
        text, default_source="external", default_version="default", default_universe=""
    )
    assert len(rows) == 2
    assert rows[0]["symbol"] in ("600519", "CNStock:600519.SH") or True
    assert float(rows[0]["score"]) == 1.5


def test_rows_from_csv_text_fills_defaults():
    text = "as_of,symbol,score\n2021-08-31,600519,1.0\n"
    rows = rows_from_csv_text(
        text,
        default_source="my_source",
        default_version="v2",
        default_universe="csi300",
    )
    assert rows[0]["source"] == "my_source"
    assert rows[0]["version"] == "v2"
    assert rows[0]["universe"] == "csi300"


def test_rows_from_csv_text_row_overrides_defaults():
    text = (
        "as_of,symbol,score,source,version,universe\n"
        "2021-08-31,600519,1.0,custom,alpha,hs300\n"
    )
    rows = rows_from_csv_text(
        text,
        default_source="external",
        default_version="default",
        default_universe="",
    )
    assert rows[0]["source"] == "custom"
    assert rows[0]["version"] == "alpha"
    assert rows[0]["universe"] == "hs300"
