import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet, apiPost, apiPatch, apiDelete } from "../../../lib/api";
import { type Todo, TodoItem, TodoItemChild } from "./TodoItem";

interface TodoListProps {
  todos: Todo[];
  todoGlow: Record<string, number>;
  newChildDraft: Record<string, string>;
  onChildDraftChange: (parentId: string, value: string) => void;
  onAddChild: (parentId: string) => void;
  onToggleStatus: (todo: Todo) => void;
  onRemoveTodo: (id: string) => void;
  onAddTop: () => void;
  newTopDraft: string;
  onNewTopDraftChange: (v: string) => void;
}

export function TodoList({
  todos,
  todoGlow,
  newChildDraft,
  onChildDraftChange,
  onAddChild,
  onToggleStatus,
  onRemoveTodo,
  onAddTop,
  newTopDraft,
  onNewTopDraftChange,
}: TodoListProps) {
  const [showCompleted, setShowCompleted] = useState(false);
  const visibleTodos = showCompleted ? todos : todos.filter(t => t.status !== "completed");
  const topLevel = visibleTodos.filter(t => !t.parent_id);
  const childrenOf = (pid: string) => visibleTodos.filter(t => t.parent_id === pid);
  const completedCount = todos.filter(t => t.status === "completed").length;

  return (
    <div className="flex flex-col min-h-0" style={{ flex: "1 1 60%" }}>
      <div className="px-3 pt-3 pb-1.5 flex items-center justify-between shrink-0">
        <p className="text-[12px] font-bold uppercase tracking-wide" style={{ color: "var(--purple)" }}>
          Todo List
        </p>
        <button
          onClick={() => setShowCompleted(v => !v)}
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{ color: showCompleted ? "var(--purple)" : "var(--text-dim)", border: "1px solid var(--border-dim)" }}
        >
          {completedCount}/{todos.length} done
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 pb-2 space-y-2">
        {topLevel.length === 0 && (
          <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>
            Manager has not produced a todo list yet.
          </p>
        )}

        {topLevel.map(top => (
          <div key={top.id}>
            <TodoItem
              todo={top}
              glowTime={todoGlow[top.id]}
              onToggle={() => onToggleStatus(top)}
              onRemove={() => onRemoveTodo(top.id)}
            />
            <div className="pl-6 pr-2 pb-2 space-y-1">
              {childrenOf(top.id).map(c => (
                <TodoItemChild
                  key={c.id}
                  todo={c}
                  glowTime={todoGlow[c.id]}
                  onToggle={() => onToggleStatus(c)}
                  onRemove={() => onRemoveTodo(c.id)}
                />
              ))}
              <div className="flex items-center gap-1 pt-1">
                <input
                  value={newChildDraft[top.id] || ""}
                  onChange={e => onChildDraftChange(top.id, e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") onAddChild(top.id); }}
                  placeholder="+ subtask"
                  className="flex-1 text-[10px] px-1.5 py-0.5 rounded outline-none"
                  style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)", color: "var(--text-primary)" }}
                />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="px-3 pb-3 pt-1 shrink-0" style={{ borderTop: "1px solid var(--border-dim)" }}>
        <div className="flex items-center gap-1">
          <input
            value={newTopDraft}
            onChange={e => onNewTopDraftChange(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") onAddTop(); }}
            placeholder="+ new task (manager-level)"
            className="flex-1 text-[11px] px-2 py-1 rounded outline-none"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            onFocus={e => (e.target.style.borderColor = "var(--purple)")}
            onBlur={e => (e.target.style.borderColor = "var(--border)")}
          />
          <button
            onClick={onAddTop}
            disabled={!newTopDraft.trim()}
            className="text-[11px] px-2 py-1 rounded disabled:opacity-30"
            style={{ background: "var(--bg-panel)", color: "var(--purple)", border: "1px solid var(--border)" }}
          >+</button>
        </div>
      </div>
    </div>
  );
}

export function useTodoList(clearKey: number, apiPrefix = "/api") {
  const [todos, setTodos] = useState<Todo[]>([]);
  const [newTopDraft, setNewTopDraft] = useState("");
  const [newChildDraft, setNewChildDraft] = useState<Record<string, string>>({});
  const [todoGlow, setTodoGlow] = useState<Record<string, number>>({});
  const prevTodosRef = useRef<Record<string, string>>({});

  const loadTodos = useCallback(async () => {
    const t = await apiGet<Todo[]>(`${apiPrefix}/todos`).catch(err => {
      console.error("Failed to load todos", err);
      return [];
    });
    const newSig: Record<string, string> = {};
    const flashed: Record<string, number> = {};
    const prev = prevTodosRef.current;
    t.forEach(td => {
      const sig = `${td.content}|${td.status}`;
      newSig[td.id] = sig;
      if (prev[td.id] !== sig) {
        flashed[td.id] = Date.now();
      }
    });
    prevTodosRef.current = newSig;
    if (Object.keys(flashed).length > 0) {
      setTodoGlow(g => ({ ...g, ...flashed }));
      window.setTimeout(() => {
        setTodoGlow(g => {
          const next = { ...g };
          for (const id of Object.keys(flashed)) {
            if (next[id] === flashed[id]) delete next[id];
          }
          return next;
        });
      }, 1100);
    }
    setTodos(t);
  }, [apiPrefix]);

  useEffect(() => { loadTodos(); }, [loadTodos, clearKey]);

  const addTop = async () => {
    const txt = newTopDraft.trim();
    if (!txt) return;
    try {
      await apiPost(`${apiPrefix}/todos`, { content: txt, owner: "manager" });
      setNewTopDraft("");
      loadTodos();
    } catch (err) {
      console.error("Failed to add todo", err);
    }
  };

  const addChild = async (parentId: string) => {
    const txt = (newChildDraft[parentId] || "").trim();
    if (!txt) return;
    try {
      await apiPost(`${apiPrefix}/todos`, { content: txt, parent_id: parentId, owner: "worker" });
      setNewChildDraft(d => ({ ...d, [parentId]: "" }));
      loadTodos();
    } catch (err) {
      console.error("Failed to add subtask", err);
    }
  };

  const toggleStatus = async (todo: Todo) => {
    const NEXT: Record<Todo["status"], Todo["status"]> = {
      pending: "in_progress",
      in_progress: "completed",
      completed: "pending",
      blocked: "pending",
    };
    const next = NEXT[todo.status];
    try {
      await apiPatch(`${apiPrefix}/todos/${todo.id}`, { status: next });
      setTodos(ts => ts.map(t => t.id === todo.id ? { ...t, status: next } : t));
    } catch (err) {
      console.error("Failed to update todo", err);
    }
  };

  const removeTodo = async (id: string) => {
    try {
      await apiDelete(`${apiPrefix}/todos/${id}`);
      setTodos(ts => ts.filter(t => t.id !== id && t.parent_id !== id));
    } catch (err) {
      console.error("Failed to delete todo", err);
    }
  };

  return {
    todos,
    newTopDraft,
    setNewTopDraft,
    newChildDraft,
    setNewChildDraft,
    todoGlow,
    addTop,
    addChild,
    toggleStatus,
    removeTodo,
    loadTodos,
  };
}
