-- migrations/20260806_industry_glass_fiber_weekly.sql
CREATE TABLE IF NOT EXISTS qd_industry_glass_fiber_weekly (
  id BIGSERIAL PRIMARY KEY,
  as_of DATE NOT NULL,
  cloth_7628_mid DOUBLE PRECISION NULL,
  yarn_2400_mid DOUBLE PRECISION NULL,
  cloth_trend SMALLINT NOT NULL CHECK (cloth_trend IN (-1, 0, 1)),
  inventory_trend SMALLINT NOT NULL CHECK (inventory_trend IN (-1, 0, 1)),
  new_capacity_flag SMALLINT NOT NULL DEFAULT 0 CHECK (new_capacity_flag IN (0, 1)),
  source VARCHAR(32) NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
  raw_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (as_of, source)
);
CREATE INDEX IF NOT EXISTS idx_qd_gf_week_as_of
  ON qd_industry_glass_fiber_weekly (as_of DESC);
