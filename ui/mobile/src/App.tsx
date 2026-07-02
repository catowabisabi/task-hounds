import React, { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost, apiPut, debugLog } from "./lib/api";
import type { Agent, LoopStatus, Suggestion } from "./lib/api";
import { LeftRail } from "./components/layout/LeftRail";
import { ToastProvider } from "./contexts/ToastContext";
import { CenterFeed } from "./components/layout/CenterFeed";
import { RightRail } from "./components/layout/RightRail";
import { PlanningTodoRail } from "./components/layout/PlanningTodoRail";
import { usePolling } from "./hooks/usePolling";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { BackgroundServerButton, BackgroundServerModal } from "./components/BackgroundServerModal";
import type { WarningFlags } from "./components/BackgroundServerModal";
import { thinkingLabels, UI_LANGUAGE_CHANGED, useUiLanguage } from "./lib/uiLanguage";
import { OpenCodeQuestionModal } from "./components/OpenCodeQuestionModal";

type Flow01RunInfo = {
  id: number;
  status: string;
  phase?: string;
  graph_step?: string;
  task?: string;
  error?: string;
  output_json?: string;
};

type FlowInterruption = {
  kind: string;
  title: string;
  reason: string;
  source: string;
  resumable: boolean;
};

type Flow01RunsResponse = Flow01RunInfo[] | { runs?: Flow01RunInfo[] };
type Flow01RunDetail = {
  ok?: boolean;
  run?: Flow01RunInfo;
  output?: { interruption?: FlowInterruption };
};

type ExecutionCommandAction = "start_fresh" | "run_once" | "pause" | "resume" | "stop";
type ExecutionCommandNotice = {
  kind: "success" | "error";
  title: string;
  message: string;
};

const getFlow01Runs = (data: Flow01RunsResponse | null | undefined): Flow01RunInfo[] => {
  if (Array.isArray(data)) return data;
  return data?.runs ?? [];
};

const isFlow01Paused = (status?: string | null) => {
  const value = (status ?? "").toLowerCase();
  return value === "paused" || value === "pausing" || value === "technical_error" || value.startsWith("paused_before_");
};

const isFlow01Running = (status?: string | null) => (status ?? "").toLowerCase() === "running";

const isFlow01Actionable = (run: Flow01RunInfo) => isFlow01Running(run.status) || isFlow01Paused(run.status);

const getRunInterruption = (run: Flow01RunInfo | null): FlowInterruption | null => {
  if (!run?.output_json) return null;
  try {
    return (JSON.parse(run.output_json) as { interruption?: FlowInterruption }).interruption ?? null;
  } catch {
    return null;
  }
};

function FlowInterruptionModal({ interruption, onClose }: { interruption: FlowInterruption; onClose: () => void }) {
  return createPortal(
    <div className="fixed inset-0 z-[220] flex items-center justify-center px-5" style={{ background: "rgba(0,0,0,0.72)" }}>
      <div className="w-full max-w-md rounded p-5" style={{ background: "var(--bg-raised)", border: "1px solid var(--amber)", boxShadow: "0 16px 40px rgba(0,0,0,0.55)" }}>
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-[14px] font-semibold" style={{ color: "var(--amber)" }}>{interruption.title}</h2>
            <p className="mt-2 text-[12px] leading-relaxed whitespace-pre-wrap break-words" style={{ color: "var(--text-primary)" }}>{interruption.reason}</p>
            <p className="mt-3 text-[10px]" style={{ color: "var(--text-dim)" }}>
              Source: {interruption.source} · {interruption.resumable ? "Can resume from checkpoint" : "Not resumable"}
            </p>
          </div>
          <button onClick={onClose} className="text-[16px] px-1" style={{ color: "var(--text-dim)" }} title="Close">×</button>
        </div>
        <div className="mt-4 flex justify-end">
          <button onClick={onClose} className="px-3 py-1.5 rounded text-[11px] font-semibold" style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>Acknowledge</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function ExecutionCommandOverlay({
  pending,
  notice,
  onClose,
}: {
  pending: ExecutionCommandAction | null;
  notice: ExecutionCommandNotice | null;
  onClose: () => void;
}) {
  if (!pending && !notice) return null;
  const labels: Record<ExecutionCommandAction, string> = {
    start_fresh: "Starting GraphFlow",
    run_once: "Starting one GraphFlow run",
    pause: "Pausing GraphFlow",
    resume: "Resuming GraphFlow",
    stop: "Stopping GraphFlow",
  };
  const isError = notice?.kind === "error";
  return createPortal(
    <div className="fixed inset-0 z-[240] flex items-center justify-center px-5" style={{ background: "rgba(0,0,0,0.72)" }}>
      <div className="w-full max-w-sm rounded p-5" style={{ background: "var(--bg-raised)", border: `1px solid ${isError ? "var(--red-dim)" : "var(--border)"}`, boxShadow: "0 16px 40px rgba(0,0,0,0.55)" }}>
        {pending ? (
          <div className="flex items-center gap-3">
            <div className="h-5 w-5 shrink-0 rounded-full border-2 animate-spin" style={{ borderColor: "var(--border)", borderTopColor: "var(--blue)" }} />
            <div>
              <h2 className="text-[13px] font-semibold">{labels[pending]}</h2>
              <p className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}>Waiting for the server to confirm the new state.</p>
            </div>
          </div>
        ) : notice ? (
          <>
            <h2 className="text-[13px] font-semibold" style={{ color: isError ? "var(--red)" : "var(--green)" }}>{notice.title}</h2>
            <p className="mt-2 text-[12px] leading-relaxed whitespace-pre-wrap break-words">{notice.message}</p>
            <div className="mt-4 flex justify-end">
              <button onClick={onClose} className="px-3 py-1.5 rounded text-[11px] font-semibold" style={{ background: isError ? "var(--red-bg)" : "var(--green-bg)", color: isError ? "var(--red)" : "var(--green)", border: `1px solid ${isError ? "var(--red-dim)" : "var(--green-dim)"}` }}>Close</button>
            </div>
          </>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

const normalizeDirectiveStatus = (data: {
  has_directive?: boolean;
  directive_content?: string | null;
  directive?: string | null;
} | null | undefined) => {
  const content = data?.directive_content ?? data?.directive ?? "";
  return {
    has_directive: data?.has_directive ?? content.trim().length > 0,
    directive_content: content,
  };
};

// ── Compact LanguageSelector for header pill ─────────────────────────────────
const PRESET_LANGS = [
  { code: "en",    label: "EN",    full: "English" },
  { code: "zh-tw", label: "ZH-TW", full: "Traditional Chinese" },
  { code: "ja",    label: "JA",    full: "Japanese" },
];

function LanguageSelector() {
  const [lang, setLang]         = useState("en");
  const [custom, setCustom]     = useState<string[]>([]);
  const [open, setOpen]         = useState(false);
  const [inputting, setInputting] = useState(false);
  const [draft, setDraft]       = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    apiGet<{ language?: string; custom_languages?: string[] }>("/api/settings")
      .then(d => {
        if (d.language) {
          setLang(d.language);
          window.dispatchEvent(new CustomEvent(UI_LANGUAGE_CHANGED, { detail: { language: d.language } }));
        }
        if (d.custom_languages) setCustom(d.custom_languages);
      })
      .catch(() => {});
  }, []);

  const persistLang = (code: string, nextCustom?: string[]) => {
    const cl = nextCustom ?? custom;
    apiPut("/api/settings", { language: code, custom_languages: cl }).catch(() => {});
  };

  const selectLang = (code: string) => {
    setLang(code);
    window.dispatchEvent(new CustomEvent(UI_LANGUAGE_CHANGED, { detail: { language: code } }));
    persistLang(code);
    setOpen(false);
    setInputting(false);
  };

  const addCustom = () => {
    const val = draft.trim();
    if (!val) { setInputting(false); return; }
    const next = custom.includes(val) ? custom : [...custom, val];
    setCustom(next);
    setLang(val);
    window.dispatchEvent(new CustomEvent(UI_LANGUAGE_CHANGED, { detail: { language: val } }));
    persistLang(val, next);
    setDraft("");
    setInputting(false);
    setOpen(false);
  };

  const allLangs = [
    ...PRESET_LANGS,
    ...custom.map(c => ({ code: c, label: c.slice(0, 6), full: c })),
  ];

  const current = allLangs.find(l => l.code === lang) ?? { label: lang.slice(0, 6), full: lang };

  return (
    <div className="relative">
      <button
        onClick={() => { setOpen(o => !o); setInputting(false); }}
        className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium transition-colors"
        style={{ background: open ? "var(--amber-bg)" : "var(--bg-panel)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
        title={current.full}
      >
        {current.label}
        <span style={{ color: "var(--amber-dim)", fontSize: "8px" }}>▼</span>
      </button>

      {open && (
        <div
          className="absolute top-full left-0 mt-1 z-50 rounded py-1 min-w-[100px]"
          style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}
        >
          {/* Preset + custom options */}
          {allLangs.map(l => (
            <button
              key={l.code}
              onClick={() => selectLang(l.code)}
              className="w-full text-left px-3 py-1 text-[11px] transition-colors"
              style={{ color: lang === l.code ? "var(--amber)" : "var(--text-secondary)", background: lang === l.code ? "var(--amber-bg)" : "transparent" }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--amber-bg)")}
              onMouseLeave={e => (e.currentTarget.style.background = lang === l.code ? "var(--amber-bg)" : "transparent")}
            >
              {l.label}
            </button>
          ))}

          {/* Divider */}
          <div style={{ borderTop: "1px solid var(--border)", margin: "4px 0" }} />

          {/* Custom row */}
          {inputting ? (
            <div className="flex items-center gap-1 px-2 py-1">
              <input
                ref={inputRef}
                className="flex-1 rounded px-1.5 py-0.5 text-[11px] outline-none min-w-0"
                style={{ background: "var(--bg-base)", border: "1px solid var(--amber)", color: "var(--text-primary)" }}
                placeholder="e.g. ko, fr..."
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") addCustom(); if (e.key === "Escape") { setInputting(false); setDraft(""); } }}
                autoFocus
              />
              <button
                onClick={addCustom}
                className="shrink-0 px-1.5 py-0.5 text-[10px] rounded font-semibold"
                style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
              >✓</button>
            </div>
          ) : (
            <button
              onClick={() => { setInputting(true); setTimeout(() => inputRef.current?.focus(), 50); }}
              className="w-full text-left px-3 py-1 text-[11px] transition-colors"
              style={{ color: "var(--text-dim)" }}
              onMouseEnter={e => (e.currentTarget.style.color = "var(--amber)")}
              onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}
            >
              + Custom
            </button>
)}
        </div>
      )}
    </div>
  );
}

  // ── RunStatus dot (header, left of brand) ───────────────────────────────────
function RunStatus({ loop, agents, pending }: { loop: LoopStatus; agents: Agent[]; pending?: "starting" | "stopping" | null }) {
  const busy = agents.filter(a => a.state === "busy").length;
  let color = "var(--text-dim)";
  let label = "Idle";
  if (pending === "starting") { color = "var(--amber)"; label = "Starting..."; }
  else if (pending === "stopping") { color = "var(--amber)"; label = "Stopping..."; }
  else if (loop.running && busy > 0) { color = "var(--green)"; label = `${busy} active`; }
  else if (loop.running) { color = "var(--amber)"; label = "Running"; }
  return (
    <span className="flex items-center gap-1 text-[11px] font-medium" style={{ color }} role="status" aria-live="polite">
      <span className={`w-1.5 h-1.5 rounded-full ${pending ? "animate-pulse" : ""}`} style={{ background: color }} />
      <span className="hidden sm:inline">{label}</span>
    </span>
  );
}

// ── Process status pill (end of pipeline bar) ─────────────────────────────────
function BrandTitle() {
  const [logoOk, setLogoOk] = useState(true);

  if (!logoOk) {
    return <span className="text-[13px] font-bold" style={{ color: "var(--amber)" }}>Task Hounds</span>;
  }

  return (
    <img
      src="/task-hounds-logo.png"
      alt="Task Hounds"
      className="block h-[50px] w-auto max-w-[220px] object-contain"
      onError={() => setLogoOk(false)}
    />
  );
}





function ProcessStatus({
  loopRunning,
  errorAgents,
  onAgentsRefresh,
}: {
  loopRunning: boolean;
  errorAgents: Agent[];
  onAgentsRefresh?: () => void;
}) {
  const [showError, setShowError] = useState(false);
  const hasError = errorAgents.length > 0;

  const handleClearError = async (name: string) => {
    await apiPost(`/api/agents/${name}/clear-error`).catch(() => {});
    onAgentsRefresh?.();
  };

  const handleMarkResolved = async (name: string) => {
    await apiPost(`/api/agents/${name}/mark-resolved`).catch(() => {});
    onAgentsRefresh?.();
  };

  const handleRetry = async (name: string) => {
    await apiPost(`/api/agents/${name}/retry`).catch(() => {});
    onAgentsRefresh?.();
  };

  let label: string;
  let color: string;
  let bg: string;
  let border: string;

  if (hasError) {
    label = "Error"; color = "var(--red)"; bg = "var(--red-bg)"; border = "var(--red-dim)";
  } else if (loopRunning) {
    label = "Processing"; color = "var(--green)"; bg = "var(--green-bg)"; border = "var(--green-dim)";
  } else {
    label = "Stopped"; color = "var(--text-dim)"; bg = "var(--bg-panel)"; border = "var(--border)";
  }

  return (
    <>
      <button
        onClick={() => hasError && setShowError(true)}
        className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-[10px] font-semibold shrink-0 transition-colors ${hasError ? "cursor-pointer hover:opacity-80" : "cursor-default"}`}
        style={{ color, background: bg, border: `1px solid ${border}` }}
        title={hasError ? `${errorAgents.map(a => a.name).join(", ")} — click for details` : label}
      >
        {hasError && <span className="animate-pulse">⚠</span>}
        {!hasError && loopRunning && <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: color }} />}
        {!hasError && !loopRunning && <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />}
        {label}
      </button>

      {showError && createPortal(
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
          style={{ background: "rgba(0,0,0,0.80)" }}
          onClick={e => { if (e.target === e.currentTarget) setShowError(false); }}
        >
          <div className="w-full max-w-md rounded-xl shadow-2xl" style={{ background: "var(--bg-raised)", border: "1px solid var(--red-dim)" }}>
            <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
              <span className="text-[13px] font-semibold" style={{ color: "var(--red)" }}>⚠ Process Error</span>
              <button onClick={() => setShowError(false)} className="text-lg leading-none" style={{ color: "var(--text-dim)" }}>×</button>
            </div>
            <div className="p-4 space-y-3">
              {errorAgents.map(a => (
                <div key={a.name} className="p-3 rounded-lg" style={{ background: "var(--red-bg)", border: "1px solid var(--red-dim)" }}>
                  <p className="text-[12px] font-semibold mb-1" style={{ color: "var(--red)" }}>{a.name}</p>
                  <p className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
                    State: <span style={{ color: "var(--red)" }}>{a.state}</span>
                  </p>
                  {a.last_seen && (
                    <p className="text-[10px] mt-1" style={{ color: "var(--text-dim)" }}>
                      Last seen: {new Date(a.last_seen).toLocaleTimeString()}
                    </p>
                  )}
                  <p className="text-[10px] mt-1" style={{ color: "var(--text-dim)" }}>
                    Check the agent stream for details, or use Kill + Restart.
                  </p>
                  <div className="flex gap-1 mt-2">
                    <button
                      onClick={() => handleClearError(a.name)}
                      className="px-2 py-1 text-[10px] rounded"
                      style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
                    >
                      Clear
                    </button>
                    <button
                      onClick={() => handleMarkResolved(a.name)}
                      className="px-2 py-1 text-[10px] rounded"
                      style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
                    >
                      Resolve
                    </button>
                    <button
                      onClick={() => handleRetry(a.name)}
                      className="px-2 py-1 text-[10px] rounded"
                      style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
                    >
                      Retry
                    </button>
                  </div>
                </div>
              ))}
            </div>
            <div className="px-4 pb-4">
              <button
                onClick={() => setShowError(false)}
                className="w-full py-1.5 rounded-lg text-[12px] font-medium"
                style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                Close
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}

// ── Pipeline status bar ────────────────────────────────────────────────────────
function PipelineBar({
  agents,
  suggestion,
  loopRunning,
  flow01Phase,
  flow01Status,
  flow01GraphStep,
  onAgentsRefresh,
}: {
  agents: Agent[];
  suggestion: Suggestion | null;
  loopRunning: boolean;
  flow01Phase?: string | null;
  flow01Status?: string | null;
  flow01GraphStep?: string | null;
  onAgentsRefresh?: () => void;
}) {
  const manager  = agents.find(a => a.name === "manager");
  const worker   = agents.find(a => a.name === "worker");

  const suggStatus = suggestion?.status ?? null;

  type StepState = "completed" | "active" | "pending" | "error";
  type Step = {
    label: string;
    description: string;
    state: StepState;
    color: string;
  };

  if (flow01Phase || flow01Status) {
    const phase = flow01Phase || "";
    const done = flow01Status === "completed";
    const unresolvedCompletion = flow01Status === "completed_with_unresolved_evidence";
    const cancelled = flow01Status === "cancelled" || flow01Status === "cancelling";
    const managerGraphLabel = flow01GraphStep?.startsWith("manager_")
      ? flow01GraphStep.replace(/^manager_/, "").replace(/_/g, " ")
      : "";
    const flowSteps: Step[] = [
      {
        label: "Directive",
        description: "receive human directive",
        state: "completed",
        color: "var(--amber)",
      },
      {
        label: "Manager",
        description: phase === "manager_running"
          ? (managerGraphLabel || "digesting directive to selecting task")
          : (phase ? "done" : "selecting task"),
        state: phase === "manager_running" ? "active" : phase ? "completed" : "pending",
        color: "var(--blue)",
      },
      {
        label: "Worker",
        description: phase === "worker_running"
          ? "executing task"
          : ((phase === "reviewer_running" || done || unresolvedCompletion || phase.startsWith("cancelled_after")) ? "done" : "waiting"),
        state: phase === "worker_running" ? "active" :
          (phase === "reviewer_running" || phase === "completed" || phase.startsWith("cancelled_after")) ? "completed" : "pending",
        color: "var(--amber)",
      },
      {
        label: "Reviewer",
        description: phase === "reviewer_running"
          ? "checking QA, bugs, risk"
          : (done ? "done" : (unresolvedCompletion ? "unresolved" : (cancelled ? "skipped" : "waiting"))),
        state: phase === "reviewer_running" ? "active" : done ? "completed" : unresolvedCompletion ? "error" : "pending",
        color: "var(--green)",
      },
      {
        label: cancelled ? "Cancelled" : unresolvedCompletion ? "Unresolved" : "Done",
        description: cancelled ? "cancelled" : unresolvedCompletion ? "completed with unresolved evidence" : "complete",
        state: cancelled || unresolvedCompletion ? "error" : done ? "completed" : "pending",
        color: cancelled ? "var(--red)" : unresolvedCompletion ? "var(--amber)" : "var(--green)",
      },
    ];
    return (
      <div className="flex items-center gap-0 px-4 py-2 bg-[var(--bg-panel)] border-b border-[var(--border)] text-[11px] shrink-0 overflow-x-auto">
        <div className="flex items-center mr-3 shrink-0">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-dim)]">GraphFlow</span>
        </div>
        <PipelineSteps steps={flowSteps} />
        <div className="flex items-center gap-2 ml-auto shrink-0 pl-4" style={{ borderLeft: "1px solid var(--border)" }}>
          <ProcessStatus
            loopRunning={loopRunning}
            errorAgents={agents.filter(a => a.state === "error" || a.state === "offline")}
            onAgentsRefresh={onAgentsRefresh}
          />
        </div>
      </div>
    );
  }

  // Determine step states
  const getSuggestionState = (): StepState => {
    if (suggStatus === "worker_done" || suggStatus === "done") return "completed";
    if (suggStatus === "released") return worker?.state === "busy" ? "active" : "pending";
    if (suggStatus === "pending") return "pending";
    return "pending";
  };

  const steps: Step[] = [
    {
      label: "Directive",
      description: "Human directive received",
      state: suggestion ? "completed" : "pending",
      color: "var(--amber)",
    },
    {
      label: "Manager",
      description: "Planning and task decomposition",
      state: manager?.state === "busy" ? "active" :
              (!!suggestion && suggStatus !== "pending") ? "completed" : "pending",
      color: "var(--blue)",
    },
    {
      label: "Suggestion",
      description: "Task queued for worker",
      state: getSuggestionState(),
      color: suggStatus === "pending" ? "var(--amber)" : "var(--purple)",
    },
    {
      label: "Worker",
      description: "Executing tasks",
      state: worker?.state === "busy" ? "active" :
              (suggStatus === "worker_done" || suggStatus === "done") ? "completed" : "pending",
      color: "var(--amber)",
    },
    {
      label: "QA",
      description: "Quality assurance review",
      state: suggStatus === "done" ? "completed" :
              (manager?.state === "busy" && suggStatus === "worker_done") ? "active" : "pending",
      color: "var(--green)",
    },
  ];

  const getIcon = (state: StepState): string => {
    switch (state) {
      case "completed": return "✓";
      case "active": return "⟳";
      case "error": return "⚠";
      default: return "○";
    }
  };

  const getStateColor = (step: Step): string => {
    if (step.state === "completed") return "var(--green)";
    if (step.state === "active") return step.color;
    if (step.state === "error") return "var(--red)";
    return "var(--text-dim)";
  };

  void getIcon;
  void getStateColor;

  if (!loopRunning && !suggestion) return null;

  return (
    <div className="flex items-center gap-0 px-4 py-2 bg-[var(--bg-panel)] border-b border-[var(--border)] text-[11px] shrink-0 overflow-x-auto">
      {/* Label */}
      <div className="flex items-center mr-3 shrink-0">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-dim)]">Current Workflow</span>
      </div>

      <PipelineSteps steps={steps} />
      <div className="flex items-center gap-2 ml-auto shrink-0 pl-4" style={{ borderLeft: "1px solid var(--border)" }}>
        <ProcessStatus
          loopRunning={loopRunning}
          errorAgents={agents.filter(a => a.state === "error" || a.state === "offline")}
          onAgentsRefresh={onAgentsRefresh}
        />
      </div>
    </div>
  );
}

function PipelineSteps({ steps }: { steps: Array<{ label: string; description: string; state: "completed" | "active" | "pending" | "error"; color: string }> }) {
  const getIcon = (state: "completed" | "active" | "pending" | "error"): string => {
    switch (state) {
      case "completed": return "✓";
      case "active": return "⟳";
      case "error": return "⚠";
      default: return "○";
    }
  };

  const getStateColor = (step: { state: "completed" | "active" | "pending" | "error"; color: string }): string => {
    if (step.state === "completed") return "var(--green)";
    if (step.state === "active") return step.color;
    if (step.state === "error") return "var(--red)";
    return "var(--text-dim)";
  };

  return (
    <div className="flex items-center gap-1 flex-1 min-w-0">
      {steps.map((s, i) => (
          <div key={s.label} className="flex items-center group relative">
            {/* Step content */}
            <div
              className={`flex flex-col items-center px-3 py-1.5 rounded-md transition-all ${
                s.state === "active"
                  ? "bg-opacity-20 animate-pulse"
                  : s.state === "completed"
                  ? "bg-opacity-10"
                  : ""
              }`}
              style={{
                backgroundColor: s.state === "active" ? s.color + "33" : s.state === "completed" ? s.color + "1a" : "transparent",
              }}
            >
              {/* Icon + Label */}
              <div className="flex items-center gap-1.5">
                <span
                  className={`text-sm font-bold transition-all ${
                    s.state === "active" ? "animate-spin" : ""
                  }`}
                  style={{ color: getStateColor(s), animationDuration: s.state === "active" ? "2s" : "0s" }}
                >
                  {getIcon(s.state)}
                </span>
                <span
                  className={`font-medium transition-all ${
                    s.state === "active"
                      ? "text-[var(--text-primary)]"
                      : s.state === "completed"
                      ? ""
                      : "text-[var(--text-dim)]"
                  }`}
                  style={{ color: s.state === "active" ? "var(--text-primary)" : getStateColor(s) }}
                >
                  {s.label}
                </span>
              </div>
              {/* Description */}
              <div
                className={`text-[9px] mt-0.5 transition-all ${
                  s.state === "active"
                    ? "text-[var(--text-primary)] opacity-80"
                    : s.state === "completed"
                    ? ""
                    : "text-[var(--text-dim)]"
                }`}
                style={{ color: s.state === "active" ? "var(--text-primary)" : s.state === "completed" ? getStateColor(s) : "var(--text-dim)" }}
              >
                {s.description}
              </div>
            </div>

            {/* Tooltip on hover */}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-[var(--bg-base)] border border-[var(--border)] rounded text-[10px] text-[var(--text-primary)] opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-10">
              {s.description}
            </div>

            {/* Arrow separator */}
            {i < steps.length - 1 && (
              <span className="text-[var(--border)] mx-1">→</span>
            )}
          </div>
        ))}
      </div>
  );
}

// ── Rail collapse helpers ─────────────────────────────────────────────────────
function RailToggle({
  side, onClick, title, children,
}: { side: "left" | "right"; onClick: () => void; title?: string; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="absolute top-1/2 -translate-y-1/2 z-10 w-4 h-12 flex items-center justify-center text-[12px] font-bold rounded transition-colors"
      style={{
        [side]: -8,
        background: "var(--bg-panel)",
        color: "var(--text-secondary)",
        border: "1px solid var(--border)",
      } as React.CSSProperties}
      onMouseEnter={e => { e.currentTarget.style.color = "var(--amber)"; }}
      onMouseLeave={e => { e.currentTarget.style.color = "var(--text-secondary)"; }}
    >{children}</button>
  );
}

function RailStub({
  side, onClick, label,
}: { side: "left" | "right"; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      title={`Expand ${label}`}
      className="shrink-0 w-6 flex flex-col items-center justify-start gap-2 pt-3 transition-colors"
      style={{
        background: "var(--bg-base)",
        borderLeft:  side === "right" ? "1px solid var(--border-dim)" : "none",
        borderRight: side === "left"  ? "1px solid var(--border-dim)" : "none",
        color: "var(--text-dim)",
      }}
      onMouseEnter={e => { e.currentTarget.style.color = "var(--amber)"; e.currentTarget.style.background = "var(--bg-panel)"; }}
      onMouseLeave={e => { e.currentTarget.style.color = "var(--text-dim)"; e.currentTarget.style.background = "var(--bg-base)"; }}
    >
      <span className="text-[14px]">{side === "left" ? "›" : "‹"}</span>
      <span
        className="text-[10px] font-medium tracking-wider"
        style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
      >
        {label}
      </span>
    </button>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const uiLanguage = useUiLanguage();
  const thinkingText = thinkingLabels(uiLanguage);
  const [isDark, setIsDark] = useState<boolean>(() => {
    const stored = localStorage.getItem('darkMode');
    return stored !== null ? stored === 'true' : true;
  });

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.remove('dark');
      document.documentElement.classList.add('light');
    }
  }, [isDark]);

  const toggleDark = () => {
    setIsDark(prev => {
      const next = !prev;
      localStorage.setItem('darkMode', String(next));
      return next;
    });
  };

  const [agents,       setAgents]      = useState<Agent[]>([]);
  const [activeAgent,  setActiveAgent] = useState("manager");
  const userPickedRef  = useRef(false);
  const [loop,         setLoop]        = useState<LoopStatus>({ running: false, pid: null });
  const [loopElapsed,  setLoopElapsed] = useState(0);
  const loopStartRef   = useRef<number | null>(null);
  const reportedFailedFlow01RunRef = useRef<number | null>(null);
  const [suggestion,   setSuggestion]  = useState<Suggestion | null>(null);
  const [directiveStatus, setDirectiveStatus] = useState({has_directive: false, directive_content: ""});
  const [loopActionError, setLoopActionError] = useState("");
  const [loopActionPending, setLoopActionPending] = useState<"starting" | "stopping" | null>(null);
  const [executionCommandPending, setExecutionCommandPending] = useState<ExecutionCommandAction | null>(null);
  const [executionCommandNotice, setExecutionCommandNotice] = useState<ExecutionCommandNotice | null>(null);
  const [autoRelease, setAutoRelease] = useState(true);
  const [thinkingEnabled, setThinkingEnabled] = useState(true);
  // GraphFlow is the only supported workflow. Keep this local alias while
  // legacy branches are removed incrementally from the surrounding panels.
  const flow01Mode = true;
  const [flow01Run, setFlow01Run] = useState<Flow01RunInfo | null>(null);
  const [flowInterruption, setFlowInterruption] = useState<FlowInterruption | null>(null);
  const [sessionReloadKey, setSessionReloadKey] = useState(0);
  const [directiveClearKey, setDirectiveClearKey] = useState(0);
  const [apiError, setApiError] = useState<{ message: string; path: string } | null>(null);
  const [runtimeFlags, setRuntimeFlags] = useState<WarningFlags>({});
  const [lockedRound, setLockedRound] = useState<{
    projectSessionId: string;
    roundNumber: number;
    directive: string;
    completionSummary?: string;
  } | null>(null);
  const [nextRoundDirective, setNextRoundDirective] = useState("");

  useEffect(() => {
    const onChatActivity = () => {
      userPickedRef.current = true;
      setActiveAgent("chat");
    };
    window.addEventListener("task-hounds-chat-activity", onChatActivity);
    return () => window.removeEventListener("task-hounds-chat-activity", onChatActivity);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const check = () => {
      apiGet<WarningFlags>("/api/settings")
        .then(s => { if (!cancelled) setRuntimeFlags(s); })
        .catch(() => {});
    };
    check();
    const interval = setInterval(check, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    const onApiError = (event: Event) => {
      const detail = (event as CustomEvent<{ message?: string; path?: string }>).detail;
      setApiError({
        message: detail?.message || "API error",
        path: detail?.path || "",
      });
    };
    window.addEventListener("task-hounds-api-error", onApiError);
    return () => window.removeEventListener("task-hounds-api-error", onApiError);
  }, []);

  // Load top-level runtime switches from backend settings on mount.
  useEffect(() => {
    apiGet<{ auto_release?: boolean; opencode_thinking_enabled?: boolean }>("/api/settings")
      .then(s => {
        setAutoRelease(s.auto_release !== false);
        setThinkingEnabled(s.opencode_thinking_enabled !== false);
      })
      .catch(() => {});
  }, []);

  const toggleAutoRelease = async () => {
    const next = !autoRelease;
    setAutoRelease(next);
    await apiPut("/api/settings", { auto_release: next }).catch(() => {});
  };
  const toggleThinking = async () => {
    const next = !thinkingEnabled;
    setThinkingEnabled(next);
    try {
      await apiPut("/api/settings", { opencode_thinking_enabled: next });
    } catch {
      setThinkingEnabled(!next);
    }
  };
  const [leftOpen, setLeftOpen]     = useState(true);
  const [planOpen, setPlanOpen]     = useState(true);
  const [rightOpen, setRightOpen]   = useState(true);
  const [serverCount, setServerCount] = useState(0);
  // Tracks whether each rail's *current* state was set by an auto-collapse pass.
  // When the user manually opens a rail, we stop auto-managing it on resize
  // (so we don't immediately undo their action). Manually closing also opts
  // out — `null` means "untouched by user", true/false means user override.
  const userRailOverride = useRef<{ left: boolean | null; plan: boolean | null; right: boolean | null }>({
    left: null, plan: null, right: null,
  });

  // Responsive auto-collapse: as window narrows, hide rails in order
  //   1) Left (Agents)  → ≥1200px to keep open
  //   2) Plan / Todo    → ≥1000px to keep open
  //   3) Right (Status) → ≥720px  to keep open
  // CenterFeed is never collapsed.
  useEffect(() => {
    const apply = () => {
      const w = window.innerWidth;
      if (userRailOverride.current.left  === null) setLeftOpen(w  >= 1200);
      if (userRailOverride.current.plan  === null) setPlanOpen(w  >= 1000);
      if (userRailOverride.current.right === null) setRightOpen(w >= 720);
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, []);

  useEffect(() => {
    const tick = async () => {
      try {
        const data = await apiGet<{ servers: unknown[] }>("/api/runtime/opencode");
        setServerCount(data.servers?.length ?? 0);
      } catch { setServerCount(0); }
    };
    tick();
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, []);

  // Minimum width for the Output/Center rail. Opening another rail must not
  // squeeze CenterFeed below this; otherwise we refuse and show a toast.
  const MIN_CENTER_PX  = 400;
  const RAIL_W = { left: 192, plan: 320, right: 288 } as const; // matches w-48 / w-80 / w-72
  const STUB_W = 24;                                            // matches RailStub w-6

  const [tooNarrow, setTooNarrow] = useState<null | string>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [showServerModal, setShowServerModal] = useState(false);
  const [runtimeRecoveryPrompt, setRuntimeRecoveryPrompt] = useState(false);
  const [runtimeRecoveryBusy, setRuntimeRecoveryBusy] = useState(false);
  const [runtimeRecoveryError, setRuntimeRecoveryError] = useState("");
  const runtimeRecoveryDismissedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const checkRuntime = async () => {
      const status = await apiGet<{
        reason?: string;
        binding_ok?: boolean;
        binding_reachable?: boolean;
      }>("/api/chat/status").catch(() => null);
      if (cancelled || !status) return;
      const unresolved = status.reason === "binding_unresolved"
        || status.binding_ok === false
        || status.binding_reachable === false;
      if (!unresolved) {
        runtimeRecoveryDismissedRef.current = false;
        setRuntimeRecoveryPrompt(false);
        setRuntimeRecoveryError("");
        return;
      }
      if (!runtimeRecoveryDismissedRef.current) setRuntimeRecoveryPrompt(true);
    };
    void checkRuntime();
    const id = window.setInterval(() => void checkRuntime(), 6000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const restartOpenCode = async () => {
    setRuntimeRecoveryBusy(true);
    setRuntimeRecoveryError("");
    try {
      const result = await apiPost<{
        ok?: boolean;
        message?: string;
      }>("/api/runtime/opencode/start");
      if (!result.ok) throw new Error(result.message || "OpenCode failed to start.");
      setRuntimeRecoveryPrompt(false);
      runtimeRecoveryDismissedRef.current = false;
      setServerCount(1);
      refresh();
    } catch (error) {
      setRuntimeRecoveryError(error instanceof Error ? error.message : String(error));
    } finally {
      setRuntimeRecoveryBusy(false);
    }
  };

  const projectedWidth = (rail: "left" | "plan" | "right") => {
    // What CenterFeed width would be if we OPENED `rail` (current open rails stay open)
    const open = { left: leftOpen, plan: planOpen, right: rightOpen, [rail]: true } as Record<string, boolean>;
    let used = 0;
    (["left", "plan", "right"] as const).forEach(k => {
      used += open[k] ? RAIL_W[k] : STUB_W;
    });
    return window.innerWidth - used;
  };

  const tryOpen = (rail: "left" | "plan" | "right", label: string) => {
    if (projectedWidth(rail) < MIN_CENTER_PX) {
      setTooNarrow(`Cannot open ${label} — the window is too narrow. The output rail must stay at least ${MIN_CENTER_PX}px wide. Resize the window or close another side rail first.`);
      return;
    }
    if (rail === "left")  { userRailOverride.current.left  = true; setLeftOpen(true); }
    if (rail === "plan")  { userRailOverride.current.plan  = true; setPlanOpen(true); }
    if (rail === "right") { userRailOverride.current.right = true; setRightOpen(true); }
  };

  const closeRail = (rail: "left" | "plan" | "right") => {
    if (rail === "left")  { userRailOverride.current.left  = false; setLeftOpen(false); }
    if (rail === "plan")  { userRailOverride.current.plan  = false; setPlanOpen(false); }
    if (rail === "right") { userRailOverride.current.right = false; setRightOpen(false); }
  };

  const selectAgent = (name: string) => {
    userPickedRef.current = true;
    setActiveAgent(name);
  };

  const fetchAgents = useCallback(async () => {
    const data = await apiGet<Agent[]>("/api/agents").catch(() => []);
    setAgents(data);
    const busy = data.find(a => a.state === "busy");
    // auto-follow only if user hasn't manually picked a tab
    if (busy && !userPickedRef.current) setActiveAgent(busy.name);
    // reset pin when all agents go idle so auto-follow resumes next cycle
    if (!busy) userPickedRef.current = false;
  }, []);

  const fetchLoop = useCallback(async () => {
    if (flow01Mode) {
      const data = await apiGet<Flow01RunsResponse>("/api/workflows/flow_01/runs?limit=20").catch(() => []);
      const runs = getFlow01Runs(data);
      const latest = runs[0] || null;
      setFlow01Run(latest);
      const interruption = getRunInterruption(latest);
      if (latest?.id != null && interruption) {
        const key = `graphflow-interruption-${latest.id}-${interruption.kind}`;
        if (localStorage.getItem(key) !== "acknowledged") setFlowInterruption(interruption);
      }
      if ((latest?.status === "failed" || latest?.status === "error") && latest?.id != null && loopStartRef.current && reportedFailedFlow01RunRef.current !== latest.id) {
        reportedFailedFlow01RunRef.current = latest.id;
        setLoopActionError("GraphFlow " + latest.status + (latest.error ? ": " + latest.error : ""));
      } else if (isFlow01Running(latest?.status) || latest?.status === "cancelling") {
        reportedFailedFlow01RunRef.current = null;
      }
      const running = latest?.status === "running" || latest?.status === "cancelling" || latest?.status === "pausing";
      setLoop({ running, pid: latest?.id ?? null });
      if (running && !loopStartRef.current) {
        loopStartRef.current = Date.now();
      } else if (!running) {
        loopStartRef.current = null;
        setLoopElapsed(0);
      }
      return;
    }
    const data = await apiGet<LoopStatus>("/api/loop/status").catch(() => ({ running: false, pid: null }));
    debugLog("[DEBUG-LAUNCH-PAD] [STEP 9] fetchLoop() poll hit: " + JSON.stringify(data), "frontend-poll");
    setLoop(data);
    if (data.running && !loopStartRef.current) {
      loopStartRef.current = Date.now();
    } else if (!data.running) {
      loopStartRef.current = null;
      setLoopElapsed(0);
    }
  }, [flow01Mode]);

  const fetchSuggestion = useCallback(async () => {
    const data = await apiGet<Suggestion>(flow01Mode ? "/api/workflows/flow_01/suggestion" : "/api/suggestion").catch(() => null);
    setSuggestion(data && Object.keys(data).length > 0 ? data : null);
  }, [flow01Mode]);

  const fetchMessages = useCallback(async () => {
    // Manager Chat owns its own conversation state. Keep this refresh hook
    // temporarily so existing lifecycle callbacks remain stable.
  }, []);

  const fetchDirectiveStatus = useCallback(async () => {
    const d = await apiGet<{ has_directive?: boolean; directive_content?: string | null; directive?: string | null }>("/api/directive/status").catch(() => ({ has_directive: false, directive_content: "" }));
    setDirectiveStatus(normalizeDirectiveStatus(d));
  }, []);

  useEffect(() => {
    const onDirectiveUpdated = () => {
      setDirectiveClearKey(key => key + 1);
      void fetchDirectiveStatus();
    };
    window.addEventListener("task-hounds-directive-updated", onDirectiveUpdated);
    return () => window.removeEventListener("task-hounds-directive-updated", onDirectiveUpdated);
  }, [fetchDirectiveStatus]);

  usePolling(() => {
    fetchAgents();
    fetchLoop();
    fetchSuggestion();
    fetchMessages();
    fetchDirectiveStatus();
  }, 4000);

  // Lifecycle changes deserve immediate feedback. Keep this separate from the
  // heavier dashboard refresh so a terminal run cannot disappear silently.
  useEffect(() => {
    const runId = flow01Run?.id;
    const status = flow01Run?.status?.toLowerCase();
    if (!runId || !["running", "pausing", "stopping", "cancelling"].includes(status ?? "")) return;

    let disposed = false;
    const watch = async () => {
      const detail = await apiGet<Flow01RunDetail>(`/api/workflows/flow_01/runs/${runId}`).catch(() => null);
      if (disposed || !detail?.run) return;
      const nextStatus = detail.run.status?.toLowerCase();
      setFlow01Run({
        ...detail.run,
        output_json: JSON.stringify(detail.output ?? {}),
      });
      const interruption = detail.output?.interruption;
      if (!["running", "pausing", "stopping", "cancelling"].includes(nextStatus) && interruption) {
        const key = `graphflow-interruption-${runId}-${interruption.kind}`;
        if (localStorage.getItem(key) !== "acknowledged") setFlowInterruption(interruption);
      }
    };

    void watch();
    const id = window.setInterval(() => void watch(), 750);
    return () => {
      disposed = true;
      window.clearInterval(id);
    };
  }, [flow01Run?.id, flow01Run?.status]);

  useEffect(() => {
    const id = setInterval(() => {
      if (loopStartRef.current)
        setLoopElapsed(Math.floor((Date.now() - loopStartRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const refresh = () => {
    fetchAgents();
    fetchLoop();
    fetchSuggestion();
    fetchMessages();
    fetchDirectiveStatus();
  };
  const sendExecutionCommand = async (action: ExecutionCommandAction) => {
    if (executionCommandPending) return;
    setLoopActionError("");
    setExecutionCommandNotice(null);
    setExecutionCommandPending(action);
    try {
      const health = await apiGet<{ active_project_session?: string | null }>("/api/health");
      const projectSessionId = health.active_project_session?.trim();
      if (!projectSessionId) throw new Error("No active project session.");

      const body: Record<string, unknown> = {
        action,
        project_session_id: projectSessionId,
      };
      if (action === "start_fresh" || action === "run_once") {
        const currentRound = await apiGet<{
          status?: string;
          round_number?: number;
          directive?: string;
          completion_summary?: string;
        }>(`/api/workflows/flow_01/rounds/current?session_id=${encodeURIComponent(projectSessionId)}`).catch(() => null);
        if (currentRound?.status === "locked") {
          setLockedRound({
            projectSessionId,
            roundNumber: currentRound.round_number || 1,
            directive: currentRound.directive || directiveStatus.directive_content,
            completionSummary: currentRound.completion_summary,
          });
          setNextRoundDirective("");
          return;
        }
        const validation = await apiPost<{ valid: boolean; errors: string[] }>("/api/validate/send-config", { agent_name: "manager" });
        if (!validation.valid) throw new Error(validation.errors?.join("; ") || "Agent configuration is invalid.");
        const settings = await apiGet<{ workspace_path?: string }>("/api/settings");
        body.workspace_path = settings.workspace_path || "";
        body.human_directive = directiveStatus.directive_content;
        await apiPut("/api/workflows/flow_01/directive", {
          workspace_path: body.workspace_path,
          directive: directiveStatus.directive_content,
        });
      } else {
        if (!flow01Run?.id) throw new Error("No GraphFlow run is available for this command.");
        body.run_id = flow01Run.id;
      }

      const result = await apiPost<{
        ok?: boolean;
        message?: string;
        error?: string;
        error_code?: string;
        round?: { round_number?: number; directive?: string; completion_summary?: string };
      }>("/api/workflows/flow_01/execution/command", body);
      if (result.error_code === "round_locked" && result.round) {
        setLockedRound({
          projectSessionId,
          roundNumber: result.round.round_number || 1,
          directive: result.round.directive || directiveStatus.directive_content,
          completionSummary: result.round.completion_summary,
        });
      }
      setExecutionCommandNotice({
        kind: "success",
        title: "Command completed",
        message: result.message || "The server confirmed the new GraphFlow state.",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "The GraphFlow command failed.";
      setLoopActionError(message);
      setExecutionCommandNotice({ kind: "error", title: "Command failed", message });
    } finally {
      setExecutionCommandPending(null);
      refresh();
    }
  };
  const hasStartContext = directiveStatus.has_directive;
  const planApiPrefix = flow01Mode ? "/api/workflows/flow_01" : "/api";
  const canPauseOrResumeFlow01 = flow01Mode && flow01Run?.id != null && isFlow01Actionable(flow01Run);

  return (
    <ToastProvider>
    <ErrorBoundary>
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Header */}
      <header className="h-[50px] shrink-0 flex items-center px-4 gap-3" style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--border)" }}>
        <RunStatus loop={loop} agents={agents} pending={loopActionPending} />
        <BrandTitle />
        <LanguageSelector />
        <button
          onClick={toggleDark}
          className="px-2 py-0.5 rounded text-[11px] font-medium transition-colors"
          style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
        >{isDark ? "☀️" : "🌙"}</button>
        <div className="ml-auto flex items-center gap-1.5">
          <span
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={{ background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}
            title="Using GraphFlow run lifecycle API"
          >GraphFlow</span>
          <button
            onClick={toggleThinking}
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={thinkingEnabled
              ? { background: "var(--purple-bg)", color: "var(--purple)", border: "1px solid var(--purple-dim)" }
              : { background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }
            }
            title={thinkingEnabled ? thinkingText.enabledTitle : thinkingText.disabledTitle}
          >{thinkingText.toggle} {thinkingEnabled ? thinkingText.on : thinkingText.off}</button>
          <button
            onClick={async () => {
              if (flow01Mode) {
                await sendExecutionCommand(
                  loop.running
                    ? "stop"
                    : isFlow01Paused(flow01Run?.status)
                      ? "resume"
                      : "start_fresh",
                );
                return;
              }
              debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Start Loop button clicked", "frontend-click");
              debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] loop.running=" + loop.running + " directiveStatus.has_directive=" + directiveStatus.has_directive, "frontend-click");
              setLoopActionError("");
              setLoopActionPending(loop.running ? "stopping" : "starting");
              if (loop.running) {
                if (flow01Mode) {
                  try {
                    const latestRun = flow01Run?.id
                      ? flow01Run
                      : getFlow01Runs(await apiGet<Flow01RunsResponse>("/api/workflows/flow_01/runs?limit=20").catch(() => [])).find(isFlow01Actionable);
                    if (!latestRun?.id) {
                      setLoopActionError("No GraphFlow run found to cancel");
                      setLoopActionPending(null);
                      fetchLoop();
                      return;
                    }
                    const result = await apiPost<{ ok?: boolean; run_id?: number; status?: string }>(`/api/workflows/flow_01/runs/${latestRun.id}/cancel`, {
                      reason: "ui_stop_button",
                      stop_worker: true,
                    });
                    setFlow01Run({ ...latestRun, status: result.status || "cancelling" });
                  } catch (err) {
                    setLoopActionError(err instanceof Error ? err.message : "Cancel GraphFlow run failed");
                  }
                  fetchLoop();
                  fetchSuggestion();
                  fetchMessages();
                  setLoopActionPending(null);
                  return;
                }
                try {
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Checking active work before stop...", "frontend-click");
                  const status = await apiGet<{ active_work: boolean; reason: string }>("/api/runtime/active-work");
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] active_work status=" + JSON.stringify(status), "frontend-click");
                  if (status.active_work) {
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Active work detected, showing confirmation dialog", "frontend-click");
                    setConfirmClose(true);
                    setLoopActionPending(null);
                    return;
                  }
                } catch {
                  // Active-work check is best-effort; stopping should still work if it is unavailable.
                }
                try {
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Calling apiPost /api/loop/stop", "frontend-click");
                  await apiPost("/api/loop/stop");
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Stop request sent OK", "frontend-click");
                  fetchLoop();
                } catch (err) {
                  setLoopActionError(err instanceof Error ? err.message : "Stop Loop failed");
                } finally {
                  setLoopActionPending(null);
                }
              } else {
                try {
                  if (flow01Mode) {
                    const validation = await apiPost<{valid: boolean; errors: string[]}>("/api/validate/send-config", { agent_name: "manager" });
                    if (!validation.valid && validation.errors?.length > 0) {
                      setLoopActionError("Config error: " + validation.errors.join("; "));
                      setLoopActionPending(null);
                      fetchLoop();
                      return;
                    }
                    const settings = await apiGet<{ workspace_path?: string }>("/api/settings").catch((): { workspace_path?: string } => ({}));
                    const health = await apiGet<{ active_project_session?: string | null }>("/api/health");
                    const projectSessionId = health.active_project_session?.trim();
                    if (!projectSessionId) {
                      setLoopActionError("No active project session");
                      setLoopActionPending(null);
                      return;
                    }
                    await apiPut("/api/workflows/flow_01/directive", {
                      workspace_path: settings.workspace_path,
                      directive: directiveStatus.directive_content,
                    });
                    const result = await apiPost<{ ok?: boolean; run_id?: number; status?: string; task?: string; error?: string; error_code?: string; round?: { round_number?: number; directive?: string; completion_summary?: string } }>("/api/workflows/flow_01/runs/0/start", {
                      project_session_id: projectSessionId,
                      workspace_path: settings.workspace_path,
                      human_directive: directiveStatus.directive_content,
                    });
                    if (result.error_code === "round_locked" && result.round) {
                      setLockedRound({
                        projectSessionId,
                        roundNumber: result.round.round_number || 1,
                        directive: result.round.directive || directiveStatus.directive_content,
                        completionSummary: result.round.completion_summary,
                      });
                      setNextRoundDirective("");
                      setLoopActionPending(null);
                      return;
                    }
                    if (result.ok === false) throw new Error(result.error || "GraphFlow start failed");
                    if (result.run_id != null) {
                      setFlow01Run({ id: result.run_id, status: result.status || "running", task: result.task });
                    }
                  } else {
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] Calling apiPost /api/loop/start ...", "frontend-click");
                    const validation = await apiPost<{valid: boolean; errors: string[]}>("/api/validate/send-config", { agent_name: "manager" });
                    if (!validation.valid && validation.errors?.length > 0) {
                      setLoopActionError("Config error: " + validation.errors.join("; "));
                      setLoopActionPending(null);
                      fetchLoop();
                      return;
                    }
                    const result = await apiPost<{ok?:boolean; started?:boolean; running?:boolean; pid?:number|null} | null>("/api/loop/start");
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] apiPost /api/loop/start returned OK, result = " + JSON.stringify(result), "frontend-click");
                  }
                  fetchLoop();
                  fetchSuggestion();
                  fetchMessages();
                  setLoopActionPending(null);
                } catch (err) {
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] apiPost /api/loop/start FAILED: " + (err instanceof Error ? err.message : String(err)), "frontend-click");
                  setLoopActionError(err instanceof Error ? err.message : "Start Loop failed");
                  setLoopActionPending(null);
                  fetchLoop();
                }
              }
            }}
            disabled={executionCommandPending !== null || loopActionPending !== null || (!loop.running && !hasStartContext)}
            className="px-2.5 py-1 rounded text-[11px] font-semibold transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            style={loop.running
              ? { background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }
              : { background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }
            }
            title={!loop.running && !hasStartContext ? "Enter a Human Directive first" : undefined}
          >{loopActionPending === "starting"
            ? "Starting..."
            : loopActionPending === "stopping"
              ? "Stopping..."
              : loop.running ? "⏹ Stop" : "▶ Start Loop"}</button>
          {(
            <button
              onClick={async () => {
                if (flow01Run?.id == null || !isFlow01Actionable(flow01Run)) return;
                await sendExecutionCommand(isFlow01Paused(flow01Run.status) ? "resume" : "pause");
              }}
              disabled={!canPauseOrResumeFlow01 || executionCommandPending !== null}
              className="px-2 py-1 rounded text-[10px] font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              style={isFlow01Paused(flow01Run?.status)
                ? { background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }
                : isFlow01Running(flow01Run?.status)
                ? { background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }
                : { background: "var(--bg-panel)", color: "var(--text-dim)", border: "1px solid var(--border)" }
              }
              title={!flow01Mode ? "Switch to GraphFlow to pause or resume runs" : isFlow01Paused(flow01Run?.status) ? "Resume from last checkpoint" : isFlow01Running(flow01Run?.status) ? "Pause before current step" : "No active or paused GraphFlow run"}
            >{isFlow01Paused(flow01Run?.status) ? "▶ Resume" : isFlow01Running(flow01Run?.status) ? "⏸ Pause" : "Pause/Resume"}</button>
          )}
          <button
            onClick={async () => {
              if (flow01Mode) {
                await sendExecutionCommand("run_once");
                return;
              }
              setLoopActionError("");
              try {
                const validation = await apiPost<{valid: boolean; errors: string[]}>("/api/validate/send-config", { agent_name: "manager" });
                if (!validation.valid && validation.errors?.length > 0) {
                  setLoopActionError("Config error: " + validation.errors.join("; "));
                  return;
                }
                await apiPost("/api/run-cycle");
                fetchAgents();
              } catch (err) {
                setLoopActionError(err instanceof Error ? err.message : "Run Once failed");
              }
            }}
            disabled={loop.running || isFlow01Paused(flow01Run?.status) || !hasStartContext || executionCommandPending !== null}
            className="px-2.5 py-1 rounded text-[11px] font-medium disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            title={!hasStartContext ? "Enter a Human Directive first" : undefined}
          >↺ Run Once</button>
          <button
            onClick={toggleAutoRelease}
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={autoRelease
              ? { background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }
              : { background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }
            }
            title={autoRelease
              ? "ON — suggestions go straight to worker. Click to pause next suggestion."
              : "OFF — next suggestion will pause at 'pending'. Click to re-enable auto-release."}
          >{autoRelease ? "🔓 Auto Release" : "⏸ Auto Release Off"}</button>
          <button
            onClick={async () => {
              const wsList = await apiGet<{id:string;active?:boolean}[]>("/api/workspaces").catch(() => []);
              const activeWs = wsList.find(w => w.active);
              if (activeWs) {
                await apiPost(`/api/workspaces/${activeWs.id}/new-session`);
              } else {
                await apiPost("/api/session/reset");
              }
              setSessionReloadKey(k => k + 1);
              // t8b: directive is per-session in user_directives, so the new
              // session starts empty. Copy the current directive forward so
              // "New Session" within the same project doesn't lose the user's
              // typed text.
              try {
                const current = await apiGet<{ content: string }>("/api/files/user_input");
                if (current?.content?.trim()) {
                  await apiPut("/api/files/user_input", { content: current.content });
                }
              } catch {
                // best-effort: if the copy fails, the new session starts empty
              }
              refresh();
            }}
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            onMouseEnter={e => { e.currentTarget.style.borderColor="var(--red-dim)"; e.currentTarget.style.color="var(--red)"; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor="var(--border)"; e.currentTarget.style.color="var(--text-secondary)"; }}
          >⊘ New Session</button>
          <button
            onClick={() => setConfirmClear(true)}
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
            onMouseEnter={e => { e.currentTarget.style.borderColor="var(--amber-dim)"; e.currentTarget.style.color="var(--amber)"; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor="var(--border)"; e.currentTarget.style.color="var(--text-secondary)"; }}
          >🗑 Clear</button>
          <BackgroundServerButton
            onClick={() => setShowServerModal(true)}
            count={serverCount}
            hasErrors={!!loopActionError}
            hasWarnings={!!runtimeFlags.mcp_detected || !!runtimeFlags.streaming_fallbacked}
          />
        </div>
      </header>

      {/* Pipeline status bar */}
      <PipelineBar
        agents={agents}
        suggestion={suggestion}
        loopRunning={loop.running}
        flow01Phase={flow01Mode ? flow01Run?.phase : null}
        flow01Status={flow01Mode ? flow01Run?.status : null}
        flow01GraphStep={flow01Mode ? flow01Run?.graph_step : null}
        onAgentsRefresh={fetchAgents}
      />

      {/* Four-column layout (each rail is collapsible) */}
      <div className="flex-1 flex min-h-0">
        {leftOpen ? (
          <div className="relative shrink-0 flex min-h-0">
            <LeftRail
              agents={agents}
              activeAgent={activeAgent}
              onSelectAgent={selectAgent}
              loopStatus={loop}
              onLoopChange={async () => {
                setSessionReloadKey(k => k + 1);
                setDirectiveClearKey(k => k + 1);
                await Promise.all([
                  fetchAgents(),
                  fetchLoop(),
                  fetchSuggestion(),
                  fetchDirectiveStatus(),
                ]);
              }}
              onRunOnce={fetchAgents}
              onReset={() => apiPost("/api/session/reset").then(refresh)}
              sessionReloadKey={sessionReloadKey}
            />
            <RailToggle side="right" onClick={() => closeRail("left")} title="Collapse left rail">‹</RailToggle>
          </div>
        ) : (
          <RailStub side="left" onClick={() => tryOpen("left", "Projects")} label="Projects" />
        )}

        <CenterFeed
          key={`center-${sessionReloadKey}`}
          agents={agents}
          activeAgent={activeAgent}
          onSelectAgent={selectAgent}
          loopRunning={loop.running}
          loopElapsed={loopElapsed}
          onRefresh={refresh}
        />

        {planOpen ? (
          <div className="relative shrink-0 flex min-h-0">
            <PlanningTodoRail key={`plan-${sessionReloadKey}`} clearKey={directiveClearKey} apiPrefix={planApiPrefix} />
            <RailToggle side="left" onClick={() => closeRail("plan")} title="Collapse plan/todo rail">›</RailToggle>
          </div>
        ) : (
          <RailStub side="right" onClick={() => tryOpen("plan", "Plan / Todo")} label="Plan / Todo" />
        )}

        {rightOpen ? (
          <div className="relative shrink-0 flex min-h-0">
            <RightRail
              key={`right-${sessionReloadKey}`}
              suggestion={suggestion}
              onSuggestionAction={fetchSuggestion}
              onProjectRefresh={() => {
                setDirectiveClearKey(key => key + 1);
                refresh();
              }}
              directiveClearKey={directiveClearKey}
              flow01Mode={flow01Mode}
            />
            <RailToggle side="left" onClick={() => closeRail("right")} title="Collapse right rail">›</RailToggle>
          </div>
        ) : (
          <RailStub side="right" onClick={() => tryOpen("right", "Status")} label="Status" />
        )}
      </div>

      {tooNarrow && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setTooNarrow(null)}
        >
          <div
            className="max-w-sm w-full rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--amber-dim)", boxShadow: "0 8px 24px rgba(0,0,0,0.6)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--amber)" }}>⚠</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--amber)" }}>Window too narrow</span>
            </div>
            <p className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-primary)" }}>{tooNarrow}</p>
            <div className="flex justify-end">
              <button
                onClick={() => setTooNarrow(null)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
              >OK</button>
            </div>
          </div>
        </div>
      )}

      {apiError && createPortal(
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.68)" }}
          onClick={() => setApiError(null)}
        >
          <div
            className="max-w-lg w-full rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--red-dim)", boxShadow: "0 8px 24px rgba(0,0,0,0.65)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--red)" }}>!</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--red)" }}>API Error</span>
            </div>
            {apiError.path && (
              <p className="text-[10px] font-mono mb-2 break-all" style={{ color: "var(--text-dim)" }}>{apiError.path}</p>
            )}
            <p className="text-[12px] leading-relaxed whitespace-pre-wrap break-words mb-3" style={{ color: "var(--text-primary)" }}>
              {apiError.message}
            </p>
            <div className="flex justify-end">
              <button
                onClick={() => setApiError(null)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
              >Close</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {runtimeRecoveryPrompt && createPortal(
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.72)" }}
        >
          <div
            className="max-w-md w-full rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--amber-dim)", boxShadow: "0 12px 32px rgba(0,0,0,0.7)" }}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--amber)" }}>!</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--amber)" }}>OpenCode is unavailable</span>
            </div>
            <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-primary)" }}>
              No reachable OpenCode serve is available. Manager, Worker, Reviewer, and Chat cannot run until the server is restored.
            </p>
            <p className="mt-2 text-[11px] leading-relaxed" style={{ color: "var(--text-secondary)" }}>
              Restart the managed OpenCode serve now?
            </p>
            {runtimeRecoveryError && (
              <p className="mt-2 text-[11px] break-words" style={{ color: "var(--red)" }}>
                {runtimeRecoveryError}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => {
                  runtimeRecoveryDismissedRef.current = true;
                  setRuntimeRecoveryPrompt(false);
                  setRuntimeRecoveryError("");
                }}
                disabled={runtimeRecoveryBusy}
                className="px-3 py-1 rounded text-[11px] font-medium disabled:opacity-50"
                style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >
                Not now
              </button>
              <button
                onClick={() => void restartOpenCode()}
                disabled={runtimeRecoveryBusy}
                className="px-3 py-1 rounded text-[11px] font-medium disabled:opacity-50"
                style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
              >
                {runtimeRecoveryBusy ? "Restarting..." : "Restart OpenCode"}
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {confirmClear && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setConfirmClear(false)}
        >
          <div
            className="max-w-sm w-full rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--amber-dim)", boxShadow: "0 8px 24px rgba(0,0,0,0.6)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--amber)" }}>⚠</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--amber)" }}>Clear this session?</span>
            </div>
            <p className="text-[12px] leading-relaxed mb-1" style={{ color: "var(--text-primary)" }}>
              Clears: streams, worker report, manager feedback, suggestion queue, messages, plan + todo list, handoff.
            </p>
            <p className="text-[11px] leading-relaxed mb-3" style={{ color: "var(--text-secondary)" }}>
              The <span style={{ color: "var(--amber)" }}>Human Directive</span> is kept — it has its own Clear button inside the right rail.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmClear(false)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >Cancel</button>
              <button
                onClick={async () => {
                  setConfirmClear(false);
                  await apiPost("/api/clear-all");
                  setDirectiveClearKey(k => k + 1);
                  refresh();
                }}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
              >Clear</button>
            </div>
          </div>
        </div>
      )}

      {confirmClose && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center px-6"
          style={{ background: "rgba(0,0,0,0.65)" }}
          onClick={() => setConfirmClose(false)}
        >
          <div
            className="max-w-sm w-full rounded p-4"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--red-dim)", boxShadow: "0 8px 24px rgba(0,0,0,0.6)" }}
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[14px]" style={{ color: "var(--red)" }}>⚠</span>
              <span className="text-[12px] font-semibold" style={{ color: "var(--red)" }}>Stop Active Work?</span>
            </div>
            <p className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-primary)" }}>
              There is active work in progress. Stopping the loop now will interrupt the current task.
              Are you sure you want to stop?
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmClose(false)}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
              >Continue Running</button>
              <button
                onClick={async () => {
                  setConfirmClose(false);
                  await apiPost("/api/loop/stop");
                  fetchLoop();
                }}
                className="px-3 py-1 rounded text-[11px] font-medium"
                style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }}
              >Stop Anyway</button>
            </div>
          </div>
        </div>
      )}

      <BackgroundServerModal
        open={showServerModal}
        onClose={() => setShowServerModal(false)}
        warnings={runtimeFlags}
        loopActionError={loopActionError}
        flow01Run={flow01Run}
        flow01Mode={flow01Mode}
      />
      {flowInterruption && flow01Run && (
        <FlowInterruptionModal
          interruption={flowInterruption}
          onClose={() => {
            localStorage.setItem(
              `graphflow-interruption-${flow01Run.id}-${flowInterruption.kind}`,
              "acknowledged",
            );
            setFlowInterruption(null);
          }}
        />
      )}
      <ExecutionCommandOverlay
        pending={executionCommandPending}
        notice={executionCommandNotice}
        onClose={() => setExecutionCommandNotice(null)}
      />
      <OpenCodeQuestionModal />

      {lockedRound && (
        <div className="fixed inset-0 z-[85] flex items-center justify-center px-6" style={{ background: "rgba(0,0,0,0.7)" }}>
          <div className="max-w-xl w-full rounded p-4" style={{ background: "var(--bg-panel)", border: "1px solid var(--blue-dim)", boxShadow: "0 12px 32px rgba(0,0,0,0.65)" }}>
            <div className="flex items-center justify-between mb-3">
              <div>
                <p className="text-[12px] font-semibold" style={{ color: "var(--blue)" }}>Round {lockedRound.roundNumber} is complete</p>
                <p className="text-[10px]" style={{ color: "var(--text-dim)" }}>A new directive is required before GraphFlow can start again.</p>
              </div>
              <button onClick={() => setLockedRound(null)} className="text-[16px]" style={{ color: "var(--text-dim)" }} title="Close">×</button>
            </div>
            <div className="rounded p-3 mb-3" style={{ background: "var(--bg-base)", border: "1px solid var(--border)" }}>
              <p className="text-[9px] font-semibold uppercase mb-1" style={{ color: "var(--text-dim)" }}>Completed directive</p>
              <p className="text-[11px] whitespace-pre-wrap max-h-32 overflow-y-auto" style={{ color: "var(--text-secondary)" }}>{lockedRound.directive}</p>
              {lockedRound.completionSummary && <p className="text-[10px] mt-2" style={{ color: "var(--green)" }}>{lockedRound.completionSummary}</p>}
            </div>
            <textarea
              value={nextRoundDirective}
              onChange={event => setNextRoundDirective(event.target.value)}
              rows={4}
              placeholder="Enter a new directive for the next round..."
              className="w-full resize-none rounded p-2 text-[12px] outline-none mb-3"
              style={{ background: "var(--bg-base)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
            />
            <div className="flex flex-wrap justify-between gap-2">
              <div className="flex gap-2">
                <button onClick={() => {
                  tryOpen("right", "Status");
                  window.setTimeout(() => window.dispatchEvent(new CustomEvent("task-hounds-open-manager-chat", { detail: { prompt: "Research this completed project and propose a focused new directive for the next round. Do not modify the completed round." } })), 100);
                  setLockedRound(null);
                }} className="px-3 py-1.5 rounded text-[11px]" style={{ background: "var(--bg-base)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }}>Research</button>
                <button onClick={() => {
                  tryOpen("right", "Status");
                  window.setTimeout(() => window.dispatchEvent(new CustomEvent("task-hounds-open-manager-chat", { detail: { prompt: "Brainstorm optional improvements for this completed project and propose a focused new directive for the next round. Do not modify the completed round." } })), 100);
                  setLockedRound(null);
                }} className="px-3 py-1.5 rounded text-[11px]" style={{ background: "var(--bg-base)", color: "var(--purple)", border: "1px solid var(--purple-dim)" }}>Brainstorm</button>
              </div>
              <button
                disabled={!nextRoundDirective.trim() || nextRoundDirective.trim() === lockedRound.directive.trim()}
                onClick={async () => {
                  await apiPost("/api/workflows/flow_01/rounds/new", {
                    project_session_id: lockedRound.projectSessionId,
                    directive: nextRoundDirective.trim(),
                  });
                  await apiPut("/api/workflows/flow_01/directive", { directive: nextRoundDirective.trim() });
                  setLockedRound(null);
                  setNextRoundDirective("");
                  setDirectiveClearKey(key => key + 1);
                  refresh();
                }}
                className="px-3 py-1.5 rounded text-[11px] font-semibold disabled:opacity-40"
                style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
              >Create Next Round</button>
            </div>
          </div>
        </div>
      )}
    </div>
    </ErrorBoundary>
    </ToastProvider>
  );
}
