export const DEBUG_MODE = (() => {
  try {
    const params = new URLSearchParams(window.location.search);
    return params.get("debug") === "1" || localStorage.getItem("task_hounds_debug") === "1";
  } catch {
    return false;
  }
})();

type JsonRecord = Record<string, unknown>;
type DebugLevel = "debug" | "info" | "warn" | "error";

export interface DebugEntry {
  sequence: number;
  timestamp: string;
  level: DebugLevel;
  category: string;
  event: string;
  format: "json" | "text" | "error" | "empty";
  data: unknown;
  page: string;
}

let installed = false;
let sequence = 0;
let apiSequence = 0;

function isSensitiveKey(key: string) {
  return /authorization|api[-_]?key|password|secret|token|credential/i.test(key);
}

function sanitize(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[max-depth]";
  if (value instanceof Error) {
    return {
      name: value.name,
      message: value.message,
      stack: value.stack,
    };
  }
  if (Array.isArray(value)) return value.map(item => sanitize(item, depth + 1));
  if (value && typeof value === "object") {
    const output: JsonRecord = {};
    for (const [key, item] of Object.entries(value as JsonRecord)) {
      output[key] = isSensitiveKey(key) ? "[redacted]" : sanitize(item, depth + 1);
    }
    return output;
  }
  if (typeof value === "string" && value.length > 8000) {
    return `${value.slice(0, 8000)}...[truncated ${value.length - 8000} chars]`;
  }
  return value;
}

function detectAndParse(value: unknown): Pick<DebugEntry, "format" | "data"> {
  if (value === null) return { format: "empty", data: null };
  if (value === undefined) return { format: "empty", data: "[undefined]" };
  if (value === "") return { format: "empty", data: "" };
  if (value instanceof Error) return { format: "error", data: sanitize(value) };
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (
      (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
      (trimmed.startsWith("[") && trimmed.endsWith("]"))
    ) {
      try {
        return { format: "json", data: sanitize(JSON.parse(trimmed)) };
      } catch {
        return { format: "text", data: sanitize(value) };
      }
    }
    return { format: "text", data: sanitize(value) };
  }
  if (typeof value === "object") return { format: "json", data: sanitize(value) };
  return { format: "text", data: String(value) };
}

async function parseBody(body: BodyInit | null | undefined): Promise<unknown> {
  if (!body) return undefined;
  if (typeof body === "string") return detectAndParse(body).data;
  if (body instanceof URLSearchParams) return sanitize(Object.fromEntries(body.entries()));
  if (body instanceof FormData) return sanitize(Object.fromEntries(body.entries()));
  return `[${body.constructor?.name || "body"}]`;
}

async function parseResponse(response: Response): Promise<unknown> {
  const clone = response.clone();
  const contentType = clone.headers.get("content-type") || "";
  try {
    if (contentType.includes("application/json")) return sanitize(await clone.json());
    const text = await clone.text();
    return text === "" ? null : sanitize(text);
  } catch (error) {
    return `[unreadable response: ${String(error)}]`;
  }
}

function describeTarget(target: EventTarget | null): JsonRecord {
  if (!(target instanceof Element)) return { target: String(target) };
  const element = target as HTMLElement;
  const trigger = target.closest("button, a, input, textarea, select, [role='button'], [role='menuitem']");
  const input = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
    ? target
    : null;
  return sanitize({
    tag: target.tagName.toLowerCase(),
    id: target.id || undefined,
    role: target.getAttribute("role") || undefined,
    name: target.getAttribute("name") || undefined,
    type: target.getAttribute("type") || undefined,
    ariaLabel: target.getAttribute("aria-label") || undefined,
    title: target.getAttribute("title") || undefined,
    text: element.innerText?.trim().slice(0, 500) || undefined,
    value: input ? (input.type === "password" ? "[redacted]" : input.value) : undefined,
    className: typeof element.className === "string" ? element.className : undefined,
    trigger: trigger && trigger !== target ? {
      tag: trigger.tagName.toLowerCase(),
      id: trigger.id || undefined,
      role: trigger.getAttribute("role") || undefined,
      ariaLabel: trigger.getAttribute("aria-label") || undefined,
      title: trigger.getAttribute("title") || undefined,
      text: (trigger as HTMLElement).innerText?.trim().slice(0, 500) || undefined,
    } : undefined,
  }) as JsonRecord;
}

export class DebugLogger {
  readonly sessionId: string;
  private queue: DebugEntry[] = [];
  private flushTimer: number | null = null;
  private transportFetch: typeof window.fetch | null = null;
  private baseUrl = window.location.origin;

  constructor() {
    this.sessionId = `ui-${new Date().toISOString().replace(/[:.]/g, "-")}-${crypto.randomUUID().slice(0, 8)}`;
  }

  configureTransport(nativeFetch: typeof window.fetch, baseUrl?: string) {
    this.transportFetch = nativeFetch;
    if (baseUrl) this.baseUrl = baseUrl;
  }

  setBaseUrl(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  log(category: string, event: string, value?: unknown, level: DebugLevel = "debug") {
    if (!DEBUG_MODE) return;
    const parsed = detectAndParse(value);
    const entry: DebugEntry = {
      sequence: ++sequence,
      timestamp: new Date().toISOString(),
      level,
      category,
      event,
      format: parsed.format,
      data: parsed.data,
      page: `${window.location.pathname}${window.location.search}${window.location.hash}`,
    };
    this.queue.push(entry);
    console[level](`[TH DEBUG][${category}] ${event}`, parsed.data);
    this.scheduleFlush();
  }

  async flush() {
    if (!DEBUG_MODE || !this.transportFetch || this.queue.length === 0) return;
    const entries = this.queue.splice(0, this.queue.length);
    if (this.flushTimer !== null) {
      window.clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    try {
      await this.transportFetch(`${this.baseUrl}/api/debug-logs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: this.sessionId,
          entries,
          user_agent: navigator.userAgent,
        }),
        keepalive: true,
      });
    } catch (error) {
      this.queue.unshift(...entries);
      console.error("[TH DEBUG][LOGGER] flush failed", error);
    }
  }

  private scheduleFlush() {
    if (this.flushTimer !== null || this.queue.length >= 50) {
      if (this.queue.length >= 50) void this.flush();
      return;
    }
    this.flushTimer = window.setTimeout(() => {
      this.flushTimer = null;
      void this.flush();
    }, 500);
  }
}

export const debugLogger = new DebugLogger();

export function debugFunctionCall(name: string, detail?: unknown) {
  debugLogger.log("FUNCTION", name, detail);
}

export function installGlobalDebugMode() {
  if (!DEBUG_MODE || installed) return;
  installed = true;

  const nativeFetch = window.fetch.bind(window);
  debugLogger.configureTransport(nativeFetch);
  debugLogger.log("SESSION", "started", {
    session_id: debugLogger.sessionId,
    url: window.location.href,
    user_agent: navigator.userAgent,
  }, "info");

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.includes("/api/debug-logs")) return nativeFetch(input, init);

    const requestId = `api-${++apiSequence}`;
    const method = init?.method || (input instanceof Request ? input.method : "GET");
    const started = performance.now();
    debugLogger.log("API_REQUEST", `${requestId} ${method} ${url}`, {
      request_id: requestId,
      method,
      url,
      headers: init?.headers,
      body: await parseBody(init?.body),
    });

    try {
      const response = await nativeFetch(input, init);
      debugLogger.log("API_RESPONSE", `${requestId} ${response.status} ${method} ${url}`, {
        request_id: requestId,
        status: response.status,
        method,
        url,
        duration_ms: Math.round((performance.now() - started) * 10) / 10,
        body: await parseResponse(response),
      }, response.ok ? "debug" : "warn");
      return response;
    } catch (error) {
      debugLogger.log("API_ERROR", `${requestId} ${method} ${url}`, error, "error");
      throw error;
    }
  };

  for (const eventName of [
    "click", "dblclick", "contextmenu", "input", "change", "submit", "keydown",
    "focusin", "focusout", "dragstart", "drop",
  ]) {
    document.addEventListener(eventName, event => {
      const keyDetail = event instanceof KeyboardEvent
        ? { key: event.key, code: event.code, ctrlKey: event.ctrlKey, altKey: event.altKey, shiftKey: event.shiftKey }
        : {};
      debugLogger.log("INTERACTION", eventName, { ...describeTarget(event.target), ...keyDetail });
    }, true);
  }

  const nativePushState = history.pushState.bind(history);
  history.pushState = (...args) => {
    debugLogger.log("NAVIGATION", "pushState", args);
    return nativePushState(...args);
  };
  const nativeReplaceState = history.replaceState.bind(history);
  history.replaceState = (...args) => {
    debugLogger.log("NAVIGATION", "replaceState", args);
    return nativeReplaceState(...args);
  };

  window.addEventListener("popstate", event => debugLogger.log("NAVIGATION", "popstate", event.state));
  window.addEventListener("hashchange", event => debugLogger.log("NAVIGATION", "hashchange", { oldURL: event.oldURL, newURL: event.newURL }));
  window.addEventListener("online", () => debugLogger.log("LIFECYCLE", "online"));
  window.addEventListener("offline", () => debugLogger.log("LIFECYCLE", "offline", undefined, "warn"));
  document.addEventListener("visibilitychange", () => debugLogger.log("LIFECYCLE", "visibilitychange", { visibilityState: document.visibilityState }));
  window.addEventListener("error", event => debugLogger.log("WINDOW_ERROR", "error", event.error || event.message, "error"));
  window.addEventListener("unhandledrejection", event => debugLogger.log("WINDOW_ERROR", "unhandledrejection", event.reason, "error"));
  window.addEventListener("pagehide", () => void debugLogger.flush());
}
