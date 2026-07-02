ALTER TABLE opencode_server_instances ADD COLUMN owner TEXT;
ALTER TABLE opencode_server_instances ADD COLUMN managed INTEGER DEFAULT 0;
ALTER TABLE opencode_server_instances ADD COLUMN status TEXT DEFAULT 'reachable';
