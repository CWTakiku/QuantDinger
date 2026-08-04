"""Tests for quant model rebalance schedule expansion."""

from datetime import date

import pytest

from app.services.quant_models.schedule import (
    expand_rebalance_dates,
    infer_schedule_from_strategy,
    to_score_as_ofs,
)


class TestInferScheduleFromStrategy:
    def test_params_infer_schedule_wins_over_code(self):
        code = "run_weekly(rebalance)\nrun_daily(update)"
        assert infer_schedule_from_strategy(code, {"infer_schedule": "daily"}) == "daily"
        assert infer_schedule_from_strategy(code, {"infer_schedule": "weekly"}) == "weekly"

    def test_run_weekly_in_code(self):
        code = "def initialize():\n    run_weekly(rebalance, weekday=1)"
        assert infer_schedule_from_strategy(code, {}) == "weekly"

    def test_run_daily_in_code(self):
        code = "def initialize():\n    run_daily(scan, time='15:05')"
        assert infer_schedule_from_strategy(code, {}) == "daily"

    def test_run_weekly_takes_precedence_over_run_daily(self):
        code = "run_daily(update)\nrun_weekly(rebalance)"
        assert infer_schedule_from_strategy(code, {}) == "weekly"

    def test_default_weekly(self):
        assert infer_schedule_from_strategy("def initialize(): pass", {}) == "weekly"


class TestExpandRebalanceDates:
    def test_weekly_mondays_iso_weekday(self):
        # ISO weekday: Mon=1 .. Sun=7
        start = date(2026, 1, 5)  # Monday
        end = date(2026, 1, 25)
        dates = expand_rebalance_dates("weekly", start, end, weekday=1)
        assert dates == [
            date(2026, 1, 5),
            date(2026, 1, 12),
            date(2026, 1, 19),
        ]

    def test_weekly_friday(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 31)
        dates = expand_rebalance_dates("weekly", start, end, weekday=5)
        assert dates == [
            date(2026, 1, 2),
            date(2026, 1, 9),
            date(2026, 1, 16),
            date(2026, 1, 23),
            date(2026, 1, 30),
        ]

    def test_daily_calendar_days(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 5)
        dates = expand_rebalance_dates("daily", start, end)
        assert dates == [
            date(2026, 1, 1),
            date(2026, 1, 2),
            date(2026, 1, 3),
            date(2026, 1, 4),
            date(2026, 1, 5),
        ]

    def test_empty_when_start_after_end(self):
        assert expand_rebalance_dates("weekly", date(2026, 2, 1), date(2026, 1, 1)) == []
        assert expand_rebalance_dates("daily", date(2026, 2, 1), date(2026, 1, 1)) == []

    def test_unknown_schedule_raises(self):
        with pytest.raises(ValueError, match="schedule"):
            expand_rebalance_dates("monthly", date(2026, 1, 1), date(2026, 1, 31))


class TestToScoreAsOfs:
    def test_calendar_day_lag(self):
        rebalance = [date(2026, 1, 12), date(2026, 1, 19)]
        assert to_score_as_ofs(rebalance, 1) == [date(2026, 1, 11), date(2026, 1, 18)]

    def test_zero_lag(self):
        rebalance = [date(2026, 1, 12)]
        assert to_score_as_ofs(rebalance, 0) == [date(2026, 1, 12)]

    def test_empty_input(self):
        assert to_score_as_ofs([], 1) == []
