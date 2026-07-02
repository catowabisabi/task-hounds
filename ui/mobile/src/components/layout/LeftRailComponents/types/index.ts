export interface Workspace {
  id: string;
  path: string;
  label: string;
  active?: boolean;
  path_missing?: boolean;
  progress_completed?: number;
  progress_total?: number;
  progress_percent?: number;
  progress_state?: "not_started" | "active" | "completed";
}

export interface ProjectSession {
  id: string;
  workspace_id: string;
  name: string;
  is_active: number;
  created_at: string;
  progress_completed?: number;
  progress_total?: number;
  progress_percent?: number;
  progress_state?: "not_started" | "active" | "completed";
}

export interface SessionRuntimeStatus {
  state: "idle" | "running" | "waiting_for_answer" | "paused" | "stopping" | "error";
  role?: string | null;
  detail?: string;
  started_at?: string | null;
  run_id?: number | null;
}
