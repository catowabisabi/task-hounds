
export interface Todo {
  id: string;
  parent_id: string | null;
  content: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  priority: "high" | "medium" | "low";
  position: number;
  owner: string | null;
  worker_task_status?: "pending" | "running" | "reported" | "skipped" | "error";
  reviewer_task_status?: "pending" | "running" | "pass" | "fail" | "needs_review" | "skipped" | "error";
  attempt_count?: number;
  human_attention_status?: "none" | "attention_required" | "resolved";
  is_active?: boolean;
  plan_revision?: number;
  archive_reason?: string | null;
  archive_note?: string | null;
  archived_at?: string | null;
  archived_by?: string | null;
  replaced_by_todo_id?: string | null;
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

const todoProgressLabel = (todo: Todo) => {
  if (!todo.content.trim()) return "Invalid empty todo";
  if (todo.human_attention_status === "attention_required") return "Needs human attention";
  if (todo.status === "completed") return "Completed by Manager";
  if (todo.worker_task_status === "skipped") return "Worker skipped; awaiting Manager decision";
  if (todo.worker_task_status === "error") return "Worker error; awaiting Manager decision";
  if (todo.reviewer_task_status === "pass") return "Reviewer passed; awaiting Manager decision";
  if (todo.reviewer_task_status === "fail") return "Reviewer failed; awaiting Manager decision";
  if (todo.reviewer_task_status === "needs_review") return "Reviewer needs changes; awaiting Manager decision";
  if (todo.reviewer_task_status === "skipped") return "Reviewer skipped; awaiting Manager decision";
  if (todo.reviewer_task_status === "error") return "Reviewer error; awaiting Manager decision";
  if (todo.reviewer_task_status === "running") return "Reviewer is checking";
  if (todo.worker_task_status === "reported") return "Worker reported; awaiting Reviewer";
  if (todo.worker_task_status === "running") return "Worker is working";
  if ((todo.attempt_count ?? 0) === 0) return "Not started";
  return "Queued for another attempt";
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
          {todo.content.trim() || "Untitled todo (invalid)"}
          <span className="block mt-1 text-[9px]" style={{ color: todo.human_attention_status === "attention_required" ? "var(--red)" : "var(--text-dim)" }}>
            {todoProgressLabel(todo)} · Attempt {todo.attempt_count ?? 0}/4 · W: {todo.worker_task_status ?? "pending"} · R: {todo.reviewer_task_status ?? "pending"}
            {todo.human_attention_status === "attention_required" ? " · Human attention required" : ""}
          </span>
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
        {todo.content.trim() || "Untitled todo (invalid)"}
        {todo.owner && <span className="ml-1 text-[9px]" style={{ color: "var(--text-dim)" }}>({todo.owner})</span>}
        <span className="block text-[9px]" style={{ color: todo.human_attention_status === "attention_required" ? "var(--red)" : "var(--text-dim)" }}>
          {todoProgressLabel(todo)} · Attempt {todo.attempt_count ?? 0}/4 · W: {todo.worker_task_status ?? "pending"} · R: {todo.reviewer_task_status ?? "pending"}
        </span>
      </span>
      <button onClick={onRemove} className="shrink-0 text-[10px] px-1" style={{ color: "var(--text-dim)" }} onMouseEnter={e => (e.currentTarget.style.color = "var(--red)")} onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}>✕</button>
    </div>
  );
}
