interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  chatEnabled: boolean;
  sending: boolean;
  onSend: () => void;
}

export function ChatInput({ value, onChange, chatEnabled, sending, onSend }: ChatInputProps) {
  return (
    <div className="flex gap-1">
      <textarea
        className="flex-1 rounded px-2 py-1 text-[12px] outline-none resize-none"
        rows={1}
        style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        placeholder={chatEnabled ? "Ask the chat agent..." : "Bind Chat to OpenCode in Runtime"}
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSend();
          }
        }}
        onFocus={e => (e.target.style.borderColor = "var(--green)")}
        onBlur={e => (e.target.style.borderColor = "var(--border)")}
      />
      <button
        onClick={onSend}
        disabled={sending || !value.trim() || !chatEnabled}
        className="px-2 py-1 text-[11px] rounded disabled:opacity-40"
        style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
      >
        Send
      </button>
    </div>
  );
}
