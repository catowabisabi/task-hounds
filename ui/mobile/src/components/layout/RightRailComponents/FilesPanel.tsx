import { useState, useEffect } from "react";
import { apiGet } from "../../../lib/api";

const FILE_KEYS = [
  { key: "worker_report",    label: "Worker Report" },
  { key: "manager_feedback", label: "Manager Feedback" },
];

interface FilesPanelProps {
  clearKey?: number;
}

export function FilesPanel({ clearKey = 0 }: FilesPanelProps) {
  const [open, setOpen] = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    const key = open;
    queueMicrotask(() => {
      if (cancelled) return;
      setContents({});
      if (key) {
        apiGet<{ content: string }>(`/api/files/${key}`)
          .then(d => { if (!cancelled) setContents({ [key]: d.content }); })
          .catch(() => {});
      }
    });
    return () => { cancelled = true; };
  }, [clearKey, open]);

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
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Files</p>
      <div className="space-y-1">
        {FILE_KEYS.map(({ key, label }) => (
          <div key={key}>
            <button onClick={() => load(key)} className="w-full text-left px-2 py-1.5 text-[12px] rounded flex justify-between items-center" style={{ background: open === key ? "var(--bg-raised)" : "var(--bg-panel)", color: "var(--text-secondary)", border: `1px solid ${open === key ? "var(--border)" : "var(--border-dim)"}` }}>
              <span>{label}</span>
              <span className="text-[10px]" style={{ color: "var(--text-dim)" }}>{open === key ? "▲" : "▼"}</span>
            </button>
            {open === key && (
              <pre className="mt-1 p-2 rounded text-[11px] max-h-36 overflow-y-auto whitespace-pre-wrap break-words" style={{ background: "var(--bg-base)", color: "var(--text-secondary)", border: "1px solid var(--border-dim)" }}>
                {contents[key] || "(empty)"}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
