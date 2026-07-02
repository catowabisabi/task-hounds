import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet, apiPost } from "../../../../../lib/api";
import type { ChatMessage } from "../../../../../lib/api";

interface ChatServiceState {
  messages: ChatMessage[];
  chatEnabled: boolean;
  chatStatus: string;
  sending: boolean;
  error: string;
  historyPage: number;
  acceptingDirectiveId: number | null;
}

interface ChatServiceActions {
  send: (text: string) => Promise<void>;
  loadMessages: () => void;
  setHistoryPage: (page: number) => void;
  refreshStatus: () => void;
  clearError: () => void;
  acceptDirective: (messageId: number) => Promise<void>;
}

export function useChatService(sessionId: string, onRefresh: () => void): ChatServiceState & ChatServiceActions {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatEnabled, setChatEnabled] = useState(false);
  const [chatStatus, setChatStatus] = useState("Checking chat runtime...");
  const [sendingBySession, setSendingBySession] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [historyPage, setHistoryPage] = useState(0);
  const [acceptingDirectiveId, setAcceptingDirectiveId] = useState<number | null>(null);
  const loadTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sending = !!sendingBySession[sessionId];

  const refreshStatus = useCallback(() => {
    apiGet<{
      enabled: boolean;
      reason?: string;
      binding_ok?: boolean;
      binding_reachable?: boolean;
      credentials_ok?: boolean;
      credential_warnings?: string[];
    }>("/api/chat/status")
      .then((data) => {
        setChatEnabled(!!data.enabled);
        const parts: string[] = [];
        if (data.enabled) parts.push("Chat runtime ready");
        else if (data.reason === "workspace_missing") parts.push("Project folder missing - relink required");
        else parts.push(data.reason ?? "Chat runtime unavailable");
        if (!data.credentials_ok && data.credential_warnings && data.credential_warnings.length > 0) {
          parts.push("credentials warning (external server may still work)");
        }
        setChatStatus(parts.join(" — "));
      })
      .catch(() => {
        setChatEnabled(false);
        setChatStatus("Chat runtime unavailable");
      });
  }, []);

  const loadMessages = useCallback(() => {
    apiGet<ChatMessage[]>(`/api/chat/messages?session_id=${encodeURIComponent(sessionId)}`)
      .then((data: ChatMessage[]) => setMessages(Array.isArray(data) ? data.filter((m: ChatMessage) => !("error" in m)) : []))
      .catch(() => {});
  }, [sessionId]);

  const send = useCallback(async (text: string) => {
    if (!text || sending) return;
    setError("");
    setSendingBySession(prev => ({ ...prev, [sessionId]: true }));
    window.dispatchEvent(new CustomEvent("task-hounds-chat-activity", { detail: { content: text } }));
    try {
      const result = await apiPost<{ ok: boolean; messages?: ChatMessage[]; error?: string }>("/api/chat/send", {
        content: text,
        session_id: sessionId,
      });
      if (result.messages) setMessages(result.messages);
      if (!result.ok) {
        if (result.error === "opencode_disabled" || result.error === "chat_runtime_unavailable") {
          setError("Live chat needs a reachable Chat role binding. Attach an external OpenCode server and press Chat in Runtime.");
        } else {
          setError(result.error ?? "Chat failed");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
    } finally {
      setSendingBySession(prev => ({ ...prev, [sessionId]: false }));
      onRefresh();
    }
  }, [sessionId, sending, onRefresh]);

  const clearError = useCallback(() => setError(""), []);

  const acceptDirective = useCallback(async (messageId: number) => {
    if (acceptingDirectiveId != null) return;
    setAcceptingDirectiveId(messageId);
    setError("");
    try {
      await apiPost(`/api/chat/messages/${messageId}/accept-directive`, {
        session_id: sessionId,
      });
      loadMessages();
      window.dispatchEvent(new CustomEvent("task-hounds-directive-updated", {
        detail: { sessionId, source: "chat" },
      }));
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Human Directive");
    } finally {
      setAcceptingDirectiveId(null);
    }
  }, [acceptingDirectiveId, loadMessages, onRefresh, sessionId]);

  useEffect(() => {
    refreshStatus();
    const statusId = setInterval(refreshStatus, 6000);
    return () => clearInterval(statusId);
  }, [refreshStatus]);

  useEffect(() => {
    loadMessages();
    loadTimerRef.current = setInterval(loadMessages, 6000);
    return () => {
      if (loadTimerRef.current) clearInterval(loadTimerRef.current);
    };
  }, [loadMessages]);

  useEffect(() => {
    queueMicrotask(() => setHistoryPage(0));
  }, [messages.length]);

  return {
    messages,
    chatEnabled,
    chatStatus,
    sending,
    error,
    historyPage,
    acceptingDirectiveId,
    send,
    loadMessages,
    setHistoryPage,
    refreshStatus,
    clearError,
    acceptDirective,
  };
}
