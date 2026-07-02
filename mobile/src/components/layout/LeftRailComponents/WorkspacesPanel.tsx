import { useState, useEffect, useRef, useCallback } from "react";
import { FolderOpen } from "lucide-react";
import { apiGet, apiPost, apiPatch, apiDelete } from "../../../lib/api";
import type { ProjectSession, SessionRuntimeStatus, Workspace } from "./types";
import { RelinkProjectModal } from "./RelinkProjectModal";
import { InfoModal } from "../../ui/InfoModal";
import { LoadingWidget } from "../../ui/LoadingWidget";

interface WorkspacesPanelProps {
  onActivate: (scope?: "workspace" | "session") => void | Promise<void>;
  sessionReloadKey?: number;
}

function ProgressBar({ completed = 0, total = 0, percent = 0, compact = false }: {
  completed?: number;
  total?: number;
  percent?: number;
  compact?: boolean;
}) {
  const safePercent = Math.max(0, Math.min(100, percent));
  const label = total === 0 ? "Not started" : total === completed ? "Complete" : `${completed}/${total} done`;
  return (
    <div className={compact ? "mt-0.5" : "px-1.5 pb-1.5"}>
      <div className="flex items-center gap-2">
        <div
          className="h-1 flex-1 overflow-hidden rounded-sm"
          style={{ background: "var(--border-dim)" }}
          role="progressbar"
          aria-label="Project progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={safePercent}
        >
          <div
            className="h-full transition-[width] duration-300"
            style={{
              width: `${safePercent}%`,
              background: safePercent === 100 ? "var(--green)" : "var(--amber)",
            }}
          />
        </div>
        <span className="w-[62px] shrink-0 text-right text-[8px]" style={{ color: "var(--text-dim)" }}>
          {label}
        </span>
      </div>
    </div>
  );
}

function parseRuntimeTime(value?: string | null): number | null {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function elapsedLabel(startedAt: string | null | undefined, now: number): string {
  const started = parseRuntimeTime(startedAt);
  if (started == null) return "";
  const total = Math.max(0, Math.floor((now - started) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function SessionRuntimeIndicator({ status, now }: {
  status?: SessionRuntimeStatus;
  now: number;
}) {
  if (!status || status.state === "idle") return null;
  const config = {
    running: { label: "Running", color: "var(--green)", background: "var(--green-bg)" },
    waiting_for_answer: { label: "Waiting for answer", color: "var(--amber)", background: "var(--amber-bg)" },
    paused: { label: "Paused", color: "var(--amber)", background: "var(--amber-bg)" },
    stopping: { label: "Stopping", color: "var(--red)", background: "var(--red-bg)" },
    error: { label: "Error", color: "var(--red)", background: "var(--red-bg)" },
  }[status.state];
  const elapsed = elapsedLabel(status.started_at, now);
  const role = status.role?.replaceAll("_", " ");
  const title = [config.label, role, status.detail, elapsed].filter(Boolean).join(" · ");

  return (
    <span
      className="inline-flex max-w-full items-center gap-1 rounded px-1.5 py-0.5 text-[8px]"
      style={{ color: config.color, background: config.background }}
      title={title}
      aria-label={title}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${status.state === "running" || status.state === "waiting_for_answer" ? "animate-pulse" : ""}`}
        style={{ background: config.color }}
      />
      <span className="truncate">{config.label}{role ? ` · ${role}` : ""}</span>
      {elapsed && <span className="shrink-0 font-mono">{elapsed}</span>}
    </span>
  );
}

export function WorkspacesPanel({ onActivate, sessionReloadKey }: WorkspacesPanelProps) {
  const [workspaces, setWorkspaces]   = useState<Workspace[]>([]);
  const [sessions, setSessions]       = useState<Record<string, ProjectSession[]>>({});
  const [expanded, setExpanded]       = useState<Record<string, boolean>>({});
  const [menuOpen, setMenuOpen]         = useState<string | null>(null);
  const [sessionMenuOpen, setSessionMenuOpen] = useState<string | null>(null);
  const [renaming, setRenaming]         = useState<string | null>(null);
  const [renameVal, setRenameVal]       = useState("");
  const [sessionRenaming, setSessionRenaming] = useState<string | null>(null);
  const [sessionRenameVal, setSessionRenameVal] = useState("");
  const [picking, setPicking]           = useState(false);
  const [pickingStage, setPickingStage] = useState("");
  const [relinking, setRelinking]       = useState(false);
  const [relinkTarget, setRelinkTarget] = useState<Workspace | null>(null);
  const [relinkError, setRelinkError]   = useState("");
  const menuRef        = useRef<HTMLDivElement>(null);
  const sessionMenuRef = useRef<HTMLDivElement>(null);
  const [infoModal, setInfoModal] = useState<{ msg: string; onOk: () => void } | null>(null);
  const [switchingTo, setSwitchingTo] = useState<string | null>(null);
  const [deleteProjectTarget, setDeleteProjectTarget] = useState<Workspace | null>(null);
  const [runtimeStatuses, setRuntimeStatuses] = useState<Record<string, SessionRuntimeStatus>>({});
  const [runtimeNow, setRuntimeNow] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    const loadRuntimeStatuses = async () => {
      const result = await apiGet<{ sessions?: Record<string, SessionRuntimeStatus> }>(
        "/api/runtime/session-statuses",
      ).catch(() => null);
      if (!cancelled && result?.sessions) setRuntimeStatuses(result.sessions);
    };
    void loadRuntimeStatuses();
    const statusTimer = window.setInterval(() => void loadRuntimeStatuses(), 2000);
    const clockTimer = window.setInterval(() => setRuntimeNow(Date.now()), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(statusTimer);
      window.clearInterval(clockTimer);
    };
  }, []);

  const loadSessions = useCallback(async (wsId: string) => {
    try {
      const data = await apiGet<ProjectSession[]>(`/api/workspaces/${wsId}/sessions`);
      setSessions(s => ({
        ...s,
        [wsId]: data,
      }));
    } catch { /* ignore */ }
  }, []);

  const load = useCallback(() => {
    return (
    apiGet<Workspace[]>("/api/workspaces").then(data => {
      setWorkspaces(data);
      const active = data.find(w => w.active);
      if (active) {
        setExpanded(current => ({ ...current, [active.id]: current[active.id] ?? true }));
        loadSessions(active.id);
      }
    }).catch(() => {})
    );
  }, [loadSessions]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => { cancelled = true; };
  }, [load]);

  useEffect(() => {
    if (!sessionReloadKey) return;
    apiGet<Workspace[]>("/api/workspaces").then(data => {
      setWorkspaces(data);
      const active = data.find(w => w.active);
      if (active) {
        setExpanded(current => ({ ...current, [active.id]: current[active.id] ?? true }));
        loadSessions(active.id);
      }
    }).catch(() => {});
  }, [loadSessions, sessionReloadKey]);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  useEffect(() => {
    if (!sessionMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (sessionMenuRef.current && !sessionMenuRef.current.contains(e.target as Node)) setSessionMenuOpen(null);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [sessionMenuOpen]);

  const pickAndAdd = async () => {
    if (picking) return;
    setPicking(true);
    setPickingStage("Selecting folder...");
    try {
      let chosen = window.electronAPI?.pickFolder
        ? await window.electronAPI.pickFolder()
        : null;
      type PickFolderResult = { path?: string; cancelled?: boolean; error?: string; already_exists?: boolean; existing_id?: string; existing_label?: string; };
      setPickingStage("Checking folder...");
      let r = chosen?.trim()
        ? await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() })
        : await apiPost<PickFolderResult>("/api/pick-folder", {});
      if (!r.path && !r.cancelled) {
        chosen = window.prompt("Project folder path");
        if (!chosen?.trim()) return;
        setPickingStage("Checking folder...");
        r = await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() });
      }
      if (r.path) {
        const path = r.path;
        if (r.already_exists && r.existing_id) {
          setPickingStage("Project already exists - activating...");
          setWorkspaces(w => w.map(item => ({ ...item, active: item.id === r.existing_id })));
          setExpanded(e => ({ ...e, [r.existing_id!]: true }));
          await activateWs(r.existing_id!);
          setPickingStage("");
          setInfoModal({ msg: `"${r.existing_label || path}" is already in your projects. Activated existing project.`, onOk: () => setInfoModal(null) });
          setPicking(false);
          return;
        }
        const label = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
        setPickingStage("Creating project...");
        const entry = await apiPost<Workspace>("/api/workspaces", { path, label });
        const firstSession: ProjectSession = {
          id: entry.id,
          workspace_id: entry.id,
          name: entry.label || "New Session",
          is_active: 1,
          created_at: (entry as Workspace & { created_at?: string }).created_at ?? "",
        };
        setWorkspaces(w => [...w.map(item => ({ ...item, active: false })), { ...entry, active: true }]);
        setSessions(s => ({ ...s, [entry.id]: [firstSession] }));
        setExpanded(e => ({ ...e, [entry.id]: true }));
        setPickingStage("Opening first session...");
        await activateWs(entry.id);
      }
    } catch (err) {
      setInfoModal({ msg: `Add project failed: ${err instanceof Error ? err.message : String(err)}`, onOk: () => setInfoModal(null) });
    } finally {
      setPicking(false);
      setPickingStage("");
    }
  };

  const switchSession = async (wsId: string, sessionId: string) => {
    setSwitchingTo(sessionId);
    try {
      await apiPost(`/api/project-sessions/${sessionId}/switch`);
      setSessions(current => Object.fromEntries(
        Object.entries(current).map(([workspaceId, items]) => [
          workspaceId,
          items.map(item => ({ ...item, is_active: item.id === sessionId ? 1 : 0 })),
        ]),
      ));
      setWorkspaces(current => current.map(item => ({ ...item, active: item.id === wsId })));
      window.dispatchEvent(new CustomEvent("task-hounds-session-switched", { detail: { sessionId } }));
      await onActivate("session");
    } catch (err) {
      setInfoModal({
        msg: `Session switch failed: ${err instanceof Error ? err.message : String(err)}`,
        onOk: () => setInfoModal(null),
      });
    } finally {
      setSwitchingTo(null);
    }
  };

  const deleteSession = async (wsId: string, sessionId: string) => {
    await apiDelete(`/api/project-sessions/${sessionId}`);
    setSessions(s => ({ ...s, [wsId]: (s[wsId] ?? []).filter(x => x.id !== sessionId) }));
    setSessionMenuOpen(null);
  };

  const startSessionRename = (s: ProjectSession) => {
    setSessionRenaming(s.id);
    setSessionRenameVal(s.name || "");
    setSessionMenuOpen(null);
  };

  const confirmSessionRename = async (wsId: string, sessionId: string) => {
    const val = sessionRenameVal.trim();
    if (!val) { setSessionRenaming(null); return; }
    await apiPatch(`/api/project-sessions/${sessionId}`, { name: val });
    setSessions(s => ({
      ...s,
      [wsId]: (s[wsId] ?? []).map(x => x.id === sessionId ? { ...x, name: val } : x),
    }));
    setSessionRenaming(null);
  };

  const activateWs = async (id: string, options: { promptRelink?: boolean } = {}) => {
    const promptRelink = options.promptRelink ?? true;
    setSwitchingTo(id);
    try {
      const r = await apiPost<{ sessions?: ProjectSession[] }>(`/api/workspaces/${id}/activate`);
      const ws = workspaces.find(x => x.id === id);
      setExpanded(current => ({ ...current, [id]: true }));
      setWorkspaces(w => w.map(x => ({ ...x, active: x.id === id })));
      if (r.sessions) setSessions(s => ({ ...s, [id]: r.sessions! }));
      else await loadSessions(id);
      await onActivate("workspace");
      if (promptRelink && ws?.path_missing) {
        setRelinkTarget({ ...ws, active: true });
        setRelinkError("");
      }
    } catch (err) {
      setInfoModal({
        msg: `Project switch failed: ${err instanceof Error ? err.message : String(err)}`,
        onOk: () => setInfoModal(null),
      });
    } finally {
      setSwitchingTo(null);
    }
  };

  const chooseFolderPath = async () => {
    let chosen = window.electronAPI?.pickFolder
      ? await window.electronAPI.pickFolder()
      : null;
    type PickFolderResult = { path?: string; cancelled?: boolean; error?: string };
    let result = chosen?.trim()
      ? await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() })
      : await apiPost<PickFolderResult>("/api/pick-folder", {});
    if (!result.path && !result.cancelled) {
      chosen = window.prompt("New project folder path");
      if (!chosen?.trim()) return "";
      result = await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() });
    }
    return result.path ?? "";
  };

  const toggleWorkspace = (wsId: string) => {
    const next = !expanded[wsId];
    setExpanded(current => ({ ...current, [wsId]: next }));
    if (next && !sessions[wsId]) void loadSessions(wsId);
  };

  const relinkWorkspace = async (ws: Workspace) => {
    if (relinking) return;
    setRelinking(true);
    setRelinkError("");
    try {
      const path = await chooseFolderPath();
      if (!path) return;
      const updated = await apiPost<{ workspace_path: string }>(`/api/workspaces/${ws.id}/relink`, { path });
      setWorkspaces(list => list.map(item => item.id === ws.id
        ? { ...item, path: updated.workspace_path ?? path, path_missing: false }
        : item
      ));
      setRelinkTarget(null);
      await activateWs(ws.id, { promptRelink: false });
    } catch (err) {
      setRelinkError(err instanceof Error ? err.message : "Relink failed");
    } finally {
      setRelinking(false);
    }
  };

  const remove = async (id: string) => {
    await apiDelete(`/api/workspaces/${id}`);
    setWorkspaces(w => w.filter(x => x.id !== id));
    setSessions(s => { const next = { ...s }; delete next[id]; return next; });
    setMenuOpen(null);
    setDeleteProjectTarget(null);
  };

  const startRename = (ws: Workspace) => {
    setRenaming(ws.id);
    setRenameVal(ws.label);
    setMenuOpen(null);
  };

  const openWorkspaceFolder = async (ws: Workspace) => {
    setMenuOpen(null);
    try {
      await apiPost(`/api/workspaces/${ws.id}/open-folder`);
    } catch (err) {
      setInfoModal({
        msg: `Open folder failed: ${err instanceof Error ? err.message : String(err)}`,
        onOk: () => setInfoModal(null),
      });
    }
  };

  const confirmRename = async (id: string) => {
    const val = renameVal.trim();
    if (!val) { setRenaming(null); return; }
    await apiPost(`/api/workspaces/${id}`, { label: val });
    setWorkspaces(w => w.map(x => x.id === id ? { ...x, label: val } : x));
    setRenaming(null);
  };

  return (
    <div className="p-3 space-y-1">
      {switchingTo && <LoadingWidget message="Loading project data..." />}
      <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "var(--text-dim)" }}>Projects</p>

      {workspaces.map(ws => {
        const isActive = !!ws.active;
        const missing = !!ws.path_missing;
        const projectRuntimeStatus = runtimeStatuses[ws.id] ?? (
          (sessions[ws.id] ?? [])
            .map(session => runtimeStatuses[session.id])
            .find(status => status && status.state !== "idle")
        );
        return (
          <div key={ws.id} className="space-y-0.5">
            <div
              className="flex items-center gap-1 rounded px-1.5 py-1 group transition-all duration-200"
              style={{
                background: missing ? "var(--amber-bg)" : (isActive ? "var(--amber-bg)" : "var(--bg-panel)"),
                border: missing ? "1px solid var(--amber-dim)" : (isActive ? "1px solid var(--amber)" : "1px solid var(--border-dim)"),
                boxShadow: missing ? "0 0 8px rgba(180,83,9,0.25)" : (isActive ? "0 0 8px rgba(245,158,11,0.35)" : "none"),
              }}
            >
              <button
                onClick={() => {
                  if (renaming) return;
                  toggleWorkspace(ws.id);
                }}
                className="shrink-0 px-0.5"
                style={{ color: isActive ? "var(--amber)" : "var(--text-dim)" }}
              >
                <span className="text-[10px]">{expanded[ws.id] ? "▼" : "▶"}</span>
              </button>

              {renaming === ws.id ? (
                <div className="flex items-center gap-1 flex-1 min-w-0" onClick={e => e.stopPropagation()}>
                  <input
                    className="flex-1 rounded px-1 py-0 text-[11px] outline-none min-w-0"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--amber)", color: "var(--text-primary)" }}
                    value={renameVal}
                    onChange={e => setRenameVal(e.target.value)}
                    onKeyDown={e => {
                      e.stopPropagation();
                      if (e.key === "Enter") confirmRename(ws.id);
                      if (e.key === "Escape") setRenaming(null);
                    }}
                    autoFocus
                  />
                  <button onClick={e => { e.stopPropagation(); confirmRename(ws.id); }} className="shrink-0 text-[11px] px-1 rounded" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>✓</button>
                  <button onClick={e => { e.stopPropagation(); setRenaming(null); }} className="shrink-0 text-[11px] px-1 rounded" style={{ color: "var(--text-secondary)" }}>✕</button>
                </div>
              ) : (
                <button
                  onClick={() => toggleWorkspace(ws.id)}
                  className="flex items-center gap-1 flex-1 text-left min-w-0"
                  title={missing ? "Original folder cannot be found. Relink to resume agent work." : "Expand project sessions"}
                >
                  <span className="text-[10px] shrink-0" style={{ filter: isActive ? "drop-shadow(0 0 4px var(--amber))" : "none" }}>📁</span>
                  <span className="text-[11px] truncate font-medium" style={{ color: isActive ? "var(--amber)" : "var(--text-secondary)" }}>{ws.label}</span>
                  <SessionRuntimeIndicator status={projectRuntimeStatus} now={runtimeNow} />
                  {missing && (
                    <span className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wide" style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}>
                      Folder Missing
                    </span>
                  )}
                </button>
              )}

              <div className="relative" ref={menuOpen === ws.id ? menuRef : undefined}>
                <button
                  onClick={e => { e.stopPropagation(); setMenuOpen(menuOpen === ws.id ? null : ws.id); }}
                  aria-label={`Project actions for ${ws.label}`}
                  className="opacity-0 group-hover:opacity-100 text-[12px] px-1 rounded transition-opacity leading-none"
                  style={{ color: "var(--text-dim)" }}
                  onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
                  onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}
                >⋯</button>
                {menuOpen === ws.id && (
                  <div className="absolute right-0 top-full mt-1 z-50 rounded py-1 min-w-[110px]" style={{ background: "var(--bg-raised)", border: "1px solid var(--border)", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
                    <button onClick={() => openWorkspaceFolder(ws)} className="w-full flex items-center gap-2 text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--text-secondary)" }} onMouseEnter={e => { e.currentTarget.style.background="var(--bg-hover)"; e.currentTarget.style.color="var(--text-primary)"; }} onMouseLeave={e => { e.currentTarget.style.background="transparent"; e.currentTarget.style.color="var(--text-secondary)"; }}>
                      <FolderOpen size={12} aria-hidden="true" />
                      <span>Open Folder</span>
                    </button>
                    <button onClick={() => startRename(ws)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--text-secondary)" }} onMouseEnter={e => { e.currentTarget.style.background="var(--bg-hover)"; e.currentTarget.style.color="var(--text-primary)"; }} onMouseLeave={e => { e.currentTarget.style.background="transparent"; e.currentTarget.style.color="var(--text-secondary)"; }}>✏ Rename</button>
                    <button onClick={() => { setMenuOpen(null); setDeleteProjectTarget(ws); }} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--red)" }} onMouseEnter={e => (e.currentTarget.style.background="var(--bg-hover)")} onMouseLeave={e => (e.currentTarget.style.background="transparent")}>✕ Remove</button>
                  </div>
                )}
              </div>
            </div>

            <ProgressBar
              completed={ws.progress_completed}
              total={ws.progress_total}
              percent={ws.progress_percent}
            />

            {expanded[ws.id] && (
              <div className="ml-2 rounded px-2 py-1.5 space-y-1.5 transition-all duration-200" style={{ background: "var(--bg-base)", border: isActive ? "1px solid var(--amber-dim)" : "1px solid var(--border-dim)", boxShadow: isActive ? "0 0 6px rgba(245,158,11,0.15)" : "none" }}>
                <p className="text-[9px] break-all leading-relaxed select-all" style={{ color: "var(--text-dim)" }} title={ws.path}>{ws.path || "(no path)"}</p>
                {missing && (
                  <button onClick={() => { setRelinkTarget(ws); setRelinkError(""); }} className="w-full rounded px-2 py-1 text-[10px] text-left" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>
                    Relink folder to resume chat and agent work
                  </button>
                )}
                {(sessions[ws.id] ?? []).length > 0 && (
                  <div className="space-y-0.5 pt-0.5" style={{ borderTop: "1px solid var(--border-dim)" }}>
                    {(sessions[ws.id] ?? []).map(s => {
                      const sActive = !!s.is_active;
                      return (
                        <div key={s.id} className="flex items-center gap-1 px-1.5 py-1 rounded text-[10px] group/sess" style={{ background: sActive ? "var(--amber-bg)" : "transparent", border: sActive ? "1px solid var(--amber-dim)" : "1px solid transparent", boxShadow: sActive ? "0 0 5px rgba(245,158,11,0.2)" : "none" }}>
                          {sessionRenaming === s.id ? (
                            <div className="flex items-center gap-1 flex-1 min-w-0">
                              <span style={{ color: "var(--amber)" }}>◆</span>
                              <input autoFocus value={sessionRenameVal} onChange={e => setSessionRenameVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter") confirmSessionRename(ws.id, s.id); if (e.key === "Escape") setSessionRenaming(null); }} onBlur={() => confirmSessionRename(ws.id, s.id)} placeholder="Session name" className="flex-1 min-w-0 text-[11px] px-1 py-0.5 rounded outline-none" style={{ background: "var(--bg-base)", border: "1px solid var(--amber-dim)", color: "var(--text-primary)" }} />
                              <button onClick={e => { e.stopPropagation(); confirmSessionRename(ws.id, s.id); }} className="shrink-0 text-[10px] px-1 rounded" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>✓</button>
                              <button onClick={e => { e.stopPropagation(); setSessionRenaming(null); }} className="shrink-0 text-[10px] px-1 rounded" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>✕</button>
                            </div>
                          ) : (
                            <button onClick={() => !sActive && !switchingTo && switchSession(ws.id, s.id)} onDoubleClick={() => startSessionRename(s)} className="flex items-center gap-1 flex-1 text-left min-w-0" style={{ cursor: sActive ? "default" : switchingTo ? "wait" : "pointer" }} title={sActive ? "Double-click to rename" : "Click to switch"}>
                              {switchingTo === s.id ? (
                                <span style={{ color: "var(--amber)" }}>◌</span>
                              ) : (
                                <span style={{ color: sActive ? "var(--amber)" : "var(--text-dim)" }}>◆</span>
                              )}
                              <span className="flex-1 min-w-0">
                                <span className="block truncate" style={{ color: sActive ? "var(--amber)" : s.name ? "var(--text-secondary)" : "var(--text-dim)" }}>
                                  {switchingTo === s.id ? "Switching..." : (s.name || "New Session")}
                                </span>
                                <SessionRuntimeIndicator status={runtimeStatuses[s.id]} now={runtimeNow} />
                                <ProgressBar
                                  completed={s.progress_completed}
                                  total={s.progress_total}
                                  percent={s.progress_percent}
                                  compact
                                />
                              </span>
                            </button>
                          )}
                          {sessionRenaming !== s.id && (
                            <div className="relative shrink-0" ref={sessionMenuOpen === s.id ? sessionMenuRef : undefined}>
                              <button onClick={e => { e.stopPropagation(); setSessionMenuOpen(sessionMenuOpen === s.id ? null : s.id); }} className="opacity-0 group-hover/sess:opacity-100 text-[11px] px-0.5 rounded transition-opacity leading-none" style={{ color: "var(--text-dim)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>⋯</button>
                              {sessionMenuOpen === s.id && (
                                <div className="absolute right-0 top-full mt-1 z-50 rounded py-1 min-w-[90px]" style={{ background: "var(--bg-raised)", border: "1px solid var(--border)", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
                                  <button onClick={() => startSessionRename(s)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--text-secondary)" }} onMouseEnter={e => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.color = "var(--text-primary)"; }} onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "var(--text-secondary)"; }}>✏ Rename</button>
                                  <button onClick={() => deleteSession(ws.id, s.id)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--red)" }} onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-hover)")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>✕ Delete</button>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
                {isActive && (
                  <button onClick={async () => { const r = await apiPost<{ sessions?: ProjectSession[] }>(`/api/workspaces/${ws.id}/new-session`); if (r.sessions) setSessions(s => ({ ...s, [ws.id]: r.sessions! })); else await loadSessions(ws.id); onActivate("session"); }} className="w-full py-0.5 text-[9px] rounded transition-colors" style={{ background: "var(--bg-base)", color: "var(--text-dim)", border: "1px solid var(--border-dim)" }} onMouseEnter={e => { e.currentTarget.style.color="var(--amber)"; e.currentTarget.style.borderColor="var(--amber-dim)"; }} onMouseLeave={e => { e.currentTarget.style.color="var(--text-dim)"; e.currentTarget.style.borderColor="var(--border-dim)"; }}>+ New Session</button>
                )}
              </div>
            )}
          </div>
        );
      })}

      <button onClick={pickAndAdd} disabled={picking} className="w-full py-1.5 text-[10px] rounded transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50" style={{ background: "var(--bg-panel)", color: "var(--text-dim)", border: "1px solid var(--border-dim)" }} onMouseEnter={e => { e.currentTarget.style.color="var(--amber)"; e.currentTarget.style.borderColor="var(--amber-dim)"; }} onMouseLeave={e => { e.currentTarget.style.color="var(--text-dim)"; e.currentTarget.style.borderColor="var(--border-dim)"; }}>
        <span>{picking ? "…" : "+"}</span><span>{picking ? pickingStage || "Working..." : "Add Project"}</span>
      </button>
      {relinkTarget && (
        <RelinkProjectModal workspace={relinkTarget} error={relinkError} busy={relinking} onCancel={() => { if (!relinking) setRelinkTarget(null); }} onRelink={() => relinkWorkspace(relinkTarget)} />
      )}
      {infoModal && (
        <InfoModal message={infoModal.msg} onOk={infoModal.onOk} />
      )}
      {deleteProjectTarget && (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.72)" }}
          onClick={() => setDeleteProjectTarget(null)}
        >
          <div
            className="w-full max-w-sm rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--red-dim)", boxShadow: "0 12px 32px rgba(0,0,0,0.7)" }}
            onClick={event => event.stopPropagation()}
          >
            <p className="text-[12px] font-semibold mb-2" style={{ color: "var(--red)" }}>Remove project?</p>
            <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-primary)" }}>
              Remove <strong>{deleteProjectTarget.label}</strong> and all of its sessions from Task Hounds?
            </p>
            <p className="mt-2 text-[10px] break-all" style={{ color: "var(--text-dim)" }}>
              {deleteProjectTarget.path}
            </p>
            <p className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
              Project files on disk will not be deleted.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeleteProjectTarget(null)}
                className="px-3 py-1 rounded text-[11px]"
                style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                Cancel
              </button>
              <button
                onClick={() => void remove(deleteProjectTarget.id)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
              >
                Remove Project
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
