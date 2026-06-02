import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../../../lib/api";
import type { Flow01Reports } from "../../../lib/api";
import { Tooltip } from "../../ui/Tooltip";

export function Flow01ReportsPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<Flow01Reports | null>(null);

  const load = useCallback(async () => {
    const next = await apiGet<Flow01Reports>("/api/workflows/flow_01/reports").catch(() => null);
    setData(next);
  }, []);

  useEffect(() => { load(); }, [load, refreshKey]);
  useEffect(() => {
    const id = window.setInterval(load, 4000);
    return () => window.clearInterval(id);
  }, [load]);

  const worker = data?.worker;
  const reviewer = data?.reviewer;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--green)" }}>Flow 01 Review</p>
        <Tooltip label="Latest Worker and Reviewer records from the flow_01 workflow database for the active project session." />
      </div>
      {!worker && !reviewer ? (
        <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No flow_01 reports yet</p>
      ) : (
        <div className="space-y-2">
          {worker && (
            <div className="rounded p-2 space-y-1" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-semibold" style={{ color: "var(--text-secondary)" }}>Worker</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: worker.test_result === "passed" ? "var(--green-bg)" : "var(--amber-bg)", color: worker.test_result === "passed" ? "var(--green)" : "var(--amber)", border: `1px solid ${worker.test_result === "passed" ? "var(--green-dim)" : "var(--amber-dim)"}` }}>{worker.test_result || "unknown"}</span>
              </div>
              {worker.files_changed.length > 0 && (
                <div className="text-[10px] leading-snug" style={{ color: "var(--text-dim)" }}>
                  {worker.files_changed.slice(0, 3).map(path => <div key={path} className="truncate" title={path}>{path}</div>)}
                </div>
              )}
              {worker.known_issues.length > 0 && (
                <div className="text-[10px] leading-snug" style={{ color: "var(--amber)" }}>
                  {worker.known_issues.slice(0, 3).map(issue => <div key={issue}>{issue}</div>)}
                </div>
              )}
            </div>
          )}
          {reviewer && (
            <div className="rounded p-2 space-y-1" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-semibold" style={{ color: "var(--text-secondary)" }}>Reviewer</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: reviewer.qa_result === "pass" ? "var(--green-bg)" : "var(--amber-bg)", color: reviewer.qa_result === "pass" ? "var(--green)" : "var(--amber)", border: `1px solid ${reviewer.qa_result === "pass" ? "var(--green-dim)" : "var(--amber-dim)"}` }}>{reviewer.qa_result || reviewer.status}</span>
              </div>
              <div className="text-[11px] max-h-20 overflow-y-auto whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>
                {reviewer.review_notes || "No review notes"}
              </div>
              {reviewer.bugs.length > 0 && (
                <div className="text-[10px] leading-snug" style={{ color: "var(--red)" }}>
                  {reviewer.bugs.slice(0, 3).map(bug => <div key={bug}>{bug}</div>)}
                </div>
              )}
              {reviewer.uiux_suggestions.length > 0 && (
                <div className="text-[10px] leading-snug" style={{ color: "var(--blue)" }}>
                  {reviewer.uiux_suggestions.slice(0, 3).map(item => <div key={item}>{item}</div>)}
                </div>
              )}
              {reviewer.possible_problems.length > 0 && (
                <div className="text-[10px] leading-snug" style={{ color: "var(--amber)" }}>
                  {reviewer.possible_problems.slice(0, 3).map(item => <div key={item}>{item}</div>)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
