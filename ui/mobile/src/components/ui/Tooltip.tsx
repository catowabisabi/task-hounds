import { Info } from "lucide-react";
import type { ReactNode } from "react";

interface TooltipProps {
  label: string;
  children?: ReactNode;
}

export function Tooltip({ label, children }: TooltipProps) {
  return (
    <span className="relative inline-flex items-center group">
      {children ?? (
        <button
          type="button"
          aria-label={label}
          className="inline-flex h-4 w-4 items-center justify-center rounded-full"
          style={{ color: "var(--text-dim)" }}
        >
          <Info size={12} aria-hidden="true" />
        </button>
      )}
      <span
        role="tooltip"
        className="pointer-events-none absolute right-0 top-full z-50 mt-1 hidden w-56 rounded px-2 py-1.5 text-[11px] leading-snug shadow-lg group-hover:block group-focus-within:block"
        style={{
          background: "var(--bg-base)",
          color: "var(--text-secondary)",
          border: "1px solid var(--border)",
        }}
      >
        {label}
      </span>
    </span>
  );
}
