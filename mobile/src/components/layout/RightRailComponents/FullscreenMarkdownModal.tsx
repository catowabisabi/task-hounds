import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";

interface FullscreenMarkdownModalProps {
  title: string;
  value: string;
  onSave: (v: string) => Promise<void>;
  onClose: () => void;
  accent?: string;
}

export function FullscreenMarkdownModal({ title, value, onSave, onClose, accent = "var(--amber)" }: FullscreenMarkdownModalProps) {
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
    saveTimerRef.current = setTimeout(() => { persistNow(draft); }, 1000);
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
    .replace(/&/g, "&")
    .replace(/</g, "<")
    .replace(/>/g, ">")
    .replace(/^# (.+)$/gm, `<div class="text-[14px] font-bold mt-2 mb-1" style="color:var(--text-primary)">$1</div>`)
    .replace(/^## (.+)$/gm, `<div class="text-[13px] font-semibold mt-1.5 mb-0.5" style="color:var(--amber)">$1</div>`)
    .replace(/^### (.+)$/gm, `<div class="text-[12px] font-medium mt-1 mb-0.5" style="color:var(--purple)">$1</div>`)
    .replace(/\*\*([^*]+)\*\*/g, `<strong style="color:var(--text-primary)">$1</strong>`)
    .replace(/`([^`]+)`/g, `<code class="px-1 rounded text-[11px]" style="background:var(--bg-raised);color:var(--purple)">$1</code>`)
    .replace(/\n/g, '<br />');

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "rgba(0,0,0,0.90)" }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="flex items-center justify-between px-4 py-2 shrink-0" style={{ borderBottom: `1px solid ${accent}33`, background: "var(--bg-base)" }}>
        <span className="text-[12px] font-semibold" style={{ color: accent }}>{title}</span>
        <div className="flex items-center gap-2">
          {saving && <span className="text-[10px] animate-pulse" style={{ color: "var(--green)" }}>saving…</span>}
          <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>Cmd+Enter to save</span>
          <button onClick={onClose} className="text-lg leading-none transition-colors duration-200" style={{ color: "var(--text-dim)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--amber)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>×</button>
        </div>
      </div>
      <div className="flex flex-1 min-h-0">
        <textarea className="flex-1 p-4 font-mono text-[16px] resize-none outline-none bg-transparent" style={{ color: "var(--text-primary)", borderRight: "1px solid var(--border)" }} value={draft} onChange={e => setDraft(e.target.value)} placeholder="Write in Markdown..." autoFocus />
        <div className="flex-1 p-4 overflow-y-auto text-[13px] leading-relaxed" style={{ color: "var(--text-secondary)", background: "var(--bg-base)" }} dangerouslySetInnerHTML={{ __html: previewHtml }} />
      </div>
      <div className="flex gap-2 justify-end px-4 py-3 shrink-0" style={{ borderTop: `1px solid ${accent}22`, background: "var(--bg-base)" }}>
        <button onClick={onClose} className="px-3 py-1.5 text-[12px] rounded transition-colors duration-200" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
        <button onClick={async () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); await persistNow(draft); onClose(); }} disabled={saving} className="px-4 py-1.5 text-[12px] font-semibold rounded disabled:opacity-50 transition-colors duration-200" style={{ background: accent === "var(--amber)" ? "var(--amber-bg)" : "var(--blue-bg)", color: accent, border: `1px solid ${accent}66` }}>{saving ? "Saving…" : "Save"}</button>
      </div>
    </div>,
    document.body
  );
}
