import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { apiPost, apiPut, apiPatch, apiGet, apiDelete } from "../../lib/api";
import type { Agent, LoopStatus, SessionInfo } from "../../lib/api";


function ConfirmModal({ message, onConfirm, onCancel }: { message: string; onConfirm: () => void; onCancel: () => void }) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.75)" }}>
      <div className="w-72 rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "#1f1f1f", border: "1px solid #ef4444" }}>
        <p className="text-[13px] font-semibold" style={{ color: "#f0ede8" }}>Confirm</p>
        <p className="text-[12px]" style={{ color: "#9ca3af" }}>{message}</p>
        <div className="flex gap-2">
          <button onClick={onConfirm} className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg" style={{ background: "#7f1d1d", border: "1px solid #ef4444", color: "#fca5a5" }}>Yes, reset</button>
          <button onClick={onCancel} className="flex-1 py-1.5 text-[12px] rounded-lg" style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#9ca3af" }}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body
  );
}

function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr);
    const now = Date.now();
    const diff = Math.floor((now - d.getTime()) / 1000);
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return "—";
  }
}

function UndoToast({ message, onUndo, onDismiss }: { message: string; onUndo: () => void; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 5000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return createPortal(
    <div
      className="fixed bottom-6 left-1/2 z-[100] flex items-center gap-3 px-4 py-2 rounded-xl shadow-2xl"
      style={{ background: "#1f1f1f", border: "1px solid #f59e0b", transform: "translateX(-50%)" }}
    >
      <span className="text-[12px]" style={{ color: "#f0ede8" }}>{message}</span>
      <button
        onClick={onUndo}
        className="px-2 py-1 text-[11px] font-semibold rounded"
        style={{ background: "#1c1408", color: "#f59e0b", border: "1px solid #78350f" }}
      >
        Undo
      </button>
      <button onClick={onDismiss} className="text-[14px]" style={{ color: "#4b5563" }}>×</button>
    </div>,
    document.body
  );
}

interface SessionManagerProps {
  onClose: () => void;
  onSessionCountChange: (count: number) => void;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function SessionManager({ onClose, onSessionCountChange }: SessionManagerProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [archived, setArchived] = useState<SessionInfo[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [undo, setUndo] = useState<{ sessionKey: string; session: SessionInfo } | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [liveData, archData] = await Promise.all([
        apiGet<{ live: SessionInfo[]; live_count: number; archived_count: number }>("/api/sessions"),
        apiGet<{ sessions: SessionInfo[] }>("/api/sessions/archived"),
      ]);
      setSessions(liveData.live || []);
      setArchived(archData.sessions || []);
      onSessionCountChange(liveData.live_count || 0);
    } catch {
      setSessions([]);
      setArchived([]);
    }
    setLoading(false);
  }, [onSessionCountChange]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  const filteredSessions = (showArchived ? archived : sessions).filter(s =>
    s.session_name.toLowerCase().includes(search.toLowerCase()) ||
    (s.agent_name && s.agent_name.toLowerCase().includes(search.toLowerCase()))
  );

  const toggleSelect = (key: string) => {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelected(next);
  };

  const toggleSelectAll = () => {
    if (selected.size === filteredSessions.length) setSelected(new Set());
    else setSelected(new Set(filteredSessions.map(s => s.session_key)));
  };

  const archiveSession = async (session: SessionInfo) => {
    try {
      await apiDelete(`/api/sessions/archive/${session.session_key}`);
      const newCount = sessions.length - 1;
      setSessions(prev => prev.filter(s => s.session_key !== session.session_key));
      setArchived(prev => [{ ...session, archived: true }, ...prev]);
      setUndo({ sessionKey: session.session_key, session });
      onSessionCountChange(newCount);
    } catch { /* ignore */ }
  };

const restoreSession = async (sessionKey: string) => {
    try {
      await apiPut(`/api/sessions/archive/${sessionKey}`, {});
      setArchived(prev => prev.filter(s => s.session_key !== sessionKey));
      const restored = archived.find(s => s.session_key === sessionKey);
      if (restored) {
        const newCount = sessions.length + 1;
        setSessions(prev => [...prev, { ...restored, archived: false }]);
        onSessionCountChange(newCount);
      }
    } catch { /* ignore */ }
  };

  const undoRestore = async () => {
    if (!undo) return;
    await restoreSession(undo.sessionKey);
    setUndo(null);
  };

  const bulkArchive = async () => {
    const toArchive = filteredSessions.filter(s => selected.has(s.session_key));
    for (const session of toArchive) {
      await archiveSession(session);
    }
    setSelected(new Set());
  };

  const columns = [
    { key: "name", label: "Name", width: "flex-1" },
    { key: "created", label: "Created", width: "w-16" },
    { key: "lastActive", label: "Last Active", width: "w-16" },
    { key: "workerStatus", label: "Status", width: "w-14" },
    { key: "tokenUsage", label: "Tokens", width: "w-14" },
  ];

  const displaySessions = filteredSessions;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(0,0,0,0.80)" }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-3xl max-h-[80vh] flex flex-col rounded-xl shadow-2xl" style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}>
        <div className="flex items-center justify-between px-4 py-3 shrink-0" style={{ borderBottom: "1px solid #2a2a2a" }}>
          <span className="text-[13px] font-semibold" style={{ color: "#f59e0b" }}>Manage Sessions</span>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 text-[10px]" style={{ color: "#6b7280" }}>
              <button onClick={() => setShowArchived(false)} className="px-2 py-1 rounded" style={{ background: !showArchived ? "#1c1408" : "#181818", color: !showArchived ? "#f59e0b" : "#6b7280" }}>Live ({sessions.length})</button>
              <button onClick={() => setShowArchived(true)} className="px-2 py-1 rounded" style={{ background: showArchived ? "#1c1408" : "#181818", color: showArchived ? "#f59e0b" : "#6b7280" }}>Archived ({archived.length})</button>
            </div>
            <button onClick={onClose} className="text-lg leading-none" style={{ color: "#4b5563" }}>×</button>
          </div>
        </div>

        <div className="flex items-center gap-2 px-4 py-2 shrink-0" style={{ borderBottom: "1px solid #1f1f1f" }}>
          <input
            className="flex-1 rounded px-2 py-1.5 text-[11px] outline-none"
            style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#f0ede8" }}
            placeholder="Search sessions..."
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {selected.size > 0 && (
            <button
              onClick={bulkArchive}
              className="px-3 py-1.5 text-[11px] font-semibold rounded"
              style={{ background: "#7f1d1d", color: "#fca5a5", border: "1px solid #ef4444" }}
            >
              Delete ({selected.size})
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="p-8 text-center text-[12px]" style={{ color: "#4b5563" }}>Loading...</div>
          ) : displaySessions.length === 0 ? (
            <div className="p-8 text-center text-[12px]" style={{ color: "#4b5563" }}>No sessions found</div>
          ) : (
            <table className="w-full text-[11px]">
              <thead>
                <tr style={{ background: "#0d0d0d", borderBottom: "1px solid #1f1f1f" }}>
                  <th className="w-8 px-2 py-1.5 text-left">
                    <input
                      type="checkbox"
                      checked={selected.size === displaySessions.length && displaySessions.length > 0}
                      onChange={toggleSelectAll}
                      className="accent-amber-500"
                    />
                  </th>
                  {columns.map(col => (
                    <th key={col.key} className={`px-2 py-1.5 text-left font-medium ${col.width}`} style={{ color: "#4b5563" }}>{col.label}</th>
                  ))}
                  <th className="w-16 px-2 py-1.5 text-right" style={{ color: "#4b5563" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {displaySessions.map(session => (
                  <tr
                    key={session.session_key}
                    className="border-b transition-colors"
                    style={{ borderColor: "#1f1f1f", background: selected.has(session.session_key) ? "#1c1408" : "transparent" }}
                    onMouseEnter={e => { if (!selected.has(session.session_key)) (e.currentTarget as HTMLElement).style.background = "#181818"; }}
                    onMouseLeave={e => { if (!selected.has(session.session_key)) (e.currentTarget as HTMLElement).style.background = "transparent"; }}
                  >
                    <td className="px-2 py-2">
                      <input
                        type="checkbox"
                        checked={selected.has(session.session_key)}
                        onChange={() => toggleSelect(session.session_key)}
                        className="accent-amber-500"
                      />
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex flex-col">
                        <span style={{ color: "#f0ede8" }}>{session.session_name}</span>
                        {session.folder_relation && (
                          <span className="text-[9px] truncate max-w-[120px]" style={{ color: "#4b5563" }} title={session.folder_relation}>
                            📁 {session.folder_relation}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-2 py-2" style={{ color: "#6b7280" }}>{formatTime(session.created_at)}</td>
                    <td className="px-2 py-2" style={{ color: "#6b7280" }}>{formatTime(session.last_active_at)}</td>
                    <td className="px-2 py-2">
                      <span className="px-1.5 py-0.5 rounded-full text-[10px]" style={{
                        background: session.worker_status === "busy" ? "#1c1408" : "#181818",
                        color: session.worker_status === "busy" ? "#f59e0b" : "#6b7280",
                      }}>
                        {session.worker_status || "—"}
                      </span>
                    </td>
                    <td className="px-2 py-2 font-mono" style={{ color: "#6b7280" }}>
                      {session.token_usage > 0 ? session.token_usage.toLocaleString() : "—"}
                    </td>
                    <td className="px-2 py-2 text-right">
                      {showArchived ? (
                        <button
                          onClick={() => restoreSession(session.session_key)}
                          className="px-2 py-1 text-[10px] rounded"
                          style={{ background: "#0a1f0f", color: "#4ade80", border: "1px solid #14532d" }}
                        >
                          Restore
                        </button>
                      ) : (
                        <button
                          onClick={() => archiveSession(session)}
                          className="px-2 py-1 text-[10px] rounded"
                          style={{ background: "#181818", color: "#ef4444", border: "1px solid #7f1d1d" }}
                        >
                          Archive
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="px-4 py-2 shrink-0 flex justify-between" style={{ borderTop: "1px solid #1f1f1f" }}>
          <span className="text-[10px]" style={{ color: "#4b5563" }}>{displaySessions.length} session{displaySessions.length !== 1 ? "s" : ""}</span>
          <span className="text-[10px]" style={{ color: "#4b5563" }}>{selected.size} selected</span>
        </div>
      </div>

      {undo && (
        <UndoToast
          message={`Archived "${undo.session.session_name}"`}
          onUndo={undoRestore}
          onDismiss={() => setUndo(null)}
        />
      )}
    </div>
  );
}

interface Workspace { id: string; path: string; label: string; active?: boolean }
interface ProjectSession { id: string; workspace_id: string; name: string; is_active: number; created_at: string }

interface WorkspacesPanelProps { onActivate: () => void; sessionReloadKey?: number }

function WorkspacesPanel({ onActivate, sessionReloadKey }: WorkspacesPanelProps) {
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
  const menuRef        = useRef<HTMLDivElement>(null);
  const sessionMenuRef = useRef<HTMLDivElement>(null);

  const loadSessions = async (wsId: string) => {
    try {
      const data = await apiGet<ProjectSession[]>(`/api/workspaces/${wsId}/sessions`);
      setSessions(s => ({ ...s, [wsId]: data }));
    } catch { /* ignore */ }
  };

  const load = () =>
    apiGet<Workspace[]>("/api/workspaces").then(data => {
      setWorkspaces(data);
      const active = data.find(w => w.active);
      if (active) {
        setExpanded(e => ({ ...e, [active.id]: true }));
        loadSessions(active.id);
      }
    }).catch(() => {});

  useEffect(() => { load(); }, []);

  // Reload active workspace sessions when parent signals a new session was created
  useEffect(() => {
    if (!sessionReloadKey) return;
    apiGet<Workspace[]>("/api/workspaces").then(data => {
      setWorkspaces(data);
      const active = data.find(w => w.active);
      if (active) {
        setExpanded(e => ({ ...e, [active.id]: true }));
        loadSessions(active.id);
      }
    }).catch(() => {});
  }, [sessionReloadKey]);

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
    try {
      const chosen = window.electronAPI?.pickFolder
        ? await window.electronAPI.pickFolder()
        : window.prompt("Project folder path");
      if (!chosen?.trim()) return;
      const r = await apiPost<{ path: string }>("/api/pick-folder", { path: chosen.trim() });
      if (r.path) {
        const path = r.path;
        // Duplicate check
        const norm = (p: string) => p.replace(/[\\/]+$/, "").toLowerCase();
        if (workspaces.some(w => norm(w.path) === norm(path))) {
          alert(`"${path}" is already in your projects.`);
          return;
        }
        const label = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
        const entry = await apiPost<Workspace>("/api/workspaces", { path, label });
        setWorkspaces(w => [...w, entry]);
        await activateWs(entry.id);
      }
    } finally {
      setPicking(false);
    }
  };

  const switchSession = async (wsId: string, sessionId: string) => {
    await apiPost(`/api/project-sessions/${sessionId}/switch`);
    setSessions(s => ({
      ...s,
      [wsId]: (s[wsId] ?? []).map(x => ({ ...x, is_active: x.id === sessionId ? 1 : 0 })),
    }));
    onActivate();
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

  const activateWs = async (id: string) => {
    const r = await apiPost<{ sessions?: ProjectSession[] }>(`/api/workspaces/${id}/activate`);
    setExpanded({ [id]: true });
    setWorkspaces(w => w.map(x => ({ ...x, active: x.id === id })));
    if (r.sessions) setSessions(s => ({ ...s, [id]: r.sessions! }));
    else loadSessions(id);
    onActivate();
  };

  const remove = async (id: string) => {
    await apiDelete(`/api/workspaces/${id}`);
    setWorkspaces(w => w.filter(x => x.id !== id));
    setSessions(s => { const next = { ...s }; delete next[id]; return next; });
    setMenuOpen(null);
  };

  const startRename = (ws: Workspace) => {
    setRenaming(ws.id);
    setRenameVal(ws.label);
    setMenuOpen(null);
  };

  const confirmRename = async (id: string) => {
    const val = renameVal.trim();
    if (!val) { setRenaming(null); return; }
    await apiPut(`/api/workspaces/${id}`, { label: val });
    setWorkspaces(w => w.map(x => x.id === id ? { ...x, label: val } : x));
    setRenaming(null);
  };

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const toggle = (id: string) => setExpanded(e => ({ ...e, [id]: !e[id] }));

  return (
    <div className="p-3 space-y-1">
      <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "#4b5563" }}>Projects</p>

      {workspaces.map(ws => {
        const isActive = !!ws.active;
        return (
          <div key={ws.id} className="space-y-0.5">
            {/* Row */}
            <div
              className="flex items-center gap-1 rounded px-1.5 py-1 group transition-all duration-200"
              style={{
                background: isActive ? "#1c1408" : "#181818",
                border: isActive ? "1px solid #f59e0b" : "1px solid #1f1f1f",
                boxShadow: isActive ? "0 0 8px rgba(245,158,11,0.35)" : "none",
              }}
            >
              {/* Expand toggle */}
              <button
                onClick={() => {
                  if (renaming) return;
                  const next = !expanded[ws.id];
                  setExpanded(e => ({ ...e, [ws.id]: next }));
                  if (next && !sessions[ws.id]) loadSessions(ws.id);
                }}
                className="shrink-0 px-0.5"
                style={{ color: isActive ? "#f59e0b" : "#4b5563" }}
              >
                <span className="text-[10px]">{expanded[ws.id] ? "▼" : "▶"}</span>
              </button>

              {/* 📁 + name — click to activate */}
              {renaming === ws.id ? (
                <div className="flex items-center gap-1 flex-1 min-w-0" onClick={e => e.stopPropagation()}>
                  <input
                    className="flex-1 rounded px-1 py-0 text-[11px] outline-none min-w-0"
                    style={{ background: "#111", border: "1px solid #f59e0b", color: "#f0ede8" }}
                    value={renameVal}
                    onChange={e => setRenameVal(e.target.value)}
                    onKeyDown={e => {
                      e.stopPropagation();
                      if (e.key === "Enter") confirmRename(ws.id);
                      if (e.key === "Escape") setRenaming(null);
                    }}
                    autoFocus
                  />
                  <button onClick={e => { e.stopPropagation(); confirmRename(ws.id); }} className="shrink-0 text-[11px] px-1 rounded" style={{ background: "#1c1408", color: "#f59e0b", border: "1px solid #78350f" }}>✓</button>
                  <button onClick={e => { e.stopPropagation(); setRenaming(null); }} className="shrink-0 text-[11px] px-1 rounded" style={{ color: "#6b7280" }}>✕</button>
                </div>
              ) : (
                <button
                  onClick={() => activateWs(ws.id)}
                  className="flex items-center gap-1 flex-1 text-left min-w-0"
                >
                  <span className="text-[10px] shrink-0" style={{ filter: isActive ? "drop-shadow(0 0 4px #f59e0b)" : "none" }}>📁</span>
                  <span className="text-[11px] truncate font-medium" style={{ color: isActive ? "#f59e0b" : "#9ca3af" }}>{ws.label}</span>
                </button>
              )}

              {/* ⋯ menu */}
              <div className="relative" ref={menuOpen === ws.id ? menuRef : undefined}>
                <button
                  onClick={e => { e.stopPropagation(); setMenuOpen(menuOpen === ws.id ? null : ws.id); }}
                  className="opacity-0 group-hover:opacity-100 text-[12px] px-1 rounded transition-opacity leading-none"
                  style={{ color: "#4b5563" }}
                  onMouseEnter={e => (e.currentTarget.style.color = "#f0ede8")}
                  onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
                >⋯</button>
                {menuOpen === ws.id && (
                  <div className="absolute right-0 top-full mt-1 z-50 rounded py-1 min-w-[110px]" style={{ background: "#1f1f1f", border: "1px solid #2a2a2a", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
                    <button onClick={() => startRename(ws)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "#9ca3af" }} onMouseEnter={e => { e.currentTarget.style.background="#2a2a2a"; e.currentTarget.style.color="#f0ede8"; }} onMouseLeave={e => { e.currentTarget.style.background="transparent"; e.currentTarget.style.color="#9ca3af"; }}>✏ Rename</button>
                    <button onClick={() => remove(ws.id)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "#ef4444" }} onMouseEnter={e => (e.currentTarget.style.background="#2a2a2a")} onMouseLeave={e => (e.currentTarget.style.background="transparent")}>✕ Remove</button>
                  </div>
                )}
              </div>
            </div>

            {/* Expanded: path + sessions */}
            {expanded[ws.id] && (
              <div
                className="ml-2 rounded px-2 py-1.5 space-y-1.5 transition-all duration-200"
                style={{
                  background: "#0d0d0d",
                  border: isActive ? "1px solid #78350f" : "1px solid #1f1f1f",
                  boxShadow: isActive ? "0 0 6px rgba(245,158,11,0.15)" : "none",
                }}
              >
                {/* Full path */}
                <p className="text-[9px] break-all leading-relaxed select-all" style={{ color: "#374151" }} title={ws.path}>
                  {ws.path || "(no path)"}
                </p>
                {/* Sessions */}
                {(sessions[ws.id] ?? []).length > 0 && (
                  <div className="space-y-0.5 pt-0.5" style={{ borderTop: "1px solid #1f1f1f" }}>
                    {(sessions[ws.id] ?? []).map(s => {
                      const sActive = !!s.is_active;
                      return (
                        <div
                          key={s.id}
                          className="flex items-center gap-1 px-1.5 py-1 rounded text-[10px] group/sess"
                          style={{
                            background: sActive ? "#1c1408" : "transparent",
                            border: sActive ? "1px solid #78350f" : "1px solid transparent",
                            boxShadow: sActive ? "0 0 5px rgba(245,158,11,0.2)" : "none",
                          }}
                        >
                          {sessionRenaming === s.id ? (
                            <div className="flex items-center gap-1 flex-1 min-w-0">
                              <span style={{ color: "#f59e0b" }}>◆</span>
                              <input
                                autoFocus
                                value={sessionRenameVal}
                                onChange={e => setSessionRenameVal(e.target.value)}
                                onKeyDown={e => {
                                  if (e.key === "Enter")  confirmSessionRename(ws.id, s.id);
                                  if (e.key === "Escape") setSessionRenaming(null);
                                }}
                                onBlur={() => confirmSessionRename(ws.id, s.id)}
                                placeholder="Session name"
                                className="flex-1 min-w-0 text-[11px] px-1 py-0.5 rounded outline-none"
                                style={{ background: "#0d0d0d", border: "1px solid #78350f", color: "#f0ede8" }}
                              />
                              <button
                                onClick={e => { e.stopPropagation(); confirmSessionRename(ws.id, s.id); }}
                                className="shrink-0 text-[10px] px-1 rounded"
                                style={{ background: "#1c1408", color: "#f59e0b", border: "1px solid #78350f" }}
                              >✓</button>
                              <button
                                onClick={e => { e.stopPropagation(); setSessionRenaming(null); }}
                                className="shrink-0 text-[10px] px-1 rounded"
                                style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
                              >✕</button>
                            </div>
                          ) : (
                            <button
                              onClick={() => !sActive && switchSession(ws.id, s.id)}
                              onDoubleClick={() => startSessionRename(s)}
                              className="flex items-center gap-1 flex-1 text-left min-w-0"
                              style={{ cursor: sActive ? "default" : "pointer" }}
                              title={sActive ? "Double-click to rename" : "Click to switch"}
                            >
                              <span style={{ color: sActive ? "#f59e0b" : "#374151" }}>◆</span>
                              <span className="flex-1 truncate" style={{ color: sActive ? "#f59e0b" : s.name ? "#6b7280" : "#374151" }}>
                                {s.name || "New Session"}
                              </span>
                            </button>
                          )}
                          {/* Session ⋯ menu */}
                          {sessionRenaming !== s.id && (
                          <div className="relative shrink-0" ref={sessionMenuOpen === s.id ? sessionMenuRef : undefined}>
                            <button
                              onClick={e => { e.stopPropagation(); setSessionMenuOpen(sessionMenuOpen === s.id ? null : s.id); }}
                              className="opacity-0 group-hover/sess:opacity-100 text-[11px] px-0.5 rounded transition-opacity leading-none"
                              style={{ color: "#4b5563" }}
                              onMouseEnter={e => (e.currentTarget.style.color = "#f0ede8")}
                              onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
                            >⋯</button>
                            {sessionMenuOpen === s.id && (
                              <div
                                className="absolute right-0 top-full mt-1 z-50 rounded py-1 min-w-[90px]"
                                style={{ background: "#1f1f1f", border: "1px solid #2a2a2a", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}
                              >
                                <button
                                  onClick={() => startSessionRename(s)}
                                  className="w-full text-left px-3 py-1.5 text-[11px] transition-colors"
                                  style={{ color: "#9ca3af" }}
                                  onMouseEnter={e => { e.currentTarget.style.background = "#2a2a2a"; e.currentTarget.style.color = "#f0ede8"; }}
                                  onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "#9ca3af"; }}
                                >✏ Rename</button>
                                <button
                                  onClick={() => deleteSession(ws.id, s.id)}
                                  className="w-full text-left px-3 py-1.5 text-[11px] transition-colors"
                                  style={{ color: "#ef4444" }}
                                  onMouseEnter={e => (e.currentTarget.style.background = "#2a2a2a")}
                                  onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                                >✕ Delete</button>
                              </div>
                            )}
                          </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
                {/* New session button */}
                {isActive && (
                  <button
                    onClick={async () => {
                      const r = await apiPost<{ sessions?: ProjectSession[] }>(`/api/workspaces/${ws.id}/new-session`);
                      if (r.sessions) {
                        setSessions(s => ({ ...s, [ws.id]: r.sessions! }));
                      } else {
                        await loadSessions(ws.id);
                      }
                      onActivate();
                    }}
                    className="w-full py-0.5 text-[9px] rounded transition-colors"
                    style={{ background: "#111", color: "#374151", border: "1px solid #1f1f1f" }}
                    onMouseEnter={e => { e.currentTarget.style.color="#f59e0b"; e.currentTarget.style.borderColor="#78350f"; }}
                    onMouseLeave={e => { e.currentTarget.style.color="#374151"; e.currentTarget.style.borderColor="#1f1f1f"; }}
                  >+ New Session</button>
                )}
              </div>
            )}
          </div>
        );
      })}

      <button
        onClick={pickAndAdd}
        disabled={picking}
        className="w-full py-1.5 text-[10px] rounded transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
        style={{ background: "#181818", color: "#4b5563", border: "1px solid #1f1f1f" }}
        onMouseEnter={e => { e.currentTarget.style.color="#f59e0b"; e.currentTarget.style.borderColor="#78350f"; }}
        onMouseLeave={e => { e.currentTarget.style.color="#4b5563"; e.currentTarget.style.borderColor="#1f1f1f"; }}
      >
        <span>{picking ? "…" : "+"}</span><span>{picking ? "Selecting..." : "Add Project"}</span>
      </button>
    </div>
  );
}

interface Props {
  agents: Agent[];
  activeAgent: string;
  onSelectAgent: (name: string) => void;
  loopStatus: LoopStatus;
  onLoopChange: () => void;
  onRunOnce: () => void;
  onReset: () => void;
  sessionReloadKey?: number;
}

export function LeftRail({ loopStatus, onLoopChange, onRunOnce, onReset, sessionReloadKey }: Props) {
  const [confirmReset, setConfirmReset] = useState(false);
  const [clearDataConfirm, setClearDataConfirm] = useState(false);

  const clearData = async () => {
    // Clear all stream files and reset worker state without DB wipe
    await Promise.allSettled([
      apiPost("/api/stream/manager/clear"),
      apiPost("/api/stream/worker/clear"),
      apiPost("/api/stream/reviewer/clear"),
    ]);
    setClearDataConfirm(false);
    onLoopChange();
  };

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const toggleLoop = async () => {
    if (loopStatus.running) await apiPost("/api/loop/stop");
    else                    await apiPost("/api/loop/start");
    onLoopChange();
  };

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const runOnce = async () => {
    await apiPost("/api/run-cycle");
    onRunOnce();
  };

  return (
    <aside className="w-48 shrink-0 flex flex-col" style={{ background: "#111111", borderRight: "1px solid #2a2a2a" }}>

      {/* ── Projects (fills entire rail) ── */}
      <div className="flex-1 overflow-y-auto">
        <WorkspacesPanel onActivate={onLoopChange} sessionReloadKey={sessionReloadKey} />
      </div>

      {confirmReset && (
        <ConfirmModal
          message="This will clear all agent sessions and reset state. Continue?"
          onConfirm={() => { setConfirmReset(false); onReset(); }}
          onCancel={() => setConfirmReset(false)}
        />
      )}

      {clearDataConfirm && (
        <ConfirmModal
          message="Clear all stream output and reset display? DB records are kept."
          onConfirm={clearData}
          onCancel={() => setClearDataConfirm(false)}
        />
      )}

    </aside>
  );
}
