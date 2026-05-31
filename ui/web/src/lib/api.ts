const FASTAPI_BASE = "http://127.0.0.1:8766";
const LEGACY_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8765";

let _base: string | null = null;
let _basePromise: Promise<string> | null = null;

async function tryPing(url: string, timeout = 3000): Promise<boolean> {
  for (const endpoint of ["/api/health", "/api/ping"]) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeout);
      const res = await fetch(`${url}${endpoint}`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (res.status < 502) return true;
    } catch {
      // Try the next health endpoint.
    }
  }
  return false;
}

async function resolveBase(): Promise<string> {
  if (_base) return _base;
  if (_basePromise) return _basePromise;

  _basePromise = (async (): Promise<string> => {
    const currentOrigin = window.location.origin;

    if (await tryPing(currentOrigin)) {
      _base = currentOrigin;
      console.log(`[API] Using current origin ${currentOrigin} (same origin, no CORS)`);
      return currentOrigin;
    }

    if (currentOrigin !== FASTAPI_BASE) {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (await tryPing(FASTAPI_BASE)) {
          _base = FASTAPI_BASE;
          console.log(`[API] Connected to FastAPI on port 8766`);
          return FASTAPI_BASE;
        }
        if (attempt < 2) {
          console.log(`[API] FastAPI not ready, retrying in 1s (${attempt + 1}/3)...`);
          await new Promise(r => setTimeout(r, 1000));
        }
      }
    }

    _base = LEGACY_BASE;
    console.log(`[API] Falling back to legacy server on port 8765`);
    return LEGACY_BASE;
  })();

  return _basePromise;
}

function getBase(): string {
  return _base ?? window.location.origin ?? LEGACY_BASE;
}

// ── Online state & retry counter ────────────────────────────────────────────

export let isOnline = true;
export let failureCount = 0;

export async function retryFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await resolveBase();
  const maxRetries = 3;
  const delays = [1000, 2000, 4000]; // 1s, 2s, 4s

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const res = await fetch(`${base}${path}`, {
        headers: { "Content-Type": "application/json", ...init?.headers },
        ...init,
      });
      if (res.ok) {
        if (!isOnline) { isOnline = true; }
        failureCount = 0;
        return res.json();
      }
      // Non-OK response = treat as failure, retry
      throw new Error(`${res.status} ${res.statusText}`);
    } catch (err) {
      if (attempt === maxRetries) {
        failureCount++;
        if (failureCount > 3) { isOnline = false; }
        throw err;
      }
      // Wait before next retry
      await new Promise(resolve => setTimeout(resolve, delays[attempt] ?? 4000));
    }
  }
  // Should never reach here, but satisfy TypeScript
  throw new Error("retryFetch: unexpected exit");
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await resolveBase();
  const res = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = body?.message || body?.error || "";
    } catch {
      try { detail = await res.text(); } catch { detail = ""; }
    }
    throw new Error(`${res.status} ${res.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return res.json();
}

export const apiGet    = <T>(path: string) => apiFetch<T>(path);
export const apiPost   = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "POST",   body: body ? JSON.stringify(body) : undefined });
export const apiPut    = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "PUT",    body: body ? JSON.stringify(body) : undefined });
export const apiPatch  = <T>(path: string, body?: unknown) =>
  apiFetch<T>(path, { method: "PATCH",  body: body ? JSON.stringify(body) : undefined });
export const apiDelete = <T>(path: string) =>
  apiFetch<T>(path, { method: "DELETE" });

export async function debugLog(msg: string, source = "frontend") {
  try {
    const base = await resolveBase();
    await fetch(`${base}/api/debug-logs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msg, source }),
    });
  } catch {
    console.log(`[DEBUG-FALLBACK] ${msg}`);
  }
}

export function wsUrl(path: string) {
  return `${getBase().replace("http", "ws")}${path}`;
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface Agent {
  id: number;
  name: string;
  role: string;
  host: string;
  port: number;
  model: string | null;
  opencode_agent: string;
  state: "idle" | "busy" | "waiting" | "error" | "offline";
  task_complete: number;
  last_error: string | null;
  last_seen: string | null;
  session_id: string | null;
  backend_type: string;
  backend_config_json: string | null;
}

export interface LoopStatus {
  running: boolean;
  pid: number | null;
}

export interface Suggestion {
  id?: number;
  content?: string;
  status?: string;
  queue_status?: string;
  status_label?: string;
  verification?: string;
  related_files?: string[];
  created_at?: string;
}

export interface ManagerMessage {
  id: number;
  content: string;
  created_at: string;
  is_human?: boolean;
  queue_status?: string;
  status_label?: string;
}

export interface ChatMessage {
  id: number;
  session_id: string;
  sender: "user" | "chat" | string;
  content: string;
  created_at: string;
}

export interface SessionInfo {
  session_key: string;
  session_name: string;
  agent_name: string | null;
  folder_relation: string;
  created_at: string;
  last_active_at: string;
  worker_status: string;
  token_usage: number;
  archived?: boolean;
}

export interface SessionsResponse {
  live: SessionInfo[];
  live_count: number;
  archived_count: number;
}

export interface ArchivedSessionsResponse {
  sessions: SessionInfo[];
}
