import { useState } from "react";
import { apiPost } from "../../lib/api";
import type { Agent, LoopStatus } from "../../lib/api";
import { WorkspacesPanel } from "./LeftRailComponents";
import { ConfirmModal } from "./LeftRailComponents/ConfirmModal";
import { LoadingWidget } from "../ui/LoadingWidget";

interface Props {
  agents: Agent[];
  activeAgent: string;
  onSelectAgent: (name: string) => void;
  loopStatus: LoopStatus;
  onLoopChange: (scope?: "workspace" | "session") => void | Promise<void>;
  onRunOnce: () => void;
  onReset: () => void;
  sessionReloadKey?: number;
}

export function LeftRail({ onLoopChange, sessionReloadKey }: Props) {
  const [confirmReset, setConfirmReset] = useState(false);
  const [clearDataConfirm, setClearDataConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);

  const handleReset = async () => {
    setResetting(true);
    try {
      await apiPost("/api/session/reset");
      window.dispatchEvent(new CustomEvent("task-hounds-session-reset"));
      onLoopChange();
    } finally {
      setResetting(false);
    }
  };

  const clearData = async () => {
    await Promise.allSettled([
      apiPost("/api/stream/manager/clear"),
      apiPost("/api/stream/worker/clear"),
      apiPost("/api/stream/reviewer/clear"),
      apiPost("/api/stream/chat/clear"),
    ]);
    setClearDataConfirm(false);
    onLoopChange();
  };

  return (
    <>
      {resetting && <LoadingWidget message="Resetting sessions..." />}
      <aside className="w-48 shrink-0 flex flex-col" style={{ background: "var(--bg-base)", borderRight: "1px solid var(--border)" }}>
        <div className="flex-1 overflow-y-auto">
          <WorkspacesPanel onActivate={onLoopChange} sessionReloadKey={sessionReloadKey} />
        </div>

        <div className="px-2 py-2 space-y-1 shrink-0" style={{ borderTop: "1px solid var(--border-dim)" }}>
          <button
            onClick={() => setConfirmReset(true)}
            className="w-full py-1 text-[10px] rounded transition-colors"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border-dim)" }}
          >Reset Sessions</button>
          <button
            onClick={() => setClearDataConfirm(true)}
            className="w-full py-1 text-[10px] rounded transition-colors"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border-dim)" }}
          >Clear Stream Data</button>
        </div>

        {confirmReset && (
          <ConfirmModal
            message="This will clear all agent sessions and reset state. Continue?"
            onConfirm={() => { setConfirmReset(false); handleReset(); }}
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
    </>
  );
}
