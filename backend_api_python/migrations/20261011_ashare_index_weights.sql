-- Point-in-time index constituent weights (CSI 300 / 500 / 1000 and others).

CREATE TABLE IF NOT EXISTS qd_ashare_index_weight (
    index_code VARCHAR(16) NOT NULL,
    trade_date DATE NOT NULL,
    con_code VARCHAR(32) NOT NULL,
    weight DOUBLE PRECISION NOT NULL,
    source VARCHAR(40) NOT NULL DEFAULT 'tushare',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (index_code, trade_date, con_code, source)
);

CREATE INDEX IF NOT EXISTS idx_ashare_index_weight_code_date
    ON qd_ashare_index_weight (index_code, trade_date);
