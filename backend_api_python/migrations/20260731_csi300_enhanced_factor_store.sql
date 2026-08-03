-- CSI300 enhanced-index factor store (weights + future northbound / consensus stubs)

CREATE TABLE IF NOT EXISTS qd_csi300_index_weights (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    con_code VARCHAR(32) NOT NULL,
    weight DOUBLE PRECISION NOT NULL,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, con_code, source)
);

CREATE INDEX IF NOT EXISTS idx_csi300_index_weights_date
  ON qd_csi300_index_weights (trade_date);

-- Placeholder for northbound / margin / consensus daily panels (filled in later tasks)
CREATE TABLE IF NOT EXISTS qd_ashare_flow_daily (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    ts_code VARCHAR(32) NOT NULL,
    north_net_buy DOUBLE PRECISION,
    margin_balance DOUBLE PRECISION,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, ts_code, source)
);

CREATE INDEX IF NOT EXISTS idx_ashare_flow_daily_date
  ON qd_ashare_flow_daily (trade_date);

CREATE TABLE IF NOT EXISTS qd_ashare_consensus_daily (
    id BIGSERIAL PRIMARY KEY,
    trade_date DATE NOT NULL,
    ts_code VARCHAR(32) NOT NULL,
    eps_fy1 DOUBLE PRECISION,
    pe_fy1 DOUBLE PRECISION,
    rating_mean DOUBLE PRECISION,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trade_date, ts_code, source)
);

CREATE INDEX IF NOT EXISTS idx_ashare_consensus_daily_date
  ON qd_ashare_consensus_daily (trade_date);
