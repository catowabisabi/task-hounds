import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost } from "../../../lib/api";
import type { Suggestion } from "../../../lib/api";
import { Tooltip } from "../../ui/Tooltip";
import { FullscreenMarkdownModal } from "./FullscreenMarkdownModal";

const SC: Record<string, { bg: string; text: string; border: string }> = {
  pending:    { bg: "var(--amber-bg)", text: "var(--amber)", border: "var(--amber-dim)" },
  released:   { bg: "var(--blue-bg)", text: "var(--blue)", border: "var(--blue-dim)" },
  worker_done:{ bg: "var(--purple-bg)", text: "var(--purple)", border: "var(--purple-dim)" },
  done:       { bg: "var(--green-bg)", text: "var(--green)", border: "var(--green-dim)" },
  paused:     { bg: "var(--bg-panel)", text: "var(--text-secondary)", border: "var(--border)" },
};

interface SuggestionPanelProps {
  suggestion: Suggestion | null;
  onAction: () => void;
}

export function SuggestionPanel({ suggestion, onAction }: SuggestionPanelProps) {
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState("");
  const [unscoped, setUnscoped] = useState<Suggestion[]>([]);

  const fetchUnscoped = useCallback(async () => {
    const rows = await apiGet<Suggestion[]>("/api/suggestions/unscoped").catch(() => []);
    setUnscoped(rows);
  }, []);

  useEffect(() => {
    fetchUnscoped();
  }, [fetchUnscoped, suggestion?.id]);

  const act = async (action: string, targetId?: number) => {
    setError("");
    setSaved("");
    try {
      const id = targetId ?? suggestion?.id;
      await apiPost(`/api/suggestion/${action}`, id ? { id } : {});
      onAction();
      fetchUnscoped();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const saveNew = async (text: string) => {
    if (!text.trim()) return;
    setSaving(true);
    setError("");
    setSaved("");
    try {
      await apiPost("/api/suggestion/new", { content: text });
      setDraft("");
      setEditing(false);
      setSaved("Queued for manager analysis");
      onAction();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const sc = SC[suggestion?.status ?? ""] ?? SC.paused;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--purple)" }}>Suggestion Queue</p>
          <Tooltip label="New suggestions start as queued for Manager. Manager turns them into worker tasks, Worker reports back, then Manager reviews and marks them processed." />
        </div>
        {suggestion?.content && (
          <button onClick={() => setModal(true)} className="text-[11px] px-1.5 py-0.5 rounded transition-colors" style={{ color: "var(--text-dim)", border: "1px solid var(--border)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--purple)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>⤢</button>
        )}
      </div>

      {suggestion?.content ? (
        <div className="space-y-2">
          <div className="text-[12px] rounded p-2 max-h-24 overflow-y-auto whitespace-pre-wrap" style={{ background: "var(--bg-panel)", color: "var(--text-primary)", border: "1px solid var(--border)" }}>{suggestion.content}</div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold" style={{ background: sc.bg, color: sc.text, border: `1px solid ${sc.border}` }}>{suggestion.status_label ?? suggestion.status}</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {suggestion.status !== "released" && suggestion.status !== "done" && (
              <button onClick={() => act("release")} className="px-2 py-1 text-[11px] rounded font-semibold" style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}>▶ Release</button>
            )}
            <button onClick={() => act("pause")} className="px-2 py-1 text-[11px] rounded" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Pause</button>
            <button onClick={() => act("done")} className="px-2 py-1 text-[11px] rounded" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Done</button>
          </div>
        </div>
      ) : (
        <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No active suggestion</p>
      )}

      {unscoped.length > 0 && (
        <div className="space-y-1 pt-1" style={{ borderTop: "1px solid var(--border)" }}>
          <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
            Unscoped Active Suggestions
          </p>
          <p className="text-[10px] leading-snug" style={{ color: "var(--amber)" }}>
            Historical unscoped rows are shown only for cleanup; they are not part of the active project queue.
          </p>
          {unscoped.slice(0, 3).map(item => {
            const status = item.status ?? "released";
            const itemSc = SC[status] ?? SC.paused;
            return (
              <div key={item.id} className="rounded p-2 space-y-1" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] max-h-14 overflow-y-auto whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>
                  {item.content}
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: itemSc.bg, color: itemSc.text, border: `1px solid ${itemSc.border}` }}>{item.status_label ?? status}</span>
                  <button onClick={() => act("done", item.id)} className="ml-auto px-1.5 py-0.5 text-[10px] rounded" style={{ background: "var(--bg-base)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Done</button>
                  {status !== "released" && (
                    <button onClick={() => act("release", item.id)} className="px-1.5 py-0.5 text-[10px] rounded" style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}>Release</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {error && <p className="text-[10px]" style={{ color: "var(--red)" }}>{error}</p>}
      {saved && <p className="text-[10px]" style={{ color: "var(--green)" }}>{saved}. Press Start Loop to get a manager response.</p>}

      {editing ? (
        <div className="space-y-1">
          <textarea className="w-full rounded p-2 text-[12px] resize-none outline-none" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-primary)" }} rows={3} value={draft} onChange={e => setDraft(e.target.value)} placeholder="New suggestion..." />
          <div className="flex gap-1">
            <button onClick={() => saveNew(draft)} disabled={saving || !draft.trim()} className="px-2 py-1 text-[11px] rounded font-semibold disabled:opacity-40" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>{saving ? "..." : "Save"}</button>
            <button onClick={() => setEditing(false)} className="px-2 py-1 text-[11px] rounded" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="flex gap-1">
          <button onClick={() => setEditing(true)} className="flex-1 py-1 text-[11px] rounded" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>+ New suggestion</button>
          <button onClick={() => setModal(true)} className="px-2 py-1 text-[11px] rounded" title="Open editor" style={{ background: "var(--bg-panel)", color: "var(--text-dim)", border: "1px solid var(--border)" }}>⤢</button>
        </div>
      )}

      {modal && <FullscreenMarkdownModal title="Suggestion" value={suggestion?.content ?? ""} onSave={saveNew} onClose={() => setModal(false)} accent="var(--purple)" />}
    </div>
  );
}
