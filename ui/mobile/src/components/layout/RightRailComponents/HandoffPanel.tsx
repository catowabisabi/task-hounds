import { useState, useEffect } from "react";
import { apiGet, apiPut } from "../../../lib/api";
import { FullscreenMarkdownModal } from "./FullscreenMarkdownModal";

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
            <li key={i} className="flex gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
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
              <tr key={k} style={{ borderBottom: "1px solid var(--border-dim)" }}>
                <td className="py-0.5 pr-2 font-medium align-top whitespace-nowrap" style={{ color: accent, width: "40%" }}>{k}</td>
                <td className="py-0.5 break-words whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{String(v)}</td>
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
      return <pre className="mt-1 p-2 rounded text-[10px] overflow-x-auto whitespace-pre-wrap break-words" style={{ background: "var(--bg-base)", color: "var(--purple)", border: "1px solid var(--purple-dim)" }}>{inner}</pre>;
    }

    if (looksLikeMarkdown(s)) {
      const lines = s.split("\n");
      return (
        <div className="mt-1 space-y-0.5">
          {lines.map((line, i) => {
            const h = line.match(/^(#{1,6})\s+(.*)/);
            if (h) return <p key={i} className="font-semibold text-[11px]" style={{ color: "var(--text-primary)", fontSize: h[1].length <= 2 ? 12 : 11 }}>{h[2]}</p>;
            const bullet = line.match(/^[\s]*[-*]\s+(.*)/);
            if (bullet) return <div key={i} className="flex gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}><span className="shrink-0" style={{ color: accent }}>•</span><span className="whitespace-pre-wrap break-words">{bullet[1]}</span></div>;
            const num = line.match(/^[\s]*(\d+)\.\s+(.*)/);
            if (num) return <div key={i} className="flex gap-1.5 text-[11px]" style={{ color: "var(--text-secondary)" }}><span className="shrink-0 font-mono" style={{ color: accent }}>{num[1]}.</span><span className="whitespace-pre-wrap break-words">{num[2]}</span></div>;
            if (!line.trim()) return <div key={i} className="h-1" />;
            return <p key={i} className="text-[11px] whitespace-pre-wrap break-words" style={{ color: "var(--text-secondary)" }}>{line}</p>;
          })}
        </div>
      );
    }

    return <p className="mt-0.5 text-[11px] whitespace-pre-wrap break-words" style={{ color: "var(--text-secondary)" }}>{s}</p>;
  };

  const content = renderContent();
  if (!content) return null;

  return (
    <div className="rounded p-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
      <p className="text-[9px] font-semibold uppercase tracking-widest mb-1" style={{ color: accent }}>{label}</p>
      {content}
    </div>
  );
}

const HANDOFF_SECTIONS = [
  { key: "current_task",        label: "Current Task",   accent: "var(--amber)" },
  { key: "human_requirements",  label: "Requirements",   accent: "var(--blue)" },
  { key: "working_direction",   label: "Direction",      accent: "var(--text-primary)" },
  { key: "current_micro_flow",  label: "Micro Flow",     accent: "var(--text-secondary)" },
  { key: "known_bugs",          label: "Known Bugs",     accent: "var(--red)" },
  { key: "completion_criteria", label: "Done When",      accent: "var(--green)" },
  { key: "human_concerns",      label: "Concerns",       accent: "var(--amber)" },
  { key: "tested_files",        label: "Tested Files",   accent: "var(--green)" },
  { key: "macro_flow",          label: "Macro Flow",     accent: "var(--text-dim)" },
  { key: "important_files",     label: "Important Files", accent: "var(--blue)" },
  { key: "available_scripts",   label: "Available Scripts", accent: "var(--green)" },
  { key: "existing_solutions",  label: "Existing Solutions", accent: "var(--text-secondary)" },
  { key: "references_demos",    label: "References",     accent: "var(--purple)" },
  { key: "file_structure",      label: "File Structure", accent: "var(--text-secondary)" },
  { key: "project_folder_location", label: "Project Folder", accent: "var(--text-dim)" },
];

interface HandoffPanelProps {
  clearKey?: number;
  apiPrefix?: string;
}

export function HandoffPanel({ clearKey = 0, apiPrefix = "/api" }: HandoffPanelProps) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [raw, setRaw] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [modal, setModal] = useState(false);

  useEffect(() => {
    if (clearKey === 0) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setData(null);
      setRaw("");
      if (open) {
        apiGet<Record<string, unknown>>(`${apiPrefix}/handoff`)
          .then(d => {
            if (cancelled) return;
            setData(d);
            setRaw(d ? JSON.stringify(d, null, 2) : "");
          })
          .catch(() => {});
      }
    });
    return () => { cancelled = true; };
  }, [apiPrefix, clearKey, open]);

  const load = async () => {
    if (open) { setOpen(false); return; }
    const d = await apiGet<Record<string, unknown>>(`${apiPrefix}/handoff`).catch(() => null);
    setData(d);
    setRaw(d ? JSON.stringify(d, null, 2) : "");
    setOpen(true);
  };

  const save = async (text: string) => {
    try {
      const parsed = JSON.parse(text);
      await apiPut(`${apiPrefix}/handoff`, parsed);
      setData(parsed);
      setRaw(text);
    } catch { /* ignore parse errors */ }
  };

  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Handoff</p>
      <div className="flex gap-1">
        <button onClick={load} className="flex-1 text-left px-2 py-1.5 text-[12px] rounded flex justify-between" style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
          <span>View latest handoff</span>
          <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>{open ? "▲" : "▼"}</span>
        </button>
        <button onClick={async () => { if (!open) await load(); setModal(true); }} className="px-2 py-1.5 text-[11px] rounded" title="Edit as JSON" style={{ background: "var(--bg-panel)", color: "var(--text-dim)", border: "1px solid var(--border)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--amber)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>⤢</button>
      </div>

      {open && data && (
        <div className="space-y-1.5 max-h-[28rem] overflow-y-auto pr-0.5">
          {!!data.updated_by && <p className="text-[9px]" style={{ color: "var(--text-dim)" }}>by {String(data.updated_by)} · v{String(data.version ?? "?")}</p>}
          {HANDOFF_SECTIONS.map(({ key, label, accent }) => (
            <HandoffValue key={key} label={label} value={data[key]} accent={accent} />
          ))}
          {Object.keys(data).filter(k => !HANDOFF_SECTIONS.some(s => s.key === k) && !["updated_by","version","updated_at","id"].includes(k)).map(k => (
            <HandoffValue key={k} label={k} value={data[k]} accent="var(--text-secondary)" />
          ))}
        </div>
      )}

      {open && !data && <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>(empty)</p>}
      {modal && <FullscreenMarkdownModal title="Handoff (JSON)" value={raw} onSave={save} onClose={() => setModal(false)} accent="var(--amber)" />}
    </div>
  );
}
