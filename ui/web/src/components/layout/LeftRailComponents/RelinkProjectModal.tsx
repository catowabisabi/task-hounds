import { useState } from "react";
import { createPortal } from "react-dom";
import type { Workspace } from "./types";

interface RelinkProjectModalProps {
  workspace: Workspace;
  error: string;
  busy: boolean;
  onRelink: () => void;
  onCancel: () => void;
}

export function RelinkProjectModal({ workspace, error, busy, onRelink, onCancel }: RelinkProjectModalProps) {
  return createPortal(
    <div className="overlaid delimiter absolute inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(0,0,0,0.75)" }}>
      <div className="w-full max-w-md rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "var(--bg-raised)", border: "1px solid var(--amber)" }}>
        <div>
          <p className="text-[13px] font-semibold" style={{ color: "var(--amber)" }}>Relink Project Folder</p>
          <p className="text-[12px] mt-1" style={{ color: "var(--text-secondary)" }}>
            Task Hounds cannot find the original folder for this project. Choose the new location to resume chat and agent work.
          </p>
        </div>
        <div className="rounded p-3" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
          <p className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--text-dim)" }}>Previous folder</p>
          <p className="text-[11px] break-all" style={{ color: "var(--text-primary)" }}>{workspace.path || "(no path recorded)"}</p>
        </div>
        {error && <p className="text-[11px]" style={{ color: "var(--red)" }}>{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={onRelink}
            disabled={busy}
            className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg disabled:opacity-50"
            style={{ background: "var(--amber-bg)", border: "1px solid var(--amber)", color: "var(--amber)" }}
          >
            {busy ? "Relinking..." : "Choose Folder"}
          </button>
          <button
            onClick={onCancel}
            disabled={busy}
            className="flex-1 py-1.5 text-[12px] rounded-lg disabled:opacity-50"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
