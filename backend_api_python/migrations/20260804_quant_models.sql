-- migrations/20260804_quant_models.sql
CREATE TABLE IF NOT EXISTS qd_quant_models (
  id               BIGSERIAL PRIMARY KEY,
  model_key        VARCHAR(80)  NOT NULL,
  display_name     VARCHAR(200) NOT NULL,
  status           VARCHAR(20)  NOT NULL DEFAULT 'draft',
  kind             VARCHAR(20)  NOT NULL,
  alpha_source     VARCHAR(80)  NOT NULL,
  alpha_version    VARCHAR(120) NOT NULL,
  universe         VARCHAR(80)  NOT NULL DEFAULT 'csi300',
  owner_user_id    BIGINT,
  provenance_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
  metrics_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  published_at     TIMESTAMPTZ,
  created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (model_key),
  UNIQUE (alpha_source, alpha_version)
);
CREATE INDEX IF NOT EXISTS idx_qd_quant_models_published
  ON qd_quant_models (status, published_at DESC);
