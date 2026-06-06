export interface Workspace {
  id: string;
  path: string;
  label: string;
  active?: boolean;
  path_missing?: boolean;
}

export interface ProjectSession {
  id: string;
  workspace_id: string;
  name: string;
  is_active: number;
  created_at: string;
}