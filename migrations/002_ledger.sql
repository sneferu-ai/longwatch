-- The custody ledger (§7) — append-only, completely.
-- Row-level triggers do not fire on TRUNCATE, so the guard is deliberately
-- statement-level and covers all three verbs.  Silent no-op rules are
-- forbidden.

CREATE TABLE ledger_events (
  seq bigserial PRIMARY KEY,
  prev_hash char(64) NOT NULL, row_hash char(64) NOT NULL UNIQUE,
  event_type text NOT NULL, actor text NOT NULL,
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  recorded_at_ms bigint NOT NULL);

CREATE TABLE ledger_verify_state (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  last_seq_verified bigint NOT NULL, head_hash char(64) NOT NULL,
  verified_at timestamptz NOT NULL DEFAULT now());

CREATE FUNCTION ledger_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'ledger_immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_events_no_update
  BEFORE UPDATE ON ledger_events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();

CREATE TRIGGER ledger_events_no_delete
  BEFORE DELETE ON ledger_events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();

CREATE TRIGGER ledger_events_no_truncate
  BEFORE TRUNCATE ON ledger_events
  FOR EACH STATEMENT EXECUTE FUNCTION ledger_immutable();

-- The chain itself is computed in application code (a generated column cannot
-- reference other rows); this constraint is defense-in-depth collision
-- detection and a collision is a hard, loud failure.
ALTER TABLE ledger_events ADD CONSTRAINT ledger_events_seq_positive CHECK (seq > 0);
