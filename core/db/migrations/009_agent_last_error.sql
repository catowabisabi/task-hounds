-- Migration 009: Add last_error column to agent_registry
-- Used by db_skill.py to record skill errors for UI visibility

ALTER TABLE agent_registry ADD COLUMN last_error TEXT;