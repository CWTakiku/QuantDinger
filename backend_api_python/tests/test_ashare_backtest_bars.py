from datetime import date

from app.services.ashare_data.bars import build_backtest_rows


def test_rows_are_forward_adjusted_to_the_window_end_and_flag_the_open():
    rows = build_backtest_rows(
        [
            {
                "trade_date": date(2020, 1, 2),
                "open": 20.0,
                "high": 22.0,
                "low": 19.0,
                "close": 21.0,
                "volume": 1000.0,
                "adj_factor": 1.0,
                "up_limit": 22.0,
                "down_limit": 18.0,
                "is_st": False,
            },
            {
                "trade_date": date(2020, 1, 3),
                "open": 11.0,
                "high": 12.0,
                "low": 10.0,
                "close": 11.5,
                "volume": 2000.0,
                "adj_factor": 2.0,
                "up_limit": 12.1,
                "down_limit": 9.9,
                "is_st": True,
            },
        ],
        suspended_dates=[date(2020, 1, 3)],
    )
    first, second = rows
    assert first["close"] == 10.5
    assert first["open"] == 10.0
    assert first["price_scale"] == 0.5
    assert first["raw_open"] == 20.0
    assert first["limit_up"] is False
    assert second["close"] == 11.5
    assert second["price_scale"] == 1.0
    assert second["is_st"] is True
    assert second["suspended"] is True
    assert second["limit_up"] is False
    assert second["limit_down"] is False


def test_open_at_the_limit_blocks_that_side_only():
    (row,) = build_backtest_rows(
        [
            {
                "trade_date": date(2024, 1, 2),
                "open": 11.0,
                "high": 11.0,
                "low": 10.5,
                "close": 10.6,
                "volume": 100.0,
                "adj_factor": 1.0,
                "up_limit": 11.0,
                "down_limit": 9.0,
                "is_st": False,
            }
        ]
    )
    assert row["limit_up"] is True
    assert row["limit_down"] is False
    assert row["open"] == 11.0
