import { useEffect, useRef, useState } from "react";
import { Check, MessageSquare, Send, X } from "lucide-react";
import { apiGet, apiPost } from "../../../lib/api";

interface ManagerChatMessage {
  id: number;
  response_id?: string | null;
  sender: "human" | "manager";
  content: string;
  created_at: string;
}

interface ManagerAmendment {
  id: string;
  response_id: string;
  amendment_type: "todo-amendment" | "user-directive-amend" | "handoff-amend";
  title: string;
  description: string;
  status: "proposed" | "applied" | "rejected";
}

interface ManagerChatData {
  messages: ManagerChatMessage[];
  amendments: ManagerAmendment[];
}

const EMPTY: ManagerChatData = { messages: [], amendments: [] };

export function ManagerChatPanel({ onApplied }: { onApplied?: () => void }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ManagerChatData>(EMPTY);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);
  const [waitNotice, setWaitNotice] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    const next = await apiGet<ManagerChatData>("/api/manager-chat").catch(() => EMPTY);
    setData(next);
  };

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [data.messages, waiting]);

  useEffect(() => {
    const openFromRound = (event: Event) => {
      const detail = (event as CustomEvent<{ prompt?: string }>).detail;
      setOpen(true);
      setDraft(detail?.prompt || "");
      void load();
    };
    window.addEventListener("task-hounds-open-manager-chat", openFromRound);
    return () => window.removeEventListener("task-hounds-open-manager-chat", openFromRound);
  }, []);

  const send = async () => {
    const content = draft.trim();
    if (!content || waiting) return;
    setDraft("");
    setError("");
    setWaiting(true);
    setWaitNotice(true);
    setData(current => ({
      ...current,
      messages: [...current.messages, {
        id: Date.now(),
        sender: "human",
        content,
        created_at: new Date().toISOString(),
      }],
    }));
    try {
      const result = await apiPost<ManagerChatData & { ok: boolean; error?: string; response_id: string; reply: string }>(
        "/api/manager-chat/send",
        {
          content,
          conversation: [...data.messages, {
            id: Date.now(),
            sender: "human" as const,
            content,
            created_at: new Date().toISOString(),
          }].slice(-20).map(message => ({
            role: message.sender,
            content: message.content,
          })),
        },
      );
      if (!result.ok) throw new Error(result.error || "Manager did not respond");
      setData(current => ({
        messages: [...current.messages, {
          id: Date.now() + 1,
          response_id: result.response_id,
          sender: "manager",
          content: result.reply,
          created_at: new Date().toISOString(),
        }],
        amendments: result.amendments,
      }));
      setWaitNotice(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setWaiting(false);
    }
  };

  const confirm = async () => {
    if (!selected.length) return;
    setError("");
    try {
      const result = await apiPost<{ ok: boolean; amendments: ManagerAmendment[] }>(
        "/api/manager-chat/confirm",
        { amendment_ids: selected },
      );
      setData(current => ({ ...current, amendments: result.amendments }));
      setSelected([]);
      onApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const proposed = data.amendments.filter(item => item.status === "proposed");

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          void load();
        }}
        className="w-full flex items-center justify-center gap-2 rounded px-3 py-2 text-[12px] font-medium"
        style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}
      >
        <MessageSquare size={14} />
        Manager Chat
      </button>

      {open && (
        <div className="fixed inset-0 z-[90] flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,.58)" }}>
          <div className="w-[min(1080px,96vw)] h-[min(720px,90vh)] flex flex-col rounded-lg overflow-hidden" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
            <header className="h-12 shrink-0 flex items-center justify-between px-4" style={{ borderBottom: "1px solid var(--border)" }}>
              <div className="flex items-center gap-2">
                <MessageSquare size={16} style={{ color: "var(--blue)" }} />
                <div>
                  <h2 className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>Manager Chat</h2>
                  <p className="text-[10px]" style={{ color: "var(--text-dim)" }}>Project-aware advice and confirmed amendments</p>
                </div>
              </div>
              <button type="button" onClick={() => setOpen(false)} title="Close Manager Chat" className="p-1 rounded" style={{ color: "var(--text-secondary)" }}>
                <X size={17} />
              </button>
            </header>

            <div className="flex flex-1 min-h-0">
              <section className="flex-1 min-w-0 flex flex-col">
                <div className="flex-1 overflow-y-auto p-4 space-y-3">
                  {data.messages.length === 0 && (
                    <div className="max-w-xl rounded p-3 text-[12px] leading-relaxed" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
                      <p className="font-semibold mb-1" style={{ color: "var(--text-primary)" }}>What Manager Chat can do</p>
                      <p>Ask about project progress, work assignments, failed reviews, or what needs your attention. Manager can propose changes to the User Directive, Todo List, or Handoff. Nothing is changed until you select an amendment and confirm it.</p>
                    </div>
                  )}
                  {data.messages.map(message => (
                    <div key={`${message.sender}-${message.id}`} className={`flex ${message.sender === "human" ? "justify-end" : "justify-start"}`}>
                      <div className="max-w-[78%] rounded-lg px-3 py-2 text-[12px] whitespace-pre-wrap break-words" style={message.sender === "human"
                        ? { background: "var(--bubble-user-bg)", color: "var(--bubble-user-text)" }
                        : { background: "var(--bubble-agent-bg)", color: "var(--bubble-agent-text)", border: "1px solid var(--bubble-agent-border)" }}>
                        {message.sender === "manager" && <div className="text-[10px] font-semibold mb-1" style={{ color: "var(--bubble-agent-name)" }}>Manager</div>}
                        {message.content}
                      </div>
                    </div>
                  ))}
                  {waiting && <p className="text-[11px]" style={{ color: "var(--text-dim)" }}>Manager is reviewing the project...</p>}
                  <div ref={endRef} />
                </div>
                {error && <p className="px-4 pb-2 text-[11px]" style={{ color: "var(--red)" }}>{error}</p>}
                <div className="p-3 flex gap-2" style={{ borderTop: "1px solid var(--border)" }}>
                  <textarea
                    value={draft}
                    onChange={event => setDraft(event.target.value)}
                    onKeyDown={event => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void send();
                      }
                    }}
                    placeholder="Ask the Manager about this project..."
                    rows={2}
                    className="flex-1 resize-none rounded p-2 text-[12px] outline-none"
                    style={{ background: "var(--bg-panel)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
                  />
                  <button type="button" onClick={send} disabled={waiting || !draft.trim()} title="Send to Manager" className="w-10 rounded flex items-center justify-center disabled:opacity-40" style={{ background: "var(--blue)", color: "#fff" }}>
                    <Send size={15} />
                  </button>
                </div>
              </section>

              <aside className="w-80 shrink-0 flex flex-col" style={{ background: "var(--bg-panel)", borderLeft: "1px solid var(--border)" }}>
                <div className="p-3" style={{ borderBottom: "1px solid var(--border)" }}>
                  <h3 className="text-[12px] font-semibold" style={{ color: "var(--text-primary)" }}>Manager amendments</h3>
                  <p className="text-[10px] mt-1" style={{ color: "var(--text-dim)" }}>Select only the changes you want Manager to apply.</p>
                </div>
                <div className="flex-1 overflow-y-auto p-3 space-y-2">
                  {proposed.length === 0 && <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No amendments awaiting confirmation</p>}
                  {proposed.map(item => {
                    const checked = selected.includes(item.id);
                    return (
                      <label key={item.id} className="block rounded p-2 cursor-pointer" style={{ background: "var(--bg-base)", border: `1px solid ${checked ? "var(--blue)" : "var(--border)"}` }}>
                        <div className="flex gap-2">
                          <input type="checkbox" checked={checked} onChange={() => setSelected(values => checked ? values.filter(id => id !== item.id) : [...values, item.id])} />
                          <div className="min-w-0">
                            <div className="text-[9px] uppercase font-semibold" style={{ color: "var(--blue)" }}>{item.amendment_type}</div>
                            <div className="text-[11px] font-medium mt-0.5" style={{ color: "var(--text-primary)" }}>{item.title}</div>
                            {item.description && <p className="text-[10px] mt-1" style={{ color: "var(--text-secondary)" }}>{item.description}</p>}
                          </div>
                        </div>
                      </label>
                    );
                  })}
                </div>
                <div className="p-3" style={{ borderTop: "1px solid var(--border)" }}>
                  <button type="button" onClick={confirm} disabled={!selected.length} className="w-full flex items-center justify-center gap-2 rounded px-3 py-2 text-[11px] font-semibold disabled:opacity-40" style={{ background: "var(--green)", color: "#fff" }}>
                    <Check size={14} />
                    Confirm selected
                  </button>
                </div>
              </aside>
            </div>
          </div>

          {waitNotice && (
            <div className="fixed right-5 top-5 z-[100] max-w-sm rounded p-3 pr-10 text-[12px]" style={{ background: "var(--bg-panel)", border: "1px solid var(--blue-dim)", color: "var(--text-primary)", boxShadow: "0 12px 30px rgba(0,0,0,.28)" }}>
              <button type="button" onClick={() => setWaitNotice(false)} title="Dismiss" className="absolute right-2 top-2" style={{ color: "var(--text-dim)" }}><X size={14} /></button>
              Message sent to Manager. Please wait for a response...
            </div>
          )}
        </div>
      )}
    </>
  );
}
