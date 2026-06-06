import { useState, useEffect, useRef } from "react";
import { apiGet, apiPost, apiPatch, apiDelete } from "../../../lib/api";
import type { ProjectSession, Workspace } from "./types";
import { RelinkProjectModal } from "./RelinkProjectModal";

interface WorkspacesPanelProps {
  onActivate: (scope?: "workspace" | "session") => void;
  sessionReloadKey?: number;
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
  const [relinking, setRelinking]       = useState(false);
  const [relinkTarget, setRelinkTarget] = useState<Workspace | null>(null);
  const [relinkError, setRelinkError]   = useState("");
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
      let chosen = window.electronAPI?.pickFolder
        ? await window.electronAPI.pickFolder()
        : null;
      type PickFolderResult = { path?: string; cancelled?: boolean; error?: string };
      let r = chosen?.trim()
        ? await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() })
        : await apiPost<PickFolderResult>("/api/pick-folder", {});
      if (!r.path && !r.cancelled) {
        chosen = window.prompt("Project folder path");
        if (!chosen?.trim()) return;
        r = await apiPost<PickFolderResult>("/api/pick-folder", { path: chosen.trim() });
      }
      if (r.path) {
        const path = r.path;
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
    } catch (err) {
      alert(`Add project failed: ${err instanceof Error ? err.message : String(err)}`);
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
    onActivate("session");
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
    const ws = workspaces.find(x => x.id === id);
    setExpanded({ [id]: true });
    setWorkspaces(w => w.map(x => ({ ...x, active: x.id === id })));
    if (r.sessions) setSessions(s => ({ ...s, [id]: r.sessions! }));
    else loadSessions(id);
    onActivate("workspace");
    if (ws?.path_missing) {
      setRelinkTarget({ ...ws, active: true });
      setRelinkError("");
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
      await activateWs(ws.id);
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
  };

  const startRename = (ws: Workspace) => {
    setRenaming(ws.id);
    setRenameVal(ws.label);
    setMenuOpen(null);
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
      <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "var(--text-dim)" }}>Projects</p>

      {workspaces.map(ws => {
        const isActive = !!ws.active;
        const missing = !!ws.path_missing;
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
                  const next = !expanded[ws.id];
                  setExpanded(e => ({ ...e, [ws.id]: next }));
                  if (next && !sessions[ws.id]) loadSessions(ws.id);
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
                  onClick={() => activateWs(ws.id)}
                  className="flex items-center gap-1 flex-1 text-left min-w-0"
                  title={missing ? "Original folder cannot be found. Relink to resume agent work." : ws.path}
                >
                  <span className="text-[10px] shrink-0" style={{ filter: isActive ? "drop-shadow(0 0 4px var(--amber))" : "none" }}>📁</span>
                  <span className="text-[11px] truncate font-medium" style={{ color: isActive ? "var(--amber)" : "var(--text-secondary)" }}>{ws.label}</span>
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
                  className="opacity-0 group-hover:opacity-100 text-[12px] px-1 rounded transition-opacity leading-none"
                  style={{ color: "var(--text-dim)" }}
                  onMouseEnter={e => (e.currentTarget.style.color = "var(--text-primary)")}
                  onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}
                >⋯</button>
                {menuOpen === ws.id && (
                  <div className="absolute right-0 top-full mt-1 z-50 rounded py-1 min-w-[110px]" style={{ background: "var(--bg-raised)", border: "1px solid var(--border)", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}>
                    <button onClick={() => startRename(ws)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--text-secondary)" }} onMouseEnter={e => { e.currentTarget.style.background="var(--bg-hover)"; e.currentTarget.style.color="var(--text-primary)"; }} onMouseLeave={e => { e.currentTarget.style.background="transparent"; e.currentTarget.style.color="var(--text-secondary)"; }}>✏ Rename</button>
                    <button onClick={() => remove(ws.id)} className="w-full text-left px-3 py-1.5 text-[11px] transition-colors" style={{ color: "var(--red)" }} onMouseEnter={e => (e.currentTarget.style.background="var(--bg-hover)")} onMouseLeave={e => (e.currentTarget.style.background="transparent")}>✕ Remove</button>
                  </div>
                )}
              </div>
            </div>

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
                            <button onClick={() => !sActive && switchSession(ws.id, s.id)} onDoubleClick={() => startSessionRename(s)} className="flex items-center gap-1 flex-1 text-left min-w-0" style={{ cursor: sActive ? "default" : "pointer" }} title={sActive ? "Double-click to rename" : "Click to switch"}>
                              <span style={{ color: sActive ? "var(--amber)" : "var(--text-dim)" }}>◆</span>
                              <span className="flex-1 truncate" style={{ color: sActive ? "var(--amber)" : s.name ? "var(--text-secondary)" : "var(--text-dim)" }}>{s.name || "New Session"}</span>
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
        <span>{picking ? "…" : "+"}</span><span>{picking ? "Selecting..." : "Add Project"}</span>
      </button>
      {relinkTarget && (
        <RelinkProjectModal workspace={relinkTarget} error={relinkError} busy={relinking} onCancel={() => { if (!relinking) setRelinkTarget(null); }} onRelink={() => relinkWorkspace(relinkTarget)} />
      )}
    </div>
  );
}
