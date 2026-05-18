-- PostgreSQL init script for composable agent stack
-- Creates databases and tables for both the orchestrator and LiteLLM

-- Orchestrator database (already created via POSTGRES_DB)
CREATE TABLE IF NOT EXISTS workflows (
    id UUID PRIMARY KEY,
    user_request TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    action_type TEXT NOT NULL DEFAULT 'execute',
    input JSONB NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    result JSONB,
    error TEXT,
    depends_on UUID[] DEFAULT '{}',
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_workflow ON tasks(workflow_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- LiteLLM database
CREATE DATABASE litellm;
