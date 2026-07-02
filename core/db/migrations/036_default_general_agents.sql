UPDATE agent_runtime_bindings
SET opencode_agent = 'general',
    updated_at = CURRENT_TIMESTAMP
WHERE role IN ('manager', 'worker', 'reviewer', 'chat');

UPDATE agent_registry
SET opencode_agent = 'general',
    updated_at = CURRENT_TIMESTAMP
WHERE role IN ('manager', 'worker', 'reviewer', 'chat');
