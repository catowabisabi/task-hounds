import { useState, useEffect, useCallback } from "react";
import { apiGet, apiDelete, apiPut } from "../../../lib/api";
import type { SessionInfo } from "../../../lib/api";
import { UndoToast } from "./UndoToast";
import { formatTime } from "./formatTime";

interface SessionTableProps {
  onSessionCountChange: (count: number) => void;
}

export function SessionTable({ onSessionCountChange }: SessionTableProps) {
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

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => { cancelled = true; };
  }, [load]);

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

  return (
    <>
      <div className="flex items-center justify-between px-4 py-3 shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <span className="text-[13px] font-semibold" style={{ color: "var(--amber)" }}>Manage Sessions</span>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-[10px]" style={{ color: "var(--text-secondary)" }}>
            <button onClick={() => setShowArchived(false)} className="px-2 py-1 rounded" style={{ background: !showArchived ? "var(--amber-bg)" : "var(--bg-panel)", color: !showArchived ? "var(--amber)" : "var(--text-secondary)" }}>Live ({sessions.length})</button>
            <button onClick={() => setShowArchived(true)} className="px-2 py-1 rounded" style={{ background: showArchived ? "var(--amber-bg)" : "var(--bg-panel)", color: showArchived ? "var(--amber)" : "var(--text-secondary)" }}>Archived ({archived.length})</button>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 px-4 py-2 shrink-0" style={{ borderBottom: "1px solid var(--border-dim)" }}>
        <input
          className="flex-1 rounded px-2 py-1.5 text-[11px] outline-none"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          placeholder="Search sessions..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        {selected.size > 0 && (
          <button
            onClick={bulkArchive}
            className="px-3 py-1.5 text-[11px] font-semibold rounded"
            style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
          >
            Delete ({selected.size})
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-8 text-center text-[12px]" style={{ color: "var(--text-dim)" }}>Loading...</div>
        ) : filteredSessions.length === 0 ? (
          <div className="p-8 text-center text-[12px]" style={{ color: "var(--text-dim)" }}>No sessions found</div>
        ) : (
          <table className="w-full text-[11px]">
            <thead>
              <tr style={{ background: "var(--bg-base)", borderBottom: "1px solid var(--border-dim)" }}>
                <th className="w-8 px-2 py-1.5 text-left">
                  <input
                    type="checkbox"
                    checked={selected.size === filteredSessions.length && filteredSessions.length > 0}
                    onChange={toggleSelectAll}
                    className="accent-amber-500"
                  />
                </th>
                {columns.map(col => (
                  <th key={col.key} className={`px-2 py-1.5 text-left font-medium ${col.width}`} style={{ color: "var(--text-dim)" }}>{col.label}</th>
                ))}
                <th className="w-16 px-2 py-1.5 text-right" style={{ color: "var(--text-dim)" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredSessions.map(session => (
                <tr
                  key={session.session_key}
                  className="border-b transition-colors"
                  style={{ borderColor: "var(--border-dim)", background: selected.has(session.session_key) ? "var(--amber-bg)" : "transparent" }}
                  onMouseEnter={e => { if (!selected.has(session.session_key)) (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)"; }}
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
                      <span style={{ color: "var(--text-primary)" }}>{session.session_name}</span>
                      {session.folder_relation && (
                        <span className="text-[9px] truncate max-w-[120px]" style={{ color: "var(--text-dim)" }} title={session.folder_relation}>
                          📁 {session.folder_relation}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-2" style={{ color: "var(--text-secondary)" }}>{formatTime(session.created_at)}</td>
                  <td className="px-2 py-2" style={{ color: "var(--text-secondary)" }}>{formatTime(session.last_active_at)}</td>
                  <td className="px-2 py-2">
                    <span className="px-1.5 py-0.5 rounded-full text-[10px]" style={{
                      background: session.worker_status === "busy" ? "var(--amber-bg)" : "var(--bg-panel)",
                      color: session.worker_status === "busy" ? "var(--amber)" : "var(--text-secondary)",
                    }}>
                      {session.worker_status || "—"}
                    </span>
                  </td>
                  <td className="px-2 py-2 font-mono" style={{ color: "var(--text-secondary)" }}>
                    {session.token_usage > 0 ? session.token_usage.toLocaleString() : "—"}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {showArchived ? (
                      <button
                        onClick={() => restoreSession(session.session_key)}
                        className="px-2 py-1 text-[10px] rounded"
                        style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
                      >
                        Restore
                      </button>
                    ) : (
                      <button
                        onClick={() => archiveSession(session)}
                        className="px-2 py-1 text-[10px] rounded"
                        style={{ background: "var(--bg-panel)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
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

      <div className="px-4 py-2 shrink-0 flex justify-between" style={{ borderTop: "1px solid var(--border-dim)" }}>
        <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>{filteredSessions.length} session{filteredSessions.length !== 1 ? "s" : ""}</span>
        <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>{selected.size} selected</span>
      </div>

      {undo && (
        <UndoToast
          message={`Archived "${undo.session.session_name}"`}
          onUndo={undoRestore}
          onDismiss={() => setUndo(null)}
        />
      )}
    </>
  );
}
