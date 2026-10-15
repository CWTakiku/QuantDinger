-- A-share data platform: one point-in-time store for research, backtest and live.
-- Prices are unadjusted (as traded); adj_factor is a backward-adjustment factor
-- rebuilt from the pre_close chain, so qfq/hfq series are derived, never stored.
-- Units: volume in shares, amount / market values in CNY.

CREATE TABLE IF NOT EXISTS qd_ashare_daily_bar (
    ts_code VARCHAR(12) NOT NULL,
    trade_date DATE NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    pre_close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    amount DOUBLE PRECISION,
    adj_factor DOUBLE PRECISION,
    up_limit DOUBLE PRECISION,
    down_limit DOUBLE PRECISION,
    is_st BOOLEAN NOT NULL DEFAULT FALSE,
    turnover_rate DOUBLE PRECISION,
    turnover_rate_f DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    total_mv DOUBLE PRECISION,
    circ_mv DOUBLE PRECISION,
    pe_ttm DOUBLE PRECISION,
    pb DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ashare_daily_bar_date ON qd_ashare_daily_bar (trade_date);

CREATE TABLE IF NOT EXISTS qd_ashare_suspension (
    ts_code VARCHAR(12) NOT NULL,
    trade_date DATE NOT NULL,
    suspend_type VARCHAR(8) NOT NULL DEFAULT '',
    suspend_timing VARCHAR(64) NOT NULL DEFAULT '',
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_ashare_suspension_date ON qd_ashare_suspension (trade_date);

CREATE TABLE IF NOT EXISTS qd_ashare_stock_basic (
    ts_code VARCHAR(12) PRIMARY KEY,
    name VARCHAR(64) NOT NULL DEFAULT '',
    market VARCHAR(32) NOT NULL DEFAULT '',
    exchange VARCHAR(16) NOT NULL DEFAULT '',
    industry VARCHAR(64) NOT NULL DEFAULT '',
    list_status VARCHAR(4) NOT NULL DEFAULT '',
    list_date DATE,
    delist_date DATE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS qd_ashare_name_history (
    ts_code VARCHAR(12) NOT NULL,
    name VARCHAR(64) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    ann_date DATE,
    change_reason VARCHAR(64) NOT NULL DEFAULT '',
    PRIMARY KEY (ts_code, start_date, name)
);

CREATE TABLE IF NOT EXISTS qd_ashare_sync_log (
    trade_date DATE NOT NULL,
    dataset VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    rows INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trade_date, dataset)
);
