-- Migration 027: flow_checkpoints.archived_at
-- P7 audit id 267: compat_archive_checkpoint was a stub returning
-- {archived,ok} without touching the DB. Restore the archive
-- side effect by adding an archived_at column to flow_checkpoints
-- and exposing db_wf.archive_checkpoint(int(cp_id)).

ALTER TABLE flow_checkpoints ADD COLUMN archived_at TEXT;

CREATE INDEX IF NOT EXISTS idx_flow_checkpoints_archived_at
    ON flow_checkpoints(archived_at);
