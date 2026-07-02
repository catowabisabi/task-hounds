import { useState } from "react";
import { ChatHeader } from "./ChatHeader";
import { ChatMessageList } from "./ChatMessageList";
import { ChatInput } from "./ChatInput";
import { useChatService } from "./hooks/useChatService";
import { useChatPanelState } from "./hooks/useChatPanelState";

interface ChatPanelProps {
  sessionId: string;
  onActivateChat: () => void;
  onRefresh: () => void;
}

export function ChatPanel({ sessionId, onActivateChat, onRefresh }: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const {
    messages,
    chatEnabled,
    chatStatus,
    sending,
    error,
    historyPage,
    acceptingDirectiveId,
    send,
    setHistoryPage,
    acceptDirective,
  } = useChatService(sessionId, onRefresh);
  const { panelHeight, isDragging, expanded, minimized, setExpanded, setMinimized, handleDragStart } = useChatPanelState();
  const pageSize = 6;
  const canGoBack = historyPage > 0;
  const canGoForward = messages.length - (historyPage + 1) * pageSize > 0;

  const handleSend = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    send(text);
  };

  return (
    <div className="shrink-0 px-4 py-3 border-t" style={{ background: "var(--bg-panel)", borderColor: "var(--border)" }}>
      <ChatHeader
        title="Chat"
        chatEnabled={chatEnabled}
        chatStatus={chatStatus}
        sending={sending}
        minimized={minimized}
        historyPage={historyPage}
        canGoBack={canGoBack}
        canGoForward={canGoForward}
        onToggleMinimize={() => { setMinimized(!minimized); setExpanded(false); }}
        onPageChange={setHistoryPage}
      />

      <ChatMessageList
        messages={messages}
        historyPage={historyPage}
        panelHeight={panelHeight}
        isDragging={isDragging}
        expanded={expanded}
        minimized={minimized}
        onDragStart={handleDragStart}
        acceptingDirectiveId={acceptingDirectiveId}
        onAcceptDirective={(messageId) => void acceptDirective(messageId)}
      />

      <ChatInput
        value={draft}
        onChange={setDraft}
        chatEnabled={chatEnabled}
        sending={sending}
        onSend={handleSend}
        onActivateChat={onActivateChat}
      />

      {error && <p className="text-[11px] mt-1" style={{ color: "var(--red)" }}>{error}</p>}
    </div>
  );
}
