-- Migration 024 (Phase-10 P1-1): dedup opencode_server_instances
--
-- Historical data can have many rows for the same (host, port) —
-- e.g. 45 rows for 127.0.0.1:18765 from repeated ensure_managed /
-- discover / attach cycles before register_external was made a
-- true upsert. This migration:
--
--   1. Picks the newest row per (host, port) as the keeper
--      (max(id) wins; tie-break by max(started_at)).
--   2. Re-points any agent_runtime_bindings rows that referenced
--      a duplicate row at the keeper id.
--   3. Deletes the duplicate rows.
--
-- Idempotent: re-running on a clean DB is a no-op.

BEGIN;

-- Step 1: build a (host, port) -> keeper_id map in a temp table.
DROP TABLE IF EXISTS _phase10_p1_1_keeper;
CREATE TEMP TABLE _phase10_p1_1_keeper AS
SELECT host, port, MAX(id) AS keeper_id
FROM opencode_server_instances
GROUP BY host, port
HAVING COUNT(*) > 1;

-- Step 2: re-point agent_runtime_bindings that reference a
-- non-keeper duplicate.
UPDATE agent_runtime_bindings
SET server_instance_id = (
    SELECT k.keeper_id
    FROM _phase10_p1_1_keeper k
    WHERE k.host = (
        SELECT host FROM opencode_server_instances
        WHERE id = agent_runtime_bindings.server_instance_id
    )
    AND k.port = (
        SELECT port FROM opencode_server_instances
        WHERE id = agent_runtime_bindings.server_instance_id
    )
)
WHERE server_instance_id IN (
    SELECT id FROM opencode_server_instances
    WHERE id NOT IN (SELECT keeper_id FROM _phase10_p1_1_keeper)
    AND (host, port) IN (SELECT host, port FROM _phase10_p1_1_keeper)
);

-- Step 3: delete duplicate rows (keep the keeper).
DELETE FROM opencode_server_instances
WHERE id NOT IN (SELECT keeper_id FROM _phase10_p1_1_keeper)
  AND (host, port) IN (SELECT host, port FROM _phase10_p1_1_keeper);

DROP TABLE _phase10_p1_1_keeper;

COMMIT;
