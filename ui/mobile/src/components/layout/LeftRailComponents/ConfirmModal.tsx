import { createPortal } from "react-dom";

interface ConfirmModalProps {
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmModal({ message, onConfirm, onCancel }: ConfirmModalProps) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: "rgba(0,0,0,0.75)" }}>
      <div className="w-72 rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "var(--bg-raised)", border: "1px solid var(--red)" }}>
        <p className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>Confirm</p>
        <p className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{message}</p>
        <div className="flex gap-2">
          <button onClick={onConfirm} className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg" style={{ background: "var(--red-bg)", border: "1px solid var(--red)", color: "var(--red)" }}>Yes, reset</button>
          <button onClick={onCancel} className="flex-1 py-1.5 text-[12px] rounded-lg" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body
  );
}
