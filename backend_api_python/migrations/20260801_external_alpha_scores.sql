-- migrations/20260801_external_alpha_scores.sql
CREATE TABLE IF NOT EXISTS qd_external_alpha_scores (
    id BIGSERIAL PRIMARY KEY,
    as_of DATE NOT NULL,
    source VARCHAR(80) NOT NULL,
    version VARCHAR(120) NOT NULL DEFAULT 'default',
    universe VARCHAR(80) NOT NULL DEFAULT '',
    symbol VARCHAR(80) NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    weight DOUBLE PRECISION,
    meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (as_of, source, version, symbol)
);
CREATE INDEX IF NOT EXISTS idx_external_alpha_scores_lookup
  ON qd_external_alpha_scores (source, version, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_external_alpha_scores_asof_symbol
  ON qd_external_alpha_scores (as_of, symbol);
