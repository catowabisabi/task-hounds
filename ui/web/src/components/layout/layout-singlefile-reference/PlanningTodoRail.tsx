import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet, apiPut, apiPost, apiPatch, apiDelete } from "../../lib/api";

interface Plan {
  content: string;
  updated_by: string | null;
  updated_at: string | null;
}

interface Todo {
  id: string;
  parent_id: string | null;
  content: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  priority: "high" | "medium" | "low";
  position: number;
  owner: string | null;
}

const STATUS_COLOR: Record<Todo["status"], string> = {
  pending:     "#6b7280",
  in_progress: "#f59e0b",
  completed:   "#22c55e",
  blocked:     "#ef4444",
};

const NEXT_STATUS: Record<Todo["status"], Todo["status"]> = {
  pending:     "in_progress",
  in_progress: "completed",
  completed:   "pending",
  blocked:     "pending",
};

export function PlanningTodoRail({ clearKey = 0 }: { clearKey?: number }) {
  // ── Plan state ──
  const [plan, setPlan]       = useState<Plan>({ content: "", updated_by: null, updated_at: null });
  const [planDraft, setPlanDraft] = useState("");
  const [planSaving, setPlanSaving] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef("");

  // ── Todos state ──
  const [todos, setTodos] = useState<Todo[]>([]);
  const [newTopDraft, setNewTopDraft] = useState("");
  const [newChildDraft, setNewChildDraft] = useState<Record<string, string>>({});

  // ── Glow-on-change state ──
  // Bumps a numeric key each time the polled value differs from what we last
  // displayed. Components read the key as `key={glowKey}` (or as a className
  // toggle) to replay the 1s glow animation.
  const [planGlowKey, setPlanGlowKey] = useState(0);
  const [todoGlow, setTodoGlow] = useState<Record<string, number>>({});
  const prevPlanRef = useRef<string>("");
  const prevTodosRef = useRef<Record<string, string>>({}); // id → "content|status"

  const loadAll = useCallback(async () => {
    const [p, t] = await Promise.all([
      apiGet<Plan>("/api/plan").catch(() => ({ content: "", updated_by: null, updated_at: null })),
      apiGet<Todo[]>("/api/todos").catch(() => []),
    ]);

    // Detect plan change — only flash if the change came from the BACKEND,
    // i.e. content differs from what the user is currently typing AND from the
    // last seen remote content.
    if (p.content !== prevPlanRef.current && p.content !== planDraft) {
      setPlanGlowKey(k => k + 1);
    }
    prevPlanRef.current = p.content;

    // Detect per-todo changes (new id, or content/status changed for known id)
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
      // Clear the glow tokens after the animation finishes (~1.1s)
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

    setPlan(p);
    // Don't overwrite the user's in-progress edits with the polled value
    if (p.content !== planDraft && document.activeElement?.tagName !== "TEXTAREA") {
      setPlanDraft(p.content);
      lastSavedRef.current = p.content;
    } else if (lastSavedRef.current === "" && p.content) {
      // First load
      setPlanDraft(p.content);
      lastSavedRef.current = p.content;
    }
    setTodos(t);
  }, [planDraft]);

  useEffect(() => { loadAll(); }, [loadAll, clearKey]);

  // Poll for changes from backend agents
  useEffect(() => {
    const id = setInterval(loadAll, 3500);
    return () => clearInterval(id);
  }, [loadAll]);

  // Debounced plan save
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    if (planDraft === lastSavedRef.current) return;
    saveTimerRef.current = setTimeout(async () => {
      setPlanSaving(true);
      await apiPut("/api/plan", { content: planDraft, updated_by: "human" }).catch(() => {});
      lastSavedRef.current = planDraft;
      setPlanSaving(false);
    }, 800);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [planDraft]);

  const topLevel  = todos.filter(t => !t.parent_id);
  const childrenOf = (pid: string) => todos.filter(t => t.parent_id === pid);

  const addTop = async () => {
    const txt = newTopDraft.trim();
    if (!txt) return;
    await apiPost("/api/todos", { content: txt, owner: "manager" }).catch(() => {});
    setNewTopDraft("");
    loadAll();
  };

  const addChild = async (parentId: string) => {
    const txt = (newChildDraft[parentId] || "").trim();
    if (!txt) return;
    await apiPost("/api/todos", { content: txt, parent_id: parentId, owner: "worker" }).catch(() => {});
    setNewChildDraft(d => ({ ...d, [parentId]: "" }));
    loadAll();
  };

  const toggleStatus = async (todo: Todo) => {
    const next = NEXT_STATUS[todo.status];
    await apiPatch(`/api/todos/${todo.id}`, { status: next }).catch(() => {});
    setTodos(ts => ts.map(t => t.id === todo.id ? { ...t, status: next } : t));
  };

  const removeTodo = async (id: string) => {
    await apiDelete(`/api/todos/${id}`).catch(() => {});
    setTodos(ts => ts.filter(t => t.id !== id && t.parent_id !== id));
  };

  return (
    <aside
      className="w-80 shrink-0 flex flex-col min-h-0"
      style={{ background: "#111111", borderLeft: "1px solid #2a2a2a", borderRight: "1px solid #2a2a2a" }}
    >
      {/* ── Plan (top half) ── */}
      <div className="flex flex-col min-h-0" style={{ flex: "1 1 40%", borderBottom: "1px solid #1f1f1f" }}>
        <div className="px-3 pt-3 pb-1.5 flex items-center justify-between shrink-0">
          <p className="text-[12px] font-bold uppercase tracking-wide" style={{ color: "#60a5fa" }}>
            Manager Planning
          </p>
          <div className="flex items-center gap-2">
            {planSaving && <span className="text-[10px] animate-pulse" style={{ color: "#4ade80" }}>saving…</span>}
            {plan.updated_by && (
              <span className="text-[9px]" style={{ color: "#4b5563" }}>
                by {plan.updated_by}
              </span>
            )}
          </div>
        </div>
        <textarea
          key={`plan-${planGlowKey}`}
          className={`flex-1 mx-3 mb-3 rounded p-2 text-[12px] resize-none outline-none transition-colors ${planGlowKey > 0 ? "data-glow" : ""}`}
          style={{ background: "#0d0d0d", border: "1px solid #1f1f1f", color: "#d1d5db", minHeight: 80 }}
          value={planDraft}
          onChange={e => setPlanDraft(e.target.value)}
          placeholder="Manager will write the plan here…"
          onFocus={e => (e.target.style.borderColor = "#60a5fa")}
          onBlur={e => (e.target.style.borderColor = "#1f1f1f")}
        />
      </div>

      {/* ── Todo list (bottom half) ── */}
      <div className="flex flex-col min-h-0" style={{ flex: "1 1 60%" }}>
        <div className="px-3 pt-3 pb-1.5 flex items-center justify-between shrink-0">
          <p className="text-[12px] font-bold uppercase tracking-wide" style={{ color: "#a78bfa" }}>
            Todo List
          </p>
          <span className="text-[10px]" style={{ color: "#4b5563" }}>
            {todos.filter(t => t.status === "completed").length}/{todos.length}
          </span>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-2 space-y-2">
          {topLevel.length === 0 && (
            <p className="text-[11px] italic" style={{ color: "#4b5563" }}>
              Manager has not produced a todo list yet.
            </p>
          )}

          {topLevel.map(top => (
            <div
              key={`${top.id}-${todoGlow[top.id] ?? 0}`}
              className={`rounded ${todoGlow[top.id] ? "data-glow" : ""}`}
              style={{ background: "#0d0d0d", border: "1px solid #1f1f1f" }}
            >
              <div className="flex items-start gap-2 p-2">
                <button
                  onClick={() => toggleStatus(top)}
                  className="mt-0.5 w-3.5 h-3.5 rounded shrink-0 flex items-center justify-center text-[9px] font-bold"
                  style={{
                    background: top.status === "completed" ? STATUS_COLOR.completed : "transparent",
                    color: top.status === "completed" ? "#111" : STATUS_COLOR[top.status],
                    border: `1px solid ${STATUS_COLOR[top.status]}`,
                  }}
                  title={top.status}
                >
                  {top.status === "completed" ? "✓" :
                   top.status === "in_progress" ? "◐" :
                   top.status === "blocked"     ? "!" : ""}
                </button>
                <span
                  className="flex-1 text-[12px] leading-tight"
                  style={{
                    color: top.status === "completed" ? "#4b5563" : "#e5e7eb",
                    textDecoration: top.status === "completed" ? "line-through" : "none",
                  }}
                >
                  {top.content}
                </span>
                <button
                  onClick={() => removeTodo(top.id)}
                  className="shrink-0 text-[11px] px-1"
                  style={{ color: "#4b5563" }}
                  onMouseEnter={e => (e.currentTarget.style.color = "#ef4444")}
                  onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
                  title="Delete"
                >✕</button>
              </div>

              {/* Children */}
              <div className="pl-6 pr-2 pb-2 space-y-1">
                {childrenOf(top.id).map(c => (
                  <div
                    key={`${c.id}-${todoGlow[c.id] ?? 0}`}
                    className={`flex items-start gap-2 rounded px-1 ${todoGlow[c.id] ? "data-glow" : ""}`}
                  >
                    <button
                      onClick={() => toggleStatus(c)}
                      className="mt-0.5 w-3 h-3 rounded shrink-0 flex items-center justify-center text-[8px] font-bold"
                      style={{
                        background: c.status === "completed" ? STATUS_COLOR.completed : "transparent",
                        color: c.status === "completed" ? "#111" : STATUS_COLOR[c.status],
                        border: `1px solid ${STATUS_COLOR[c.status]}`,
                      }}
                      title={c.status}
                    >
                      {c.status === "completed" ? "✓" :
                       c.status === "in_progress" ? "◐" :
                       c.status === "blocked"     ? "!" : ""}
                    </button>
                    <span
                      className="flex-1 text-[11px] leading-tight"
                      style={{
                        color: c.status === "completed" ? "#4b5563" : "#9ca3af",
                        textDecoration: c.status === "completed" ? "line-through" : "none",
                      }}
                    >
                      {c.content}
                      {c.owner && <span className="ml-1 text-[9px]" style={{ color: "#4b5563" }}>({c.owner})</span>}
                    </span>
                    <button
                      onClick={() => removeTodo(c.id)}
                      className="shrink-0 text-[10px] px-1"
                      style={{ color: "#4b5563" }}
                      onMouseEnter={e => (e.currentTarget.style.color = "#ef4444")}
                      onMouseLeave={e => (e.currentTarget.style.color = "#4b5563")}
                    >✕</button>
                  </div>
                ))}

                {/* Add sub-item */}
                <div className="flex items-center gap-1 pt-1">
                  <input
                    value={newChildDraft[top.id] || ""}
                    onChange={e => setNewChildDraft(d => ({ ...d, [top.id]: e.target.value }))}
                    onKeyDown={e => { if (e.key === "Enter") addChild(top.id); }}
                    placeholder="+ subtask"
                    className="flex-1 text-[10px] px-1.5 py-0.5 rounded outline-none"
                    style={{ background: "#181818", border: "1px solid #1f1f1f", color: "#d1d5db" }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Add top-level item */}
        <div className="px-3 pb-3 pt-1 shrink-0" style={{ borderTop: "1px solid #1f1f1f" }}>
          <div className="flex items-center gap-1">
            <input
              value={newTopDraft}
              onChange={e => setNewTopDraft(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") addTop(); }}
              placeholder="+ new task (manager-level)"
              className="flex-1 text-[11px] px-2 py-1 rounded outline-none"
              style={{ background: "#181818", border: "1px solid #2a2a2a", color: "#e5e7eb" }}
              onFocus={e => (e.target.style.borderColor = "#a78bfa")}
              onBlur={e => (e.target.style.borderColor = "#2a2a2a")}
            />
            <button
              onClick={addTop}
              disabled={!newTopDraft.trim()}
              className="text-[11px] px-2 py-1 rounded disabled:opacity-30"
              style={{ background: "#181818", color: "#a78bfa", border: "1px solid #2a2a2a" }}
            >+</button>
          </div>
        </div>
      </div>
    </aside>
  );
}
