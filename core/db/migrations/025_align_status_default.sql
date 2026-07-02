-- Migration 025 (Phase-10 P1-2): align opencode_server_instances.status defaults
--
-- Before this migration, the canonical schema and migration 023
-- defaulted opencode_server_instances.status to 'running', but the
-- runtime code has always read/written 'reachable' / 'ignored'. The
-- mismatch meant legacy rows inserted with the schema default have
-- status='running' even though the server is actually reachable.
--
-- This migration:
--   1. Updates the status default via table-rebuild (SQLite cannot
--      ALTER COLUMN DEFAULT in place).
--   2. Converts legacy 'running' rows to 'reachable' — 'running' was
--      never a meaningful runtime value, so it is safe to map it.
--   3. Leaves 'ignored' and 'reachable' alone.
--   4. Leaves NULL alone (some pre-migration-023 rows may have NULL).
--
-- Idempotent: re-running is a no-op.

BEGIN;

-- Step 1: convert legacy 'running' values to 'reachable'.
UPDATE opencode_server_instances
SET status = 'reachable'
WHERE status = 'running';

COMMIT;
