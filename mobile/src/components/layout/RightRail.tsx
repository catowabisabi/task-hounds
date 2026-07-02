import type { Suggestion } from "../../lib/api";
import { RuntimePanel } from "../ui/RuntimePanel";
import {
  HumanDirectivePanel,
  ManagerChatPanel,
  SuggestionPanel,
  FilesPanel,
  HandoffPanel,
  Flow01ReportsPanel,
} from "./RightRailComponents";

interface Props {
  suggestion: Suggestion | null;
  onSuggestionAction: () => void;
  onProjectRefresh: () => void;
  directiveClearKey?: number;
  flow01Mode?: boolean;
}

export function RightRail({ suggestion, onSuggestionAction, onProjectRefresh, directiveClearKey = 0, flow01Mode = false }: Props) {
  const workflowApiPrefix = flow01Mode ? "/api/workflows/flow_01" : "/api";
  return (
    <aside
      className="w-72 shrink-0 flex flex-col min-h-0"
      style={{ background: "var(--bg-base)", borderLeft: "1px solid var(--border)" }}
    >
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 space-y-4" style={{ borderBottom: "1px solid var(--border-dim)" }}>
          <HumanDirectivePanel clearKey={directiveClearKey} />
          {/* <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
            <ChatAgentPanel />
          </div> */}
          <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
            <ManagerChatPanel onApplied={onProjectRefresh} />
          </div>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>System Status</p>
          <SuggestionPanel suggestion={suggestion} onAction={onSuggestionAction} apiPrefix={workflowApiPrefix} />
          {flow01Mode && (
            <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
              <Flow01ReportsPanel refreshKey={directiveClearKey} />
            </div>
          )}
          <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
            <FilesPanel clearKey={directiveClearKey} />
          </div>
          <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
            <HandoffPanel clearKey={directiveClearKey} apiPrefix={workflowApiPrefix} />
          </div>
          <div style={{ borderTop: "1px solid var(--border-dim)" }} className="pt-3">
            <RuntimePanel />
          </div>
        </div>
      </div>
    </aside>
  );
}
