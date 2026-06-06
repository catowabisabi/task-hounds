import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";

// Mock the api module BEFORE importing the component. apiPost/apiPut
// return resolved promises so the component's event handlers don't
// throw when clicked. apiGet returns our fixture.
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPutMock = vi.fn();

vi.mock("../../lib/api", () => ({
  apiGet: (...args: unknown[]) => apiGetMock(...args),
  apiPost: (...args: unknown[]) => apiPostMock(...args),
  apiPut: (...args: unknown[]) => apiPutMock(...args),
}));

import { RuntimePanel } from "./RuntimePanel";

const baseReadyStatus = {
  ok: true,
  ready: true,
  runtime_available: true,
  unavailable_reason: null,
  managed_opencode_count: 1,
  external_opencode_count: 0,
  active_work: null,
  last_checkpoint: null,
  role_bindings: [
    { id: 1, role: "manager", host: "127.0.0.1", port: 18765, server_instance_id: 1, model: "m", opencode_agent: "a" },
    { id: 2, role: "worker",  host: "127.0.0.1", port: 18765, server_instance_id: 1, model: "m", opencode_agent: "a" },
    { id: 3, role: "reviewer",host: "127.0.0.1", port: 18765, server_instance_id: 1, model: "m", opencode_agent: "a" },
    { id: 4, role: "chat",    host: "127.0.0.1", port: 18765, server_instance_id: 1, model: "m", opencode_agent: "a" },
  ],
  policy: {
    close_behavior: "ask",
    background_mode_enabled: false,
    max_managed_opencode_servers: 1,
  },
  managed_health: {
    ok: true,
    host: "127.0.0.1",
    port: 18765,
    pid: 12345,
    credential_warnings: [],
  },
};

const noCredsStatus = {
  ...baseReadyStatus,
  ready: false,
  runtime_available: false,
  unavailable_reason: "missing_credentials",
  managed_health: {
    ...baseReadyStatus.managed_health,
    ok: false,
    pid: null,
    credential_warnings: [
      "provider 'minimax-coding-plan' has empty apiKey — set OPENCODE_API_KEY_MINIMAX",
      "provider 'bailian-coding-plan' has empty apiKey — set OPENCODE_API_KEY_BAILIAN",
    ],
  },
};

const noServerStatus = {
  ...baseReadyStatus,
  ready: false,
  runtime_available: false,
  unavailable_reason: "no_reachable_server",
  managed_health: {
    ...baseReadyStatus.managed_health,
    credential_warnings: [],
  },
};

const bindingsBrokenStatus = {
  ...baseReadyStatus,
  ready: false,
  runtime_available: false,
  unavailable_reason: "bindings_missing_server_instance_id",
};

beforeEach(() => {
  apiGetMock.mockReset();
  apiPostMock.mockReset();
  apiPutMock.mockReset();
  apiPostMock.mockResolvedValue({ ok: true });
  apiPutMock.mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
});

describe("RuntimePanel — ready / unavailable_reason / credential_warnings", () => {
  it("renders the Ready badge when status.ready=true", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return baseReadyStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runtime-ready-badge")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("runtime-unavailable-banner")).not.toBeInTheDocument();
  });

  it("renders the unavailable banner with reason when status.ready=false and reason=missing_credentials", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return noCredsStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runtime-unavailable-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("runtime-unavailable-reason")).toHaveTextContent("missing_credentials");
    expect(screen.getByTestId("runtime-credential-warnings")).toBeInTheDocument();
    const items = screen.getByTestId("runtime-credential-warnings").querySelectorAll("li");
    expect(items.length).toBe(2);
    expect(items[0]).toHaveTextContent(/minimax-coding-plan/);
    expect(items[1]).toHaveTextContent(/bailian-coding-plan/);
    expect(screen.queryByTestId("runtime-ready-badge")).not.toBeInTheDocument();
  });

  it("renders the unavailable banner with reason=no_reachable_server", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return noServerStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runtime-unavailable-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("runtime-unavailable-reason")).toHaveTextContent("no_reachable_server");
  });

  it("renders the unavailable banner with reason=bindings_missing_server_instance_id", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return bindingsBrokenStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("runtime-unavailable-banner")).toBeInTheDocument();
    });
    expect(screen.getByTestId("runtime-unavailable-reason")).toHaveTextContent("bindings_missing_server_instance_id");
  });
});

const runningLoop = {
  running: true,
  loop_running: true,
  loop_state: "running",
  pid: 12345,
  last_start_error: null,
  last_error_at: null,
};

const failedLoop = {
  running: false,
  loop_running: false,
  loop_state: "failed",
  pid: null,
  last_start_error: "startup handshake did not complete within 10.0s",
  last_error_at: "2026-06-05T15:30:00Z",
};

const stoppedLoop = {
  running: false,
  loop_running: false,
  loop_state: "stopped",
  pid: null,
  last_start_error: null,
  last_error_at: null,
};

describe("RuntimePanel — loop state / retry / last_start_error", () => {
  it("renders the loop state badge with state=running and hides the retry button", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return baseReadyStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      if (path === "/api/workflow/status") return runningLoop;
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("loop-state-badge")).toBeInTheDocument();
    });
    const badge = screen.getByTestId("loop-state-badge");
    expect(badge).toHaveAttribute("data-state", "running");
    expect(screen.getByTestId("loop-state-value")).toHaveTextContent("running");
    expect(screen.queryByTestId("loop-retry-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loop-last-start-error")).not.toBeInTheDocument();
  });

  it("renders the loop state badge with state=failed, last_start_error visible, retry button enabled", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return baseReadyStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      if (path === "/api/workflow/status") return failedLoop;
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("loop-state-value")).toHaveTextContent("failed");
    });
    expect(screen.getByTestId("loop-state-badge")).toHaveAttribute("data-state", "failed");
    expect(screen.getByTestId("loop-last-start-error")).toHaveTextContent(/startup handshake did not complete within 10\.0s/);
    const retry = screen.getByTestId("loop-retry-button");
    expect(retry).toBeInTheDocument();
    expect(retry).toHaveTextContent(/Retry Loop/);
  });

  it("clicking the retry button calls apiPost('/api/workflow/start-loop')", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return baseReadyStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      if (path === "/api/workflow/status") return failedLoop;
      return {};
    });
    apiPostMock.mockResolvedValue({ ok: true, started: true, state: "running", pid: 99 });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("loop-retry-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("loop-retry-button"));
    await waitFor(() => {
      const calls = apiPostMock.mock.calls.map(c => c[0]);
      expect(calls).toContain("/api/workflow/start-loop");
    });
  });

  it("renders the loop state badge with state=stopped and shows a Start Loop button (not Retry)", async () => {
    apiGetMock.mockImplementation(async (path: string) => {
      if (path === "/api/runtime/status") return baseReadyStatus;
      if (path === "/api/runtime/opencode") return { servers: [] };
      if (path === "/api/runtime/checkpoints") return { checkpoints: [] };
      if (path === "/api/workflow/status") return stoppedLoop;
      return {};
    });

    render(<RuntimePanel />);

    await waitFor(() => {
      expect(screen.getByTestId("loop-state-value")).toHaveTextContent("stopped");
    });
    const btn = screen.getByTestId("loop-retry-button");
    expect(btn).toHaveTextContent(/Start Loop/);
  });
});
