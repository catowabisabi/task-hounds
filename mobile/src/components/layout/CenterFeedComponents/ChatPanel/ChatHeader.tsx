import { thinkingLabels, useUiLanguage } from "../../../../lib/uiLanguage";

interface ChatHeaderProps {
  title?: string;
  chatEnabled: boolean;
  chatStatus: string;
  sending: boolean;
  minimized: boolean;
  historyPage: number;
  canGoBack: boolean;
  canGoForward: boolean;
  onToggleMinimize: () => void;
  onPageChange: (page: number) => void;
}

export function ChatHeader({
  title = "Chat",
  chatEnabled,
  chatStatus,
  sending,
  minimized,
  historyPage,
  canGoBack,
  canGoForward,
  onToggleMinimize,
  onPageChange,
}: ChatHeaderProps) {
  const thinkingText = thinkingLabels(useUiLanguage());
  return (
    <div className="flex items-center gap-2 mb-1">
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--green)" }}>{title}</p>
      {sending && <span className="text-[10px] animate-pulse" style={{ color: "var(--green)" }}>{thinkingText.thinking}</span>}
      <span className="ml-auto text-[10px] truncate max-w-[120px]" style={{ color: chatEnabled ? "var(--text-secondary)" : "var(--red)" }}>{chatStatus}</span>

      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(historyPage - 1)}
          disabled={!canGoBack}
          className="px-1 py-0.5 text-[10px] rounded disabled:opacity-30"
          style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          ◀
        </button>
        <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>P{historyPage + 1}</span>
        <button
          onClick={() => onPageChange(historyPage + 1)}
          disabled={!canGoForward}
          className="px-1 py-0.5 text-[10px] rounded disabled:opacity-30"
          style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          ▶
        </button>
      </div>

      <button
        onClick={onToggleMinimize}
        className="px-1 py-0.5 text-[11px] rounded transition-colors"
        style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        title={minimized ? "Restore" : "Minimize"}
      >
        {minimized ? "[ ]" : "---"}
      </button>
    </div>
  );
}
