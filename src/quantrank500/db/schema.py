"""Application schema (spec §7), prototype scope: UUID ledger tier only.

webauthn_credentials arrives at M6 with the ranked tier. Role-level INSERT-only
permission hardening arrives at M5; the structural constraints live here from day one.
Idempotent: every statement is IF NOT EXISTS.
"""

import psycopg

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    public_id      UUID PRIMARY KEY,
    api_token_hash CHAR(64) NOT NULL,            -- SHA-256; the token itself is never stored
    display_name   VARCHAR(32),
    verified_at    TIMESTAMPTZ,                  -- set when passkey is bound (M6)
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_verified_names ON identities (LOWER(display_name))
    WHERE verified_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS predictions (
    id           SERIAL PRIMARY KEY,
    identity_id  UUID REFERENCES identities(public_id) NOT NULL,
    ticker       VARCHAR(10) NOT NULL,
    session_date DATE NOT NULL,
    entry_price  DECIMAL(12,4) NOT NULL CHECK (entry_price > 0),
    stop_loss    DECIMAL(12,4) NOT NULL CHECK (stop_loss > 0),
    target_price DECIMAL(12,4) NOT NULL CHECK (target_price > 0),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    commit_nonce CHAR(32) NOT NULL,              -- random 128-bit; defeats brute-forcing
    status       VARCHAR(10) CHECK (status IN
                 ('queued','working','active','unfilled','closed')) DEFAULT 'queued',
    fill_price   DECIMAL(12,4),
    fill_time    TIMESTAMPTZ,
    volatile_open_flag BOOLEAN DEFAULT FALSE,
    CONSTRAINT one_per_session      UNIQUE (identity_id, session_date),
    CONSTRAINT valid_long_plan      CHECK (stop_loss < entry_price
                                           AND target_price > entry_price),
    CONSTRAINT min_target_distance  CHECK (target_price >= entry_price * 1.085),
    CONSTRAINT min_risk_distance    CHECK (entry_price - stop_loss >= entry_price * 0.001)
);
-- status/fill fields are a worker-maintained projection; prediction_events is the truth.
CREATE INDEX IF NOT EXISTS idx_predictions_identity ON predictions (identity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions (status);
CREATE INDEX IF NOT EXISTS idx_predictions_status_session ON predictions (status, session_date);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions (ticker);

CREATE TABLE IF NOT EXISTS settlements (
    prediction_id INTEGER PRIMARY KEY REFERENCES predictions(id),
    exit_price   DECIMAL(12,4) NOT NULL CHECK (exit_price > 0),
    pnl          DECIMAL(12,4) NOT NULL,          -- standardized: 500 x (exit - fill) / fill
    result       VARCHAR(10) CHECK (result IN ('win','loss','breakeven','unresolved')) NOT NULL,
    settled_by   VARCHAR(20) NOT NULL CHECK (settled_by IN
                 ('stop','target','expiry','halt','ambiguous')),
    settled_bar_open DECIMAL(12,4), settled_bar_high DECIMAL(12,4),
    settled_bar_low  DECIMAL(12,4), settled_bar_close DECIMAL(12,4),
    settled_at   TIMESTAMPTZ,                     -- market time of the settling print/bar
    provider_record_id TEXT,
    ingested_at  TIMESTAMPTZ,
    closed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prediction_events (    -- INSERT-ONLY. THE SOURCE OF TRUTH.
    id            BIGSERIAL PRIMARY KEY,
    prediction_id INTEGER REFERENCES predictions(id) NOT NULL,
    event_type    VARCHAR(20) NOT NULL CHECK (event_type IN
                  ('posted','working','filled','unfilled','settled',
                   'corporate_action','correction')),
    payload       JSONB NOT NULL,
    provider_record_id TEXT,
    ingested_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    prev_hash     CHAR(64) NOT NULL,
    event_hash    CHAR(64) NOT NULL               -- SHA-256(prev_hash || canonical payload)
);

CREATE TABLE IF NOT EXISTS settlement_runs (
    id           SERIAL PRIMARY KEY,
    session_date DATE NOT NULL UNIQUE,
    started_at   TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status       VARCHAR(12) CHECK (status IN ('running','completed','failed')) NOT NULL,
    data_mode    VARCHAR(6) CHECK (data_mode IN ('ticks','bars','mixed')),
    predictions_settled INTEGER,
    predictions_unfilled INTEGER,
    daily_root_hash CHAR(64),
    notes        TEXT
);
"""


def apply_app_schema(conn: psycopg.Connection) -> None:
    conn.execute(APP_SCHEMA)
    conn.commit()
