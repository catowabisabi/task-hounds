import { useEffect, useState } from "react";
import { useStream } from "../../../hooks/useStream";
import { StateBadge } from "../../ui/Badge";
import { StreamView } from "../../ui/StreamView";
import { apiPost } from "../../../lib/api";
import type { Agent } from "../../../lib/api";
import { TimerDisplay } from "./TimerDisplay";

export function AgentStream({ agent, sessionId, onClear, onStop }: {
  agent: Agent;
  sessionId: string;
  onClear: () => void;
  onStop: () => void;
}) {
  const content = useStream(agent.name, sessionId);
  const errorText = agent.last_error?.trim();
  const errorKey = errorText ? `${agent.name}:${errorText}` : "";
  const [hiddenErrorKey, setHiddenErrorKey] = useState("");
  const [errorFading, setErrorFading] = useState(false);

  const handleClear = async () => {
    await apiPost(`/api/stream/${agent.name}/clear`);
    onClear();
  };

  const handleClearError = async () => {
    if (!errorText) return;
    setHiddenErrorKey(errorKey);
    await apiPost(`/api/agents/${agent.name}/clear-error`).catch(() => {});
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
  const isActive = agent.state === "busy" || agent.state === "waiting";
  const showErrorText = Boolean(errorText && hiddenErrorKey !== errorKey);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [localLastStreamMs, setLocalLastStreamMs] = useState<number | null>(null);
  const stepStartedMs = agent.current_step_started_at ? Date.parse(agent.current_step_started_at) : NaN;
  const lastStreamMs = agent.last_stream_at ? Date.parse(agent.last_stream_at) : (localLastStreamMs ?? NaN);
  const stepElapsed = Number.isFinite(stepStartedMs) ? Math.max(0, Math.floor((nowMs - stepStartedMs) / 1000)) : null;
  const silentElapsed = Number.isFinite(lastStreamMs) ? Math.max(0, Math.floor((nowMs - lastStreamMs) / 1000)) : null;
  const sourceLabel = agent.step_source?.trim();
  const stepLabel = `${sourceLabel ? `[${sourceLabel}] ` : ""}${agent.current_step?.trim() || "working"}`;
  const maybeStuck = isActive && silentElapsed !== null && silentElapsed >= 180;

  useEffect(() => {
    if (!isActive) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isActive]);

  useEffect(() => {
    if (!content) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setLocalLastStreamMs(Date.now());
    });
    return () => { cancelled = true; };
  }, [content]);

  useEffect(() => {
    if (!errorKey || hiddenErrorKey === errorKey) return;
    queueMicrotask(() => setErrorFading(false));
    const fadeTimer = window.setTimeout(() => setErrorFading(true), 10000);
    const hideTimer = window.setTimeout(() => setHiddenErrorKey(errorKey), 10400);
    return () => {
      window.clearTimeout(fadeTimer);
      window.clearTimeout(hideTimer);
    };
  }, [errorKey, hiddenErrorKey]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div
        className="flex items-center gap-2 px-4 py-2 shrink-0 flex-wrap"
        style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--border)" }}
      >
        <StateBadge state={agent.state as "idle" | "busy" | "waiting" | "error" | "offline"} />
        {isActive && (
          <span className="text-[11px] animate-pulse" style={{ color: maybeStuck ? "var(--red)" : "var(--amber)" }}>
            {stepLabel}{stepElapsed !== null ? ` · ${stepElapsed}s` : ""}
            {silentElapsed !== null ? ` · last output ${silentElapsed}s ago` : ""}
          </span>
        )}
        {isCrashed && (
          <span className="text-[11px]" style={{ color: "var(--red)" }}>
            {errorText ? "error" : "crashed"}
          </span>
        )}
        <TimerDisplay agentName={agent.name} />
        <div className="ml-auto flex gap-1">
          {agent.name === "worker" && (
            <button
              onClick={handleRestart}
              className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
              style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
            >
              Restart Worker
            </button>
          )}
          <button
            onClick={handleStop}
            className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
            style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
          >
            Kill
          </button>
          <button
            onClick={handleClear}
            className="px-2 py-1 text-[10px] rounded transition-colors duration-200"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          >
            Clear
          </button>
        </div>
      </div>

      {showErrorText && (
        <div
          className="flex items-start gap-3 px-4 py-2 text-[11px] leading-relaxed transition-opacity duration-500"
          style={{
            background: "var(--red-bg)",
            color: "var(--red)",
            borderBottom: "1px solid var(--red-dim)",
            opacity: errorFading ? 0 : 1,
          }}
          title={errorText}
        >
          <span className="min-w-0 flex-1">{errorText}</span>
          <button
            onClick={handleClearError}
            className="h-5 w-5 shrink-0 rounded leading-none transition-colors duration-200"
            style={{ color: "var(--red)", border: "1px solid var(--red-dim)", opacity: errorFading ? 0.45 : 1 }}
            title="Clear error"
            aria-label="Clear error"
          >
            ×
          </button>
        </div>
      )}

      <StreamView content={content} className="flex-1 bg-[var(--bg-base)]" />
    </div>
  );
}
