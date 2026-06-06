import { useEffect, useRef } from "react";
import { apiPatch, apiDelete } from "../../../lib/api";

export interface Todo {
  id: string;
  parent_id: string | null;
  content: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  priority: "high" | "medium" | "low";
  position: number;
  owner: string | null;
}

export const STATUS_COLOR: Record<Todo["status"], string> = {
  pending:     "var(--text-secondary)",
  in_progress: "var(--amber)",
  completed:   "var(--green)",
  blocked:     "var(--red)",
};

export const NEXT_STATUS: Record<Todo["status"], Todo["status"]> = {
  pending:     "in_progress",
  in_progress: "completed",
  completed:   "pending",
  blocked:     "pending",
};

interface TodoItemProps {
  todo: Todo;
  glowTime?: number;
  onToggle: () => void;
  onRemove: () => void;
}

export function TodoItem({ todo, glowTime, onToggle, onRemove }: TodoItemProps) {
  return (
    <div
      key={`${todo.id}-${glowTime ?? 0}`}
      className={`rounded ${glowTime ? "data-glow" : ""}`}
      style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}
    >
      <div className="flex items-start gap-2 p-2">
        <button
          onClick={onToggle}
          className="mt-0.5 w-3.5 h-3.5 rounded shrink-0 flex items-center justify-center text-[9px] font-bold"
          style={{
            background: todo.status === "completed" ? STATUS_COLOR.completed : "transparent",
            color: todo.status === "completed" ? "var(--bg-base)" : STATUS_COLOR[todo.status],
            border: `1px solid ${STATUS_COLOR[todo.status]}`,
          }}
          title={todo.status}
        >
          {todo.status === "completed" ? "✓" :
           todo.status === "in_progress" ? "◐" :
           todo.status === "blocked"     ? "!" : ""}
        </button>
        <span
          className="flex-1 text-[12px] leading-tight"
          style={{
            color: todo.status === "completed" ? "var(--text-dim)" : "var(--text-primary)",
            textDecoration: todo.status === "completed" ? "line-through" : "none",
          }}
        >
          {todo.content}
        </span>
        <button onClick={onRemove} className="shrink-0 text-[11px] px-1" style={{ color: "var(--text-dim)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--red)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")} title="Delete">✕</button>
      </div>
    </div>
  );
}

interface TodoItemChildProps {
  todo: Todo;
  glowTime?: number;
  onToggle: () => void;
  onRemove: () => void;
}

export function TodoItemChild({ todo, glowTime, onToggle, onRemove }: TodoItemChildProps) {
  return (
    <div className="flex items-start gap-2 rounded px-1" style={glowTime ? { background: "transparent" } : {}}>
      <button
        onClick={onToggle}
        className="mt-0.5 w-3 h-3 rounded shrink-0 flex items-center justify-center text-[8px] font-bold"
        style={{
          background: todo.status === "completed" ? STATUS_COLOR.completed : "transparent",
          color: todo.status === "completed" ? "var(--bg-base)" : STATUS_COLOR[todo.status],
          border: `1px solid ${STATUS_COLOR[todo.status]}`,
        }}
        title={todo.status}
      >
        {todo.status === "completed" ? "✓" :
         todo.status === "in_progress" ? "◐" :
         todo.status === "blocked"     ? "!" : ""}
      </button>
      <span
        className="flex-1 text-[11px] leading-tight"
        style={{
          color: todo.status === "completed" ? "var(--text-dim)" : "var(--text-secondary)",
          textDecoration: todo.status === "completed" ? "line-through" : "none",
        }}
      >
        {todo.content}
        {todo.owner && <span className="ml-1 text-[9px]" style={{ color: "var(--text-dim)" }}>({todo.owner})</span>}
      </span>
      <button onClick={onRemove} className="shrink-0 text-[10px] px-1" style={{ color: "var(--text-dim)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--red)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>✕</button>
    </div>
  );
}
