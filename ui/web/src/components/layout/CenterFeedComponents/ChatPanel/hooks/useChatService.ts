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
}

interface ChatServiceActions {
  send: (text: string) => Promise<void>;
  loadMessages: () => void;
  setHistoryPage: (page: number) => void;
  refreshStatus: () => void;
  clearError: () => void;
}

export function useChatService(sessionId: string, onRefresh: () => void): ChatServiceState & ChatServiceActions {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatEnabled, setChatEnabled] = useState(false);
  const [chatStatus, setChatStatus] = useState("Checking chat runtime...");
  const [sendingBySession, setSendingBySession] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [historyPage, setHistoryPage] = useState(0);
  const loadTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sending = !!sendingBySession[sessionId];

  const refreshStatus = useCallback(() => {
    apiGet<{enabled: boolean; reason?: string}>("/api/chat/status")
      .then((data: { enabled: boolean; reason?: string }) => {
        setChatEnabled(!!data.enabled);
        setChatStatus(data.enabled ? "Chat runtime ready" : (data.reason ?? "Chat runtime unavailable"));
      })
      .catch(() => {
        setChatEnabled(false);
        setChatStatus("Chat runtime unavailable");
      });
  }, []);

  const loadMessages = useCallback(() => {
    apiGet<ChatMessage[]>("/api/chat/messages")
      .then((data: ChatMessage[]) => setMessages(Array.isArray(data) ? data.filter((m: ChatMessage) => !("error" in m)) : []))
      .catch(() => {});
  }, []);

  const send = useCallback(async (text: string) => {
    if (!text || sending) return;
    setError("");
    setSendingBySession(prev => ({ ...prev, [sessionId]: true }));
    try {
      const result = await apiPost<{ ok: boolean; messages?: ChatMessage[]; error?: string }>("/api/chat/send", { content: text });
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
    setHistoryPage(0);
  }, [messages.length]);

  return {
    messages,
    chatEnabled,
    chatStatus,
    sending,
    error,
    historyPage,
    send,
    loadMessages,
    setHistoryPage,
    refreshStatus,
    clearError,
  };
}