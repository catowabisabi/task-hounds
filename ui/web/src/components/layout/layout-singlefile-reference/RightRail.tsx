import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost, apiPut } from "../../lib/api";
import type { Suggestion, ManagerMessage, ChatMessage } from "../../lib/api";
import { RuntimePanel } from "../ui/RuntimePanel";

// ── Fullscreen markdown modal ───────────────────────────────────────────────────
function FullscreenMarkdownModal({
  title,
  value,
  onSave,
  onClose,
  accent = "#f59e0b",
}: {
  title: string;
  value: string;
  onSave: (v: string) => Promise<void>;
  onClose: () => void;
  accent?: string;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef(value);

  const persistNow = useCallback(async (v: string) => {
    if (v === lastSavedRef.current) return;
    lastSavedRef.current = v;
    setSaving(true);
    await onSave(v);
    setSaving(false);
  }, [onSave]);

  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      persistNow(draft);
    }, 1000);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [draft, persistNow]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
        persistNow(draft).then(() => onClose());
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [draft, persistNow, onClose]);

  const previewHtml = draft
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^# (.+)$/gm, '<div class="text-[14px] font-bold mt-2 mb-1" style="color:#f0ede8">$1</div>')
    .replace(/^## (.+)$/gm, '<div class="text-[13px] font-semibold mt-1.5 mb-0.5" style="color:#d4b06a">$1</div>')
    .replace(/^### (.+)$/gm, '<div class="text-[12px] font-medium mt-1 mb-0.5" style="color:#a78bfa">$1</div>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:#f0ede8">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1 rounded text-[11px]" style="background:#1a1a1a;color:#a78bfa">$1</code>')
    .replace(/\n/g, '<br />');

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: "rgba(0,0,0,0.90)" }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="flex items-center justify-between px-4 py-2 shrink-0"
        style={{ borderBottom: `1px solid ${accent}33`, background: "#0d0d0d" }}
      >
        <span className="text-[12px] font-semibold" style={{ color: accent }}>{title}</span>
        <div className="flex items-center gap-2">
          {saving && <span className="text-[10px] animate-pulse" style={{ color: "#4ade80" }}>saving…</span>}
          <span className="text-[9px]" style={{ color: "#4b5563" }}>Cmd+Enter to save</span>
          <button
          onClick={onClose}
          className="text-lg leading-none transition-colors duration-200"
          style={{ color: "#4b5563" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#f59e0b")}
          onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
        >×</button>
        </div>
      </div>

      <div className="flex flex-1 min-h-0">
        <textarea
          className="flex-1 p-4 font-mono text-[16px] resize-none outline-none bg-transparent"
          style={{ color: "#f0ede8", borderRight: "1px solid #2a2a2a" }}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder="Write in Markdown..."
          autoFocus
        />
        <div
          className="flex-1 p-4 overflow-y-auto text-[13px] leading-relaxed"
          style={{ color: "#d1d5db", background: "#111111" }}
          dangerouslySetInnerHTML={{ __html: previewHtml }}
        />
      </div>

      <div
        className="flex gap-2 justify-end px-4 py-3 shrink-0"
        style={{ borderTop: `1px solid ${accent}22`, background: "#0d0d0d" }}
      >
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-[12px] rounded transition-colors duration-200"
          style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
        >
          Cancel
        </button>
        <button
          onClick={async () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); await persistNow(draft); onClose(); }}
          disabled={saving}
          className="px-4 py-1.5 text-[12px] font-semibold rounded disabled:opacity-50 transition-colors duration-200"
          style={{ background: accent === "#f59e0b" ? "#1c1408" : "#080f1c", color: accent, border: `1px solid ${accent}66` }}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>,
    document.body
  );
}

// ── Human Directive ────────────────────────────────────────────────────────────
function HumanDirectivePanel({ clearKey = 0 }: { clearKey?: number }) {
  const [content, setContent] = useState("");
  const [saved, setSaved]     = useState(false);
  const [modal, setModal]     = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    apiGet<{ content: string }>("/api/files/user_input")
      .then(d => setContent(d.content))
      .catch(() => {});
  }, [clearKey]);

  const save = async (val: string) => {
    await apiPut("/api/files/user_input", { content: val });
    setContent(val);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-bold uppercase tracking-wide" style={{ color: "#f59e0b" }}>
          Human Directive
        </p>
        <button
          onClick={() => setModal(true)}
          title="Open full editor"
          className="text-[11px] px-1.5 py-0.5 rounded transition-colors"
          style={{ color: "#4b5563", border: "1px solid #2a2a2a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#f59e0b")}
          onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
        >⤢</button>
      </div>
      <div className="relative">
        <textarea
          className="w-full rounded p-2 text-[16px] resize-none outline-none transition-colors"
          style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#f0ede8" }}
          rows={4}
          value={content}
          onChange={e => setContent(e.target.value)}
          placeholder="Enter task or directive..."
          onFocus={e => (e.target.style.borderColor = "#f59e0b")}
          onBlur={e => (e.target.style.borderColor = "#2a2a2a")}
        />
      </div>
      <div className="flex gap-1">
        <button
          onClick={() => save(content)}
          className="flex-1 py-1 text-[11px] rounded font-semibold transition-colors"
          style={{ background: saved ? "#0a1f0f" : "#1c1408", color: saved ? "#4ade80" : "#f59e0b", border: `1px solid ${saved ? "#14532d" : "#78350f"}` }}
        >
          {saved ? "✓ Saved" : "Save"}
        </button>
        <button
          onClick={() => content.trim() ? setConfirmClear(true) : save("")}
          className="px-2 py-1 text-[11px] rounded transition-colors"
          style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}
        >Clear</button>
      </div>
      {modal && (
        <FullscreenMarkdownModal
          title="Human Directive"
          value={content}
          onSave={save}
          onClose={() => setModal(false)}
          accent="#f59e0b"
        />
      )}
      {confirmClear && createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setConfirmClear(false)}
        >
          <div
            className="max-w-sm w-full rounded p-4"
            style={{ background: "#181818", border: "1px solid #78350f", boxShadow: "0 8px 24px rgba(0,0,0,0.6)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "#f59e0b" }}>⚠</span>
              <span className="text-[12px] font-semibold" style={{ color: "#f59e0b" }}>Clear Human Directive?</span>
            </div>
            <p className="text-[12px] leading-relaxed mb-3" style={{ color: "#d1d5db" }}>
              This will erase your typed directive for this session. The directive
              is important — Start Loop / Run Once requires it. This can't be undone.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmClear(false)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "#181818", color: "#9ca3af", border: "1px solid #2a2a2a" }}
              >Cancel</button>
              <button
                onClick={() => { setConfirmClear(false); save(""); }}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "#1c1408", color: "#f59e0b", border: "1px solid #78350f" }}
              >Clear Directive</button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}

// ── Messages to Manager ───────────────────────────────────────────────────────
function MessagesPanel({ messages, onRefresh }: { messages: ManagerMessage[]; onRefresh: () => void }) {
  const [modal, setModal]   = useState(false);
  const [draft, setDraft]   = useState("");

  const send = async (text: string) => {
    if (!text.trim()) return;
    await apiPost("/api/manager-messages", { content: text });
    setDraft(""); onRefresh();
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#60a5fa" }}>
          Messages to Manager
        </p>
        <button
          onClick={() => setModal(true)}
          className="text-[11px] px-1.5 py-0.5 rounded transition-colors"
          style={{ color: "#4b5563", border: "1px solid #2a2a2a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#60a5fa")}
          onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
        >⤢</button>
      </div>
      <div className="space-y-1 max-h-24 overflow-y-auto">
        {messages.length === 0
          ? <p className="text-[11px] italic" style={{ color: "#4b5563" }}>No messages</p>
          : messages.slice(-5).map(m => (
            <div key={m.id} className="text-[11px] rounded p-1.5 whitespace-pre-wrap" style={{ background: "#181818", color: "#9ca3af", border: "1px solid #1f1f1f" }}>
              {m.content}
            </div>
          ))
        }
      </div>
      <div className="flex gap-1">
        <input
          className="flex-1 rounded px-2 py-1 text-[12px] outline-none"
          style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#f0ede8" }}
          placeholder="Quick message..."
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => e.key === "Enter" && send(draft)}
          onFocus={e => (e.target.style.borderColor = "#60a5fa")}
          onBlur={e => (e.target.style.borderColor = "#2a2a2a")}
        />
        <button onClick={() => send(draft)} className="px-2 py-1 text-[11px] rounded" style={{ background: "#181818", color: "#60a5fa", border: "1px solid #1e3a5f" }}>
          Send
        </button>
      </div>
      {modal && (
        <FullscreenMarkdownModal
          title="Message to Manager"
          value=""
          onSave={send}
          onClose={() => setModal(false)}
          accent="#60a5fa"
        />
      )}
    </div>
  );
}

// ── Suggestion Queue ──────────────────────────────────────────────────────────
function ChatPanel() {
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
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    const refreshStatus = () => {
      apiGet<{enabled: boolean; reason?: string}>("/api/chat/status")
        .then(data => {
          setChatEnabled(!!data.enabled);
          setChatStatus(data.enabled ? "Chat runtime ready" : (data.reason ?? "Chat runtime unavailable"));
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
    setMessages(prev => [
      ...prev,
      { id: Date.now(), session_id: "", sender: "user", content: text, created_at: new Date().toISOString() },
    ]);
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
      load();
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#22c55e" }}>Chat Agent</p>
        {sending && <span className="text-[10px] animate-pulse" style={{ color: "#22c55e" }}>thinking...</span>}
      </div>
      <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
        {messages.length === 0 ? (
          <p className="text-[11px] italic" style={{ color: "#4b5563" }}>No chat yet</p>
        ) : messages.slice(-12).map((m, idx) => {
          const mine = m.sender === "user";
          return (
            <div
              key={`${m.id}-${idx}`}
              className="text-[11px] rounded p-2 whitespace-pre-wrap"
              style={{
                background: mine ? "#0a1620" : "#111b13",
                color: mine ? "#bfdbfe" : "#d9f99d",
                border: mine ? "1px solid #1e3a5f" : "1px solid #14532d",
              }}
            >
              <div className="text-[9px] uppercase mb-1" style={{ color: mine ? "#60a5fa" : "#4ade80" }}>
                {mine ? "You" : "Chat"}
              </div>
              {m.content}
            </div>
          );
        })}
      </div>
      {error && <p className="text-[11px]" style={{ color: "#f87171" }}>{error}</p>}
      <div className="flex gap-1">
<textarea
          className="flex-1 rounded px-2 py-1 text-[12px] outline-none resize-none"
          rows={2}
          style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#f0ede8" }}
          placeholder={chatEnabled ? "Ask the chat agent..." : "Bind Chat to an OpenCode server in Runtime"}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          onFocus={e => (e.target.style.borderColor = "#22c55e")}
          onBlur={e => (e.target.style.borderColor = "#2a2a2a")}
        />
        <button
          onClick={send}
          disabled={sending || !draft.trim() || !chatEnabled}
          className="px-2 py-1 text-[11px] rounded disabled:opacity-40"
          style={{ background: "#0a1f0f", color: "#4ade80", border: "1px solid #14532d" }}
        >
          Send
        </button>
      </div>
      {!chatEnabled && (
        <p className="text-[10px] mt-1" style={{ color: "#6b7280" }}>
          {chatStatus}
        </p>
      )}
    </div>
  );
}

const SC: Record<string, { bg: string; text: string; border: string }> = {
  pending:    { bg: "#1c1408", text: "#f59e0b", border: "#78350f" },
  released:   { bg: "#080f1c", text: "#60a5fa", border: "#1e3a5f" },
  worker_done:{ bg: "#12091f", text: "#a78bfa", border: "#4c1d95" },
  done:       { bg: "#0a1f0f", text: "#22c55e", border: "#14532d" },
  paused:     { bg: "#181818", text: "#6b7280", border: "#2a2a2a" },
};

function SuggestionPanel({ suggestion, onAction }: { suggestion: Suggestion | null; onAction: () => void }) {
  const [modal, setModal]   = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft]   = useState("");

  const act = async (action: string) => {
    await apiPost(`/api/suggestion/${action}`, suggestion?.id ? { id: suggestion.id } : {});
    onAction();
  };

  const saveNew = async (text: string) => {
    if (!text.trim()) return;
    await apiPost("/api/suggestion/new", { content: text });
    setEditing(false); onAction();
  };

  const sc = SC[suggestion?.status ?? ""] ?? SC.paused;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#a78bfa" }}>
          Suggestion Queue
        </p>
        {suggestion?.content && (
          <button
            onClick={() => setModal(true)}
            className="text-[11px] px-1.5 py-0.5 rounded transition-colors"
            style={{ color: "#4b5563", border: "1px solid #2a2a2a" }}
            onMouseEnter={e => (e.currentTarget.style.color = "#a78bfa")}
            onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
          >⤢</button>
        )}
      </div>

      {suggestion?.content ? (
        <div className="space-y-2">
          <div className="text-[12px] rounded p-2 max-h-24 overflow-y-auto whitespace-pre-wrap" style={{ background: "#181818", color: "#e5e0d8", border: "1px solid #2a2a2a" }}>
            {suggestion.content}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold" style={{ background: sc.bg, color: sc.text, border: `1px solid ${sc.border}` }}>
              {suggestion.status}
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {suggestion.status !== "released" && suggestion.status !== "done" && (
              <button onClick={() => act("release")} className="px-2 py-1 text-[11px] rounded font-semibold" style={{ background: "#080f1c", color: "#60a5fa", border: "1px solid #1e3a5f" }}>▶ Release</button>
            )}
            <button onClick={() => act("pause")} className="px-2 py-1 text-[11px] rounded" style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}>Pause</button>
            <button onClick={() => act("done")} className="px-2 py-1 text-[11px] rounded" style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}>Done</button>
          </div>
        </div>
      ) : (
        <p className="text-[11px] italic" style={{ color: "#4b5563" }}>No active suggestion</p>
      )}

      {editing ? (
        <div className="space-y-1">
          <textarea
            className="w-full rounded p-2 text-[12px] resize-none outline-none"
            style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#f0ede8" }}
            rows={3} value={draft} onChange={e => setDraft(e.target.value)} placeholder="New suggestion..."
          />
          <div className="flex gap-1">
            <button onClick={() => saveNew(draft)} className="px-2 py-1 text-[11px] rounded font-semibold" style={{ background: "#1c1408", color: "#f59e0b", border: "1px solid #78350f" }}>Save</button>
            <button onClick={() => setEditing(false)} className="px-2 py-1 text-[11px] rounded" style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="flex gap-1">
          <button onClick={() => setEditing(true)} className="flex-1 py-1 text-[11px] rounded" style={{ background: "#181818", color: "#6b7280", border: "1px solid #2a2a2a" }}>+ New suggestion</button>
          <button onClick={() => setModal(true)} className="px-2 py-1 text-[11px] rounded" title="Open editor" style={{ background: "#181818", color: "#4b5563", border: "1px solid #2a2a2a" }}>⤢</button>
        </div>
      )}

      {modal && (
        <FullscreenMarkdownModal
          title="Suggestion"
          value={suggestion?.content ?? ""}
          onSave={saveNew}
          onClose={() => setModal(false)}
          accent="#a78bfa"
        />
      )}
    </div>
  );
}

// ── Files panel ───────────────────────────────────────────────────────────────
const FILE_KEYS = [
  { key: "worker_report",    label: "Worker Report" },
  { key: "manager_feedback", label: "Manager Feedback" },
];

function FilesPanel({ clearKey = 0 }: { clearKey?: number }) {
  const [open, setOpen]         = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, string>>({});

  useEffect(() => {
    // Drop cache + reload currently-open file when clear is triggered
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setContents({});
    if (open) {
      apiGet<{ content: string }>(`/api/files/${open}`)
        .then(d => setContents({ [open]: d.content }))
        .catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearKey]);

  const load = async (key: string) => {
    if (open === key) { setOpen(null); return; }
    if (contents[key] === undefined) {
      const d = await apiGet<{ content: string }>(`/api/files/${key}`).catch(() => ({ content: "" }));
      setContents(prev => ({ ...prev, [key]: d.content }));
    }
    setOpen(key);
  };

  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#4b5563" }}>Files</p>
      <div className="space-y-1">
        {FILE_KEYS.map(({ key, label }) => (
          <div key={key}>
            <button onClick={() => load(key)} className="w-full text-left px-2 py-1.5 text-[12px] rounded flex justify-between items-center" style={{ background: open === key ? "#1f1f1f" : "#181818", color: "#9ca3af", border: `1px solid ${open === key ? "#2a2a2a" : "#1f1f1f"}` }}>
              <span>{label}</span>
              <span className="text-[10px]" style={{ color: "#4b5563" }}>{open === key ? "▲" : "▼"}</span>
            </button>
            {open === key && (
              <pre className="mt-1 p-2 rounded text-[11px] max-h-36 overflow-y-auto whitespace-pre-wrap break-words" style={{ background: "#0d0d0d", color: "#9ca3af", border: "1px solid #1f1f1f" }}>
                {contents[key] || "(empty)"}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Handoff rendering helpers ─────────────────────────────────────────────────

const CODE_FENCE_RE = /^```[\s\S]*?^```/m;
const INLINE_CODE_RE = /`[^`]+`/;

function looksLikeCode(s: string): boolean {
  return CODE_FENCE_RE.test(s) || INLINE_CODE_RE.test(s) || s.includes("\n    ") || s.startsWith("    ");
}

function looksLikeMarkdown(s: string): boolean {
  return /^#{1,6} /m.test(s) || /\*\*[^*]+\*\*/.test(s) || /^\s*[-*] /m.test(s) || /^\s*\d+\. /m.test(s);
}

function HandoffValue({ label, value, accent }: { label: string; value: unknown; accent: string }) {
  if (value === null || value === undefined || value === "") return null;

  const renderContent = () => {
    if (Array.isArray(value)) {
      const items = value.map(i => String(i)).filter(Boolean);
      if (!items.length) return null;
      return (
        <ul className="space-y-0.5 mt-1">
          {items.map((item, i) => (
            <li key={i} className="flex gap-1.5 text-[11px]" style={{ color: "#9ca3af" }}>
              <span className="shrink-0 mt-0.5" style={{ color: accent }}>•</span>
              <span className="whitespace-pre-wrap break-words">{item}</span>
            </li>
          ))}
        </ul>
      );
    }

    if (typeof value === "object") {
      const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== null && v !== "");
      if (!entries.length) return null;
      return (
        <table className="w-full mt-1 text-[10px] border-collapse">
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k} style={{ borderBottom: "1px solid #1f1f1f" }}>
                <td className="py-0.5 pr-2 font-medium align-top whitespace-nowrap" style={{ color: accent, width: "40%" }}>{k}</td>
                <td className="py-0.5 break-words whitespace-pre-wrap" style={{ color: "#9ca3af" }}>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }

    const s = String(value).trim();
    if (!s) return null;

    if (looksLikeCode(s)) {
      const inner = s.replace(/^```[a-z]*\n?/, "").replace(/\n?```$/, "");
      return (
        <pre
          className="mt-1 p-2 rounded text-[10px] overflow-x-auto whitespace-pre-wrap break-words"
          style={{ background: "#0d0d0d", color: "#a78bfa", border: "1px solid #4c1d95" }}
        >
          {inner}
        </pre>
      );
    }

    if (looksLikeMarkdown(s)) {
      const lines = s.split("\n");
      return (
        <div className="mt-1 space-y-0.5">
          {lines.map((line, i) => {
            const h = line.match(/^(#{1,6})\s+(.*)/);
            if (h) return (
              <p key={i} className="font-semibold text-[11px]" style={{ color: "#f0ede8", fontSize: h[1].length <= 2 ? 12 : 11 }}>
                {h[2]}
              </p>
            );
            const bullet = line.match(/^[\s]*[-*]\s+(.*)/);
            if (bullet) return (
              <div key={i} className="flex gap-1.5 text-[11px]" style={{ color: "#9ca3af" }}>
                <span className="shrink-0" style={{ color: accent }}>•</span>
                <span className="whitespace-pre-wrap break-words">{bullet[1]}</span>
              </div>
            );
            const num = line.match(/^[\s]*(\d+)\.\s+(.*)/);
            if (num) return (
              <div key={i} className="flex gap-1.5 text-[11px]" style={{ color: "#9ca3af" }}>
                <span className="shrink-0 font-mono" style={{ color: accent }}>{num[1]}.</span>
                <span className="whitespace-pre-wrap break-words">{num[2]}</span>
              </div>
            );
            if (!line.trim()) return <div key={i} className="h-1" />;
            return <p key={i} className="text-[11px] whitespace-pre-wrap break-words" style={{ color: "#9ca3af" }}>{line}</p>;
          })}
        </div>
      );
    }

    return <p className="mt-0.5 text-[11px] whitespace-pre-wrap break-words" style={{ color: "#9ca3af" }}>{s}</p>;
  };

  const content = renderContent();
  if (!content) return null;

  return (
    <div className="rounded p-2" style={{ background: "#0d0d0d", border: "1px solid #1f1f1f" }}>
      <p className="text-[9px] font-semibold uppercase tracking-widest mb-1" style={{ color: accent }}>{label}</p>
      {content}
    </div>
  );
}

const HANDOFF_SECTIONS = [
  { key: "current_task",        label: "Current Task",   accent: "#f59e0b" },
  { key: "human_requirements",  label: "Requirements",   accent: "#60a5fa" },
  { key: "working_direction",   label: "Direction",      accent: "#e5e0d8" },
  { key: "current_micro_flow",  label: "Micro Flow",     accent: "#9ca3af" },
  { key: "known_bugs",          label: "Known Bugs",     accent: "#ef4444" },
  { key: "completion_criteria", label: "Done When",      accent: "#22c55e" },
  { key: "human_concerns",      label: "Concerns",       accent: "#fbbf24" },
  { key: "macro_flow",          label: "Macro Flow",     accent: "#4b5563" },
];

function HandoffPanel({ clearKey = 0 }: { clearKey?: number }) {
  const [data, setData]   = useState<Record<string, unknown> | null>(null);
  const [raw, setRaw]     = useState<string>("");
  const [open, setOpen]   = useState(false);
  const [modal, setModal] = useState(false);

  useEffect(() => {
    if (clearKey === 0) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setData(null);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRaw("");
    if (open) {
      apiGet<Record<string, unknown>>("/api/handoff")
        .then(d => { setData(d); setRaw(d ? JSON.stringify(d, null, 2) : ""); })
        .catch(() => {});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearKey]);

  const load = async () => {
    if (open) { setOpen(false); return; }
    const d = await apiGet<Record<string, unknown>>("/api/handoff").catch(() => null);
    setData(d);
    setRaw(d ? JSON.stringify(d, null, 2) : "");
    setOpen(true);
  };

  const save = async (text: string) => {
    try {
      const parsed = JSON.parse(text);
      await apiPut("/api/handoff", parsed);
      setData(parsed);
      setRaw(text);
    } catch { /* ignore parse errors */ }
  };

  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "#4b5563" }}>Handoff</p>
      <div className="flex gap-1">
        <button
          onClick={load}
          className="flex-1 text-left px-2 py-1.5 text-[12px] rounded flex justify-between"
          style={{ background: "#181818", color: "#9ca3af", border: "1px solid #2a2a2a" }}
        >
          <span>View latest handoff</span>
          <span className="text-[10px]" style={{ color: "#4b5563" }}>{open ? "▲" : "▼"}</span>
        </button>
        <button
          onClick={async () => { if (!open) await load(); setModal(true); }}
          className="px-2 py-1.5 text-[11px] rounded"
          title="Edit as JSON"
          style={{ background: "#181818", color: "#4b5563", border: "1px solid #2a2a2a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#f59e0b")}
          onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
        >⤢</button>
      </div>

      {open && data && (
        <div className="space-y-1.5 max-h-[28rem] overflow-y-auto pr-0.5">
          {!!data.updated_by && (
            <p className="text-[9px]" style={{ color: "#4b5563" }}>
              by {String(data.updated_by)} · v{String(data.version ?? "?")}
            </p>
          )}
          {HANDOFF_SECTIONS.map(({ key, label, accent }) => (
            <HandoffValue key={key} label={label} value={data[key]} accent={accent} />
          ))}
          {Object.keys(data)
            .filter(k => !HANDOFF_SECTIONS.some(s => s.key === k) && !["updated_by","version","updated_at","id"].includes(k))
            .map(k => (
              <HandoffValue key={k} label={k} value={data[k]} accent="#6b7280" />
            ))
          }
        </div>
      )}

      {open && !data && (
        <p className="text-[11px] italic" style={{ color: "#4b5563" }}>(empty)</p>
      )}

      {modal && (
        <FullscreenMarkdownModal
          title="Handoff (JSON)"
          value={raw}
          onSave={save}
          onClose={() => setModal(false)}
          accent="#f59e0b"
        />
      )}
    </div>
  );
}

// ── Main RightRail ────────────────────────────────────────────────────────────
interface Props {
  suggestion: Suggestion | null;
  messages: ManagerMessage[];
  onSuggestionAction: () => void;
  onMessagesRefresh: () => void;
  directiveClearKey?: number;
}

export function RightRail({ suggestion, messages, onSuggestionAction, onMessagesRefresh, directiveClearKey = 0 }: Props) {
  return (
    <aside
      className="w-72 shrink-0 flex flex-col min-h-0"
      style={{ background: "#111111", borderLeft: "1px solid #2a2a2a" }}
    >
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 space-y-4" style={{ borderBottom: "1px solid #1f1f1f" }}>
          <HumanDirectivePanel clearKey={directiveClearKey} />
          <div style={{ borderTop: "1px solid #1f1f1f" }} className="pt-3">
            <ChatPanel />
          </div>
          <div style={{ borderTop: "1px solid #1f1f1f" }} className="pt-3">
            <MessagesPanel messages={messages} onRefresh={onMessagesRefresh} />
          </div>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-[9px] font-bold uppercase tracking-widest" style={{ color: "#4b5563" }}>System Status</p>
          <SuggestionPanel suggestion={suggestion} onAction={onSuggestionAction} />
          <div style={{ borderTop: "1px solid #1f1f1f" }} className="pt-3">
            <FilesPanel clearKey={directiveClearKey} />
          </div>
          <div style={{ borderTop: "1px solid #1f1f1f" }} className="pt-3">
            <HandoffPanel clearKey={directiveClearKey} />
          </div>
          <div style={{ borderTop: "1px solid #1f1f1f" }} className="pt-3">
            <RuntimePanel />
          </div>
        </div>
      </div>
    </aside>
  );
}
