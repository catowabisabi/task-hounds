import { createPortal } from "react-dom";

interface LoadingWidgetProps {
  message?: string;
}

export function LoadingWidget({ message = "Loading..." }: LoadingWidgetProps) {
  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex flex-col items-center justify-center gap-4"
      style={{ background: "rgba(0,0,0,0.7)", backdropFilter: "blur(4px)" }}
    >
      <div className="relative w-12 h-12">
        <div
          className="absolute inset-0 rounded-full border-4"
          style={{ borderColor: "var(--blue-dim)", borderTopColor: "transparent", animation: "spin 1s linear infinite" }}
        />
        <div
          className="absolute inset-2 rounded-full border-4"
          style={{ borderColor: "var(--purple-dim)", borderBottomColor: "transparent", animation: "spin 0.8s linear infinite reverse" }}
        />
      </div>
      <p className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
        {message}
      </p>
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>,
    document.body
  );
}
