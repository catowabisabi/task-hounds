import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "child_process";
import path from "path";
import fs from "fs";
import os from "os";

const PROJECT_ROOT = "C:\\Users\\enoma\\Desktop\\opencode-work\\agent-works\\software\\power-teams";
const CORE_DIR = path.join(PROJECT_ROOT, "core");
const PORT = "18765";

function tempDbPath(): string {
  return path.join(os.tmpdir(), `power_teams_smoke_${Date.now()}_${Math.random().toFixed(6)}.db`);
}

async function seedDb(dbPath: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const code = `import sys; sys.path.insert(0, ${JSON.stringify(path.join(PROJECT_ROOT, "core"))}); import os; os.environ["POWER_TEAMS_DB"]=${JSON.stringify(dbPath)}; from power_teams.db import init_db, seed_default_agents; init_db(); seed_default_agents();`;
    const child = spawn("python", ["-c", code], {
      cwd: PROJECT_ROOT,
      stdio: "pipe",
    });
    let stderr = "";
    child.stderr?.on("data", (d) => { stderr += d.toString(); });
    child.on("close", (code) => { if (code === 0) resolve(); else reject(new Error(stderr || `exit ${code}`)); });
  });
}

async function startBackend(dbPath: string): Promise<ChildProcess> {
  const env = { ...process.env, POWER_TEAMS_DB: dbPath };
  const child = spawn("python", ["-m", "api.fastapi_server", "--port", PORT], {
    cwd: CORE_DIR,
    env,
    stdio: "pipe",
  });
  await new Promise<void>((resolve) => {
    child.stdout?.on("data", (d) => {
      const s = d.toString();
      if (s.includes("Starting FastAPI server") || s.includes("127.0.0.1:" + PORT)) resolve();
    });
    setTimeout(resolve, 5000);
  });
  return child;
}

async function stopBackend(child: ChildProcess): Promise<void> {
  return new Promise((resolve) => {
    child.on("close", resolve);
    try { child.kill("SIGTERM"); } catch { /* ignore */ }
    setTimeout(resolve, 2000);
  });
}

async function withBackend(fn: (page: Page) => Promise<void>): Promise<void> {
  const dbPath = tempDbPath();
  await seedDb(dbPath);
  const backend = await startBackend(dbPath);
  const browser = await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.goto(`http://127.0.0.1:${PORT}`, { waitUntil: "load" });
    await page.waitForTimeout(2000);
    await fn(page);
  } finally {
    await browser.close();
    await stopBackend(backend);
    try { fs.unlinkSync(dbPath); } catch { /* ignore */ }
  }
}

import { chromium } from "@playwright/test";

test.describe("UI Smoke Tests", () => {

  test("1. Page loads with Task Hounds branding", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
    });
  });

  test("2. Header controls are present (Start Loop, Run Once, Auto Release, New Session)", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
      await page.waitForSelector("text=Start Loop");
      await page.waitForSelector("text=Run Once");
      await page.waitForSelector("text=Auto Release");
      await page.waitForSelector("text=New Session");
    });
  });

  test("3. Left rail shows Projects header and Add Project button", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
      await page.waitForSelector("text=PROJECTS");
      await page.waitForSelector("text=Add Project");
    });
  });

  test("4. Right rail shows Chat Agent panel with no-opencode behavior", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
      await page.waitForSelector("text=Chat Agent");
      await page.waitForSelector("text=No chat yet");
    });
  });

  test("5. Runtime panel shows + Checkpoint, ■ Stop All, ◇ Discover buttons and status boxes", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
      await page.waitForSelector("text=RUNTIME");
      await page.waitForSelector("text=+ Checkpoint");
      await page.waitForSelector("text=■ Stop All");
      await page.waitForSelector("text=◇ Discover");
      await page.waitForSelector("text=Managed");
      await page.waitForSelector("text=External");
    });
  });

  test("6. Health endpoint responds with opencode_enabled: false", async () => {
    const dbPath = tempDbPath();
    await seedDb(dbPath);
    const backend = await startBackend(dbPath);
    try {
      const resp = await fetch(`http://127.0.0.1:${PORT}/api/health`);
      const json = await resp.json() as { opencode_enabled?: boolean };
      expect(json.opencode_enabled).toBe(false);
    } finally {
      await stopBackend(backend);
      try { fs.unlinkSync(dbPath); } catch { /* ignore */ }
    }
  });

  test.describe("Click sequences", () => {

test("A. Left rail shows Projects panel when expanded", async () => {
    await withBackend(async (page) => {
      await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
      await page.waitForSelector("text=PROJECTS", { timeout: 8000 });
      await page.waitForSelector("text=Add Project", { timeout: 5000 });
    });
  });

    test("B. New Session button triggers session reload (no crash)", async () => {
      await withBackend(async (page) => {
        await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
        await page.click('button:has-text("New Session")');
        await page.waitForTimeout(500);
        await page.waitForSelector("text=⚡ Task Hounds");
      });
    });

    test("C. Runtime Discover button fetches external servers (no crash)", async () => {
      await withBackend(async (page) => {
        await page.waitForSelector("text=⚡ Task Hounds", { timeout: 10_000 });
        await page.click('button:has-text("◇ Discover")');
        await page.waitForTimeout(1000);
        await page.waitForSelector("text=RUNTIME");
      });
    });

  });

});
