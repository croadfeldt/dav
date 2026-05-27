-- Migration 008: pr_comments table + uc_pr_comment_links
--
-- PR comments ingested from managed_repos rows with role=issue-source.
-- Either via the GitHub poller (M5) or the webhook receiver (M6). Each
-- comment lands in 'new' status; the operator dismisses or drafts-to-UC
-- via the Inbox UI (M8). When a UC is drafted from a comment, the link
-- table records the provenance.
--
-- Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS pr_comments (
    id                  SERIAL PRIMARY KEY,
    uuid                UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),

    -- Source repo (FK into the managed_repos registry — comment ingestion
    -- only happens for rows with role=issue-source). Cascading delete is
    -- intentional: removing a repo from the registry also removes its
    -- ingested comments. tenant_id is denormalized for fast filtering
    -- without joining.
    repo_uuid           UUID NOT NULL REFERENCES managed_repos(uuid) ON DELETE CASCADE,
    tenant_id           TEXT NOT NULL DEFAULT 'default',

    -- GitHub identifiers. github_comment_id + github_comment_type is the
    -- natural primary key from the source; (repo_uuid, github_comment_id,
    -- github_comment_type) UNIQUE handles upserts on re-poll / webhook
    -- replay. type vocabulary is open-ended; v1 uses:
    --   - 'issue_comment'              (the PR body comments thread)
    --   - 'pull_request_review_comment' (per-line review comments)
    github_comment_id   BIGINT NOT NULL,
    github_comment_type TEXT   NOT NULL,
    pr_number           INTEGER NOT NULL,
    pr_title            TEXT,
    pr_url              TEXT,

    -- Author + content
    author_login        TEXT NOT NULL,
    author_url          TEXT,
    body                TEXT NOT NULL,
    comment_url         TEXT,

    -- Lifecycle in the Inbox
    --   'new'           — freshly ingested; awaiting curation
    --   'dismissed'     — operator decided this isn't a UC candidate
    --   'drafted_to_uc' — operator created a UC from this; see uc_pr_comment_links
    status              TEXT NOT NULL DEFAULT 'new',
    status_changed_at   TIMESTAMPTZ,
    status_changed_by   TEXT,

    -- Timestamps
    github_created_at   TIMESTAMPTZ NOT NULL,
    github_updated_at   TIMESTAMPTZ NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Ingestion provenance: how the comment landed here
    --   'poller'  — periodic GitHub API poll (M5)
    --   'webhook' — pushed by GitHub webhook (M6)
    --   'manual'  — operator-added (rare; testing)
    ingestion_source    TEXT NOT NULL DEFAULT 'poller',

    UNIQUE (repo_uuid, github_comment_id, github_comment_type)
);

CREATE INDEX IF NOT EXISTS idx_pr_comments_status      ON pr_comments (status, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_pr_comments_repo        ON pr_comments (repo_uuid);
CREATE INDEX IF NOT EXISTS idx_pr_comments_tenant      ON pr_comments (tenant_id);
CREATE INDEX IF NOT EXISTS idx_pr_comments_pr          ON pr_comments (repo_uuid, pr_number);
CREATE INDEX IF NOT EXISTS idx_pr_comments_github_upd  ON pr_comments (github_updated_at DESC);

-- Provenance: links a UC (managed or corpus) to the PR comment that drove
-- its creation. One comment can spawn multiple UCs; one UC can be linked
-- to multiple comments (rare). M7 + M8 use this for the Inbox → UC flow.
CREATE TABLE IF NOT EXISTS uc_pr_comment_links (
    id              SERIAL PRIMARY KEY,
    uc_uuid         UUID NOT NULL,
    pr_comment_uuid UUID NOT NULL REFERENCES pr_comments(uuid) ON DELETE CASCADE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_by       TEXT NOT NULL DEFAULT 'system',
    -- Optional reviewer-facing note about the draft (e.g., "renamed
    -- VM-X-Y to standard-vm-provision and added soft-dep variant")
    notes           TEXT,
    UNIQUE (uc_uuid, pr_comment_uuid)
);

CREATE INDEX IF NOT EXISTS idx_uc_pr_links_uc      ON uc_pr_comment_links (uc_uuid);
CREATE INDEX IF NOT EXISTS idx_uc_pr_links_comment ON uc_pr_comment_links (pr_comment_uuid);

-- Poll state per repo: when did we last successfully poll, what was the
-- newest comment we saw, etc. One row per managed_repos.uuid with
-- role=issue-source. Created on first poll; updated each successful pass.
CREATE TABLE IF NOT EXISTS pr_comment_poll_state (
    repo_uuid           UUID PRIMARY KEY REFERENCES managed_repos(uuid) ON DELETE CASCADE,
    last_poll_started_at  TIMESTAMPTZ,
    last_poll_finished_at TIMESTAMPTZ,
    last_poll_ok          BOOLEAN,
    last_poll_error       TEXT,
    comments_seen_total   BIGINT NOT NULL DEFAULT 0,
    -- Newest comment github_updated_at we've seen across both comment
    -- types. The poller uses this as a watermark to bail early on pages
    -- of comments where everything is older.
    newest_seen_updated_at TIMESTAMPTZ
);

COMMIT;
