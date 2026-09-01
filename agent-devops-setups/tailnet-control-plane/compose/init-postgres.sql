-- pheno_catalog bootstrap
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  host TEXT NOT NULL,
  tailscale_ip TEXT,
  role TEXT,
  last_heartbeat TIMESTAMPTZ,
  meta JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  suite TEXT NOT NULL,
  device_profile TEXT,
  model_alias TEXT,
  definition JSONB NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
  id BIGSERIAL PRIMARY KEY,
  experiment_id TEXT REFERENCES experiments(experiment_id),
  timestamp TIMESTAMPTZ NOT NULL,
  device_role TEXT,
  host TEXT,
  model_key TEXT,
  prefill_tps DOUBLE PRECISION,
  decode_tps DOUBLE PRECISION,
  ttft_s DOUBLE PRECISION,
  e2e_s DOUBLE PRECISION,
  notes TEXT,
  driver TEXT,
  source TEXT,
  langfuse_trace_id TEXT,
  artifact_uri TEXT,
  raw JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS runs_experiment_ts ON runs (experiment_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS runs_host_ts ON runs (host, timestamp DESC);

CREATE TABLE IF NOT EXISTS sync_manifest (
  device_id TEXT PRIMARY KEY,
  last_sync TIMESTAMPTZ,
  git_sha TEXT,
  meta JSONB DEFAULT '{}'::jsonb
);
