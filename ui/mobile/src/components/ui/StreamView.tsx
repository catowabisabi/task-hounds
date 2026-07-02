import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { isOnline } from "../../lib/api";
import { JsonTree } from "./JsonTree";

type EvText       = { t: "text";       text: string; ts?: number };
type EvThink      = { t: "think";      text: string; ts?: number };
type EvTool       = { t: "tool";       name: string; status: string; input: Record<string,unknown>; output: string; error: string; ts?: number };
type EvStepEnd    = { t: "step_end";   reason: string; tokens: Record<string,unknown>; cost: number; ts?: number };
type EvSys        = { t: "sys";        msg: string; kind?: "elapsed"|"warn"|"info"; ts?: number };
type EvFlow       = { t: "flow";       msg: string; suggestion?: string; status?: string; ts?: number };
type EvError      = { t: "error";      msg: string; ts?: number };
type EvPermission = { t: "permission"; tool: string; patterns: unknown; ts?: number };
type EvRaw        = { t: "raw";        text: string; ts?: number };
type Event        = EvText | EvThink | EvTool | EvStepEnd | EvSys | EvFlow | EvError | EvPermission | EvRaw;

const TYPE_COLORS: Record<string, string> = {
  text:     "var(--text-secondary)",
  think:    "var(--purple)",
  tool:     "var(--blue)",
  step_end: "var(--green)",
  sys:      "var(--amber)",
  flow:     "var(--green)",
  error:    "var(--red)",
  raw:      "var(--text-dim)",
};

const TYPE_LABELS: Record<string, string> = {
  text:     "TEXT",
  think:    "THINK",
  tool:     "TOOL",
  step_end: "STEP",
  sys:      "SYSTEM",
  flow:     "FLOW",
  error:    "ERROR",
  raw:      "RAW",
};

const TYPE_BG: Record<string, string> = {
  text:     "var(--bg-base)",
  think:    "var(--purple-bg)",
  tool:     "var(--blue-bg)",
  step_end: "var(--green-bg)",
  sys:      "var(--amber-bg)",
  flow:     "var(--green-bg)",
  error:    "var(--red-bg)",
  raw:      "var(--bg-base)",
};

function typeColor(t: string): string {
  return TYPE_COLORS[t] ?? "var(--text-secondary)";
}

function parseEvents(content: string): Event[] {
  const out: Event[] = [];
  for (const line of content.split("\n")) {
    const raw = line.trim();
    if (!raw) continue;
    try {
      const ev = JSON.parse(raw) as Event;
      if (ev && typeof ev.t === "string") { out.push(ev); continue; }
    } catch { /* fall through */ }
    out.push({ t: "raw", text: raw });
  }
  return out;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function loadCollapsed(key: string): boolean {
  try {
    const v = localStorage.getItem(`pt_${key}`);
    return v === null ? true : v !== "false";
  } catch { return true; }
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function saveCollapsed(key: string, open: boolean) {
  try { localStorage.setItem(`pt_${key}`, open ? "true" : "false"); } catch { /* empty */ }
}

const TOOL_ICON: Record<string, string> = {
  bash:"$", shell:"$", execute:"$",
  read:"~", glob:"~", grep:"~", find:"~", ls:"~",
  write:"✎", edit:"✎", create:"✎", patch:"✎",
  webfetch:"↗", fetch:"↗", http:"↗",
  task:"⋯", agent:"⋯",
};
function toolIcon(n: string) {
  const k = n.toLowerCase();
  for (const [t, v] of Object.entries(TOOL_ICON)) if (k.includes(t)) return v;
  return "⚙";
}
function toolColor(n: string) {
  const k = n.toLowerCase();
  if (["bash","shell","execute"].some(t => k.includes(t))) return "var(--amber)";
  if (["read","glob","grep","find","ls"].some(t => k.includes(t))) return "var(--blue)";
  if (["write","edit","create","patch"].some(t => k.includes(t))) return "var(--amber)";
  if (["webfetch","fetch","http"].some(t => k.includes(t))) return "var(--purple)";
  return "var(--text-secondary)";
}
function toolPrimaryDetail(name: string, inp: Record<string,unknown>): string {
  const k = name.toLowerCase();
  if (["bash","shell","execute"].some(t => k.includes(t)))
    return String(inp.command || inp.cmd || inp.Command || "");
  if (["read","write","edit","create","patch"].some(t => k.includes(t)))
    return String(inp.filePath || inp.file_path || inp.path || inp.Path || "");
  if (k.includes("glob")) return String(inp.pattern || inp.Pattern || "");
  if (k.includes("grep")) return String(inp.pattern || inp.regex || inp.Pattern || "");
  if (k.includes("fetch") || k.includes("http")) return String(inp.url || inp.URL || "");
  for (const v of Object.values(inp)) {
    const s = String(v);
    if (s && s !== "undefined" && s !== "null" && s.length > 1) return s.slice(0, 160);
  }
  return "";
}
function toolDescription(inp: Record<string,unknown>): string {
  return String(inp.description || inp.desc || "");
}

const XML_TAGS = [
  { tag: "MANAGER_MESSAGE",    label: "Manager Message",    color: "var(--blue)",   bg: "var(--blue-bg)",   border: "var(--blue-dim)" },
  { tag: "SUGGESTION_CONTENT", label: "Suggestion",         color: "var(--purple)", bg: "var(--purple-bg)", border: "var(--purple-dim)" },
  { tag: "SUGGESTION_VERIFICATION", label: "Verification",  color: "var(--green)",  bg: "var(--green-bg)",  border: "var(--green-dim)" },
  { tag: "HANDOFF_UPDATE",     label: "Handoff Update",     color: "var(--amber)",  bg: "var(--amber-bg)",  border: "var(--amber-dim)" },
  { tag: "WORKER_REPORT",      label: "Worker Report",      color: "var(--amber)",   bg: "var(--amber-bg)",  border: "var(--amber-dim)" },
  { tag: "PLAN",               label: "Plan",               color: "var(--green)",   bg: "var(--green-bg)",  border: "var(--green-dim)" },
  { tag: "TODO_LIST",          label: "Todo List",          color: "var(--amber)",   bg: "var(--amber-bg)",  border: "var(--amber-dim)" },
];

interface XmlBlock { tag: string; label: string; color: string; bg: string; border: string; content: string }

interface CodeSegment {
  type: "code";
  lang: string;
  code: string;
}

interface TextSegment {
  type: "text";
  text: string;
}

type RichSegment = CodeSegment | TextSegment;

function splitFencedCode(text: string): RichSegment[] {
  const segments: RichSegment[] = [];
  const fence = /```([^\n`]*)\n([\s\S]*?)```/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = fence.exec(text)) !== null) {
    if (match.index > last) segments.push({ type: "text", text: text.slice(last, match.index) });
    segments.push({
      type: "code",
      lang: match[1]?.trim() || "text",
      code: match[2] ?? "",
    });
    last = match.index + match[0].length;
  }
  if (last < text.length) segments.push({ type: "text", text: text.slice(last) });
  return segments;
}

function inlineParts(text: string): Array<{ type: "text" | "code"; value: string }> {
  const parts: Array<{ type: "text" | "code"; value: string }> = [];
  const inline = /`([^`\n]+)`/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = inline.exec(text)) !== null) {
    if (match.index > last) parts.push({ type: "text", value: text.slice(last, match.index) });
    parts.push({ type: "code", value: match[1] ?? "" });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ type: "text", value: text.slice(last) });
  return parts;
}

function inferLangFromPath(path: string): string {
  const ext = path.split(/[\\/]/).pop()?.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    py: "python",
    js: "javascript",
    jsx: "jsx",
    ts: "typescript",
    tsx: "tsx",
    html: "html",
    css: "css",
    json: "json",
    md: "markdown",
    markdown: "markdown",
    txt: "text",
    yml: "yaml",
    yaml: "yaml",
    xml: "xml",
    sql: "sql",
    sh: "bash",
    ps1: "powershell",
  };
  return map[ext] ?? (ext || "text");
}

function parseJsonBlock(code: string, lang: string): unknown | null {
  const trimmed = code.trim();
  const looksJson = ["json", "jsonc"].includes(lang.toLowerCase()) || trimmed.startsWith("{") || trimmed.startsWith("[");
  if (!looksJson) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function isManagerOutput(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  return [
    "input_digest",
    "decision",
    "manager_message",
    "plan",
    "todo_list",
    "suggestion_content",
    "suggestion_verification",
    "handoff_update",
  ].some(key => key in value);
}

function ManagerOutputCard({ data }: { data: Record<string, unknown> }) {
  const todos = Array.isArray(data.todo_list) ? data.todo_list : [];
  const handoff = isRecord(data.handoff_update) ? data.handoff_update : null;
  const sections = [
    { label: "Manager Message", value: data.manager_message, color: "var(--blue)" },
    { label: "Decision", value: data.decision, color: "var(--purple)" },
    { label: "Plan", value: data.plan, color: "var(--green)" },
    { label: "Suggestion", value: data.suggestion_content, color: "var(--amber)" },
    { label: "Verification", value: data.suggestion_verification, color: "var(--green)" },
  ].filter(section => textValue(section.value));

  return (
    <div className="p-2 space-y-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>
      {sections.map(section => (
        <div key={section.label} className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <div className="text-[9px] uppercase tracking-wider font-semibold mb-1" style={{ color: section.color }}>{section.label}</div>
          <div className="whitespace-pre-wrap break-words leading-relaxed" style={{ color: "var(--text-primary)" }}>{textValue(section.value)}</div>
        </div>
      ))}

      {todos.length > 0 && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <div className="text-[9px] uppercase tracking-wider font-semibold mb-1" style={{ color: "var(--amber)" }}>Todos</div>
          <div className="space-y-1">
            {todos.map((item, idx) => {
              const todo: Record<string, unknown> = isRecord(item) ? item : { content: item };
              const status = textValue(todo.status) || "pending";
              const priority = textValue(todo.priority);
              return (
                <div key={idx} className="rounded px-2 py-1" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
                  <div className="whitespace-pre-wrap break-words" style={{ color: "var(--text-primary)" }}>{textValue(todo.content) || textValue(item)}</div>
                  <div className="mt-0.5 flex gap-1 text-[9px]" style={{ color: "var(--text-dim)" }}>
                    <span>{status}</span>
                    {priority && <span>Priority: {priority}</span>}
                    {textValue(todo.owner) && <span>Owner: {textValue(todo.owner)}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {handoff && Object.keys(handoff).length > 0 && (
        <details className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <summary className="cursor-pointer text-[9px] uppercase tracking-wider font-semibold" style={{ color: "var(--text-secondary)" }}>Handoff</summary>
          <div className="mt-1 space-y-1">
            {Object.entries(handoff).map(([key, value]) => (
              <div key={key} className="grid grid-cols-[8rem_1fr] gap-2">
                <span style={{ color: "var(--text-dim)" }}>{key}</span>
                <span className="whitespace-pre-wrap break-words" style={{ color: "var(--text-primary)" }}>{textValue(value)}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function CodeBlock({ code, lang = "text", tone = "default" }: { code: string; lang?: string; tone?: "default" | "success" | "error" }) {
  const [copied, setCopied] = useState(false);
  const [view, setView] = useState<"tree" | "raw">("tree");
  const parsedJson = useMemo(() => parseJsonBlock(code, lang), [code, lang]);
  const managerOutput = useMemo(() => isManagerOutput(parsedJson) ? parsedJson : null, [parsedJson]);
  const accent = tone === "error" ? "var(--red)" : tone === "success" ? "var(--green)" : "var(--purple)";
  const bg = tone === "error" ? "var(--red-bg)" : tone === "success" ? "var(--green-bg)" : "var(--bg-base)";

  const copy = async () => {
    await navigator.clipboard?.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="my-1 rounded overflow-hidden" style={{ border: "1px solid var(--border)", background: bg }}>
      <div className="flex items-center gap-2 px-2 py-1" style={{ background: "var(--bg-raised)", borderBottom: "1px solid var(--border)" }}>
        <span className="text-[9px] uppercase tracking-wider font-semibold" style={{ color: accent }}>{lang || "text"}</span>
        {parsedJson !== null && (
          <div className="flex items-center gap-1 ml-auto">
            <button
              onClick={() => setView("tree")}
              className="px-1.5 py-0.5 text-[9px] rounded font-medium transition-colors"
              style={{ background: view === "tree" ? "var(--purple-bg)" : "var(--bg-panel)", color: view === "tree" ? "var(--purple)" : "var(--text-secondary)", border: "1px solid var(--border-dim)" }}
              title="Show JSON tree"
            >
              {managerOutput ? "Summary" : "Tree"}
            </button>
            <button
              onClick={() => setView("raw")}
              className="px-1.5 py-0.5 text-[9px] rounded font-medium transition-colors"
              style={{ background: view === "raw" ? "var(--purple-bg)" : "var(--bg-panel)", color: view === "raw" ? "var(--purple)" : "var(--text-secondary)", border: "1px solid var(--border-dim)" }}
              title="Show raw JSON"
            >
              Raw
            </button>
          </div>
        )}
        <button
          onClick={copy}
          className={`${parsedJson === null ? "ml-auto" : ""} px-1.5 py-0.5 text-[9px] rounded font-medium transition-colors`}
          style={{ background: "var(--bg-panel)", color: copied ? "var(--green)" : "var(--text-secondary)", border: "1px solid var(--border-dim)" }}
          title="Copy code"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      {managerOutput && view === "tree" ? (
        <ManagerOutputCard data={managerOutput} />
      ) : parsedJson !== null && view === "tree" ? (
        <div className="p-2 text-[11px] overflow-x-auto leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          <JsonTree data={parsedJson} maxDepth={5} />
        </div>
      ) : (
        <pre className="p-2 text-[11px] overflow-x-auto whitespace-pre-wrap break-words leading-relaxed" style={{ color: accent }}>
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

function RichText({ text, isNew = false }: { text: string; isNew?: boolean }) {
  return (
    <>
      {splitFencedCode(text).map((segment, idx) => {
        if (segment.type === "code") {
          return <CodeBlock key={idx} code={segment.code} lang={segment.lang} />;
        }
        if (!segment.text.trim()) return null;
        return (
          <div
            key={idx}
            className={`py-0.5 whitespace-pre-wrap leading-relaxed ${isNew ? "stream-code-text-new" : ""}`}
            style={{ color: "var(--text-primary)" }}
          >
            {inlineParts(segment.text).map((part, partIdx) =>
              part.type === "code" ? (
                <code key={partIdx} className="px-1 rounded text-[11px]" style={{ background: "var(--bg-raised)", color: "var(--purple)" }}>
                  {part.value}
                </code>
              ) : (
                <span key={partIdx}>{part.value}</span>
              )
            )}
          </div>
        );
      })}
    </>
  );
}

function extractXmlBlocks(text: string): Array<{ type: "text" | "block"; value: string; block?: XmlBlock }> {
  const segments: Array<{ type: "text" | "block"; value: string; block?: XmlBlock }> = [];
  let remaining = text;
  while (remaining.length > 0) {
    let earliest: { idx: number; def: typeof XML_TAGS[0] } | null = null;
    for (const def of XML_TAGS) {
      const idx = remaining.indexOf(`<${def.tag}`);
      if (idx !== -1 && (earliest === null || idx < earliest.idx)) earliest = { idx, def };
    }
    if (!earliest) { segments.push({ type: "text", value: remaining }); break; }
    if (earliest.idx > 0) segments.push({ type: "text", value: remaining.slice(0, earliest.idx) });
    const closeTag = `</${earliest.def.tag}>`;
    const closeIdx = remaining.indexOf(closeTag, earliest.idx);
    if (closeIdx === -1) { segments.push({ type: "text", value: remaining.slice(earliest.idx) }); break; }
    const openEnd = remaining.indexOf(">", earliest.idx) + 1;
    const content = remaining.slice(openEnd, closeIdx).trim();
    segments.push({ type: "block", value: "", block: { ...earliest.def, content } });
    remaining = remaining.slice(closeIdx + closeTag.length);
  }
  return segments;
}

function XmlBlockView({ block, isNew }: { block: XmlBlock; isNew: boolean }) {
  const [open, setOpen] = useState(true);
  return (
    <div
      className={`my-1.5 rounded-lg overflow-hidden ${isNew ? "ring-1 ring-amber-500/20" : ""}`}
      style={{ border: `1px solid ${block.border}`, background: block.bg }}
    >
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-semibold"
        style={{ color: block.color }}
      >
        <span className="flex-1 text-left uppercase tracking-wide">{block.label}</span>
        <span className="opacity-40 font-normal">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div
          className="px-3 pb-3 text-[12px] whitespace-pre-wrap break-words leading-relaxed"
          style={{ color: "var(--text-primary)", borderTop: `1px solid ${block.border}` }}
        >
          <RichText text={block.content} isNew={isNew} />
        </div>
      )}
    </div>
  );
}

function TextSegments({ text, isNew }: { text: string; isNew: boolean }) {
  const segs = extractXmlBlocks(text);
  return (
    <>
      {segs.map((s, i) =>
        s.type === "block" && s.block
          ? <XmlBlockView key={i} block={s.block} isNew={isNew} />
          : s.value.trim()
          ? (
            <RichText key={i} text={s.value} isNew={isNew} />
          )
          : null
      )}
    </>
  );
}

function fmtTokens(tokens: Record<string,unknown>): string {
  const t = Number(tokens.total || 0);
  const r = Number(tokens.reasoning || 0);
  const o = Number(tokens.output || 0);
  const i = Number(tokens.input || 0);
  if (!t) return "";
  const parts = [`${t.toLocaleString()} tok`];
  if (r) parts.push(`${r} think`);
  if (o) parts.push(`${o} out`);
  if (i) parts.push(`${i} in`);
  return parts.join(" · ");
}

function fmtTokenPill(tokens: Record<string,unknown>): string {
  const t = Number(tokens.total || 0);
  if (!t) return "";
  if (t >= 1000) return `🔣 ${(t / 1000).toFixed(1)}k`;
  return `🔣 ${t}`;
}

function TypeBlock({ ev, children }: { ev: Event; children: React.ReactNode }) {
  const tc = typeColor(ev.t);
  return (
    <div
      className="my-0.5 rounded-r border-l-3"
      style={{ borderLeftColor: tc, background: TYPE_BG[ev.t] ?? "var(--bg-base)" }}
    >
      {children}
    </div>
  );
}

function ThinkBlock({ text, isNew }: { text: string; isNew: boolean }) {
  const [open, setOpen] = useState(false);
  const preview = text.replace(/\n/g, " ").slice(0, 100);
  return (
    <div
      className={`my-1 rounded-r border-l-2 transition-all duration-200 ${isNew ? "border-[var(--purple)]" : "border-[var(--purple)]"}`}
      style={{ background: "var(--purple-bg)" }}
    >
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full text-left flex items-start gap-2 px-2 py-1 text-[11px] italic"
        style={{ color: "var(--purple)" }}
      >
        <span className="shrink-0 not-italic mt-0.5 opacity-70">◈</span>
        <span className="flex-1 truncate opacity-80">{preview}{text.length > 100 && !open ? "…" : ""}</span>
        <span className="shrink-0 not-italic opacity-40 text-[10px] mt-0.5">{open ? "▲" : "▼"} {text.length}c</span>
      </button>
      {open && (
        <div className="px-6 pb-2 text-[11px] whitespace-pre-wrap" style={{ color: "var(--purple)" }}>
          {text}
        </div>
      )}
    </div>
  );
}

function ReadOutput({ ev }: { ev: EvTool }) {
  const inp = ev.input;
  const filePath = String(inp.filePath || inp.file_path || inp.path || inp.Path || "");
  const metaLines: string[] = [];
  if (inp.linesTotal) metaLines.push(`${inp.linesTotal} lines`);
  if (inp.total) metaLines.push(`${inp.total} chars`);

  return (
    <div className="ml-6 mt-0.5 space-y-1">
      <div className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: "var(--bg-raised)" }}>
        <span className="text-[10px] font-bold shrink-0" style={{ color: "var(--blue)" }}>~</span>
        <span className="flex-1 truncate text-[11px]" style={{ color: "var(--text-primary)" }}>{filePath}</span>
        <button
          onClick={() => navigator.clipboard.writeText(filePath)}
          className="shrink-0 px-1 py-0.5 text-[9px] rounded" style={{ background: "var(--blue-dim)", color: "var(--blue)" }}
          title="Copy path"
        >Copy</button>
        <a
          href={`file://${filePath}`}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 px-1 py-0.5 text-[9px] rounded" style={{ background: "var(--blue-dim)", color: "var(--blue)" }}
          title="Open file"
        >Open</a>
        {metaLines.length > 0 && (
          <span className="text-[9px] shrink-0" style={{ color: "var(--text-dim)" }}>{metaLines.join(" · ")}</span>
        )}
      </div>
      {ev.output && (
        <CodeBlock code={ev.output} lang={inferLangFromPath(filePath)} tone="success" />
      )}
      {ev.error && (
        <CodeBlock code={ev.error} lang="text" tone="error" />
      )}
    </div>
  );
}

function BashOutput({ ev }: { ev: EvTool }) {
  const inp = ev.input;
  const cmd = String(inp.command || inp.cmd || inp.Command || "");
  const desc = toolDescription(inp);

  return (
    <div className="ml-6 mt-0.5 space-y-1">
      {desc && (
        <div className="text-[10px] px-2 pt-1 opacity-70" style={{ color: "var(--text-secondary)" }}>{desc}</div>
      )}
      <div className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: "var(--bg-raised)" }}>
        <span className="text-[10px] font-bold shrink-0" style={{ color: "var(--amber)" }}>$</span>
        <code className="flex-1 text-[11px] truncate" style={{ color: "var(--text-primary)" }}>{cmd}</code>
        <span className="text-[9px] shrink-0" style={{ color: "var(--text-dim)" }}>Execute shell command</span>
      </div>
      {ev.output && (
        <CodeBlock code={ev.output} lang="text" tone="success" />
      )}
      {ev.error && (
        <CodeBlock code={ev.error} lang="text" tone="error" />
      )}
    </div>
  );
}

function GenericToolOutput({ ev }: { ev: EvTool }) {
  return (
    <div className="ml-6 mt-0.5 space-y-1">
      {Object.keys(ev.input).filter(k => k !== "description" && k !== "desc").length > 0 && (
        <CodeBlock
          code={JSON.stringify(
            Object.fromEntries(Object.entries(ev.input).filter(([k]) => k !== "description" && k !== "desc")),
            null, 2
          )}
          lang="json"
        />
      )}
      {ev.output && (
        <CodeBlock code={ev.output} lang="text" tone="success" />
      )}
      {ev.error && (
        <CodeBlock code={ev.error} lang="text" tone="error" />
      )}
    </div>
  );
}

function ToolRow({ ev }: { ev: EvTool }) {
  const icon   = toolIcon(ev.name);
  const color  = toolColor(ev.name);
  const primary = toolPrimaryDetail(ev.name, ev.input);

  return (
    <TypeBlock ev={ev}>
      <div className="w-full flex items-start gap-2 text-[12px] px-1 py-0.5">
        <span className="shrink-0 w-4 text-center font-bold mt-0.5" style={{ color }}>{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium shrink-0" style={{ color }}>{ev.name}</span>
            {primary && (
              <span className="truncate text-[11px]" style={{ color: "var(--text-primary)" }}>{primary}</span>
            )}
            {ev.status === "error" && (
              <span className="ml-auto shrink-0 text-[10px] px-1 rounded" style={{ color: "var(--red)", background: "var(--red-bg)" }}>error</span>
            )}
            {ev.status === "running" && (
              <span className="ml-auto shrink-0 text-[10px] animate-pulse" style={{ color: "var(--amber)" }}>…</span>
            )}
          </div>
          {toolDescription(ev.input) && (
            <div className="text-[10px] mt-0.5 opacity-60" style={{ color: "var(--text-secondary)" }}>{toolDescription(ev.input)}</div>
          )}
        </div>
      </div>
    </TypeBlock>
  );
}

interface ToolGroup {
  gkey: string;
  name: string;
  icon: string;
  color: string;
  events: EvTool[];
  startIdx: number;
  createdAt: number;
}

function groupToolEvents(events: Event[]): Array<ToolGroup | Event> {
  const out: Array<ToolGroup | Event> = [];
  let i = 0;
  while (i < events.length) {
    const ev = events[i];
    if (ev.t !== "tool") { out.push(ev); i++; continue; }
    const name = ev.name;
    const sameTool = (e: Event) => e.t === "tool" && e.name === name;
    const group: EvTool[] = [ev];
    let j = i + 1;
    while (j < events.length && sameTool(events[j])) {
      group.push(events[j] as EvTool);
      j++;
    }
    if (group.length === 1) {
      out.push(ev);
    } else {
      // Prefer the first event's timestamp (in ms); fall back to now()
      const firstTs = (group[0].ts ?? 0) * 1000;
      out.push({
        gkey: `tool_group_${i}_${name}`,
        name: group[0].name,
        icon: toolIcon(group[0].name),
        color: toolColor(group[0].name),
        events: group,
        startIdx: i,
        createdAt: firstTs > 0 ? firstTs : Date.now(),
      });
    }
    i = j;
  }
  return out;
}

function ToolGroupCard({ grp, isNew }: { grp: ToolGroup; isNew: boolean }) {
  const gkey = `pt_${grp.gkey}`;
  const [open, setOpen] = useState(() => {
    try {
      const v = localStorage.getItem(gkey);
      return v === null ? false : v === "true";
    } catch { return false; }
  });

  const toggle = () => {
    const next = !open;
    setOpen(next);
    try { localStorage.setItem(gkey, next ? "true" : "false"); } catch { /* empty */ }
  };

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _firstEv = grp.events[0];
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _lastEv = grp.events[grp.events.length - 1];
  const count = grp.events.length;
  // eslint-disable-next-line react-hooks/purity
  const duration = grp.createdAt > 0 ? Date.now() - grp.createdAt : 0;

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  };

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };

  const getItemLabel = () => {
    const name = grp.name.toLowerCase();
    if (["read", "glob", "grep", "find", "ls"].some(t => name.includes(t))) return "items";
    if (["bash", "shell", "execute"].some(t => name.includes(t))) return "commands";
    return "items";
  };

  return (
    <div
      className={`my-0.5 rounded overflow-hidden ${isNew ? "ring-1 ring-amber-500/20" : ""}`}
      style={{ border: `1px solid ${grp.color}22`, background: `${grp.color}08` }}
    >
      <button
        onClick={toggle}
        className="w-full flex items-center gap-2 px-2 py-1 text-[11px]"
        style={{ background: `${grp.color}11`, color: grp.color }}
      >
        <span className="shrink-0 font-bold" style={{ color: grp.color }}>{grp.icon}</span>
        <span className="flex-1 text-left font-medium">
          {grp.icon} {grp.name} ({count} {getItemLabel()})
        </span>
        <span className="text-[9px] opacity-60 shrink-0" style={{ color: grp.color }}>
          {formatTime(grp.createdAt)}
        </span>
        {count > 1 && duration > 0 && (
          <span className="text-[9px] opacity-60 shrink-0" style={{ color: grp.color }}>
            {formatDuration(duration)}
          </span>
        )}
        {!open && (
          <span className="text-[9px] opacity-40 shrink-0" style={{ color: grp.color }}>
            click to expand
          </span>
        )}
        <span className="shrink-0 opacity-50 text-[10px]">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="space-y-0.5 pb-1">
          {grp.events.map((ev, gi) => (
            <ToolGroupItem
              key={gi}
              ev={ev}
              gi={gi}
              count={count}
              gkey={gkey}
              color={grp.color}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolGroupItem({
  ev, gi, count, gkey, color,
}: { ev: EvTool; gi: number; count: number; gkey: string; color: string }) {
  const kn = `${gkey}_${gi}`;
  const [gopen, setGopen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(kn);
      return v === null ? true : v === "true";
    } catch { return true; }
  });
  const hasOutput = !!(ev.output || ev.error || JSON.stringify(ev.input).length > 80);
  const lname = ev.name.toLowerCase();

  return (
    <div className="mx-1 rounded" style={{ border: `1px solid ${color}15` }}>
      <div className="flex items-start gap-2 px-1 py-0.5">
        <span className="shrink-0 w-3 text-center text-[10px] mt-0.5" style={{ color }}>
          {gi === 0 ? "→" : gi === count - 1 ? "←" : "·"}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-medium shrink-0" style={{ color }}>{ev.status || "ok"}</span>
            {hasOutput && (
              <button
                onClick={() => {
                  const no = !gopen;
                  setGopen(no);
                  try { localStorage.setItem(kn, no ? "true" : "false"); } catch { /* ignore */ }
                }}
                className="ml-auto text-[9px] opacity-40 hover:opacity-80"
                style={{ color }}
              >
                {gopen ? "▲" : "▼"}
              </button>
            )}
          </div>
          {gopen && (
            <div className="mt-0.5">
              {lname === "read" ? (
                <ReadOutput ev={ev} />
              ) : lname.includes("bash") || lname.includes("shell") ? (
                <BashOutput ev={ev} />
              ) : (
                <GenericToolOutput ev={ev} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function EventRow({ ev, isNew }: { ev: Event; isNew: boolean }) {
  switch (ev.t) {
    case "text":
      return <TextSegments text={ev.text} isNew={isNew} />;
    case "think":
      return <ThinkBlock text={ev.text} isNew={isNew} />;
    case "tool":
      return <ToolRow ev={ev} />;
    case "step_end": {
      const tok = fmtTokens(ev.tokens);
      const pill = fmtTokenPill(ev.tokens);
      if (!tok && !ev.cost) return null;
      return (
        <TypeBlock ev={ev}>
          <div className="flex gap-3 text-[10px] py-0.5 px-2 items-center flex-wrap" style={{ color: "var(--green)" }}>
            {tok && <span>{tok}</span>}
            {pill && <span className="ml-auto shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}>{pill}</span>}
            {ev.cost > 0 && <span>${ev.cost.toFixed(4)}</span>}
            {ev.reason && ev.reason !== "stop" && <span className="opacity-60">stop: {ev.reason}</span>}
          </div>
        </TypeBlock>
      );
    }
    case "sys":
      return (
        <TypeBlock ev={ev}>
          {ev.kind === "elapsed"
            ? <div className="text-[10px] italic py-0.5 px-2" style={{ color: "var(--amber-dim)" }}>⏱ {ev.msg}</div>
            : ev.kind === "warn"
            ? <div className="text-[10px] py-0.5 px-2" style={{ color: "var(--amber)" }}>⚠ {ev.msg}</div>
            : <div className="text-[10px] py-0.5 px-2" style={{ color: "var(--amber)" }}>{ev.msg}</div>
        }
        </TypeBlock>
      );
    case "flow":
      return (
        <TypeBlock ev={ev}>
          <div className="px-2 py-1 text-[11px]" style={{ color: "var(--green)" }}>
            <div className="font-semibold">{ev.msg}</div>
            {ev.suggestion && (
              <div className="mt-0.5 opacity-75" style={{ color: "var(--text-secondary)" }}>
                {ev.status ? `${ev.status}: ` : ""}{ev.suggestion}
              </div>
            )}
          </div>
        </TypeBlock>
      );
    case "error":
      return (
        <TypeBlock ev={ev}>
          <div className="flex gap-2 text-[12px] px-2 py-1 rounded" style={{ color: "var(--red)", background: "var(--red-bg)" }}>
            <span className="shrink-0">✕</span><span>{ev.msg}</span>
          </div>
        </TypeBlock>
      );
    case "permission":
      return (
        <TypeBlock ev={ev}>
          <div className="text-[10px] py-0.5 px-2" style={{ color: "var(--amber)", opacity: 0.6 }}>
            ✓ auto-approved: {ev.tool}
          </div>
        </TypeBlock>
      );
    case "raw":
      return (
        <TypeBlock ev={ev}>
          <div className="text-[10px] py-0.5 px-2 opacity-40" style={{ color: "var(--text-dim)" }}>{ev.text}</div>
        </TypeBlock>
      );
    default:
      return null;
  }
}

function LatestBanner({ ev, timestamp }: { ev: Event | ToolGroup; timestamp: number }) {
  let preview = "";
  let tKey: string;

  // ToolGroup case (group of same-name tool calls)
  if ("gkey" in ev) {
    const grp = ev as ToolGroup;
    tKey = "tool";
    const last = grp.events[grp.events.length - 1];
    preview = `[${grp.name} ×${grp.events.length}] ${toolPrimaryDetail(grp.name, last.input)}`.slice(0, 80);
  } else {
    const e = ev as Event;
    tKey = e.t;
    if      (e.t === "text")  preview = e.text.replace(/\n/g, " ").slice(0, 80);
    else if (e.t === "think") preview = e.text.replace(/\n/g, " ").slice(0, 80);
    else if (e.t === "tool")  preview = `[${e.name}] ${toolPrimaryDetail(e.name, e.input)}`.slice(0, 80);
    else if (e.t === "sys")   preview = e.msg.slice(0, 80);
    else if (e.t === "flow")  preview = e.msg.slice(0, 80);
    else if (e.t === "error") preview = e.msg.slice(0, 80);
  }
  if (!preview) return null;

  const tc = typeColor(tKey as Event["t"]);
  const timeStr = new Date(timestamp).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 shrink-0"
      style={{
        background: "var(--blue-bg)",
        borderBottom: "1px solid var(--blue-dim)",
        boxShadow: "0 0 12px rgba(59, 130, 246, 0.15)"
      }}
    >
      <div className="flex flex-col items-start gap-0.5">
        <span
          className="font-bold tracking-wider"
          style={{ color: "var(--blue)", fontSize: "10px", letterSpacing: "0.1em" }}
        >
          LATEST OUTPUT
        </span>
        <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>
          {timeStr}
        </span>
      </div>
      <span
        className="px-1.5 py-0.5 text-[9px] font-bold rounded shrink-0"
        style={{ background: `${tc}20`, color: tc, border: `1px solid ${tc}40` }}
      >
        {TYPE_LABELS[tKey] ?? tKey}
      </span>
      <span className="truncate text-[11px]" style={{ color: "var(--text-primary)" }}>{preview}{preview.length >= 80 ? "…" : ""}</span>
    </div>
  );
}

function topSignature(item: ToolGroup | Event): string {
  if ("gkey" in item) return `g:${(item as ToolGroup).gkey}:${(item as ToolGroup).events.length}`;
  const ev = item as Event;
  switch (ev.t) {
    case "text":  return `t:${ev.text.length}:${ev.text.slice(-30)}`;
    case "think": return `th:${ev.text.length}:${ev.text.slice(-30)}`;
    case "tool":  return `tl:${ev.name}:${JSON.stringify(ev.input).slice(0,40)}:${ev.status}:${(ev.output||"").length}`;
    case "sys":   return `s:${ev.msg.slice(-40)}`;
    case "flow":  return `f:${ev.msg.slice(-40)}:${ev.status || ""}`;
    case "error": return `e:${ev.msg.slice(-40)}`;
    case "step_end": return `se:${ev.tokens.total||0}:${ev.cost||0}`;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    default: return `${ev.t}:${(ev as any).text?.slice?.(-30) ?? ""}`;
  }
}

export function StreamView({ content, className = "" }: { content: string; className?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const userScrolling = useRef(false);
  const [showOffline, setShowOffline] = useState(false);

  const events   = parseEvents(content);
  const groups   = groupToolEvents(events);
  const reversed = [...groups].reverse();
  const hasContent = reversed.length > 0;
  // Read the actual event timestamp; fall back to now() only if missing.
  const newestItem = reversed[0];
  const newestTs = newestItem
    ? ("gkey" in newestItem
        ? (newestItem as ToolGroup).createdAt
        : (((newestItem as Event).ts ?? 0) * 1000) ||
          // eslint-disable-next-line react-hooks/purity
          Date.now())
    : 0;
  const latestTimestamp = newestTs;

  // Use the newest item's signature as a re-mount key — when it changes the
  // top entry re-renders fresh and replays its slide-in animation.
  const newestKey = hasContent ? topSignature(reversed[0]) : "";

  useEffect(() => {
    const id = setInterval(() => setShowOffline(!isOnline), 2000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (follow && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [content, follow]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    if (el.scrollTop > 80 && !userScrolling.current) {
      userScrolling.current = true;
      setFollow(false);
    }
    if (el.scrollTop <= 4 && userScrolling.current) {
      userScrolling.current = false;
      setFollow(true);
    }
  }, []);

  return (
    <div className={`relative flex flex-col min-h-0 ${className}`}>
      {showOffline && (
        <div
          className="flex items-center gap-2 px-4 py-2 shrink-0 text-[11px] font-medium"
          style={{ background: "var(--red-bg)", color: "var(--red)", borderBottom: "1px solid var(--red-dim)" }}
        >
          <span className="animate-pulse">●</span>
          <span>Offline — connection lost, retrying…</span>
        </div>
      )}
      {hasContent && <LatestBanner ev={reversed[0]} timestamp={latestTimestamp} />}

      {!follow && (
        <button
          onClick={() => { setFollow(true); userScrolling.current = false; if (containerRef.current) containerRef.current.scrollTop = 0; }}
          className="absolute top-2 right-2 z-10 flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium shadow-lg transition-all"
          style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
        >
          ↑ Follow Latest
        </button>
      )}

      <div
        ref={containerRef}
        className="font-mono text-[12px] overflow-y-auto flex-1 p-4"
        onScroll={handleScroll}
      >
        {!hasContent ? (
          <span className="text-[var(--text-dim)] italic text-[11px]">waiting for output...</span>
        ) : (
          reversed.map((item, i) => {
            const isNewest = i === 0;
            // Keying the newest by its signature re-mounts it on each new event,
            // replaying the slide-in animation; older items keep stable keys so
            // they don't re-animate when ranks shift.
            const itemKey = isNewest ? `newest-${newestKey}` : `older-${i}-${topSignature(item).slice(0, 32)}`;
            return (
              <div
                key={itemKey}
                className={isNewest ? "stream-newest-enter mb-3" : (i === 1 ? "stream-settle" : "")}
                style={isNewest
                  ? {
                      fontSize: "14px",
                      opacity: 1,
                      padding: "8px 10px",
                      borderRadius: "6px",
                      background: "linear-gradient(180deg, rgba(59,130,246,0.10) 0%, rgba(59,130,246,0.02) 100%)",
                      border: "1px solid rgba(59,130,246,0.35)",
                      boxShadow: "0 0 14px rgba(59,130,246,0.18)",
                    }
                  : {
                      fontSize: "11px",
                      opacity: i === 1 ? 0.85 : 0.6 - Math.min(0.25, (i - 1) * 0.04),
                      transform: `scale(${1 - Math.min(0.05, (i - 1) * 0.01)})`,
                      transformOrigin: "top center",
                      transition: "opacity 200ms ease-out, transform 200ms ease-out",
                    }
                }
              >
                {"gkey" in item ? (
                  <ToolGroupCard grp={item as ToolGroup} isNew={isNewest} />
                ) : (
                  <EventRow ev={item as Event} isNew={isNewest} />
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
