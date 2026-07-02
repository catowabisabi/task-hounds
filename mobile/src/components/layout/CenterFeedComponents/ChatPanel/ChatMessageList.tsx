import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../../../lib/api";

function RenderMessageContent({ content }: { content: string }) {
  // Simple markdown: **bold**, `code`, ## headings, lists
  const html = content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, '<div class="text-[11px] font-semibold" style="color:var(--text-secondary)">$1</div>')
    .replace(/^## (.+)$/gm, '<div class="text-[11px] font-semibold" style="color:var(--amber)">$1</div>')
    .replace(/^# (.+)$/gm, '<div class="text-[11px] font-bold" style="color:var(--text-primary)">$1</div>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--text-primary)">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1 rounded text-[10px]" style="background:var(--bg-raised);color:var(--purple)">$1</code>')
    .replace(/^- (.+)$/gm, '<div class="text-[11px]" style="color:var(--text-secondary)">• $1</div>')
    .replace(/\n/g, '<br />');

  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

interface ChatMessageListProps {
  messages: ChatMessage[];
  historyPage: number;
  panelHeight: number;
  isDragging: boolean;
  expanded: boolean;
  minimized: boolean;
  onDragStart: (e: React.MouseEvent) => void;
  acceptingDirectiveId: number | null;
  onAcceptDirective: (messageId: number) => void;
}

export function ChatMessageList({
  messages,
  historyPage,
  panelHeight,
  isDragging,
  expanded,
  minimized,
  onDragStart,
  acceptingDirectiveId,
  onAcceptDirective,
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

  const senderLabel = (sender?: string) => {
    if (isHumanSender(sender)) return "You";
    return sender?.trim() || "AI";
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
              <div
                key={`${m.id}-${idx}`}
                className={`flex gap-2 items-end ${mine ? "flex-row-reverse" : "flex-row"}`}
              >
                <div
                  className="flex items-center justify-center rounded-full shrink-0"
                  style={{
                    width: 26,
                    height: 26,
                    background: mine ? "var(--blue-bg)" : "var(--green-bg)",
                    color: mine ? "var(--blue)" : "var(--green)",
                    border: `1px solid ${mine ? "var(--blue-dim)" : "var(--green-dim)"}`,
                    fontSize: 10,
                    fontWeight: 600,
                  }}
                >
                  {mine ? "You" : "AI"}
                </div>
                <div
                  className="max-w-[78%] px-3 py-2 text-[11px] whitespace-pre-wrap break-words leading-relaxed"
                  style={mine
                    ? {
                        background: "var(--bubble-user-bg)",
                        color: "var(--bubble-user-text)",
                        borderRadius: "14px 14px 4px 14px",
                      }
                    : {
                        background: "var(--bubble-agent-bg)",
                        color: "var(--bubble-agent-text)",
                        border: "1px solid var(--bubble-agent-border)",
                        borderRadius: "14px 14px 14px 4px",
                      }}
                >
                  {!mine && (
                    <div
                      style={{
                        fontSize: 10,
                        color: "var(--bubble-agent-name)",
                        marginBottom: 2,
                        fontWeight: 500,
                      }}
                    >
                      {senderLabel(m.sender)}
                    </div>
                  )}
                  <RenderMessageContent content={m.content} />
                  {!mine && m.directive_proposal && (
                    <div
                      className="mt-2 pt-2"
                      style={{ borderTop: "1px solid var(--bubble-agent-border)" }}
                    >
                      <details>
                        <summary
                          className="cursor-pointer text-[10px] font-medium"
                          style={{ color: "var(--blue)" }}
                        >
                          Review Human Directive proposal
                        </summary>
                        <div
                          className="mt-2 max-h-48 overflow-y-auto rounded p-2 text-[10px] whitespace-pre-wrap"
                          style={{
                            background: "var(--bg-base)",
                            color: "var(--text-secondary)",
                            border: "1px solid var(--border)",
                          }}
                        >
                          {m.directive_proposal}
                        </div>
                      </details>
                      <button
                        onClick={() => onAcceptDirective(m.id)}
                        disabled={m.proposal_status === "saved" || acceptingDirectiveId === m.id}
                        className="mt-2 w-full rounded px-2 py-1.5 text-[10px] font-semibold disabled:opacity-60"
                        style={{
                          background: m.proposal_status === "saved" ? "var(--green-bg)" : "var(--blue-bg)",
                          color: m.proposal_status === "saved" ? "var(--green)" : "var(--blue)",
                          border: `1px solid ${m.proposal_status === "saved" ? "var(--green-dim)" : "var(--blue-dim)"}`,
                        }}
                      >
                        {m.proposal_status === "saved"
                          ? "Saved as Human Directive"
                          : acceptingDirectiveId === m.id
                            ? "Saving..."
                            : "Save as Human Directive"}
                      </button>
                    </div>
                  )}
                </div>
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
