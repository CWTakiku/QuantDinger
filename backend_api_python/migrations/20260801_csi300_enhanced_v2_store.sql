-- CSI300 enhanced-index v2 store: daily_basic + industry map

CREATE TABLE IF NOT EXISTS qd_ashare_daily_basic (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    ts_code VARCHAR(32) NOT NULL,
    close DOUBLE PRECISION,
    pe_ttm DOUBLE PRECISION,
    pb DOUBLE PRECISION,
    ps_ttm DOUBLE PRECISION,
    dv_ttm DOUBLE PRECISION,
    total_mv DOUBLE PRECISION,
    circ_mv DOUBLE PRECISION,
    turnover_rate DOUBLE PRECISION,
    volume_ratio DOUBLE PRECISION,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, ts_code, source)
);
CREATE INDEX IF NOT EXISTS idx_ashare_daily_basic_date ON qd_ashare_daily_basic (trade_date);

CREATE TABLE IF NOT EXISTS qd_ashare_industry_map (
    id BIGSERIAL PRIMARY KEY,
    ts_code VARCHAR(32) NOT NULL,
    industry VARCHAR(64) NOT NULL,
    industry_src VARCHAR(40) NOT NULL DEFAULT 'tushare_stock_basic',
    as_of DATE NOT NULL,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ts_code, industry_src, as_of, source)
);
