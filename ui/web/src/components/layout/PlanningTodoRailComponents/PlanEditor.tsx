import { useState, useEffect, useCallback, useRef } from "react";
import { apiGet, apiPut } from "../../../lib/api";

interface Plan {
  content: string;
  updated_by: string | null;
  updated_at: string | null;
}

interface PlanEditorProps {
  plan: Plan;
  planDraft: string;
  planSaving: boolean;
  planGlowKey: number;
  onDraftChange: (v: string) => void;
}

export function PlanEditor({ plan, planDraft, planSaving, planGlowKey, onDraftChange }: PlanEditorProps) {
  return (
    <div className="flex flex-col min-h-0" style={{ flex: "1 1 40%", borderBottom: "1px solid var(--border-dim)" }}>
      <div className="px-3 pt-3 pb-1.5 flex items-center justify-between shrink-0">
        <p className="text-[12px] font-bold uppercase tracking-wide" style={{ color: "var(--blue)" }}>
          Manager Planning
        </p>
        <div className="flex items-center gap-2">
          {planSaving && <span className="text-[10px] animate-pulse" style={{ color: "var(--green)" }}>saving…</span>}
          {plan.updated_by && (
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>
              by {plan.updated_by}
            </span>
          )}
        </div>
      </div>
      <textarea
        key={`plan-${planGlowKey}`}
        className={`flex-1 mx-3 mb-3 rounded p-2 text-[12px] resize-none outline-none transition-colors ${planGlowKey > 0 ? "data-glow" : ""}`}
        style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-primary)", minHeight: 80 }}
        value={planDraft}
        onChange={e => onDraftChange(e.target.value)}
        placeholder="Manager will write the plan here…"
        onFocus={e => (e.target.style.borderColor = "var(--blue)")}
        onBlur={e => (e.target.style.borderColor = "var(--border-dim)")}
      />
    </div>
  );
}

export function usePlanEditor(clearKey: number, apiPrefix = "/api") {
  const [plan, setPlan] = useState<Plan>({ content: "", updated_by: null, updated_at: null });
  const [planDraft, setPlanDraft] = useState("");
  const [planSaving, setPlanSaving] = useState(false);
  const [planGlowKey, setPlanGlowKey] = useState(0);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedRef = useRef("");
  const prevPlanRef = useRef<string>("");

  const loadPlan = useCallback(async () => {
    const p = await apiGet<Plan>(`${apiPrefix}/plan`).catch(() => ({ content: "", updated_by: null, updated_at: null }));
    if (p.content !== prevPlanRef.current && p.content !== planDraft) {
      setPlanGlowKey(k => k + 1);
    }
    prevPlanRef.current = p.content;
    setPlan(p);
    if (p.content !== planDraft && document.activeElement?.tagName !== "TEXTAREA") {
      setPlanDraft(p.content);
      lastSavedRef.current = p.content;
    } else if (lastSavedRef.current === "" && p.content) {
      setPlanDraft(p.content);
      lastSavedRef.current = p.content;
    }
  }, [apiPrefix, planDraft]);

  useEffect(() => { loadPlan(); }, [loadPlan, clearKey]);

  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    if (planDraft === lastSavedRef.current) return;
    saveTimerRef.current = setTimeout(async () => {
      setPlanSaving(true);
      await apiPut(`${apiPrefix}/plan`, { content: planDraft, updated_by: "human" }).catch(() => {});
      lastSavedRef.current = planDraft;
      setPlanSaving(false);
    }, 800);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [apiPrefix, planDraft]);

  return { plan, planDraft, setPlanDraft, planSaving, planGlowKey, loadPlan };
}
