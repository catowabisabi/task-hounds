import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost, apiPut } from "../../lib/api";
import type { Agent } from "../../lib/api";
import { StateBadge } from "../ui/Badge";
import { StreamView } from "../ui/StreamView";
import { useStream } from "../../hooks/useStream";

// ── Agent config modal (gear icon per tab) ────────────────────────────────────
function AgentConfig({ agent, onSave }: { agent: Agent; onSave: () => void }) {
  const [open, setOpen]       = useState(false);
  const [port, setPort]       = useState(String(agent.port));
  const [backend, setBackend] = useState(agent.backend_type);
  const [model, setModel]     = useState(agent.model ?? "");
  const [ocAgent, setOcAgent] = useState(agent.opencode_agent ?? "");
  const [health, setHealth]   = useState<string | null>(null);
  const [saving, setSaving]   = useState(false);

  useEffect(() => {
    setPort(String(agent.port));
    setBackend(agent.backend_type);
    setModel(agent.model ?? "");
    setOcAgent(agent.opencode_agent ?? "");
    setHealth(null);
  }, [agent.name, agent.port, agent.backend_type, agent.model, agent.opencode_agent]);

  const save = async () => {
    setSaving(true);
    await apiPut(`/api/agents/${agent.name}`, {
      port: parseInt(port) || agent.port,
      backend_type: backend,
      model: model || null,
      opencode_agent: ocAgent || null,
    });
    setSaving(false);
    onSave();
    setOpen(false);
  };

  const applyHealth = async () => {
    setHealth("checking...");
    try {
      await apiPut(`/api/agents/${agent.name}`, {
        port: parseInt(port) || agent.port,
        backend_type: backend,
        model: model || null,
        opencode_agent: ocAgent || null,
      });
      const r = await apiPost<{ ok: boolean; status?: string; error?: { message: string } }>(
        `/api/agents/${agent.name}/health`
      );
      setHealth(r.ok ? `ok — ${r.status ?? "healthy"}` : `fail — ${r.error?.message ?? "unhealthy"}`);
    } catch {
      setHealth("unreachable");
    }
  };

  const apply = async () => {
    setSaving(true);
    await apiPut(`/api/agents/${agent.name}`, {
      port: parseInt(port) || agent.port,
      backend_type: backend,
      model: model || null,
      opencode_agent: ocAgent || null,
    });
    setSaving(false);
    onSave();
  };

  const checkHealth = async () => {
    setHealth("checking...");
    try {
      const r = await apiPost<{ ok: boolean; status?: string; output?: { status?: string }; error?: { message: string } | string }>(
        `/api/agents/${agent.name}/health`,
        {
          port: parseInt(port) || agent.port,
          backend_type: backend,
          model: model || null,
          opencode_agent: ocAgent || null,
        }
      );
      const msg = typeof r.error === "string" ? r.error : r.error?.message;
      setHealth(r.ok ? `ok - ${r.status ?? r.output?.status ?? "healthy"}` : `fail - ${msg ?? "unhealthy"}`);
    } catch (err) {
      setHealth(err instanceof Error ? err.message : "unreachable");
    }
  };

  const modal = open ? createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.75)" }}
      onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}
    >
      <div className="w-80 rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "#1f1f1f", border: "1px solid #2a2a2a" }}>
        <div className="flex items-center justify-between">
          <p className="text-[13px] font-semibold text-[#f0ede8]">
            <span style={{ color: "#f59e0b" }}>⚙</span> {agent.name}
          </p>
          <button onClick={() => setOpen(false)} className="text-[#4b5563] hover:text-[#9ca3af] text-lg leading-none">×</button>
        </div>
        {[
          { label: "Backend", node: (
            <select className="w-full rounded px-2 py-1.5 text-[12px] outline-none" style={{ background: "#111", border: "1px solid #2a2a2a", color: "#f0ede8" }} value={backend} onChange={e => setBackend(e.target.value)}>
              <option value="opencode">opencode</option>
              <option value="hermes">hermes</option>
            </select>
          )},
          { label: "Model", node: (
            <div className="relative">
              <input className="w-full rounded px-2 py-1.5 text-[12px] outline-none" style={{ background: "#111", border: "1px solid #2a2a2a", color: "#f0ede8" }} value={model} onChange={e => setModel(e.target.value)} placeholder="<provider-id>/<model-id>" />
              <a href="https://opencode.ai/docs/models/" target="_blank" rel="noopener noreferrer" className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] underline" style={{ color: "#60a5fa" }}>docs</a>
            </div>
          )},
          { label: "Opencode Agent", node: (
            <input className="w-full rounded px-2 py-1.5 text-[12px] outline-none" style={{ background: "#111", border: "1px solid #2a2a2a", color: "#f0ede8" }} value={ocAgent} onChange={e => setOcAgent(e.target.value)} placeholder="e.g. coding" />
          )},
          { label: "Port", node: (
            <input className="w-full rounded px-2 py-1.5 text-[12px] outline-none" style={{ background: "#111", border: "1px solid #2a2a2a", color: "#f0ede8" }} value={port} onChange={e => setPort(e.target.value)} />
          )},
        ].map(({ label, node }) => (
          <div key={label} className="space-y-1">
            <label className="text-[10px] uppercase tracking-wide" style={{ color: "#4b5563" }}>{label}</label>
            {node}
          </div>
        ))}
        <div className="space-y-1">
          <button onClick={checkHealth} className="w-full py-1.5 text-[11px] rounded" style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#9ca3af" }}>Check Health</button>
          {health && <p className="text-[11px] text-center" style={{ color: health.startsWith("ok") ? "#22c55e" : health === "checking..." ? "#9ca3af" : "#ef4444" }}>{health}</p>}
        </div>
        <div className="flex gap-2">
          <button onClick={apply} disabled={saving} className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg disabled:opacity-50" style={{ background: "#181818", color: "#9ca3af", border: "1px solid #2a2a2a" }}>
            Apply
          </button>
          <button onClick={save} disabled={saving} className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg disabled:opacity-50" style={{ background: "#f59e0b", color: "#111" }}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button onClick={() => setOpen(false)} className="px-3 py-1.5 text-[12px] rounded-lg" style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#9ca3af" }}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body
  ) : null;

  return (
    <>
      <button
        onClick={e => { e.stopPropagation(); setOpen(true); }}
        className="text-[11px] px-1 py-0.5 rounded transition-colors"
        style={{ color: "#4b5563" }}
        title={`Configure ${agent.name}`}
        onMouseEnter={e => (e.currentTarget.style.color = "#f59e0b")}
        onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
      >⚙</button>
      {modal}
    </>
  );
}

// ── Timer ─────────────────────────────────────────────────────────────────────
function TimerDisplay({ agentName }: { agentName: string }) {
  const [text, setText] = useState("");
  useEffect(() => {
    const load = () =>
      apiGet<{ content: string }>(`/api/timer/${agentName}`)
        .then(d => setText(d.content))
        .catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [agentName]);
  if (!text || text === "0m 0s") return null;
  return <span className="text-[11px]" style={{ color: "#60a5fa" }}>next: {text}</span>;
}

// ── Single agent stream panel ─────────────────────────────────────────────────
function AgentStream({ agent, onClear, onStop }: {
  agent: Agent;
  onClear: () => void;
  onStop: () => void;
}) {
  const content = useStream(agent.name);

  const handleClear = async () => {
    await apiPost(`/api/stream/${agent.name}/clear`);
    onClear();
  };

  const handleStop = async () => {
    await apiPost(`/api/agents/${agent.name}/kill`);
    await apiPost(`/api/stream/${agent.name}/clear`);
    onStop();
  };

  const handleRestart = async () => {
    await apiPost(`/api/worker/restart`);
    onStop();
  };

  const isCrashed = agent.state === "error";
  const isBusy    = agent.state === "busy";

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Sub-header */}
      <div
        className="flex items-center gap-2 px-4 py-2 shrink-0 flex-wrap"
        style={{ background: "#181818", borderBottom: "1px solid #2a2a2a" }}
      >
        <StateBadge state={agent.state as "idle"|"busy"|"waiting"|"error"|"offline"} />
        {isBusy && (
          <span className="text-[11px] animate-pulse" style={{ color: "#f59e0b" }}>working...</span>
        )}
        {isCrashed && (
          <span className="text-[11px]" style={{ color: "#ef4444" }}>⚠ crashed</span>
        )}
        <TimerDisplay agentName={agent.name} />
        <div className="ml-auto flex gap-1">
          {agent.name === "worker" && (
            <button
              onClick={handleRestart}
              className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
              style={{ background: "#0a1f0f", color: "#4ade80", border: "1px solid #14532d" }}
            >
              ↺ Restart Worker
            </button>
          )}
          <button
            onClick={handleStop}
            className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
            style={{ background: "#1c0a0a", color: "#f87171", border: "1px solid #7f1d1d" }}
          >
            ⏹ Kill
          </button>
          <button
            onClick={handleClear}
            className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
            style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
          >
            Clear
          </button>
        </div>
      </div>

      <StreamView content={content} className="flex-1 bg-[#111111]" />
    </div>
  );
}

// ── Settings toggle (moved from RightRail) ────────────────────────────────────
function SettingsToggle({ onRefresh }: { onRefresh: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="px-3 py-1.5 text-[11px] rounded transition-colors duration-200 btn-blue-accent"
        style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
      >
        ⚙ Settings
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 w-56 rounded-lg shadow-xl z-20 p-3"
          style={{ background: "#1a1a1a", border: "1px solid #2a2a2a" }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider mb-2" style={{ color: "#4b5563" }}>Agent Settings</p>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 text-[11px] cursor-pointer" style={{ color: "#9ca3af" }}>
              <input type="checkbox" className="accent-blue-500" defaultChecked />
              <span style={{ color: "#60a5fa" }}>Auto-follow</span>
            </label>
            <label className="flex items-center gap-2 text-[11px] cursor-pointer" style={{ color: "#9ca3af" }}>
              <input type="checkbox" className="accent-blue-500" defaultChecked />
              <span style={{ color: "#a78bfa" }}>Show timestamps</span>
            </label>
            <div style={{ borderTop: "1px solid #2a2a2a", marginTop: "8px", paddingTop: "8px" }}>
              <button
                onClick={() => { setOpen(false); onRefresh(); }}
                className="w-full px-2 py-1 text-[10px] rounded transition-colors duration-200"
                style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Chat Panel ───────────────────────────────────────────────────────────────
function ChatPanel({
  sessionId,
  onActivateChat,
  onRefresh,
}: {
  sessionId: string;
  onActivateChat: () => void;
  onRefresh: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [sendingBySession, setSendingBySession] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [chatEnabled, setChatEnabled] = useState(false);
  const [chatStatus, setChatStatus] = useState("Checking chat runtime...");
  const sending = !!sendingBySession[sessionId];

  useEffect(() => {
    const refreshStatus = () => {
      apiGet<{enabled: boolean; reason?: string}>("/api/chat/status")
        .then(data => {
          setChatEnabled(!!data.enabled);
          setChatStatus(data.enabled ? "Chat runtime ready" : (data.reason ?? "Chat runtime unavailable"));
        })
        .catch(() => {
          setChatEnabled(false);
          setChatStatus("Chat runtime unavailable");
        });
    };
    refreshStatus();
    const id = setInterval(refreshStatus, 6000);
    return () => clearInterval(id);
  }, []);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    onActivateChat();
    onRefresh();
    setDraft("");
    setSendingBySession(prev => ({ ...prev, [sessionId]: true }));
    setError("");
    try {
      const result = await apiPost<{ ok: boolean; error?: string }>("/api/chat/send", { content: text });
      if (!result.ok) {
        if (result.error === "opencode_disabled" || result.error === "chat_runtime_unavailable") {
          setError("Live chat needs a reachable Chat role binding. Attach an external OpenCode server and press Chat in Runtime.");
        } else {
          setError(result.error ?? "Chat failed");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
    } finally {
      setSendingBySession(prev => ({ ...prev, [sessionId]: false }));
      onRefresh();
    }
  };

  return (
    <div className="shrink-0 px-4 py-3 border-t" style={{ background: "#181818", borderColor: "#2a2a2a" }}>
      <div className="flex items-center gap-2 mb-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#22c55e" }}>Chat</p>
        {sending && <span className="text-[10px] animate-pulse" style={{ color: "#22c55e" }}>thinking...</span>}
        <span className="ml-auto text-[10px] truncate max-w-[180px]" style={{ color: chatEnabled ? "#6b7280" : "#f87171" }}>{chatStatus}</span>
      </div>
      <div className="flex gap-1">
        <textarea
          className="flex-1 rounded px-2 py-1 text-[12px] outline-none resize-none"
          rows={1}
          style={{ background: "#111", border: "1px solid #2a2a2a", color: "#f0ede8" }}
          placeholder={chatEnabled ? "Ask the chat agent..." : "Bind Chat to OpenCode in Runtime"}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          onFocus={e => (e.target.style.borderColor = "#22c55e")}
          onBlur={e => (e.target.style.borderColor = "#2a2a2a")}
        />
        <button
          onClick={send}
          disabled={sending || !draft.trim() || !chatEnabled}
          className="px-2 py-1 text-[11px] rounded disabled:opacity-40"
          style={{ background: "#0a1f0f", color: "#4ade80", border: "1px solid #14532d" }}
        >
          Send
        </button>
      </div>
      {error && <p className="text-[11px] mt-1" style={{ color: "#f87171" }}>{error}</p>}
    </div>
  );
}

// ── Main CenterFeed ───────────────────────────────────────────────────────────
interface Props {
  agents: Agent[];
  activeAgent: string;
  onSelectAgent: (name: string) => void;
  loopRunning: boolean;
  loopElapsed: number;
  onRefresh: () => void;
}

export function CenterFeed({ agents, activeAgent, onSelectAgent, onRefresh }: Props) {
  const agent = agents.find(a => a.name === activeAgent) ?? agents[0];
  const [sessionId, setSessionId] = useState("legacy");

  useEffect(() => {
    const loadSession = () => {
      apiGet<{ active_project_session?: string }>("/api/health")
        .then(h => setSessionId(h.active_project_session || "legacy"))
        .catch(() => setSessionId("legacy"));
    };
    loadSession();
    const id = setInterval(loadSession, 5000);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="flex-1 flex flex-col min-w-0 min-h-0" style={{ background: "var(--bg-base)" }}>
      {/* Agent tabs — name + status dot + gear in same row */}
      {agents.length > 0 && (
        <div className="flex shrink-0 items-stretch" style={{ background: "#181818", borderBottom: "1px solid #2a2a2a" }}>
          {agents.map(a => (
            <div
              key={a.name}
              className="flex items-center border-b-2 transition-colors duration-200"
              style={{ borderBottomColor: activeAgent === a.name ? "#f59e0b" : "transparent" }}
            >
              {/* Tab label — click to select */}
              <button
                onClick={() => onSelectAgent(a.name)}
                className="pl-4 pr-2 py-2 text-[12px] font-medium flex items-center gap-1.5"
                style={{ color: activeAgent === a.name ? "#f0ede8" : "#4b5563" }}
              >
                {a.name}
                <StateBadge state={a.state as "idle"|"busy"|"waiting"|"error"|"offline"} />
              </button>
              {/* Gear — click to configure */}
              <div className="pr-3">
                <AgentConfig agent={a} onSave={onRefresh} />
              </div>
            </div>
          ))}
          <div className="ml-auto flex items-center pr-2">
            <SettingsToggle onRefresh={onRefresh} />
          </div>
        </div>
      )}

      {/* Stream */}
      {agent ? (
        <AgentStream key={agent.name} agent={agent} onClear={onRefresh} onStop={onRefresh} />
      ) : (
        <div className="flex-1 flex items-center justify-center text-[11px]" style={{ color: "#4b5563" }}>
          No agents found
        </div>
      )}

      {/* Chat Panel */}
      <ChatPanel
        key={sessionId}
        sessionId={sessionId}
        onActivateChat={() => onSelectAgent("chat")}
        onRefresh={onRefresh}
      />
    </main>
  );
}
