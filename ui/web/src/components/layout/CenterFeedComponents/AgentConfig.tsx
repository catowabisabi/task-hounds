import { useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { apiGet, apiPut, apiPost } from "../../../lib/api";
import type { Agent } from "../../../lib/api";

export function AgentConfig({ agent, onSave }: { agent: Agent; onSave: () => void }) {
  const [open, setOpen]       = useState(false);
  const [port, setPort]       = useState(String(agent.port));
  const [model, setModel]     = useState(agent.model ?? "");
  const [ocAgent, setOcAgent] = useState(agent.opencode_agent ?? "");
  const [health, setHealth]   = useState<string | null>(null);
  const [saving, setSaving]   = useState(false);
  const [models, setModels]   = useState<{ id: string; name?: string }[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [agents, setAgents]   = useState<{ id: string; name?: string }[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [availableModels, setAvailableModels] = useState<{ id: string; name?: string; supports_thinking: boolean }[]>([]);
  const [validationMsg, setValidationMsg] = useState<{ text: string; type: 'error' | 'warning' } | null>(null);

  useEffect(() => {
    setPort(String(agent.port));
    setModel(agent.model ?? "");
    setOcAgent(agent.opencode_agent ?? "");
    setHealth(null);
    setModels([]);
    setAgents([]);
  }, [agent.name, agent.port, agent.backend_type, agent.model, agent.opencode_agent]);

  const fetchModels = async () => {
    if (models.length > 0) return;
    setModelsLoading(true);
    try {
      const r = await apiGet<{ models?: { id: string; name?: string }[] }>(
        `/api/opencode/models`
      );
      if (r.models) {
        setModels(r.models);
      }
    } catch {
      // Silently fail — user falls back to manual input
    } finally {
      setModelsLoading(false);
    }
  };

  const fetchAgents = async () => {
    if (agents.length > 0) return;
    setAgentsLoading(true);
    try {
      const r = await apiGet<{ agents?: { id: string; name?: string }[] } | { id: string; name?: string }[]>(
        `/api/opencode/agents`
      );
      const nextAgents = Array.isArray(r) ? r : r.agents;
      if (nextAgents) {
        setAgents(nextAgents);
      }
    } catch {
      // Silently fail — user falls back to manual input
    } finally {
      setAgentsLoading(false);
    }
  };

  const fetchAvailableModels = async () => {
    try {
      const r = await apiGet<{ models?: { id: string; name?: string; supports_thinking: boolean }[] }>(
        `/api/opencode/available-models`
      );
      if (r.models) {
        setAvailableModels(r.models);
      }
    } catch {
      // Silently fail — validation falls back to backend check
    }
  };

  useEffect(() => {
    if (!open) return;
    fetchModels();
    fetchAgents();
    fetchAvailableModels();
  }, [open]);

  const save = async () => {
    setSaving(true);
    setValidationMsg(null);
    const nextPort = parseInt(port) || agent.port;
    const nextAgent = ocAgent || "general";
    const nextModel = model || null;

    if (nextModel && availableModels.length > 0) {
      const match = availableModels.find(m => m.id === nextModel);
      if (!match) {
        setValidationMsg({ text: `Model '${nextModel}' is not in opencode.jsonc. Add it to your config first.`, type: 'error' });
        setSaving(false);
        return;
      }
      if (!match.supports_thinking) {
        setValidationMsg({ text: `Model '${nextModel}' does not support thinking. --thinking will be omitted at runtime.`, type: 'warning' });
      }
    }

    try {
      await apiPut(`/api/agents/${agent.name}`, {
        host: agent.host || "127.0.0.1",
        port: nextPort,
        backend_type: "opencode",
        model: nextModel,
        opencode_agent: nextAgent,
      });
      await apiPut(`/api/runtime/bindings/${agent.name}`, {
        host: agent.host || "127.0.0.1",
        port: nextPort,
        model: nextModel,
        opencode_agent: nextAgent,
        binding_source: "user",
      });
      onSave();
      setOpen(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setValidationMsg({ text: msg, type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const checkHealth = async () => {
    setHealth("checking...");
    try {
      const r = await apiPost<{ ok: boolean; status?: string; output?: { status?: string }; error?: { message: string } | string }>(
        `/api/agents/${agent.name}/health`,
        {
          port: parseInt(port) || agent.port,
          backend_type: "opencode",
          model: model || null,
          opencode_agent: ocAgent || null,
        }
      );
      const msg = typeof r.error === "string" ? r.error : r.error?.message;
      setHealth(r.ok ? `ok - ${r.status ?? r.output?.status ?? "healthy"}` : `fail - ${msg ?? "unhealthy"}`);
    } catch (err) {
      setHealth(err instanceof Error ? err.message : "unreachable");
    }
  };

  const modal = open ? createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: "rgba(0,0,0,0.75)" }}
      onClick={e => { if (e.target === e.currentTarget) setOpen(false); }}
    >
      <div className="w-80 rounded-xl p-5 shadow-2xl space-y-4" style={{ background: "var(--bg-raised)", border: "1px solid var(--border)" }}>
        <div className="flex items-center justify-between">
          <p className="text-[13px] font-semibold text-[var(--text-primary)]">
            <span style={{ color: "var(--amber)" }}>⚙</span> {agent.name}
          </p>
          <button onClick={() => setOpen(false)} className="text-[var(--text-dim)] hover:text-[var(--text-secondary)] text-lg leading-none">×</button>
        </div>
        {[
          { label: "Backend", node: (
            <div className="rounded px-2 py-1.5 text-[12px]" style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-secondary)" }}>
              opencode
              <span className="ml-1 text-[10px]" style={{ color: "var(--text-dim)" }}>(only)</span>
            </div>
          )},
          { label: "Model", node: (
            <div className="space-y-1">
              {models.length > 0 ? (
                <div className="space-y-1.5">
                  <select
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    value={model}
                    onChange={e => setModel(e.target.value)}
                  >
                    <option value="">— select or type below —</option>
                    {models.map(m => (
                      <option key={m.id} value={m.id}>{m.name ?? m.id}</option>
                    ))}
                  </select>
                  <input
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-dim)" }}
                    value={model}
                    onChange={e => setModel(e.target.value)}
                    placeholder="Or type custom model ID..."
                  />
                </div>
              ) : (
                <div className="relative">
                  <input
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    value={model}
                    onChange={e => setModel(e.target.value)}
                    placeholder={modelsLoading ? "Loading model list..." : "provider-id/model-id"}
                  />
                  {modelsLoading && <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px]" style={{ color: "var(--text-dim)" }}>(…)</span>}
                </div>
              )}
              <a href="https://opencode.ai/docs/models/" target="_blank" rel="noopener noreferrer" className="text-[10px] underline" style={{ color: "var(--blue)" }}>View available models</a>
            </div>
          )},
          { label: "Opencode Agent", node: (
            <div className="space-y-1">
              {agents.length > 0 ? (
                <div className="space-y-1.5">
                  <select
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    value={ocAgent}
                    onChange={e => setOcAgent(e.target.value)}
                  >
                    <option value="">— select or type below —</option>
                    {agents.map(a => (
                      <option key={a.id} value={a.id}>{a.name ?? a.id}</option>
                    ))}
                  </select>
                  <input
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border-dim)", color: "var(--text-dim)" }}
                    value={ocAgent}
                    onChange={e => setOcAgent(e.target.value)}
                    placeholder="Or type custom agent name..."
                  />
                </div>
              ) : (
                <div className="relative">
                  <input
                    className="w-full rounded px-2 py-1.5 text-[12px] outline-none"
                    style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                    value={ocAgent}
                    onChange={e => setOcAgent(e.target.value)}
                    placeholder={agentsLoading ? "Loading agent list..." : "sisyphus-junior, build, general"}
                  />
                  {agentsLoading && <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10px]" style={{ color: "var(--text-dim)" }}>(…)</span>}
                </div>
              )}
              <p className="text-[10px]" style={{ color: "var(--text-dim)" }}>Available agents loaded from OpenCode server. Pick or type custom name.</p>
            </div>
          )},
          { label: "Port", node: (
            <input className="w-full rounded px-2 py-1.5 text-[12px] outline-none" style={{ background: "var(--bg-base)", border: "1px solid var(--border)", color: "var(--text-primary)" }} value={port} onChange={e => setPort(e.target.value)} />
          )},
        ].map(({ label, node }) => (
          <div key={label} className="space-y-1">
            <label className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-dim)" }}>{label}</label>
            {node}
          </div>
        ))}
        <div className="space-y-1">
          <button onClick={checkHealth} className="w-full py-1.5 text-[11px] rounded" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>Check Health</button>
          {health && <p className="text-[11px] text-center" style={{ color: health.startsWith("ok") ? "var(--green)" : health === "checking..." ? "var(--text-secondary)" : "var(--red)" }}>{health}</p>}
        </div>
        {validationMsg && (
          <div className="rounded px-2 py-1.5 text-[11px]" style={{
            background: validationMsg.type === 'error' ? 'var(--red-bg)' : 'var(--amber-bg)',
            color: validationMsg.type === 'error' ? 'var(--red)' : 'var(--amber)',
            border: `1px solid ${validationMsg.type === 'error' ? 'var(--red-dim)' : 'var(--amber-dim)'}`,
          }}>
            {validationMsg.text}
          </div>
        )}
        <div className="flex gap-2">
          <button onClick={save} disabled={saving} className="flex-1 py-1.5 text-[12px] font-semibold rounded-lg disabled:opacity-50" style={{ background: "var(--amber)", color: "var(--bg-base)" }}>
            {saving ? "Saving..." : "Save"}
          </button>
          <button onClick={() => setOpen(false)} className="px-3 py-1.5 text-[12px] rounded-lg" style={{ background: "var(--bg-panel)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>Cancel</button>
        </div>
      </div>
    </div>,
    document.body
  ) : null;

  return (
    <>
      <button
        onClick={e => { e.stopPropagation(); setOpen(true); }}
        className="text-[11px] px-1 py-0.5 rounded transition-colors"
        style={{ color: "var(--text-dim)" }}
        title={`Configure ${agent.name}`}
        onMouseEnter={e => (e.currentTarget.style.color = "var(--amber)")}
        onMouseLeave={e => (e.currentTarget.style.color = "var(--text-dim)")}
      >⚙</button>
      {modal}
    </>
  );
}
