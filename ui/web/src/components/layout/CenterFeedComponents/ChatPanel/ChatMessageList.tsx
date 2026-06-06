import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../../../lib/api";

interface ChatMessageListProps {
  messages: ChatMessage[];
  historyPage: number;
  panelHeight: number;
  isDragging: boolean;
  expanded: boolean;
  minimized: boolean;
  onDragStart: (e: React.MouseEvent) => void;
}

export function ChatMessageList({
  messages,
  historyPage,
  panelHeight,
  isDragging,
  expanded,
  minimized,
  onDragStart,
}: ChatMessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const pageSize = 6;
  const displayStart = Math.max(0, messages.length - (historyPage + 1) * pageSize);
  const displayEnd = messages.length - historyPage * pageSize;
  const displayedMessages = messages.slice(displayStart, displayEnd);

  useEffect(() => {
    if (messages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages.length]);

  const getMessageAreaHeight = () => {
    if (minimized) return 24;
    if (expanded) return "100%";
    return panelHeight;
  };

  const isHumanSender = (sender?: string) => {
    const normalized = (sender || "").toLowerCase();
    return normalized === "human" || normalized === "user" || normalized === "me";
  };

  return (
    <>
      <div
        className="w-full h-2 flex items-center justify-center cursor-row-resize mb-1"
        onMouseDown={onDragStart}
        style={{ cursor: isDragging ? "row-resize" : "row-resize" }}
      >
        <div className="w-12 h-1 rounded-full flex items-center justify-center gap-0.5" style={{ background: "var(--border)" }}>
          <span style={{ fontSize: "6px", color: "var(--text-dim)" }}>⬤ ⬤ ⬤</span>
        </div>
      </div>

      {messages.length > 0 ? (
        <div
          className="mb-2 space-y-1 overflow-y-auto rounded p-2"
          style={{
            background: "var(--bg-base)",
            border: "1px solid var(--border-dim)",
            maxHeight: getMessageAreaHeight(),
            height: typeof getMessageAreaHeight() === "number" ? getMessageAreaHeight() : undefined,
            minHeight: minimized ? 24 : 40,
          }}
        >
          {displayedMessages.map((m, idx) => {
            const mine = isHumanSender(m.sender);
            return (
              <div key={`${m.id}-${idx}`} className="text-[11px] whitespace-pre-wrap" style={{ color: mine ? "var(--blue)" : "var(--green)" }}>
                <span style={{ color: mine ? "var(--blue)" : "var(--green)", fontSize: "9px" }}>{mine ? "You" : "Chat"}:</span> {m.content}
              </div>
            );
          })}
          <div ref={messagesEndRef} />
        </div>
      ) : (
        <div
          className="mb-2 text-[11px] rounded p-2 text-center"
          style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-dim)" }}
        >
          No messages
        </div>
      )}
    </>
  );
}
