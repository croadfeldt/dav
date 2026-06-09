-- Migration 018: audit log — who did what + auth events (login/logout/timeout).
--
-- Append-only record of mutating API actions (auto-captured in a dedicated
-- middleware, fire-and-forget so it never adds request latency) and auth events.
-- Reads are NOT audited (mutations + auth only). project_id is intentionally
-- NOT a foreign key so audit history survives project deletion. See audit.py.

BEGIN;

CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor        TEXT,                              -- email / identity (NULL = anonymous)
    actor_source TEXT,                              -- session | proxy | service | unknown
    action       TEXT NOT NULL,                     -- e.g. 'post:/api/runs', 'auth.login'
    method       TEXT,
    path         TEXT,
    object_type  TEXT,
    object_id    TEXT,
    project_id   BIGINT,                            -- nullable, NOT a FK (survives project delete)
    outcome      TEXT NOT NULL DEFAULT 'success',   -- success | denied | error | failure
    status_code  INTEGER,
    ip           TEXT,
    user_agent   TEXT,
    summary      TEXT,
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor   ON audit_log(lower(actor));
CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log(outcome);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_log(project_id);

COMMIT;
