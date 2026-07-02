type State = "idle" | "busy" | "waiting" | "error" | "offline";

const cfg: Record<State, { pill: string; dot: string; label: string }> = {
  idle:    { pill: "bg-[var(--green-bg)] text-[var(--green)] border-[var(--green-dim)]",   dot: "bg-[var(--green)]",                         label: "idle"    },
  busy:    { pill: "bg-[var(--amber-bg)] text-[var(--amber)] border-[var(--amber-dim)]",   dot: "bg-[var(--amber)] animate-pulse",           label: "busy"    },
  waiting: { pill: "bg-[var(--purple-bg)] text-[var(--purple)] border-[var(--purple-dim)]", dot: "bg-[var(--purple)] animate-pulse",         label: "waiting" },
  error:   { pill: "bg-[var(--red-bg)] text-[var(--red)] border-[var(--red-dim)]",         dot: "bg-[var(--red)]",                           label: "error"   },
  offline: { pill: "bg-[var(--bg-raised)] text-[var(--text-dim)] border-[var(--border)]",  dot: "bg-[var(--bg-hover)]",                      label: "offline" },
};

export function StateBadge({ state }: { state: State }) {
  const c = cfg[state] ?? cfg.offline;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border ${c.pill}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.dot}`} />
      {c.label}
    </span>
  );
}

export function stateLeftBorder(state: State): string {
  const map: Record<State, string> = {
    idle:    "border-l-2 border-l-[var(--green)]",
    busy:    "border-l-2 border-l-[var(--amber)]",
    waiting: "border-l-2 border-l-[var(--purple)]",
    error:   "border-l-2 border-l-[var(--red)]",
    offline: "border-l-2 border-l-[var(--bg-hover)]",
  };
  return map[state] ?? map.offline;
}
