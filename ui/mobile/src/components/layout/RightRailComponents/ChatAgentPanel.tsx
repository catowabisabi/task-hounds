import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost } from "../../../lib/api";
import type { ChatMessage } from "../../../lib/api";
import { thinkingLabels, useUiLanguage } from "../../../lib/uiLanguage";

export function ChatAgentPanel() {
  const thinkingText = thinkingLabels(useUiLanguage());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [chatEnabled, setChatEnabled] = useState(false);
  const [chatStatus, setChatStatus] = useState("Checking chat runtime...");

  const load = useCallback(() => {
    apiGet<ChatMessage[]>("/api/chat/messages")
      .then(data => setMessages(Array.isArray(data) ? data.filter(m => !("error" in m)) : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 6000);
    const onReset = () => load();
    window.addEventListener("task-hounds-session-reset", onReset);
    return () => {
      clearInterval(id);
      window.removeEventListener("task-hounds-session-reset", onReset);
    };
  }, [load]);

  useEffect(() => {
    const refreshStatus = () => {
      apiGet<{
        enabled: boolean;
        reason?: string;
        credentials_ok?: boolean;
        credential_warnings?: string[];
      }>("/api/chat/status")
        .then(data => {
          setChatEnabled(!!data.enabled);
          const parts: string[] = [];
          if (data.enabled) parts.push("Chat runtime ready");
          else parts.push(data.reason ?? "Chat runtime unavailable");
          if (!data.credentials_ok && data.credential_warnings && data.credential_warnings.length > 0) {
            parts.push("credentials warning");
          }
          setChatStatus(parts.join(" — "));
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
    setDraft("");
    setSending(true);
    setError("");
    window.dispatchEvent(new CustomEvent("task-hounds-chat-activity", { detail: { content: text } }));
    try {
      const validation = await apiPost<{ valid: boolean; errors: string[]; warnings: string[] }>("/api/validate/send-config", { agent_name: "chat" });
      if (!validation.valid || validation.errors.length > 0) {
        const errorMsg = validation.errors.join("; ");
        setError(`Configuration error: ${errorMsg}. Please check the Chat Agent binding.`);
        setSending(false);
        return;
      }
      const result = await apiPost<{ ok: boolean; messages?: ChatMessage[]; error?: string }>("/api/chat/send", { content: text });
      if (result.messages) setMessages(result.messages);
      if (!result.ok) {
        if (result.error === "opencode_disabled" || result.error === "chat_runtime_unavailable") {
          setError("Live chat needs a reachable Chat role binding.");
        } else {
          setError(result.error ?? "Chat failed");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
      load();
    } finally {
      setSending(false);
    }
  };

  const isHumanSender = (sender?: string) => {
    const normalized = (sender || "").toLowerCase();
    return normalized === "human" || normalized === "user" || normalized === "me";
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--green)" }}>Chat Agent</p>
        {sending && <span className="text-[10px] animate-pulse" style={{ color: "var(--green)" }}>{thinkingText.thinking}</span>}
      </div>
      <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
        {messages.length === 0 ? (
          <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No chat yet</p>
        ) : messages.slice(-12).map((m, idx) => {
          const mine = isHumanSender(m.sender);
          return (
            <div key={`${m.id}-${idx}`} className="text-[11px] rounded p-2 whitespace-pre-wrap" style={{ background: mine ? "var(--blue-bg)" : "var(--green-bg)", color: mine ? "var(--blue)" : "var(--green)", border: mine ? "1px solid var(--blue-dim)" : "1px solid var(--green-dim)" }}>
              <div className="text-[9px] uppercase mb-1" style={{ color: mine ? "var(--blue)" : "var(--green)" }}>{mine ? "You" : "Chat"}</div>
              {m.content}
            </div>
          );
        })}
      </div>
      {error && <p className="text-[11px]" style={{ color: "var(--red)" }}>{error}</p>}
      <div className="flex gap-1">
        <textarea className="flex-1 rounded px-2 py-1 text-[12px] outline-none resize-none" rows={2} style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-primary)" }} placeholder={chatEnabled ? "Ask the chat agent..." : "Bind Chat to an OpenCode server in Runtime"} value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} onFocus={e => (e.target.style.borderColor = "var(--green)")} onBlur={e => (e.target.style.borderColor = "var(--border)")} />
        <button onClick={send} disabled={sending || !draft.trim()} className="px-2 py-1 text-[11px] rounded disabled:opacity-40" style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}>Send</button>
      </div>
      {!chatEnabled && <p className="text-[10px] mt-1" style={{ color: "var(--text-secondary)" }}>{chatStatus}</p>}
    </div>
  );
}
