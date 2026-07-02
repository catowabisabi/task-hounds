import { createContext, useContext, useState, useCallback, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

interface Toast {
  id: number;
  msg: string;
  type: "success" | "error" | "info";
}

interface ToastContextValue {
  showToast: (msg: string, type?: Toast["type"]) => void;
}

const ToastContext = createContext<ToastContextValue>({
  showToast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: () => void }) {
  const colors = {
    success: { bg: "var(--green-bg)", color: "var(--green)", border: "var(--green-dim)" },
    error: { bg: "var(--red-bg)", color: "var(--red)", border: "var(--red-dim)" },
    info: { bg: "var(--blue-bg)", color: "var(--blue)", border: "var(--blue-dim)" },
  };
  const c = colors[toast.type];
  return (
    <div
      className="px-3 py-2 rounded-lg text-[11px] font-medium text-center shadow-lg pointer-events-auto cursor-pointer"
      style={{ background: c.bg, color: c.color, border: `1px solid ${c.border}` }}
      onClick={onRemove}
    >
      {toast.msg}
    </div>
  );
}

function ToastContainer({ toasts, onRemove }: { toasts: Toast[]; onRemove: (id: number) => void }) {
  return createPortal(
    <div className="fixed top-4 left-1/2 z-[200] flex flex-col gap-2 w-full max-w-sm pointer-events-none" style={{ transform: "translateX(-50%)" }}>
      {toasts.map(t => (
        <ToastItem key={t.id} toast={t} onRemove={() => onRemove(t.id)} />
      ))}
    </div>,
    document.body
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const showToast = useCallback((msg: string, type: Toast["type"] = "info") => {
    const id = ++idRef.current;
    setToasts(prev => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      <ToastContainer toasts={toasts} onRemove={removeToast} />
      {children}
    </ToastContext.Provider>
  );
}
