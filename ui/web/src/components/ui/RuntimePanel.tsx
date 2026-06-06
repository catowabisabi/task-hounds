import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost, apiPut } from "../../lib/api";

interface RuntimeStatus {
  ok: boolean;
  ready: boolean;
  runtime_available: boolean;
  unavailable_reason: string | null;
  managed_opencode_count: number;
  external_opencode_count: number;
  active_work: string | null;
  last_checkpoint: {
    id: string;
    project_session_id: string;
    workspace_id: string;
    reason: string;
    status: string;
    created_at: string;
  } | null;
  role_bindings: RoleBinding[] | Record<string, RoleBinding | null>;
  policy: {
    close_behavior: string;
    background_mode_enabled: boolean;
    max_managed_opencode_servers: number;
  };
  managed_health?: {
    ok: boolean;
    host: string;
    port: number;
    pid: number | null;
    credential_warnings: string[];
  };
}

interface RoleBinding {
  id?: number;
  role: string;
  agent_id?: number | null;
  session_id?: string | null;
  host?: string;
  port?: number;
  opencode_agent?: string;
  model?: string | null;
  binding_source?: string;
  updated_at?: string;
}

interface OpencodeServer {
  id: string;
  host: string;
  port: number;
  pid: number;
  owner: string;
  status: string;
  created_at: string;
  project_session_id: string | null;
}

interface Checkpoint {
  id: string;
  project_session_id: string;
  workspace_id: string;
  reason: string;
  status: string;
  created_at: string;
}

interface DiscoveredServer {
  host: string;
  port: number;
}

interface OpencodeResponse { servers: OpencodeServer[] }
interface CheckpointsResponse { checkpoints: Checkpoint[] }
interface DiscoverResponse { discovered: DiscoveredServer[] }
interface StopAllResponse { ok: boolean; results: Array<{ server_id: string; ok: boolean; error?: string }> }
interface CreateCheckpointResponse { ok: boolean; checkpoint_id: string }

interface LoopStatus {
  running: boolean;
  loop_running: boolean;
  loop_state: "stopped" | "starting" | "running" | "failed" | string;
  pid: number | null;
  last_start_error: string | null;
  last_error_at: string | null;
}

const roleAccent = (role: string) => {
  switch (role) {
    case "manager": return "var(--amber)";
    case "worker": return "var(--blue)";
    case "reviewer": return "var(--purple)";
    case "chat": return "var(--green)";
    default: return "var(--text-secondary)";
  }
};

const formatTime = (iso: string) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
};

export function RuntimePanel() {
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [servers, setServers] = useState<OpencodeServer[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [discovered, setDiscovered] = useState<DiscoveredServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ msg: string; type: "success" | "error" } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loopStatus, setLoopStatus] = useState<LoopStatus | null>(null);
  const [loopBusy, setLoopBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, srv, cp, ls] = await Promise.all([
        apiGet<RuntimeStatus>("/api/runtime/status"),
        apiGet<OpencodeResponse>("/api/runtime/opencode"),
        apiGet<CheckpointsResponse>("/api/runtime/checkpoints"),
        apiGet<LoopStatus>("/api/workflow/status"),
      ]);
      setStatus(s);
      setServers(srv.servers ?? []);
      setCheckpoints(cp.checkpoints ?? []);
      setLoopStatus(ls);
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Runtime unavailable");
      setDiscovered([]);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [load]);

  const flash = (msg: string, type: "success" | "error") => {
    setFeedback({ msg, type });
    setTimeout(() => setFeedback(null), 2500);
  };

  const errorMessage = (err: unknown) => err instanceof Error ? err.message : String(err || "Unknown error");

  const bindingFor = (role: string): RoleBinding | null => {
    const bindings = status?.role_bindings;
    if (!bindings) return null;
    if (Array.isArray(bindings)) return bindings.find(b => b.role === role) ?? null;
    return bindings[role] ?? null;
  };

  const handleCreateCheckpoint = async () => {
    if (!status?.last_checkpoint) return;
    setLoading(true);
    try {
      const result = await apiPost<CreateCheckpointResponse>("/api/runtime/checkpoint", {
        project_session_id: status.last_checkpoint.project_session_id,
        workspace_id: status.last_checkpoint.workspace_id,
        reason: "Manual checkpoint from Runtime panel",
        notes: "",
      });
      if (result.ok) {
        flash(`Checkpoint created: ${result.checkpoint_id}`, "success");
        load();
      }
    } catch {
      flash("Failed to create checkpoint", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleStopAll = async () => {
    setLoading(true);
    try {
      const result = await apiPost<StopAllResponse>("/api/runtime/stop-all");
      if (result.ok) {
        const failed = result.results.filter(r => !r.ok).length;
        flash(
          failed === 0 ? `Stopped ${result.results.length} server(s)` : `Stopped ${result.results.length}, ${failed} failed`,
          failed > 0 ? "error" : "success"
        );
        load();
      }
    } catch {
      flash("Failed to stop servers", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleDiscover = async () => {
    setLoading(true);
    try {
      const result = await apiPost<DiscoverResponse>("/api/runtime/discover");
      setDiscovered(result.discovered ?? []);
      flash(`Found ${result.discovered?.length ?? 0} opencode server(s)`, "success");
    } catch (err) {
      setDiscovered([]);
      flash(`Discover failed: ${errorMessage(err)}`, "error");
    } finally {
      setLoading(false);
    }
  };

  const handleAttach = async (host: string, port: number) => {
    setLoading(true);
    try {
      await apiPost("/api/runtime/attach", { host, port });
      flash(`Attached ${host}:${port}`, "success");
      load();
    } catch (err) {
      flash(`Attach failed: ${errorMessage(err)}`, "error");
    } finally {
      setLoading(false);
    }
  };

  const handleTest = async (host: string, port: number) => {
    setLoading(true);
    try {
      const result = await apiPost<{host: string; port: number; reachable: boolean}>("/api/runtime/test", { host, port });
      flash(result.reachable ? `✓ ${host}:${port} reachable` : `✗ ${host}:${port} unreachable`, result.reachable ? "success" : "error");
    } catch (err) {
      flash(`Test failed: ${errorMessage(err)}`, "error");
    } finally {
      setLoading(false);
    }
  };

  const handleIgnore = async (host: string, port: number) => {
    setLoading(true);
    try {
      await apiPost("/api/runtime/ignore", { host, port });
      flash(`Ignored ${host}:${port}`, "success");
      load();
    } catch (err) {
      flash(`Ignore failed: ${errorMessage(err)}`, "error");
    } finally {
      setLoading(false);
    }
  };

  const handleAssign = async (host: string, port: number, role: string) => {
    setLoading(true);
    try {
      await apiPost("/api/runtime/attach", { host, port });
      await apiPut(`/api/runtime/bindings/${role}`, { host, port });
      flash(`Assigned ${host}:${port} as ${role}`, "success");
      await load();
    } catch (err) {
      flash(`Assign failed: ${errorMessage(err)}`, "error");
    } finally {
      setLoading(false);
    }
  };

  const toggle = (key: string) => setExpanded(prev => prev === key ? null : key);

  const handleStartLoop = async () => {
    setLoopBusy(true);
    try {
      const result = await apiPost<{ started: boolean; state: string; error?: string | null; reason?: string | null }>("/api/workflow/start-loop");
      if (result?.started) {
        flash(`Loop started (pid=${(result as { pid?: number | null }).pid ?? "?"})`, "success");
      } else {
        flash(`Loop start failed: ${result?.error ?? result?.reason ?? "unknown"}`, "error");
      }
      load();
    } catch (err) {
      flash(`Loop start error: ${errorMessage(err)}`, "error");
    } finally {
      setLoopBusy(false);
    }
  };

  const loopState = loopStatus?.loop_state ?? "stopped";
  const loopStateAccent = (s: string) => {
    switch (s) {
      case "running": return "var(--green)";
      case "starting": return "var(--amber)";
      case "failed": return "var(--red)";
      default: return "var(--text-dim)";
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--amber)" }}>Runtime</p>
        {loading && <span className="text-[10px] animate-pulse" style={{ color: "var(--text-dim)" }}>...</span>}
      </div>

      {status && !status.ready && (
        <div
          data-testid="runtime-unavailable-banner"
          className="rounded p-2 space-y-1"
          style={{ background: "var(--red-bg)", border: "1px solid var(--red-dim)" }}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--red)" }}>
            Runtime not ready
          </p>
          <p className="text-[10px]" style={{ color: "var(--text-primary)" }}>
            <span data-testid="runtime-unavailable-reason">{
              status.unavailable_reason ?? "unknown"
            }</span>
          </p>
          {status.managed_health?.credential_warnings && status.managed_health.credential_warnings.length > 0 && (
            <ul
              data-testid="runtime-credential-warnings"
              className="text-[10px] space-y-0.5 pl-3"
              style={{ color: "var(--text-secondary)" }}
            >
              {status.managed_health.credential_warnings.map((w, i) => (
                <li key={i} className="list-disc">{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {status?.ready && (
        <div
          data-testid="runtime-ready-badge"
          className="rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-wider inline-block"
          style={{ background: "var(--green-bg)", color: "var(--green)", border: "1px solid var(--green-dim)" }}
        >
          Ready
        </div>
      )}

      <div
        data-testid="loop-state-panel"
        className="rounded p-2 space-y-1"
        style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}
      >
        <div className="flex items-center justify-between">
          <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Workflow Loop</p>
          <span
            data-testid="loop-state-badge"
            data-state={loopState}
            className="text-[9px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wider"
            style={{
              background: loopState === "running" ? "var(--green-bg)"
                : loopState === "starting" ? "var(--amber-bg)"
                : loopState === "failed" ? "var(--red-bg)"
                : "var(--bg-base)",
              color: loopStateAccent(loopState),
              border: `1px solid ${loopStateAccent(loopState)}`,
            }}
          >
            <span data-testid="loop-state-value">{loopState}</span>
          </span>
        </div>
        {loopStatus?.last_start_error && (
          <p
            data-testid="loop-last-start-error"
            className="text-[10px] leading-snug"
            style={{ color: "var(--red)" }}
          >
            {loopStatus.last_start_error}
            {loopStatus.last_error_at ? ` · ${formatTime(loopStatus.last_error_at)}` : ""}
          </p>
        )}
        {loopState !== "running" && loopState !== "starting" && (
          <button
            data-testid="loop-retry-button"
            onClick={handleStartLoop}
            disabled={loopBusy}
            className="w-full px-2 py-1 text-[10px] rounded font-semibold disabled:opacity-40"
            style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
          >
            {loopBusy ? "Starting..." : loopState === "failed" ? "↻ Retry Loop" : "▶ Start Loop"}
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1">
        <button onClick={handleCreateCheckpoint} disabled={loading}
          className="px-2 py-1 text-[10px] rounded font-semibold disabled:opacity-40 transition-colors"
          style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}>
          + Checkpoint
        </button>
        <button onClick={handleStopAll} disabled={loading}
          className="px-2 py-1 text-[10px] rounded disabled:opacity-40 transition-colors"
          style={{ background: "var(--bg-panel)", color: "var(--red)", border: "1px solid var(--border)" }}>
          ⏹ Stop All
        </button>
        <button onClick={handleDiscover} disabled={loading}
          className="px-2 py-1 text-[10px] rounded disabled:opacity-40 transition-colors"
          style={{ background: "var(--bg-panel)", color: "var(--blue)", border: "1px solid var(--border)" }}>
          ◇ Discover
        </button>
      </div>

      {feedback && (
        <div className="text-[10px] px-2 py-1 rounded animate-pulse"
          style={{ background: feedback.type === "success" ? "var(--green-bg)" : "var(--red-bg)", color: feedback.type === "success" ? "var(--green)" : "var(--red)", border: `1px solid ${feedback.type === "success" ? "var(--green-dim)" : "var(--red-dim)"}` }}>
          {feedback.msg}
        </div>
      )}

      <div className="grid grid-cols-2 gap-1">
        <div className="rounded p-1.5" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
          <p className="text-[9px] uppercase tracking-wider mb-0.5" style={{ color: "var(--text-dim)" }}>Managed</p>
          <p className="text-[13px] font-semibold" style={{ color: "var(--amber)" }}>{status?.managed_opencode_count ?? "—"}</p>
        </div>
        <div className="rounded:p-1.5" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)" }}>
          <p className="text-[9px] uppercase tracking-wider mb-0.5" style={{ color: "var(--text-dim)" }}>External</p>
          <p className="text-[13px] font-semibold" style={{ color: "var(--blue)" }}>{status?.external_opencode_count ?? "—"}</p>
        </div>
      </div>

      {status?.active_work && (
        <div className="rounded p-2" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
          <p className="text-[9px] uppercase tracking-wider mb-1" style={{ color: "var(--text-dim)" }}>Active Work</p>
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-primary)" }}>{status.active_work}</p>
        </div>
      )}

      {status?.policy && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("policy")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Policy</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "policy" ? "▲" : "▼"}</span>
          </button>
          {expanded === "policy" && (
            <div className="mt-1.5 space-y-1">
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Close behavior</span>
                <span style={{ color: "var(--amber)" }}>{status.policy.close_behavior}</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Background mode</span>
                <span style={{ color: status.policy.background_mode_enabled ? "var(--green)" : "var(--text-secondary)" }}>
                  {status.policy.background_mode_enabled ? "ON" : "OFF"}
                </span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Max servers</span>
                <span style={{ color: "var(--text-secondary)" }}>{status.policy.max_managed_opencode_servers}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {status?.role_bindings && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("bindings")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Role Bindings</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "bindings" ? "▲" : "▼"}</span>
          </button>
          {expanded === "bindings" && (
            <div className="mt-1.5 space-y-1">
              {(["manager", "worker", "reviewer", "chat"] as const).map(role => {
                const binding = bindingFor(role);
                const accent = roleAccent(role);
                return (
                  <div key={role} className="flex items-center gap-2">
                    <span className="text-[9px] font-semibold uppercase tracking-wider w-16 shrink-0" style={{ color: accent }}>{role}</span>
                    {binding ? (
                      <span className="text-[10px]" style={{ color: "var(--text-secondary)" }}>
                        {binding.host && binding.port ? `${binding.host}:${binding.port}` : binding.agent_id != null ? `agent#${binding.agent_id}` : "bound"}
                        {binding.session_id ? ` · ${binding.session_id.slice(0, 8)}` : ""}
                      </span>
                    ) : (
                      <span className="text-[10px] italic" style={{ color: "var(--text-dim)" }}>unbound</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {status?.last_checkpoint && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("checkpoint")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Last Checkpoint</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "checkpoint" ? "▲" : "▼"}</span>
          </button>
          {expanded === "checkpoint" && (
            <div className="mt-1.5 space-y-1">
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Reason</span>
                <span className="text-[10px] text-right max-w-[120px] truncate" style={{ color: "var(--text-primary)" }}>{status.last_checkpoint.reason}</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Time</span>
                <span style={{ color: "var(--text-secondary)" }}>{formatTime(status.last_checkpoint.created_at)}</span>
              </div>
              <div className="flex justify-between text-[11px]">
                <span style={{ color: "var(--text-secondary)" }}>Status</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: status.last_checkpoint.status === "active" ? "var(--green-bg)" : "var(--bg-panel)", color: status.last_checkpoint.status === "active" ? "var(--green)" : "var(--text-secondary)" }}>
                  {status.last_checkpoint.status}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {servers.length > 0 && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("servers")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Servers ({servers.length})</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "servers" ? "▲" : "▼"}</span>
          </button>
          {expanded === "servers" && (
            <div className="mt-1.5 space-y-1 max-h-40 overflow-y-auto">
              {servers.map(server => (
                <div key={server.id} className="rounded p-1.5" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] font-mono" style={{ color: "var(--amber)" }}>{server.host}:{server.port}</span>
                    <span className="text-[9px] px-1 py-0.5 rounded"
                      style={{ background: server.status === "running" ? "var(--green-bg)" : "var(--bg-panel)", color: server.status === "running" ? "var(--green)" : "var(--text-secondary)" }}>
                      {server.status}
                    </span>
                  </div>
                  <div className="flex justify-between mt-0.5">
                    <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>pid {server.pid}</span>
                    <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{server.owner}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {discovered.length > 0 && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("discovered")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Discovered ({discovered.length})</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "discovered" ? "▲" : "▼"}</span>
          </button>
          {expanded === "discovered" && (
            <div className="mt-1.512 space-y-1">
              {discovered.map((d, i) => (
                <div key={i} className="rounded p-1.5" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1">
                      <span style={{ color: "var(--blue)" }}>◇</span>
                      <span className="font-mono text-[10px]" style={{ color: "var(--text-secondary)" }}>{d.host}:{d.port}</span>
                      <span className="px-1 py-0.5 text-[8px] rounded" style={{ background: "var(--blue-dim)", color: "var(--blue)" }}>External</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-wrap mt-1">
                    <button
                      onClick={() => handleAttach(d.host, d.port)}
                      className="px-1.5 py-0.5 text-[9px] rounded font-semibold"
                      style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
                    >
                      Attach
                    </button>
                    <button
                      onClick={() => handleTest(d.host, d.port)}
                      className="px-1.5 py-0.5 text-[9px] rounded"
                      style={{ background: "var(--bg-panel)", color: "var(--blue)", border: "1px solid var(--border)" }}
                    >
                      Test
                    </button>
                    <button
                      onClick={() => handleIgnore(d.host, d.port)}
                      className="px-1.5 py-0.5 text-[9px] rounded"
                      style={{ background: "var(--bg-panel)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
                    >
                      Ignore
                    </button>
                  </div>
                  <div className="flex items-center gap-0.5 flex-wrap mt-0.5">
                    <button onClick={() => handleAssign(d.host, d.port, "manager")} className="px-1 py-0.5 text-[8px] rounded" style={{ background: "var(--bg-panel)", color: "var(--amber)", border: "1px solid var(--border)" }}>Mgr</button>
                    <button onClick={() => handleAssign(d.host, d.port, "worker")} className="px-1 py-0.5 text-[8px] rounded" style={{ background: "var(--bg-panel)", color: "var(--blue)", border: "1px solid var(--border)" }}>Wrk</button>
                    <button onClick={() => handleAssign(d.host, d.port, "reviewer")} className="px-1 py-0.5 text-[8px] rounded" style={{ background: "var(--bg-panel)", color: "var(--purple)", border: "1px solid var(--border)" }}>Rev</button>
                    <button onClick={() => handleAssign(d.host, d.port, "chat")} className="px-1 py-0.5 text-[8px] rounded" style={{ background: "var(--bg-panel)", color: "var(--green)", border: "1px solid var(--border)" }}>Chat</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {expanded === "discovered" && discovered.length === 0 && (
        <p className="text-[11px] italic" style={{ color: "var(--text-dim)" }}>No local opencode server found.</p>
      )}

      {checkpoints.length > 0 && (
        <div className="rounded p-2" style={{ background: "var(--bg-panel)", border: "1px solid var(--border-dim)" }}>
          <button onClick={() => toggle("history")} className="w-full flex items-center justify-between text-left">
            <p className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>Checkpoint History ({checkpoints.length})</p>
            <span className="text-[9px]" style={{ color: "var(--text-dim)" }}>{expanded === "history" ? "▲" : "▼"}</span>
          </button>
          {expanded === "history" && (
            <div className="mt-1.5 space-y-1 max-h-40 overflow-y-auto">
              {checkpoints.slice(0, 10).map(cp => (
                <div key={cp.id} className="rounded p-1.5" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)" }}>
                  <div className="flex justify-between items-start">
                    <span className="text-[10px] truncate max-w-[100px]" style={{ color: "var(--text-primary)" }}>{cp.reason || cp.id.slice(0, 8)}</span>
                    <span className="text-[9px] px-1 py-0.5 rounded shrink-0 ml-1"
                      style={{ background: cp.status === "active" ? "var(--green-bg)" : "var(--bg-panel)", color: cp.status === "active" ? "var(--green)" : "var(--text-secondary)" }}>
                      {cp.status}
                    </span>
                  </div>
                  <p className="text-[9px] mt-0.5" style={{ color: "var(--text-dim)" }}>{formatTime(cp.created_at)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!status && (
        <p className="text-[11px] italic" style={{ color: loadError ? "var(--red)" : "var(--text-dim)" }}>
          {loadError ? `Runtime unavailable: ${loadError}` : "Connecting to runtime..."}
        </p>
      )}
    </div>
  );
}
