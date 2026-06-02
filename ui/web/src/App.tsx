import React, { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPost, apiPut, debugLog } from "./lib/api";
import type { Agent, LoopStatus, Suggestion, ManagerMessage } from "./lib/api";
import { LeftRail } from "./components/layout/LeftRail";
import { CenterFeed } from "./components/layout/CenterFeed";
import { RightRail } from "./components/layout/RightRail";
import { PlanningTodoRail } from "./components/layout/PlanningTodoRail";
import { usePolling } from "./hooks/usePolling";
import { ErrorBoundary } from "./components/ErrorBoundary";

// ── Compact LanguageSelector for header pill ─────────────────────────────────
const PRESET_LANGS = [
  { code: "en",    label: "EN",    full: "English" },
  { code: "zh-tw", label: "繁中",  full: "繁體中文" },
  { code: "ja",    label: "日文",  full: "日本語" },
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
        if (d.language) setLang(d.language);
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

          {/* 自定義 row */}
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
              + 自定義
            </button>
)}
        </div>
      )}
    </div>
  );
}

  // ── RunStatus dot (header, left of brand) ───────────────────────────────────
function RunStatus({ loop, agents }: { loop: LoopStatus; agents: Agent[] }) {
  const busy = agents.filter(a => a.state === "busy").length;
  let color = "var(--text-dim)";
  let label = "Idle";
  if (loop.running && busy > 0) { color = "var(--green)"; label = `${busy} active`; }
  else if (loop.running) { color = "var(--amber)"; label = "Running"; }
  return (
    <span className="flex items-center gap-1 text-[11px] font-medium" style={{ color }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
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
  flow01Task,
  onAgentsRefresh,
}: {
  agents: Agent[];
  suggestion: Suggestion | null;
  loopRunning: boolean;
  flow01Phase?: string | null;
  flow01Status?: string | null;
  flow01Task?: string | null;
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
    const cancelled = flow01Status === "cancelled" || flow01Status === "cancelling";
    const flowSteps: Step[] = [
      {
        label: "Directive",
        description: "Human directive received",
        state: "completed",
        color: "var(--amber)",
      },
      {
        label: "Manager",
        description: phase === "manager_running" ? "Manager agent is building prompt / calling LLM / parsing task JSON" : (flow01Task || "Manager selected one task"),
        state: phase === "manager_running" ? "active" : phase ? "completed" : "pending",
        color: "var(--blue)",
      },
      {
        label: "Worker",
        description: phase === "worker_running" ? (flow01Task || "Worker agent executing manager-selected task") : "Worker report recorded",
        state: phase === "worker_running" ? "active" :
          (phase === "reviewer_running" || phase === "completed" || phase.startsWith("cancelled_after")) ? "completed" : "pending",
        color: "#f97316",
      },
      {
        label: "Reviewer",
        description: phase === "reviewer_running" ? "Reviewer agent checking QA, bugs, UX, and risks" : "Reviewer feedback",
        state: phase === "reviewer_running" ? "active" : done ? "completed" : "pending",
        color: "var(--green)",
      },
      {
        label: cancelled ? "Cancelled" : "Done",
        description: cancelled ? "Workflow cancellation requested" : "Workflow completed",
        state: cancelled ? "error" : done ? "completed" : "pending",
        color: cancelled ? "var(--red)" : "var(--green)",
      },
    ];
    return (
      <div className="flex items-center gap-0 px-4 py-2 bg-[var(--bg-panel)] border-b border-[var(--border)] text-[11px] shrink-0 overflow-x-auto">
        <div className="flex items-center mr-3 shrink-0">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-dim)]">flow_01</span>
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
      color: "#f97316",
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
                      ? "text-[#111]"
                      : s.state === "completed"
                      ? ""
                      : "text-[#4b5563]"
                  }`}
                  style={{ color: s.state === "active" ? "#111" : getStateColor(s) }}
                >
                  {s.label}
                </span>
              </div>
              {/* Description */}
              <div
                className={`text-[9px] mt-0.5 transition-all ${
                  s.state === "active"
                    ? "text-[#111] opacity-80"
                    : s.state === "completed"
                    ? ""
                    : "text-[#4b5563]"
                }`}
                style={{ color: s.state === "active" ? "#111" : s.state === "completed" ? getStateColor(s) : "#4b5563" }}
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
  const [suggestion,   setSuggestion]  = useState<Suggestion | null>(null);
  const [messages,     setMessages]    = useState<ManagerMessage[]>([]);
  const [hasDirective, setHasDirective] = useState(false);
  const [directiveStatus, setDirectiveStatus] = useState({has_directive: false, directive_content: ""});
  const [loopActionError, setLoopActionError] = useState("");
  const [autoRelease, setAutoRelease] = useState(true);
  const [flow01Mode, setFlow01Mode] = useState(() => localStorage.getItem("workflowMode") === "flow_01");
  const [flow01Run, setFlow01Run] = useState<{ id?: number; status?: string; phase?: string; task?: string } | null>(null);
  const [sessionReloadKey, setSessionReloadKey] = useState(0);
  const [directiveClearKey, setDirectiveClearKey] = useState(0);
  const [apiError, setApiError] = useState<{ message: string; path: string } | null>(null);

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

  // Load auto_release flag from backend settings on mount
  useEffect(() => {
    apiGet<{ auto_release?: boolean }>("/api/settings")
      .then(s => setAutoRelease(s.auto_release !== false))
      .catch(() => {});
  }, []);

  const toggleAutoRelease = async () => {
    const next = !autoRelease;
    setAutoRelease(next);
    await apiPut("/api/settings", { auto_release: next }).catch(() => {});
  };
  const toggleFlow01Mode = () => {
    setFlow01Mode(prev => {
      const next = !prev;
      localStorage.setItem("workflowMode", next ? "flow_01" : "default");
      return next;
    });
  };
  const [leftOpen, setLeftOpen]     = useState(true);
  const [planOpen, setPlanOpen]     = useState(true);
  const [rightOpen, setRightOpen]   = useState(true);
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

  // Minimum width for the Output/Center rail. Opening another rail must not
  // squeeze CenterFeed below this; otherwise we refuse and show a toast.
  const MIN_CENTER_PX  = 400;
  const RAIL_W = { left: 192, plan: 320, right: 288 } as const; // matches w-48 / w-80 / w-72
  const STUB_W = 24;                                            // matches RailStub w-6

  const [tooNarrow, setTooNarrow] = useState<null | string>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

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
      const data = await apiGet<{ runs: Array<{ id: number; status: string; phase?: string; task?: string }> }>("/api/workflows/flow_01/runs?limit=1").catch(() => ({ runs: [] }));
      const latest = data.runs?.[0] || null;
      setFlow01Run(latest);
      const running = latest?.status === "running" || latest?.status === "cancelling";
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
    const data = await apiGet<ManagerMessage[]>(flow01Mode ? "/api/workflows/flow_01/manager-messages" : "/api/manager-messages").catch(() => []);
    setMessages(data);
  }, [flow01Mode]);

  const fetchDirective = useCallback(async () => {
    const d = await apiGet<{ has_content: boolean }>("/api/user-input/has-content").catch(() => ({ has_content: false }));
    setHasDirective(d.has_content);
  }, []);

  const fetchDirectiveStatus = useCallback(async () => {
    const d = await apiGet<{ has_directive: boolean; directive_content: string }>("/api/directive/status").catch(() => ({ has_directive: false, directive_content: "" }));
    setDirectiveStatus(d);
  }, []);

  usePolling(() => {
    fetchAgents();
    fetchLoop();
    fetchSuggestion();
    fetchMessages();
    fetchDirective();
    fetchDirectiveStatus();
  }, 4000);

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
    fetchDirective();
    fetchDirectiveStatus();
  };
  const hasStartContext = directiveStatus.has_directive;
  const planApiPrefix = flow01Mode ? "/api/workflows/flow_01" : "/api";

  return (
    <ErrorBoundary>
    <div className="h-screen flex flex-col overflow-hidden" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>
      {/* Header */}
      <header className="h-[50px] shrink-0 flex items-center px-4 gap-3" style={{ background: "var(--bg-panel)", borderBottom: "1px solid var(--border)" }}>
        <RunStatus loop={loop} agents={agents} />
        <BrandTitle />
        <LanguageSelector />
        <button
          onClick={toggleDark}
          className="px-2 py-0.5 rounded text-[11px] font-medium transition-colors"
          style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
        >{isDark ? "☀️" : "🌙"}</button>
        <div className="ml-auto flex items-center gap-1.5">
            {loopActionError && (
              <span className="text-[10px] max-w-[220px] truncate" style={{ color: "var(--red)" }} title={loopActionError}>
                {loopActionError}
              </span>
            )}
          {flow01Mode && flow01Run?.id && (
            <span className="text-[10px] max-w-[180px] truncate" style={{ color: "var(--blue)" }} title={flow01Run.task || ""}>
              flow_01 #{flow01Run.id} {flow01Run.status}
            </span>
          )}
          <button
            onClick={toggleFlow01Mode}
            className="px-2.5 py-1 rounded text-[11px] font-medium transition-colors"
            style={flow01Mode
              ? { background: "var(--blue-bg)", color: "var(--blue)", border: "1px solid var(--blue-dim)" }
              : { background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }
            }
            title={flow01Mode ? "Using flow_01 fake DB and start-loop API" : "Using default Task Hounds loop"}
          >{flow01Mode ? "flow_01" : "default"}</button>
          <button
            onClick={async () => {
              debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Start Loop button clicked", "frontend-click");
              debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] loop.running=" + loop.running + " directiveStatus.has_directive=" + directiveStatus.has_directive, "frontend-click");
              setLoopActionError("");
              if (loop.running) {
                if (flow01Mode) {
                  try {
                    const latestRun = flow01Run?.id
                      ? flow01Run
                      : (await apiGet<{ runs: Array<{ id: number; status: string; phase?: string; task?: string }> }>("/api/workflows/flow_01/runs?limit=1").catch(() => ({ runs: [] }))).runs?.[0];
                    if (!latestRun?.id) {
                      setLoopActionError("No flow_01 run found to cancel");
                      fetchLoop();
                      return;
                    }
                    const result = await apiPost<{ ok?: boolean; run_id?: number; status?: string }>(`/api/workflows/flow_01/runs/${latestRun.id}/cancel`, {
                      reason: "ui_stop_button",
                      stop_worker: true,
                    });
                    setFlow01Run({ ...latestRun, status: result.status || "cancelling" });
                  } catch (err) {
                    setLoopActionError(err instanceof Error ? err.message : "Cancel flow_01 run failed");
                  }
                  fetchLoop();
                  fetchSuggestion();
                  fetchMessages();
                  return;
                }
                try {
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Checking active work before stop...", "frontend-click");
                  const status = await apiGet<{ active_work: boolean; reason: string }>("/api/runtime/active-work");
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] active_work status=" + JSON.stringify(status), "frontend-click");
                  if (status.active_work) {
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Active work detected, showing confirmation dialog", "frontend-click");
                    setConfirmClose(true);
                    return;
                  }
                } catch { }
                debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Calling apiPost /api/loop/stop", "frontend-click");
                await apiPost("/api/loop/stop");
                debugLog("[DEBUG-LAUNCH-PAD] [STEP 1] Stop request sent OK", "frontend-click");
                fetchLoop();
              } else {
                try {
                  if (flow01Mode) {
                    const settings = await apiGet<{ workspace_path?: string }>("/api/settings").catch((): { workspace_path?: string } => ({}));
                    await apiPut("/api/workflows/flow_01/directive", {
                      workspace_path: settings.workspace_path,
                      directive: directiveStatus.directive_content,
                    });
                    const result = await apiPost<{ ok?: boolean; run_id?: number; status?: string; task?: string }>("/api/workflows/flow_01/start-loop", {
                      workspace_path: settings.workspace_path,
                      directive: directiveStatus.directive_content,
                      suggested_task: "Start from the current Human Directive and create the first observable worker task.",
                      emit_real_ui_signals: true,
                      use_real_worker: true,
                    });
                    setFlow01Run({ id: result.run_id, status: result.status, task: result.task });
                  } else {
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] Calling apiPost /api/loop/start ...", "frontend-click");
                    const result = await apiPost<{ok?:boolean; started?:boolean; running?:boolean; pid?:number|null} | null>("/api/loop/start");
                    debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] apiPost /api/loop/start returned OK, result = " + JSON.stringify(result), "frontend-click");
                  }
                  fetchLoop();
                  fetchSuggestion();
                  fetchMessages();
                } catch (err) {
                  debugLog("[DEBUG-LAUNCH-PAD] [STEP 2+3] apiPost /api/loop/start FAILED: " + (err instanceof Error ? err.message : String(err)), "frontend-click");
                  setLoopActionError(err instanceof Error ? err.message : "Start Loop failed");
                  fetchLoop();
                }
              }
            }}
            disabled={!loop.running && !hasStartContext}
            className="px-2.5 py-1 rounded text-[11px] font-semibold transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
            style={loop.running
              ? { background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red-dim)" }
              : { background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }
            }
            title={!loop.running && !hasStartContext ? "Enter a Human Directive first" : undefined}
          >{loop.running ? "⏹ Stop" : "▶ Start Loop"}</button>
          <button
            onClick={async () => {
              setLoopActionError("");
              try {
                await apiPost("/api/run-cycle");
                fetchAgents();
              } catch (err) {
                setLoopActionError(err instanceof Error ? err.message : "Run Once failed");
              }
            }}
            disabled={loop.running || !hasStartContext}
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
              setDirectiveClearKey(k => k + 1);
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
        </div>
      </header>

      {/* Pipeline status bar */}
      <PipelineBar
        agents={agents}
        suggestion={suggestion}
        loopRunning={loop.running}
        flow01Phase={flow01Mode ? flow01Run?.phase : null}
        flow01Status={flow01Mode ? flow01Run?.status : null}
        flow01Task={flow01Mode ? flow01Run?.task : null}
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
              onLoopChange={(scope) => {
                if (scope === "workspace") setHasDirective(false);
                fetchLoop();
                fetchSuggestion();
                fetchMessages();
                fetchDirective();
                setDirectiveClearKey(k => k + 1);
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
          agents={agents}
          activeAgent={activeAgent}
          onSelectAgent={selectAgent}
          loopRunning={loop.running}
          loopElapsed={loopElapsed}
          onRefresh={refresh}
        />

        {planOpen ? (
          <div className="relative shrink-0 flex min-h-0">
            <PlanningTodoRail clearKey={directiveClearKey} apiPrefix={planApiPrefix} />
            <RailToggle side="left" onClick={() => closeRail("plan")} title="Collapse plan/todo rail">›</RailToggle>
          </div>
        ) : (
          <RailStub side="right" onClick={() => tryOpen("plan", "Plan / Todo")} label="Plan / Todo" />
        )}

        {rightOpen ? (
          <div className="relative shrink-0 flex min-h-0">
            <RightRail
              suggestion={suggestion}
              messages={messages}
              onSuggestionAction={fetchSuggestion}
              onMessagesRefresh={fetchMessages}
              directiveClearKey={directiveClearKey}
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
    </div>
    </ErrorBoundary>
  );
}
