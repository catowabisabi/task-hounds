import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { setApiBase } from '../../lib/api';
import {
  DEFAULT_SERVER_URL,
  getServerUrl,
  normalizeServerUrl,
  saveServerUrl,
} from '../../lib/mobileSettings';

type Props = {
  children: ReactNode;
};

export function ConnectionGate({ children }: Props) {
  const [address, setAddress] = useState(DEFAULT_SERVER_URL);
  const [connected, setConnected] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');

  async function connect(candidate: string, persist: boolean) {
    setChecking(true);
    setError('');
    try {
      const normalized = normalizeServerUrl(candidate);
      const response = await fetch(`${normalized}/api/health`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (persist) await saveServerUrl(normalized);
      setApiBase(normalized);
      setAddress(normalized);
      setConnected(true);
    } catch (reason) {
      setConnected(false);
      setError(
        reason instanceof Error
          ? `無法連接：${reason.message}`
          : '無法連接 Task Hounds',
      );
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    getServerUrl()
      .then((stored) => {
        setAddress(stored);
        if (stored) return connect(stored, false);
        setChecking(false);
      })
      .catch(() => setChecking(false));
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    void connect(address, true);
  }

  if (connected) return children;

  return (
    <main className="mobile-connect">
      <section className="mobile-connect__card">
        <div className="mobile-connect__mark">TH</div>
        <h1>Task Hounds</h1>
        <p>透過 Tailscale 連接你的電腦</p>
        <form onSubmit={submit}>
          <label htmlFor="server-address">Task Hounds 地址</label>
          <input
            id="server-address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
            inputMode="url"
            autoCapitalize="none"
            autoCorrect="off"
            placeholder="https://device-name.tailnet-name.ts.net"
          />
          {error && <div className="mobile-connect__error">{error}</div>}
          <button type="submit" disabled={checking || !address.trim()}>
            {checking ? '正在連接…' : '連接'}
          </button>
        </form>
        <small>手機必須已連接同一個 Tailscale tailnet。</small>
      </section>
    </main>
  );
}
