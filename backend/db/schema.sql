-- AegisPay persistence schema (PostgreSQL-compatible; also runs on SQLite for dev/tests).
-- Money is integer micro-dollars (1 USD = 1,000,000). Timestamps are ISO-8601 UTC.

CREATE TABLE IF NOT EXISTS contracts (
    agreement_id            VARCHAR(128) PRIMARY KEY,
    buyer_agent_id          VARCHAR(128) NOT NULL,
    worker_agent_id         VARCHAR(128) NOT NULL,
    worker_paypal_email     VARCHAR(254) NOT NULL,
    budget_micros           BIGINT NOT NULL CHECK (budget_micros > 0),
    task_description        TEXT NOT NULL,
    timeout_seconds         INTEGER NOT NULL,
    fsm_state               VARCHAR(40) NOT NULL,
    paypal_order_id         VARCHAR(128),
    paypal_authorization_id VARCHAR(128),
    paypal_capture_id       VARCHAR(128),
    paypal_payout_batch_id  VARCHAR(128),
    verification_score      DOUBLE PRECISION,
    authorized_at           TIMESTAMPTZ,
    report_json             TEXT,
    created_at              TIMESTAMPTZ NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS state_transitions (
    seq          BIGINT PRIMARY KEY,
    agreement_id VARCHAR(128) NOT NULL REFERENCES contracts (agreement_id),
    from_state   VARCHAR(40),
    to_state     VARCHAR(40) NOT NULL,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_agreement ON state_transitions (agreement_id, seq);

-- entry_kind: TRANSFER (double-entry journal line) or DEPOSIT (external funding of debit_account).
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id       VARCHAR(64) PRIMARY KEY,
    seq            BIGINT NOT NULL,
    entry_kind     VARCHAR(16) NOT NULL DEFAULT 'TRANSFER',
    debit_account  VARCHAR(64) NOT NULL,
    credit_account VARCHAR(64) NOT NULL,
    amount_micros  BIGINT NOT NULL CHECK (amount_micros > 0),
    reference_id   VARCHAR(128) NOT NULL,
    currency       VARCHAR(3) NOT NULL DEFAULT 'USD',
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_seq ON ledger_entries (seq);
CREATE INDEX IF NOT EXISTS idx_ledger_reference ON ledger_entries (reference_id);

CREATE TABLE IF NOT EXISTS payouts (
    payout_id     VARCHAR(64) PRIMARY KEY,
    agreement_id  VARCHAR(128) NOT NULL REFERENCES contracts (agreement_id),
    batch_id      VARCHAR(128) NOT NULL,
    amount_micros BIGINT NOT NULL,
    status        VARCHAR(24) NOT NULL,   -- DISPATCHED | SUCCEEDED | FAILED | UNCLAIMED
    attempt       INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payouts_batch ON payouts (batch_id);
CREATE INDEX IF NOT EXISTS idx_payouts_agreement ON payouts (agreement_id);

-- event_id PRIMARY KEY gives webhook idempotency: replays of the same PayPal event are rejected.
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id    VARCHAR(128) PRIMARY KEY,
    event_type  VARCHAR(128) NOT NULL,
    resource_id VARCHAR(128),
    payload     TEXT NOT NULL,
    outcome     VARCHAR(64),
    received_at TIMESTAMPTZ NOT NULL
);
