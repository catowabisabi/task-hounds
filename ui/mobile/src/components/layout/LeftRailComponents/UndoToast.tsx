import { useEffect } from "react";
import { createPortal } from "react-dom";

interface UndoToastProps {
  message: string;
  onUndo: () => void;
  onDismiss: () => void;
}

export function UndoToast({ message, onUndo, onDismiss }: UndoToastProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 5000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return createPortal(
    <div
      className="fixed bottom-6 left-1/2 z-[100] flex items-center gap-3 px-4 py-2 rounded-xl shadow-2xl"
      style={{ background: "var(--bg-raised)", border: "1px solid var(--amber)", transform: "translateX(-50%)" }}
    >
      <span className="text-[12px]" style={{ color: "var(--text-primary)" }}>{message}</span>
      <button
        onClick={onUndo}
        className="px-2 py-1 text-[11px] font-semibold rounded"
        style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
      >
        Undo
      </button>
      <button onClick={onDismiss} className="text-[14px]" style={{ color: "var(--text-dim)" }}>×</button>
    </div>,
    document.body
  );
}

export function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  try {
    const d = new Date(isoStr);
    const now = Date.now();
    const diff = Math.floor((now - d.getTime()) / 1000);
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return "—";
  }
}
