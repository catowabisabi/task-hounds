import { useState, useEffect } from "react";
import { apiGet } from "../../lib/api";
import type { Agent } from "../../lib/api";
import { StateBadge } from "../ui/Badge";
import { AgentConfig, TimerDisplay, AgentStream, SettingsToggle, ChatPanel } from "./CenterFeedComponents";

export { AgentConfig, TimerDisplay, AgentStream, SettingsToggle, ChatPanel } from "./CenterFeedComponents";

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
        <div className="flex shrink-0 items-stretch" style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--border)" }}>
          {agents.map(a => (
            <div
              key={a.name}
              className="flex items-center border-b-2 transition-colors duration-200"
              style={{ borderBottomColor: activeAgent === a.name ? "var(--amber)" : "transparent" }}
            >
              {/* Tab label — click to select */}
              <button
                onClick={() => onSelectAgent(a.name)}
                className="pl-4 pr-2 py-2 text-[12px] font-medium flex items-center gap-1.5"
                style={{ color: activeAgent === a.name ? "var(--text-primary)" : "var(--text-dim)" }}
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
        <div className="flex-1 flex items-center justify-center text-[11px]" style={{ color: "var(--text-dim)" }}>
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
