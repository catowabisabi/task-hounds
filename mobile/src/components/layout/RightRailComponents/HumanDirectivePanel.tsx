import { useState, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPut } from "../../../lib/api";
import { FullscreenMarkdownModal } from "./FullscreenMarkdownModal";

interface HumanDirectivePanelProps {
  clearKey?: number;
}

export function HumanDirectivePanel({ clearKey = 0 }: HumanDirectivePanelProps) {
  const [content, setContent] = useState("");
  const [saved, setSaved]     = useState(false);
  const [modal, setModal]     = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(() => {
    apiGet<{ content: string }>("/api/files/user_input")
      .then(d => setContent(d.content))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [clearKey, load]);

  useEffect(() => {
    const onDirectiveUpdated = () => load();
    window.addEventListener("task-hounds-directive-updated", onDirectiveUpdated);
    return () => window.removeEventListener("task-hounds-directive-updated", onDirectiveUpdated);
  }, [load]);

  const save = async (val: string) => {
    await apiPut("/api/files/user_input", { content: val });
    setContent(val);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-bold uppercase tracking-wide" style={{ color: "var(--amber)" }}>Human Directive</p>
        <button onClick={() => setModal(true)} title="Open full editor" className="text-[11px] px-1.5 py-0.5 rounded transition-colors" style={{ color: "var(--text-dim)", border: "1px solid var(--border)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--amber)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>⤢</button>
      </div>
      <div className="relative">
        <textarea className="w-full rounded p-2 text-[12px] resize-none outline-none transition-colors" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-primary)" }} rows={4} value={content} onChange={e => setContent(e.target.value)} placeholder="Write a directive for this project..." onFocus={e => (e.target.style.borderColor = "var(--amber)")} onBlur={e => (e.target.style.borderColor = "var(--border)")} />
      </div>
      <div className="flex gap-1">
        <button onClick={() => save(content)} className="flex-1 py-1 text-[11px] rounded font-semibold transition-colors" style={{ background: saved ? "var(--green-bg)" : "var(--amber-bg)", color: saved ? "var(--green)" : "var(--amber)", border: `1px solid ${saved ? "var(--green-dim)" : "var(--amber-dim)"}` }}>{saved ? "✓ Saved" : "Save"}</button>
        <button onClick={() => content.trim() ? setConfirmClear(true) : save("")} className="px-2 py-1 text-[11px] rounded transition-colors" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Clear</button>
      </div>
      {modal && <FullscreenMarkdownModal title="Human Directive" value={content} onSave={save} onClose={() => setModal(false)} accent="var(--amber)" />}
      {confirmClear && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center px-6" style={{ background: "rgba(0,0,0,0.65)" }} onClick={() => setConfirmClear(false)}>
          <div className="max-w-sm w-full rounded p-4" style={{ background: "var(--bg-panel)", border: "1px solid var(--amber-dim)", boxShadow: "0 8px 24px rgba(0,0,0,0.6)" }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--amber)" }}>⚠</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--amber)" }}>Clear Human Directive?</span>
            </div>
            <p className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-primary)" }}>This will erase your typed directive for this session. The directive is important — Start Loop / Run Once requires it. This can't be undone.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmClear(false)} className="px-3 py-1 rounded text-[11px] font-medium" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
              <button onClick={() => { setConfirmClear(false); save(""); }} className="px-3 py-1 rounded text-[11px] font-medium" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>Clear Directive</button>
            </div>
          </div>
        </div>,
        document.body
      )}
</div>
  );
}
