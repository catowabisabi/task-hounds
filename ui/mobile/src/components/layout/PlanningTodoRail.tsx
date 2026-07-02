import { useEffect, useCallback } from "react";
import { PlanEditor, usePlanEditor } from "./PlanningTodoRailComponents";
import { TodoList, useTodoList } from "./PlanningTodoRailComponents";

export function PlanningTodoRail({ clearKey = 0, apiPrefix = "/api" }: { clearKey?: number; apiPrefix?: string }) {
  const { plan, planDraft, setPlanDraft, planSaving, planGlowKey, loadPlan } = usePlanEditor(clearKey, apiPrefix);
  const {
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
  } = useTodoList(clearKey, apiPrefix);

  const loadAll = useCallback(async () => {
    await Promise.all([loadPlan(), loadTodos()]);
  }, [loadPlan, loadTodos]);

  const handleChildDraftChange = (parentId: string, value: string) => {
    setNewChildDraft(d => ({ ...d, [parentId]: value }));
  };

  // Poll for changes from backend agents
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void loadAll();
    }, 3000);
    return () => clearInterval(id);
  }, [loadAll]);

  return (
    <aside
      className="w-80 shrink-0 flex flex-col min-h-0"
      style={{ background: "var(--bg-base)", borderLeft: "1px solid var(--border)", borderRight: "1px solid var(--border)" }}
    >
      <PlanEditor
        plan={plan}
        planDraft={planDraft}
        planSaving={planSaving}
        planGlowKey={planGlowKey}
        onDraftChange={setPlanDraft}
      />
      <TodoList
        todos={todos}
        todoGlow={todoGlow}
        newChildDraft={newChildDraft}
        onChildDraftChange={handleChildDraftChange}
        onAddChild={addChild}
        onToggleStatus={toggleStatus}
        onRemoveTodo={removeTodo}
        onAddTop={addTop}
        newTopDraft={newTopDraft}
        onNewTopDraftChange={setNewTopDraft}
      />
    </aside>
  );
}
