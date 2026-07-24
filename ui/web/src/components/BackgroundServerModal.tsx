import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { apiGet, apiPost, apiPut } from '../lib/api';

export interface McpServerInfo {
  server_name: string;
  config_file: string;
}

export interface WarningFlags {
  mcp_detected?: boolean;
  mcp_servers?: McpServerInfo[];
  streaming_fallbacked?: boolean;
}

interface ConfigInfoData {
  xdg_config_home: string | null;
  opencode_config_dir: string;
  opencode_jsonc_paths: string[];
  power_teams_db: string;
  note?: string;
}

interface OpencodeServer {
  id: string; host: string; port: number; pid: number; owner: string;
  status: string; created_at: string; last_seen?: string;
  project_session_id: string | null; model?: string | null; opencode_agent?: string | null;
}

interface OpencodeResponse { servers: OpencodeServer[]; }
interface StopAllResponse { ok: boolean; results: Array<{ server_id: string; ok: boolean; error?: string }>; }
interface DiscoverResponse { discovered: Array<{ host: string; port: number }>; }
interface GraphFlowCapacity {
  ok: boolean;
  reason: string | null;
  active_jobs: number;
  max_active_jobs: number;
  worker_count: number;
  opencode_concurrency: number;
  cpu_percent: number | null;
  max_cpu_percent: number;
  memory_percent: number | null;
  max_memory_percent: number;
}
interface RuntimeStatusResponse { graphflow_capacity?: GraphFlowCapacity; }

function relativeTime(iso: string | undefined): string {
  if (!iso) return '-';
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch { return '-'; }
}

function ownerType(owner: string): 'managed' | 'external' | 'unknown' {
  const o = owner.toLowerCase();
  if (o === 'managed') return 'managed';
  if (o === 'external') return 'external';
  return 'unknown';
}

function statusDotColor(status: string): string {
  switch (status) {
    case 'running': return 'var(--green)';
    case 'starting': return 'var(--amber)';
    case 'stopped':
    case 'crashed': return 'var(--red)';
    default: return 'var(--text-dim)';
  }
}

function groupServers(servers: OpencodeServer[]): Map<string, OpencodeServer[]> {
  const groups = new Map<string, OpencodeServer[]>();
  for (const s of servers) {
    const key = s.project_session_id ?? '__unmanaged__';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(s);
  }
  const sorted = Array.from(groups.entries()).sort((a, b) => {
    const aTime = Math.max(...a[1].map(s => new Date(s.last_seen ?? s.created_at).getTime()));
    const bTime = Math.max(...b[1].map(s => new Date(s.last_seen ?? s.created_at).getTime()));
    return aTime - bTime;
  });
  return new Map(sorted);
}

interface Toast { id: number; msg: string; type: 'success' | 'error'; }

function ToastContainer({ toasts, onRemove }: { toasts: Toast[]; onRemove: (id: number) => void }) {
  return (
    <div className='absolute top-4 left-1/2 -translate-x-1/2 z-10 flex flex-col gap-2 w-full max-w-sm pointer-events-none'>
      {toasts.map(t => (
        <div key={t.id} className='px-3 py-2 rounded-lg text-[11px] font-medium text-center shadow-lg pointer-events-auto'
          style={{ background: t.type === 'success' ? 'var(--green-bg)' : 'var(--red-bg)', color: t.type === 'success' ? 'var(--green)' : 'var(--red)', border: '1px solid ' + (t.type === 'success' ? 'var(--green-dim)' : 'var(--red-dim)') }}
          onClick={() => onRemove(t.id)}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

interface StopAllConfirmProps { count: number; onConfirm: () => void; onCancel: () => void; busy: boolean; }

function StopAllConfirm({ count, onConfirm, onCancel, busy }: StopAllConfirmProps) {
  return createPortal(
    <div className='fixed inset-0 z-[60] flex items-center justify-center' style={{ background: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(4px)' }}>
      <div className='w-72 rounded-xl p-5 shadow-2xl space-y-4' style={{ background: 'var(--bg-raised)', border: '1px solid var(--red)' }}>
        <p className='text-[13px] font-semibold' style={{ color: 'var(--red)' }}>Stop All Servers?</p>
        <p className='text-[12px]' style={{ color: 'var(--text-secondary)' }}>
          This will stop {count} managed server{count !== 1 ? 's' : ''}. This action cannot be undone.
        </p>
        <div className='flex gap-2'>
          <button onClick={onConfirm} disabled={busy} className='flex-1 py-1.5 text-[12px] font-semibold rounded-lg disabled:opacity-50'
            style={{ background: 'var(--red-bg)', border: '1px solid var(--red)', color: 'var(--red)' }}>
            {busy ? 'Stopping...' : 'Yes, stop all'}
          </button>
          <button onClick={onCancel} disabled={busy} className='flex-1 py-1.5 text-[12px] rounded-lg disabled:opacity-50'
            style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

interface ServerCardProps { server: OpencodeServer; onStop: (id: string) => void; stopping: boolean; }

function ServerCard({ server, onStop, stopping }: ServerCardProps) {
  const type = ownerType(server.owner);
  const dotColor = statusDotColor(server.status);
  return (
    <div className='rounded-lg p-3 flex flex-col gap-2' style={{ background: 'var(--bg-base)', border: '1px solid var(--border-dim)' }}>
      <div className='flex items-center gap-2 flex-wrap'>
        <span className='w-2 h-2 rounded-full shrink-0' style={{ background: dotColor }} />
        <span className='font-mono text-[12px] font-semibold' style={{ color: 'var(--text-primary)' }}>{server.host}:{server.port}</span>
        <span className='px-1.5 py-0.5 text-[9px] rounded font-semibold uppercase tracking-wider'
          style={type === 'managed' ? { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-dim)' }
            : type === 'external' ? { background: 'var(--purple-bg)', color: 'var(--purple)', border: '1px solid var(--purple-dim)' }
            : { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-dim)' }}>
          {type}
        </span>
        <span className='ml-auto text-[9px] px-1.5 py-0.5 rounded shrink-0'
          style={{ background: server.status === 'running' ? 'var(--green-bg)' : 'var(--bg-panel)', color: dotColor }}>
          {server.status}
        </span>
      </div>
      <div className='flex items-center gap-3 flex-wrap text-[10px]' style={{ color: 'var(--text-secondary)' }}>
        {server.pid > 0 && <span className='font-mono'>pid {server.pid}</span>}
        {server.model && <span className='truncate max-w-[120px]'>{server.model}</span>}
        {server.opencode_agent && <span className='truncate max-w-[100px]'>{server.opencode_agent}</span>}
        <span className='ml-auto text-[9px]' style={{ color: 'var(--text-dim)' }}>{relativeTime(server.last_seen)}</span>
      </div>
      <div className='flex justify-end'>
        <button onClick={() => onStop(server.id)} disabled={stopping}
          className='text-[10px] px-2 py-1 rounded font-semibold disabled:opacity-40 transition-colors'
          style={{ color: 'var(--red)', border: '1px solid transparent' }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--red-dim)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'transparent'; }}>
          Stop
        </button>
      </div>
    </div>
  );
}

interface BackgroundServerButtonProps {
  onClick: () => void;
  count: number;
  hasErrors?: boolean;
  hasWarnings?: boolean;
}

export function BackgroundServerButton({ onClick, count, hasErrors, hasWarnings }: BackgroundServerButtonProps) {
  const borderStyle = hasErrors
    ? '1px solid var(--red-dim)'
    : hasWarnings
    ? '1px solid var(--amber-dim)'
    : '1px solid var(--purple-dim)';
  return (
    <button onClick={onClick}
      className='relative flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold transition-colors shrink-0'
      style={{ background: 'var(--purple-bg)', color: 'var(--purple)', border: borderStyle }}
      onMouseEnter={e => { e.currentTarget.style.background = 'var(--purple)'; e.currentTarget.style.color = '#fff'; }}
      onMouseLeave={e => { e.currentTarget.style.background = 'var(--purple-bg)'; e.currentTarget.style.color = 'var(--purple)'; }}>
      Servers
      {count > 0 && (
        <span className='flex items-center justify-center rounded-full text-[9px] font-bold min-w-[16px] h-4 px-1'
          style={{ background: 'var(--purple)', color: '#fff' }}>
          {count}
        </span>
      )}
      {hasErrors && <span className='w-2 h-2 rounded-full' style={{ background: 'var(--red)' }} />}
      {!hasErrors && hasWarnings && <span className='w-2 h-2 rounded-full' style={{ background: 'var(--amber)' }} />}
    </button>
  );
}


interface BackgroundServerModalProps {
  open: boolean;
  onClose: () => void;
  warnings?: WarningFlags;
  loopActionError?: string;
  flow01Run?: { id?: number; status?: string; phase?: string; error?: string } | null;
  flow01Mode?: boolean;
}

export function BackgroundServerModal({
  open,
  onClose,
  warnings,
  loopActionError,
  flow01Run,
  flow01Mode,
}: BackgroundServerModalProps) {
  const [servers, setServers] = useState<OpencodeServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [showStopAllConfirm, setShowStopAllConfirm] = useState(false);
  const [stopAllBusy, setStopAllBusy] = useState(false);
  const [configInfo, setConfigInfo] = useState<ConfigInfoData | null>(null);
  const [capacity, setCapacity] = useState<GraphFlowCapacity | null>(null);
  const [capacityDraft, setCapacityDraft] = useState({ maxJobs: 10, workers: 10, opencode: 10 });
  const [capacitySaving, setCapacitySaving] = useState(false);
  const toastIdRef = useRef(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reportedErrorSigRef = useRef<string>('');

  const pushToast = useCallback((msg: string, type: 'success' | 'error') => {
    const id = ++toastIdRef.current;
    setToasts(prev => [...prev, { id, msg, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 5000);
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await apiGet<OpencodeResponse>('/api/runtime/opencode');
      setServers(data.servers ?? []);
    } catch { /* silent fail */ }
    try {
      const status = await apiGet<RuntimeStatusResponse>('/api/runtime/status');
      if (status.graphflow_capacity) {
        setCapacity(status.graphflow_capacity);
        setCapacityDraft({
          maxJobs: status.graphflow_capacity.max_active_jobs,
          workers: status.graphflow_capacity.worker_count,
          opencode: status.graphflow_capacity.opencode_concurrency,
        });
      }
    } catch { /* silent fail */ }
  }, []);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    intervalRef.current = setInterval(load, 30000);
    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [open, load]);

  useEffect(() => {
    if (!open) return;
    if (configInfo) return;
    apiGet<ConfigInfoData>('/api/opencode/config-info')
      .then(setConfigInfo)
      .catch(() => {});
  }, [open, configInfo]);

  const flow01Status = flow01Run?.status ? String(flow01Run.status) : '';
  const errorSig = loopActionError ? `loop:${loopActionError}` : '';

  useEffect(() => {
    if (!open || !errorSig || errorSig === reportedErrorSigRef.current) return;
    reportedErrorSigRef.current = errorSig;
    if (loopActionError) {
      pushToast('Server error: ' + loopActionError, 'error');
    }
  }, [open, errorSig, loopActionError, pushToast]);

  const hasErrors = !!loopActionError;
  const hasWarnings = !!warnings?.mcp_detected || !!warnings?.streaming_fallbacked;

  const handleRefresh = async () => {
    setLoading(true);
    try { await apiPost<DiscoverResponse>('/api/runtime/discover'); } catch { /* optional */ }
    await load();
    setLoading(false);
  };

  const saveCapacity = async () => {
    const maxJobs = Math.max(1, Math.floor(capacityDraft.maxJobs || 1));
    const workers = Math.max(1, Math.floor(capacityDraft.workers || 1));
    const opencode = Math.max(1, Math.floor(capacityDraft.opencode || 1));
    setCapacitySaving(true);
    try {
      await apiPut('/api/runtime/policy', {
        graphflow_max_active_jobs: maxJobs,
        graphflow_worker_count: workers,
        opencode_concurrency: opencode,
      });
      pushToast('Saved GraphFlow capacity', 'success');
      await load();
    } catch (err) {
      pushToast('Capacity save failed: ' + (err instanceof Error ? err.message : String(err)), 'error');
    } finally {
      setCapacitySaving(false);
    }
  };

  const handleStop = async (id: string) => {
    setStoppingIds(prev => new Set(prev).add(id));
    try {
      const res = await apiPost<{ok: boolean; outcome?: string; error?: string}>(`/api/runtime/opencode/${id}/stop`);
      if (res.ok) {
        const srv = servers.find(s => s.id === id);
        pushToast('Stopped ' + (srv?.host ?? id) + ':' + (srv?.port ?? ''), 'success');
        await load();
      } else {
        const srv = servers.find(s => s.id === id);
        pushToast('Failed to stop ' + (srv?.host ?? '') + ':' + (srv?.port ?? '') + ': ' + (res.error ?? 'unknown'), 'error');
      }
    } catch (err) {
      const srv = servers.find(s => s.id === id);
      pushToast('Failed to stop ' + (srv?.host ?? '') + ':' + (srv?.port ?? '') + ': ' + (err instanceof Error ? err.message : String(err)), 'error');
    } finally {
      setStoppingIds(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  };

  const handleStopAll = async () => {
    setStopAllBusy(true);
    try {
      const res = await apiPost<StopAllResponse>('/api/runtime/stop-all');
      if (res.ok) {
        const failed = res.results.filter((r: { ok: boolean }) => !r.ok).length;
        pushToast(failed === 0 ? 'Stopped ' + res.results.length + ' server(s)' : failed + ' of ' + res.results.length + ' failed to stop', failed > 0 ? 'error' : 'success');
        await load();
      }
    } catch (err) {
      pushToast('Stop all failed: ' + (err instanceof Error ? err.message : String(err)), 'error');
    } finally {
      setStopAllBusy(false);
      setShowStopAllConfirm(false);
    }
  };

  if (!open) return null;

  const groups = groupServers(servers);
  const managedCount = servers.filter(s => ownerType(s.owner) === 'managed').length;

  return createPortal(
    <>
      <div className='fixed inset-0 z-50 flex items-center justify-center p-4'
        style={{ background: 'rgba(0,0,0,0.68)', backdropFilter: 'blur(4px)' }}
        onKeyDown={e => e.key === 'Escape' && onClose()}
        onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        role='dialog' aria-modal='true' aria-label='Background Servers'>
        <div className='relative w-full max-w-4xl max-h-[80vh] rounded-xl shadow-2xl flex flex-col'
          style={{ background: 'var(--bg-raised)', border: '1px solid var(--border)' }}>
          <ToastContainer toasts={toasts} onRemove={id => setToasts(prev => prev.filter(t => t.id !== id))} />

          <div className='flex items-center justify-between px-5 py-4 shrink-0' style={{ borderBottom: '1px solid var(--border-dim)' }}>
            <div className='flex items-center gap-3'>
              <span style={{ color: 'var(--purple)' }}>⚡</span>
              <h2 className='text-[13px] font-semibold' style={{ color: 'var(--text-primary)' }}>Background Servers</h2>
              <span className='text-[11px] px-2 py-0.5 rounded-full' style={{ background: 'var(--purple-bg)', color: 'var(--purple)', border: '1px solid var(--purple-dim)' }}>
                {servers.length} total
              </span>
            </div>
            <div className='flex items-center gap-2'>
              <button onClick={handleRefresh} disabled={loading}
                className='text-[11px] px-2.5 py-1 rounded font-medium disabled:opacity-50 transition-colors'
                style={{ background: 'var(--bg-panel)', color: 'var(--blue)', border: '1px solid var(--border)' }}>
                Refresh
              </button>
              {managedCount > 0 && (
                <button onClick={() => setShowStopAllConfirm(true)}
                  className='text-[11px] px-2.5 py-1 rounded font-medium transition-colors'
                  style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-dim)' }}>
                  Stop All ({managedCount})
                </button>
              )}
              <button onClick={onClose} aria-label='Close'
                className='w-7 h-7 flex items-center justify-center rounded-lg transition-colors'
                style={{ background: 'var(--bg-panel)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                ✕
              </button>
            </div>
          </div>

          <div className='flex-1 overflow-y-auto p-5 space-y-4'>
            {hasErrors && (
              <div className='rounded-lg p-3 space-y-2' style={{ background: 'var(--red-bg)', border: '1px solid var(--red-dim)' }}>
                <div className='text-[11px] font-semibold' style={{ color: 'var(--red)' }}>Server Errors</div>
                {loopActionError && (
                  <div className='text-[10px] font-mono break-words' style={{ color: 'var(--text-primary)' }}>
                    <strong>Loop Error:</strong> {loopActionError}
                  </div>
                )}
              </div>
            )}

            {capacity && (
              <div className='rounded-lg p-3 space-y-3' style={{ background: 'var(--bg-base)', border: '1px solid var(--border-dim)' }}>
                <div className='flex items-center justify-between gap-3'>
                  <div>
                    <div className='text-[11px] font-semibold' style={{ color: capacity.ok ? 'var(--green)' : 'var(--amber)' }}>GraphFlow Capacity</div>
                    <div className='text-[10px] mt-1' style={{ color: 'var(--text-secondary)' }}>
                      {capacity.active_jobs}/{capacity.max_active_jobs} jobs running
                      {capacity.cpu_percent != null && ` · CPU ${capacity.cpu_percent.toFixed(1)}%`}
                      {capacity.memory_percent != null && ` · Memory ${capacity.memory_percent.toFixed(1)}%`}
                    </div>
                  </div>
                  <button onClick={saveCapacity} disabled={capacitySaving}
                    className='px-2.5 py-1 rounded text-[10px] font-semibold disabled:opacity-50'
                    style={{ background: 'var(--blue-bg)', color: 'var(--blue)', border: '1px solid var(--blue-dim)' }}>
                    {capacitySaving ? 'Saving...' : 'Save'}
                  </button>
                </div>
                {capacity.reason && (
                  <div className='text-[10px] rounded p-2' style={{ background: 'var(--amber-bg)', color: 'var(--text-primary)', border: '1px solid var(--amber-dim)' }}>
                    {capacity.reason}
                  </div>
                )}
                <div className='grid grid-cols-3 gap-2'>
                  <label className='text-[9px] uppercase tracking-wider' style={{ color: 'var(--text-dim)' }}>
                    Parallel jobs
                    <input type='number' min={1} max={200} value={capacityDraft.maxJobs}
                      onChange={e => setCapacityDraft(prev => ({ ...prev, maxJobs: Number(e.target.value) }))}
                      className='mt-1 w-full rounded px-2 py-1 text-[12px] font-mono'
                      style={{ background: 'var(--bg-panel)', color: 'var(--text-primary)', border: '1px solid var(--border)' }} />
                  </label>
                  <label className='text-[9px] uppercase tracking-wider' style={{ color: 'var(--text-dim)' }}>
                    Workers
                    <input type='number' min={1} max={200} value={capacityDraft.workers}
                      onChange={e => setCapacityDraft(prev => ({ ...prev, workers: Number(e.target.value) }))}
                      className='mt-1 w-full rounded px-2 py-1 text-[12px] font-mono'
                      style={{ background: 'var(--bg-panel)', color: 'var(--text-primary)', border: '1px solid var(--border)' }} />
                  </label>
                  <label className='text-[9px] uppercase tracking-wider' style={{ color: 'var(--text-dim)' }}>
                    OpenCode calls
                    <input type='number' min={1} max={200} value={capacityDraft.opencode}
                      onChange={e => setCapacityDraft(prev => ({ ...prev, opencode: Number(e.target.value) }))}
                      className='mt-1 w-full rounded px-2 py-1 text-[12px] font-mono'
                      style={{ background: 'var(--bg-panel)', color: 'var(--text-primary)', border: '1px solid var(--border)' }} />
                  </label>
                </div>
              </div>
            )}

            {hasWarnings && (
              <div className='rounded-lg p-3 space-y-2' style={{ background: 'var(--amber-bg)', border: '1px solid var(--amber-dim)' }}>
                <div className='text-[11px] font-semibold' style={{ color: 'var(--amber)' }}>Warnings</div>
                {warnings?.mcp_detected && (
                  <div className='text-[10px]' style={{ color: 'var(--text-primary)' }}>
                    MCP servers detected (may cause streaming issues)
                    {warnings.mcp_servers && warnings.mcp_servers.length > 0 && (
                      <ul className='mt-1 ml-4 list-disc text-[9px] font-mono'>
                        {warnings.mcp_servers.slice(0, 3).map((srv, i) => (
                          <li key={i}>{srv.server_name}: {srv.config_file}</li>
                        ))}
                        {warnings.mcp_servers.length > 3 && (
                          <li className='text-[8px]' style={{ color: 'var(--text-dim)' }}>
                            ... and {warnings.mcp_servers.length - 3} more
                          </li>
                        )}
                      </ul>
                    )}
                  </div>
                )}
                {warnings?.streaming_fallbacked && (
                  <div className='text-[10px]' style={{ color: 'var(--text-primary)' }}>
                    Streaming fallback mode is active
                  </div>
                )}
              </div>
            )}

            {flow01Mode && flow01Run && (
              <div className='rounded-lg p-3' style={{ background: 'var(--blue-bg)', border: '1px solid var(--blue-dim)' }}>
                <div className='text-[11px] font-semibold mb-1' style={{ color: 'var(--blue)' }}>Flow_01 Status</div>
                <div className='text-[10px] font-mono' style={{ color: 'var(--text-primary)' }}>
                  Run #{flow01Run.id} — {flow01Status}
                  {flow01Run.phase && <div className='text-[9px] mt-0.5' style={{ color: 'var(--text-dim)' }}>Phase: {flow01Run.phase}</div>}
                  {flow01Run.error && <div className='text-[9px] mt-1 break-words' style={{ color: 'var(--text-dim)' }}>{flow01Run.error}</div>}
                </div>
              </div>
            )}

            {configInfo && (
              <div className='rounded-lg p-3' style={{ background: 'var(--purple-bg)', border: '1px solid var(--purple-dim)' }}>
                <div className='text-[11px] font-semibold mb-2' style={{ color: 'var(--purple)' }}>Config & DB</div>
                <div className='text-[9px] font-mono space-y-1' style={{ color: 'var(--text-secondary)' }}>
                  <div className='break-all'>
                    <strong>DB:</strong> {configInfo.power_teams_db}
                  </div>
                  <div className='break-all'>
                    <strong>XDG_CONFIG_HOME:</strong> {configInfo.xdg_config_home || <span style={{ color: 'var(--text-dim)', fontStyle: 'italic' }}>not set (Windows uses AppData)</span>}
                  </div>
                  <div className='break-all'>
                    <strong>Config Dir:</strong> {configInfo.opencode_config_dir}
                  </div>
                  {configInfo.note && (
                    <div className='mt-1 text-[8px]' style={{ color: 'var(--text-dim)' }}>{configInfo.note}</div>
                  )}
                </div>
              </div>
            )}

            {servers.length === 0 ? (
              <div className='flex flex-col items-center justify-center py-12 gap-3'>
                <span className='text-[24px]'>🛑</span>
                <p className='text-[12px]' style={{ color: 'var(--text-dim)' }}>No background servers detected.</p>
                <p className='text-[11px]' style={{ color: 'var(--text-dim)' }}>Start an opencode server with <code className='font-mono px-1 rounded' style={{ background: 'var(--bg-panel)' }}>opencode serve</code> to see it here.</p>
              </div>
            ) : (
              <div className='space-y-6'>
                {Array.from(groups.entries()).map(([sessionId, srvList]) => (
                  <div key={sessionId}>
                    <p className='text-[10px] font-semibold uppercase tracking-wider mb-2' style={{ color: 'var(--text-dim)' }}>
                      {sessionId === '__unmanaged__' ? 'Unmanaged Servers' : 'Session ' + sessionId.slice(0, 8)}
                    </p>
                    <div className='grid gap-2' style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
                      {srvList.map(srv => (
                        <ServerCard key={srv.id} server={srv} onStop={handleStop} stopping={stoppingIds.has(srv.id)} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      {showStopAllConfirm && (
        <StopAllConfirm
          count={managedCount}
          onConfirm={handleStopAll}
          onCancel={() => setShowStopAllConfirm(false)}
          busy={stopAllBusy}
        />
      )}
    </>,
    document.body
  );
}
