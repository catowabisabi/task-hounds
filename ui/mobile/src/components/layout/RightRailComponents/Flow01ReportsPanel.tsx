import { useCallback, useEffect, useState } from "react";
import { apiGet } from "../../../lib/api";
import type { Flow01Reports } from "../../../lib/api";
import { Tooltip } from "../../ui/Tooltip";
import { JsonTree } from "../../ui/JsonTree";

type Tone = "green" | "amber" | "red" | "blue";

function toneStyle(tone: Tone) {
  return {
    background: `var(--${tone}-bg)`,
    color: `var(--${tone})`,
    border: `1px solid var(--${tone}-dim)`,
  };
}

function normalized(value?: string | null) {
  return (value ?? "").trim().toLowerCase();
}

function reviewerTone(status?: string | null, qaResult?: string | null): Tone {
  const qa = normalized(qaResult);
  const state = normalized(status);
  if (qa === "pass") return "green";
  if (qa === "fail" || state === "failed" || state === "error") return "red";
  if (state === "running" || qa === "running") return "blue";
  return "amber";
}

function reviewerLabel(status?: string | null, qaResult?: string | null) {
  const qa = normalized(qaResult);
  const state = normalized(status);
  if (state === "running" || qa === "running") return "Reviewing";
  if (qa === "pass") return "Passed";
  if (qa === "fail") return "Failed";
  if (qa === "needs_review") return "Needs review";
  return qaResult || status || "Waiting";
}

function displayFinding(item: unknown): string {
  if (typeof item === "string") return item;
  if (typeof item === "number" || typeof item === "boolean") return String(item);
  if (!item || typeof item !== "object") return "";

  const finding = item as Record<string, unknown>;
  const heading = [finding.severity, finding.type, finding.title]
    .filter(value => typeof value === "string" && value.trim())
    .map(String)
    .join(" · ");
  const description = [finding.description, finding.message, finding.content]
    .find(value => typeof value === "string" && value.trim());
  if (heading && description) return `${heading}: ${description}`;
  if (description) return String(description);
  if (heading) return heading;

  try {
    return JSON.stringify(item);
  } catch {
    return "Unrecognized review finding";
  }
}

function CompactList({ title, items, tone = "amber" }: { title: string; items?: unknown[]; tone?: Tone }) {
  const visible = (items ?? []).map(displayFinding).filter(Boolean);
  if (visible.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[10px] font-semibold" style={{ color: `var(--${tone})` }}>{title}</div>
      <div className="space-y-1">
        {visible.slice(0, 4).map((item, index) => (
          <div key={`${title}-${index}`} className="text-[10px] leading-snug rounded px-2 py-1" style={{ background: "var(--bg-base)", color: "var(--text-secondary)", border: "1px solid var(--border-dim)" }}>
            {item}
          </div>
        ))}
      </div>
      {visible.length > 4 && (
        <div className="text-[10px]" style={{ color: "var(--text-dim)" }}>+{visible.length - 4} more</div>
      )}
    </div>
  );
}

export function Flow01ReportsPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<Flow01Reports | null>(null);

  const load = useCallback(async () => {
    const next = await apiGet<Flow01Reports>("/api/workflows/flow_01/reports").catch(() => null);
    setData(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => { cancelled = true; };
  }, [load, refreshKey]);
  useEffect(() => {
    const id = window.setInterval(load, 4000);
    return () => window.clearInterval(id);
  }, [load]);

  const worker = data?.worker;
  const reviewer = data?.reviewer;
  const reviewIsRunning = normalized(reviewer?.status) === "running" || normalized(reviewer?.qa_result) === "running";
  const hasReviewFindings = Boolean(
    reviewer?.review_notes ||
    reviewer?.bugs?.length ||
    reviewer?.possible_problems?.length ||
    reviewer?.safety_security_risks?.length ||
    reviewer?.uiux_suggestions?.length ||
    reviewer?.scripts_documented
  );
  const evidenceFiles = (worker?.files_changed ?? []).filter(path => {
    const normalizedPath = path.replace(/\\/g, "/");
    if (normalizedPath.endsWith("/")) return false;
    if (/(^|\/)(node_modules|\.git)(\/|$)/.test(normalizedPath)) return false;
    return !/\.(db|db-shm|db-wal|log)$/i.test(normalizedPath);
  });
  const reviewTone = reviewerTone(reviewer?.status, reviewer?.qa_result);
  const actionRequired = normalized(reviewer?.qa_result) === "needs_review"
    ? "Manager needs to decide whether to retry, revise the task, or ask for your input."
    : normalized(reviewer?.qa_result) === "fail" || normalized(reviewer?.status) === "failed"
      ? "Manager should revise or retry this task. You only need to act if Manager asks for a decision."
      : "";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--green)" }}>Work Review</p>
        <Tooltip label="What GraphFlow attempted, the evidence it produced, and whether you need to act." />
      </div>
      {!worker && !reviewer ? (
        <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No GraphFlow reports yet</p>
      ) : (
        <div className="space-y-2">
          {reviewer && (
            <div className="rounded p-2 space-y-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-[10px] font-semibold" style={{ color: "var(--text-secondary)" }}>Outcome</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded-full" style={toneStyle(reviewTone)}>{reviewerLabel(reviewer.status, reviewer.qa_result)}</span>
                {reviewer.completed_at && <span className="text-[10px] truncate" style={{ color: "var(--text-dim)" }}>{reviewer.completed_at}</span>}
              </div>

              {reviewIsRunning && !hasReviewFindings ? (
                <div className="text-[10px] leading-snug rounded px-2 py-1" style={{ ...toneStyle("blue"), background: "var(--blue-bg)" }}>
                  Reviewer is checking the Worker output now. Findings will appear here when the review finishes.
                </div>
              ) : (
                <>
                  {worker?.report && <div className="space-y-1"><div className="text-[10px] font-semibold" style={{ color: "var(--text-secondary)" }}>What happened</div><div className="text-[10px] leading-snug rounded px-2 py-1 whitespace-pre-wrap" style={{ background: "var(--bg-base)", color: "var(--text-secondary)", border: "1px solid var(--border-dim)" }}>{worker.report}</div></div>}
                  <div className="space-y-1">
                    <div className="text-[10px] font-semibold" style={{ color: "var(--text-secondary)" }}>Evidence</div>
                    <div className="text-[10px] rounded px-2 py-1" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>
                      <div>Verification: {worker?.test_result || "No verification result"}</div>
                      {evidenceFiles.slice(0, 5).map(path => <div key={path} className="truncate" title={path}>{path}</div>)}
                      {evidenceFiles.length === 0 && <div style={{ color: "var(--text-dim)" }}>No verified source-file changes reported</div>}
                    </div>
                  </div>
                  <CompactList title="Bugs" items={reviewer.bugs} tone="red" />
                  <CompactList title="Risks" items={reviewer.safety_security_risks} tone="red" />
                  <CompactList title="Possible problems" items={reviewer.possible_problems} tone="amber" />
                  <CompactList title="UI/UX suggestions" items={reviewer.uiux_suggestions} tone="blue" />
                  {actionRequired && <div className="text-[10px] leading-snug rounded px-2 py-1" style={{ ...toneStyle("amber"), background: "var(--amber-bg)" }}><span className="font-semibold">Next step: </span>{actionRequired}</div>}
                </>
              )}

              <details className="text-[10px]">
                <summary className="cursor-pointer text-[10px] font-medium" style={{ color: "var(--text-dim)" }}>
                  Technical details
                </summary>
                <div className="mt-1 p-1 rounded" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
                  <JsonTree data={{ worker, reviewer }} maxDepth={4} />
                </div>
              </details>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
