import numpy as np
import pandas as pd

from app.services.ashare_factor_book.book import cash_dividend, select_names, simulate
from app.services.ashare_factor_book.evaluate import (
    cross_section_chart,
    estimate_weights,
    factor_rank_correlation,
    last_trainable_date,
    quarter_starts,
)
from app.services.ashare_factor_book.factors import raw_factor_panels


def test_momentum_ignores_a_later_close():
    dates = pd.bdate_range("2024-01-01", periods=40)
    symbols = ["600000.SH", "000001.SZ"]
    close = pd.DataFrame(10.0, index=dates, columns=symbols)
    close.iloc[-5:] = 12.0
    turnover = pd.DataFrame(1.0, index=dates, columns=symbols)
    before = raw_factor_panels(close, turnover)
    changed = close.copy()
    changed.iloc[-1] = 30.0
    after = raw_factor_panels(changed, turnover)
    pd.testing.assert_series_equal(before["mom_20_skip5"].iloc[-2], after["mom_20_skip5"].iloc[-2])
    assert before["rev_5"].iloc[-2, 0] != after["rev_5"].iloc[-1, 0]


def test_trainable_date_ends_before_the_forward_label_overlaps_live():
    dates = pd.bdate_range("2020-12-01", "2021-01-29")
    live = pd.Timestamp("2021-01-04")
    last = last_trainable_date(dates, live, 5)
    assert dates[dates.get_loc(last) + 5] == dates[int(dates.searchsorted(live)) - 1]


def test_quarter_starts_stay_inside_each_quarter():
    dates = pd.bdate_range("2021-01-01", "2021-07-15")
    starts = quarter_starts(dates, pd.Timestamp("2021-02-01"), pd.Timestamp("2021-07-15"))
    assert starts[0] >= pd.Timestamp("2021-02-01")
    assert starts[0] < pd.Timestamp("2021-04-01")
    assert starts[1] >= pd.Timestamp("2021-04-01")
    assert starts[1] < pd.Timestamp("2021-07-01")


def test_negative_training_ic_flips_the_sign():
    dates = pd.bdate_range("2020-01-01", periods=30)
    symbols = [f"6000{i:02d}.SH" for i in range(8)]
    forward = pd.DataFrame(
        [[float(i) for i in range(8)] for _ in range(30)],
        index=dates,
        columns=symbols,
        dtype=float,
    )
    factor = -forward
    universe = pd.DataFrame(True, index=dates, columns=symbols)
    stats = estimate_weights(
        {"rev_5": factor},
        forward,
        universe,
        dates,
        dates[0],
        dates[-1],
        step=5,
        min_obs=3,
        min_names=5,
    )
    assert stats["rev_5"]["sign"] == -1.0
    assert stats["rev_5"]["weight"] == 1.0


def test_rank_buffer_sells_outside_names_and_refills_from_the_top():
    score = pd.Series({"A": 3.0, "B": 2.0, "C": 1.0, "D": 0.0})
    chosen = select_names(
        score,
        ["D", "B"],
        can_hold={"A", "B", "C", "D"},
        can_buy={"A", "B", "C"},
        can_sell={"A", "B", "C", "D"},
        holdings=2,
        buffer=3,
    )
    assert chosen == ["B", "A"]


def test_limit_down_position_cannot_be_forced_out():
    score = pd.Series({"A": 1.0, "Z": 5.0})
    chosen = select_names(
        score,
        ["Z"],
        can_hold=set(),
        can_buy={"A"},
        can_sell=set(),
        holdings=1,
        buffer=1,
    )
    assert chosen[0] == "Z"


def test_dividend_credit_offsets_the_raw_price_drop():
    cash = cash_dividend(100, 9.0, 1.0, 10.0 / 9.0)
    assert abs((100 * 9.0 + cash) - 1000.0) < 1e-6


def test_limit_up_open_is_not_bought():
    dates = pd.bdate_range("2024-06-03", periods=4)
    symbol = "600000.SH"
    price = pd.DataFrame(10.0, index=dates, columns=[symbol])
    blocked = pd.DataFrame(False, index=dates, columns=[symbol])
    allowed = pd.DataFrame(True, index=dates, columns=[symbol])
    score = pd.DataFrame(1.0, index=dates, columns=[symbol])
    result = simulate(
        raw_open=price,
        raw_close=price,
        adj_factor=pd.DataFrame(1.0, index=dates, columns=[symbol]),
        score=score,
        can_hold=allowed,
        can_buy=blocked,
        can_sell=allowed,
        trade_positions=[1],
        capital=100_000.0,
        slippage=0.0,
        holdings=1,
        buffer=1,
        max_weight=1.0,
    )
    assert result["final_shares"] == {}
    assert abs(float(result["equity"].iloc[-1]) - 100_000.0) < 1e-6


def test_flat_price_buy_only_loses_the_fee():
    dates = pd.bdate_range("2024-06-03", periods=4)
    symbol = "600000.SH"
    price = pd.DataFrame(10.0, index=dates, columns=[symbol])
    allowed = pd.DataFrame(True, index=dates, columns=[symbol])
    result = simulate(
        raw_open=price,
        raw_close=price,
        adj_factor=pd.DataFrame(1.0, index=dates, columns=[symbol]),
        score=pd.DataFrame(1.0, index=dates, columns=[symbol]),
        can_hold=allowed,
        can_buy=allowed,
        can_sell=allowed,
        trade_positions=[1],
        capital=100_000.0,
        slippage=0.0,
        holdings=1,
        buffer=1,
        max_weight=1.0,
    )
    assert result["final_shares"].get(symbol, 0) >= 100
    assert float(result["equity"].iloc[-1]) < 100_000.0
    assert float(result["equity"].iloc[-1]) > 99_000.0


def test_cross_section_chart_puts_the_high_score_group_on_top():
    dates = pd.bdate_range("2024-01-02", periods=40)
    symbols = [f"{600000 + index}.SH" for index in range(10)]
    levels = np.arange(10, dtype=float)
    score = pd.DataFrame(np.tile(levels, (len(dates), 1)), index=dates, columns=symbols)
    forward = score * 0.01
    mask = pd.DataFrame(True, index=dates, columns=symbols)
    chart = cross_section_chart(
        score,
        forward,
        mask,
        dates,
        dates[0],
        dates[-1],
        step=5,
        groups=5,
        cost_rate=0.0,
        min_names=10,
    )
    assert chart["rankIc"] > 0.99
    assert chart["groupCurves"][-1]["finalNet"] > chart["groupCurves"][0]["finalNet"]
    assert len(chart["icSeries"]) >= 3
    assert chart["longShortCurve"][-1]["net"] > 1.0
    assert chart["stability"]["rankAutocorrelation"] > 0.99
    assert chart["icSeries"][4]["rolling"] is not None
    correlation = factor_rank_correlation(
        {"alpha": score, "copy": score.copy()},
        mask,
        dates,
        dates[0],
        dates[-1],
        step=5,
        min_names=10,
    )
    assert correlation["matrix"][0][1] == 1.0
