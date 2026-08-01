from datetime import date
from unittest.mock import patch

import pandas as pd

from app.services.external_alpha.store import (
    load_external_alpha_scores_as_of,
    persist_external_alpha_scores,
)


def test_persist_normalizes_symbol_and_defaults():
    captured = []

    class _Cur:
        def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append(params)

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        out = persist_external_alpha_scores(
            [{"as_of": "2021-08-31", "symbol": "600519", "score": 1.25}]
        )
    assert out["inserted"] == 1
    assert out["skipped"] == 0
    # as_of, source, version, universe, symbol, score, weight, meta_json
    row = captured[0]
    assert row[1] == "external"
    assert row[2] == "default"
    assert row[4] == "CNStock:600519.SH"
    assert float(row[5]) == 1.25


def test_persist_skips_invalid_weight_and_inserts_valid_sibling():
    captured = []

    class _Cur:
        def execute(self, sql, params=None):
            if "INSERT" in sql.upper():
                captured.append(params)

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    rows = [
        {"as_of": "2021-08-31", "symbol": "600519", "score": 1.0, "weight": "not-a-number"},
        {"as_of": "2021-08-31", "symbol": "600036", "score": 2.0, "weight": 0.5},
    ]
    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        out = persist_external_alpha_scores(rows)
    assert out["inserted"] == 1
    assert out["skipped"] == 1
    assert len(out["errors"]) == 1
    assert out["errors"][0].startswith("row0:")
    assert len(captured) == 1
    assert captured[0][4] == "CNStock:600036.SH"
    assert float(captured[0][5]) == 2.0
    assert captured[0][6] == 0.5


def test_load_pit_uses_max_as_of_not_future():
    rows = [
        {"symbol": "CNStock:600519.SH", "score": 0.5, "as_of": date(2021, 8, 31)},
    ]

    class _Cur:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params=None):
            self.queries.append((sql, params))

        def fetchone(self):
            # first query: max as_of
            return {"as_of": date(2021, 8, 31)}

        def fetchall(self):
            return rows

        def close(self):
            pass

    cur = _Cur()

    class _Db:
        def cursor(self):
            return cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        series = load_external_alpha_scores_as_of(
            date(2021, 9, 15), source="external", version=None
        )
    assert list(series.index) == ["CNStock:600519.SH"]
    assert float(series.iloc[0]) == 0.5
    # ensure bound uses <= requested day
    assert any("as_of <=" in q[0].replace("\n", " ") or "as_of <= ?" in q[0] or "as_of <= %s" in q[0]
               or "<=" in q[0] for q in cur.queries)


def test_load_empty_when_no_as_of():
    class _Cur:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return None

        def fetchall(self):
            return []

        def close(self):
            pass

    class _Db:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("app.services.external_alpha.store.get_db_connection", return_value=_Db()):
        series = load_external_alpha_scores_as_of(date(2020, 1, 1), source="missing")
    assert isinstance(series, pd.Series)
    assert series.empty
