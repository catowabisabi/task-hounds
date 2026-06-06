ALTER TABLE agent_registry ADD COLUMN backend_type TEXT DEFAULT 'opencode';
ALTER TABLE agent_registry ADD COLUMN backend_config_json TEXT;
