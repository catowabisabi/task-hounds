import { createPortal } from "react-dom";

interface InfoModalProps {
  message: string;
  onOk: () => void;
}

export function InfoModal({ message, onOk }: InfoModalProps) {
  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center" style={{ background: "rgba(0,0,0,0.75)" }}>
      <div className="w-72 rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "var(--bg-raised)", border: "1px solid var(--blue)" }}>
        <p className="text-[13px] font-semibold" style={{ color: "var(--blue)" }}>Notice</p>
        <p className="text-[12px]" style={{ color: "var(--text-primary)" }}>{message}</p>
        <button
          onClick={onOk}
          className="w-full py-1.5 text-[12px] font-semibold rounded-lg"
          style={{ background: "var(--blue-bg)", border: "1px solid var(--blue)", color: "var(--blue)" }}
        >
          OK
        </button>
      </div>
    </div>,
    document.body
  );
}
