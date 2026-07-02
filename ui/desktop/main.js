'use strict';

const { app, BrowserWindow, shell, dialog, ipcMain } = require('electron');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const http = require('http');
const fs = require('fs');
const net = require('net');

// Settings
let SERVER_PORT = Number(process.env.TASK_HOUNDS_PORT || 8766);
const SERVER_HOST = '127.0.0.1';
function serverUrl() {
  return `http://${SERVER_HOST}:${SERVER_PORT}`;
}
function healthCheckUrl() {
  return `${serverUrl()}/api/health`;
}

function canUsePort(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once('error', () => resolve(false));
    server.once('listening', () => {
      server.close(() => resolve(true));
    });
    server.listen(port, SERVER_HOST);
  });
}

function getListeningPids(port) {
  const cmd = process.platform === 'win32'
    ? [
        'powershell',
        '-NoProfile',
        '-Command',
        `Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique`
      ]
    : ['sh', '-c', `lsof -tiTCP:${port} -sTCP:LISTEN 2>/dev/null`];

  try {
    const output = execFileSync(cmd[0], cmd.slice(1), {
      encoding: 'utf8',
      timeout: 5000,
      windowsHide: true,
    });
    return [...new Set(output
      .split(/\r?\n/)
      .map((line) => Number(line.trim()))
      .filter((pid) => Number.isInteger(pid) && pid > 0 && pid !== process.pid))];
  } catch (_) {
    return [];
  }
}

function stopListeningPids(pids) {
  let stopped = true;
  for (const pid of pids) {
    try {
      if (process.platform === 'win32') {
        execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
          stdio: 'ignore',
          timeout: 10000,
          windowsHide: true,
        });
      } else {
        execFileSync('kill', ['-TERM', String(pid)], {
          stdio: 'ignore',
          timeout: 10000,
        });
      }
    } catch (err) {
      console.warn(`[Electron] Failed to stop pid=${pid}: ${err.message}`);
      stopped = false;
    }
  }
  return stopped;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntilCanUsePort(port, timeoutMs = 5000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await canUsePort(port)) return true;
    await sleep(200);
  }
  return canUsePort(port);
}

async function chooseAlternateServerPort(preferred) {
  if (preferred !== 8766 && await canUsePort(8766)) return 8766;
  if (preferred !== 8765 && await canUsePort(8765)) return 8765;
  for (let port = 18951; port <= 19000; port++) {
    if (port === preferred) continue;
    if (await canUsePort(port)) return port;
  }
  return null;
}

async function chooseServerPort() {
  const preferred = Number(process.env.TASK_HOUNDS_PORT || 8766);
  if (await canUsePort(preferred)) return preferred;

  const pids = getListeningPids(preferred);
  const pidText = pids.length ? `\n\nProcess id(s): ${pids.join(', ')}` : '';
  const choice = dialog.showMessageBoxSync({
    type: 'warning',
    title: 'Backend port already in use',
    message: `Port ${SERVER_HOST}:${preferred} is already in use.`,
    detail:
      'Do you want Task Hounds to stop the process using this port?\n\n' +
      'Yes: stop it and reuse the requested port.\n' +
      'No: keep it running and start Task Hounds on a new port.\n' +
      'Quit: close Task Hounds without starting.' +
      pidText,
    buttons: ['Yes, stop it', 'No, use new port', 'Quit'],
    defaultId: 1,
    cancelId: 2,
    noLink: true,
  });

  if (choice === 2) {
    app.quit();
    return null;
  }

  if (choice === 0) {
    if (!pids.length) {
      dialog.showErrorBox(
        'Could not stop backend',
        `Task Hounds could not identify the process using ${SERVER_HOST}:${preferred}.`
      );
      app.quit();
      return null;
    }
    stopListeningPids(pids);
    if (await waitUntilCanUsePort(preferred)) return preferred;

    const retryChoice = dialog.showMessageBoxSync({
      type: 'warning',
      title: 'Port is still busy',
      message: `Port ${SERVER_HOST}:${preferred} is still unavailable.`,
      detail:
        'Task Hounds stopped the detected process, but Windows has not released the port or another process took it.\n\n' +
        'Do you want to start Task Hounds on a new port instead?',
      buttons: ['Use new port', 'Quit'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });
    if (retryChoice === 0) {
      const alternate = await chooseAlternateServerPort(preferred);
      if (alternate !== null) return alternate;
    }
    dialog.showErrorBox(
      'Port is still busy',
      `Task Hounds tried to stop the process using ${SERVER_HOST}:${preferred}, but the port is still unavailable.`
    );
    app.quit();
    return null;
  }

  const alternate = await chooseAlternateServerPort(preferred);
  if (alternate !== null) return alternate;
  throw new Error('No free local port is available for Task Hounds backend');
}
const POLL_INTERVAL_MS = 500;   // How often to ping the server.
// Supervisor starts OpenCode before FastAPI, so a cold first launch can take
// longer than a direct backend start while the splash remains visible.
const MAX_WAIT_MS      = 75000;

// Python command resolution
function resolvePythonCmd() {
  // On Windows, prefer the Python Launcher, then python, then python3.
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    try {
      execFileSync(cmd, ['--version'], { stdio: 'ignore', timeout: 3000, windowsHide: true });
      console.log(`[Electron] Found Python command: ${cmd}`);
      return cmd;
    } catch (_) {
      // Try the next candidate.
    }
  }
  // Return a default command so the spawn error handler can show the failure.
  const fallback = process.platform === 'win32' ? 'python' : 'python3';
  console.warn(`[Electron] No usable Python command found; falling back to: ${fallback}`);
  return fallback;
}

function augmentedEnv(extra = {}) {
  return Object.assign({}, process.env, extra);
}

function managedOpenCodeBin() {
  return path.join(
    APP_ROOT,
    'core',
    'runtime',
    'opencode_runtime',
    'node_modules',
    'opencode-ai',
    'bin',
    process.platform === 'win32' ? 'opencode.exe' : 'opencode'
  );
}

function hasOpenCodeCommand() {
  return fs.existsSync(managedOpenCodeBin());
}

function showOpenCodeInstallDialog() {
  dialog.showErrorBox(
    'Managed OpenCode is not installed',
    `Task Hounds needs its managed OpenCode runtime before it can start agents.\n\nRun installation.cmd from the Task Hounds root, then restart Task Hounds.\n\nExpected binary:\n\n${managedOpenCodeBin()}`
  );
}

// Resolve the app root. Dev and packaged paths are different.
const APP_ROOT = app.isPackaged
  ? process.resourcesPath
  : path.join(__dirname, '..', '..');

const SERVER_PACKAGE = 'task_hounds_api';
const APP_ICON = app.isPackaged
  ? path.join(process.resourcesPath, 'docs', 'image', 'Task-Hounds-Logo-Small.png')
  : path.join(APP_ROOT, 'docs', 'image', 'Task-Hounds-Logo-Small.png');
const SPLASH_BANNER = app.isPackaged
  ? path.join(process.resourcesPath, 'docs', 'image', 'banner2.png')
  : path.join(APP_ROOT, 'docs', 'image', 'banner2.png');

function desktopDataDir() {
  return app.isPackaged
    ? path.join(app.getPath('userData'), 'data')
    : path.join(APP_ROOT, 'data');
}

function desktopRuntimeDir() {
  return app.isPackaged
    ? path.join(app.getPath('userData'), 'runtime')
    : path.join(APP_ROOT, 'core', 'runtime');
}

// Startup diagnostics
console.log('[Electron] === Startup diagnostics ===');
console.log('[Electron] app.isPackaged  :', app.isPackaged);
console.log('[Electron] __dirname       :', __dirname);
console.log('[Electron] resourcesPath   :', process.resourcesPath || '(not applicable)');
console.log('[Electron] APP_ROOT        :', APP_ROOT);
console.log('[Electron] SERVER_PACKAGE  :', SERVER_PACKAGE);
console.log('[Electron] package dir     :', path.join(APP_ROOT, 'core', SERVER_PACKAGE));
console.log('[Electron] healthCheckUrl():', healthCheckUrl());

// Globals
let mainWindow   = null;
let splashWindow = null;
let pythonProcess = null;
let _abortServerWait = null; // Abort health checks immediately if Python exits.

// Global error handling
app.on('uncaughtException', (err, origin) => {
  console.error(`[Electron] uncaughtException at ${origin}:`, err);
  dialog.showErrorBox(
    'Unexpected application error',
    `An unhandled error occurred and Task Hounds will close.\n\nError: ${err.message}\n\nPlease report this issue.`
  );
  app.exit(1);
});

process.on('uncaughtException', (err) => {
  console.error('[Electron] process uncaughtException:', err);
  dialog.showErrorBox(
    'Unexpected application error',
    `An unhandled error occurred and Task Hounds will close.\n\nError: ${err.message}\n\nPlease report this issue.`
  );
  app.exit(1);
});

// Start the runtime owner. In packaged builds this is a self-contained
// PyInstaller runtime; development keeps using the local Python environment.
function startPythonServer() {
  const EXTRA_BIN_DIR = app.isPackaged
    ? path.join(process.resourcesPath, 'extra-bin')
    : path.join(__dirname, 'extra-bin');
  const PACKAGED_EXE = path.join(
    EXTRA_BIN_DIR,
    'task-hounds-runtime',
    'task-hounds-runtime.exe'
  );

  let usePackaged = false;

  if (fs.existsSync(PACKAGED_EXE)) {
    console.log(`[Electron] Found packaged server exe: ${PACKAGED_EXE}`);
    usePackaged = true;
  } else if (!fs.existsSync(path.join(APP_ROOT, 'core', SERVER_PACKAGE))) {
    const msg = `Cannot find the backend server.\n\nTried:\n  1. ${PACKAGED_EXE}\n  2. python -m ${SERVER_PACKAGE} (requires core/${SERVER_PACKAGE} package)\n\nAPP_ROOT: ${APP_ROOT}\n\nPlease run installation.cmd to install Python dependencies.`;
    console.error('[Electron]', msg);
    dialog.showErrorBox('Backend server not found', msg);
    app.quit();
    return;
  }

  const dataDir = desktopDataDir();
  const runtimeDir = desktopRuntimeDir();
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(runtimeDir, { recursive: true });

  const env = augmentedEnv({
    POWER_TEAMS_DB: path.join(dataDir, 'power_teams.db'),
    POWER_TEAMS_RUNTIME_DIR: runtimeDir,
    TASK_HOUNDS_APP_ROOT: APP_ROOT,
    TASK_HOUNDS_PORT: String(SERVER_PORT),
    TASK_HOUNDS_PORT_CONFLICT: 'quit',
    PYTHONIOENCODING: 'utf-8',
    PYTHONPATH: [
      process.env.PYTHONPATH,
      path.join(APP_ROOT, 'core'),
    ].filter(Boolean).join(path.delimiter),
  });

  if (usePackaged) {
    // PyInstaller mode: run the packaged exe directly.
    // The exe is self-contained and does not need a Python interpreter.
    console.log(`[Electron] Starting packaged supervisor: "${PACKAGED_EXE}" --port ${SERVER_PORT}`);
    console.log(`[Electron] cwd: ${APP_ROOT}`);

    pythonProcess = spawn(PACKAGED_EXE, [
      '--runtime-role', 'supervisor',
      '--host', SERVER_HOST,
      '--port', String(SERVER_PORT),
    ], {
      cwd: APP_ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    pythonProcess.stdout.on('data', (data) => {
      process.stdout.write(`[Server] ${data}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      process.stderr.write(`[Server ERR] ${data}`);
    });

    pythonProcess.on('error', (err) => {
      console.error('[Electron] Failed to start packaged server:', err.message);
      dialog.showErrorBox(
        'Server startup failed',
        `Could not start the backend server in packaged mode.\n\nError: ${err.message}`
      );
      app.quit();
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Server] exited (code=${code}, signal=${signal})`);
      if (code !== 0 && code !== null) {
        console.error(`[Electron] Server exited unexpectedly, code=${code}`);
        if (_abortServerWait) {
          _abortServerWait(new Error(`Server exited unexpectedly (exit code=${code})`));
        } else {
          dialog.showErrorBox(
            'Server exited unexpectedly',
            `The backend server exited unexpectedly (exit code=${code}).\nTask Hounds will close.`
          );
          app.quit();
        }
      }
    });
  } else {
    // Python mode
    const PYTHON_CMD = resolvePythonCmd();
    console.log(`[Electron] starting supervisor: ${PYTHON_CMD} -m ${SERVER_PACKAGE}.supervisor --port ${SERVER_PORT}`);
    console.log(`[Electron] cwd: ${APP_ROOT}`);

    pythonProcess = spawn(PYTHON_CMD, [
      '-m', `${SERVER_PACKAGE}.supervisor`,
      '--host', SERVER_HOST,
      '--port', String(SERVER_PORT),
    ], {
      cwd: APP_ROOT,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    pythonProcess.stdout.on('data', (data) => {
      process.stdout.write(`[Python] ${data}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      process.stderr.write(`[Python ERR] ${data}`);
    });

    pythonProcess.on('error', (err) => {
      console.error('[Electron] Failed to start Python process:', err.message);
      dialog.showErrorBox(
        'Python startup failed',
        `Could not start the runtime supervisor.\nPlease confirm Python 3.11+ is installed.\n\nError: ${err.message}`
      );
      app.quit();
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Python] exited (code=${code}, signal=${signal})`);
      if (code !== 0 && code !== null) {
        console.error(`[Electron] Python server exited unexpectedly, code=${code}`);
        if (_abortServerWait) {
          _abortServerWait(new Error(`Python server exited unexpectedly (exit code=${code})`));
        } else {
          dialog.showErrorBox(
            'Python server exited unexpectedly',
            `The backend server exited unexpectedly (exit code=${code}).\nTask Hounds will close.`
          );
          app.quit();
        }
      }
    });
  }
}

// Wait for the backend server to become ready.
function waitForServer(timeout) {
  const start = Date.now();
  let pingCount = 0;
  let done = false; // Prevent multiple resolve/reject calls.

  return new Promise((resolve, reject) => {
    // Expose abort at module scope so the Python exit handler can stop polling.
    _abortServerWait = (err) => {
      if (!done) {
        done = true;
        _abortServerWait = null;
        reject(err);
      }
    };

    function ping() {
      if (done) return; // Polling was stopped externally.

      const elapsed = Date.now() - start;
      if (elapsed >= timeout) {
        done = true;
        _abortServerWait = null;
        return reject(new Error(`Server was not ready within ${timeout / 1000} seconds`));
      }

      pingCount++;
      if (pingCount % 10 === 1) {
        // Log every 5 seconds to keep startup output readable.
        console.log(`[Electron] health check #${pingCount} -> ${healthCheckUrl()} (elapsed ${Math.round(elapsed / 1000)}s)`);
      }

      let settled = false; // Prevent error + timeout from scheduling duplicate pings.

      const req = http.get(healthCheckUrl(), (res) => {
        // Consume the response body so the socket is released.
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300 && !done) {
          console.log(`[Electron] server ready. HTTP ${res.statusCode}`);
          done = true;
          _abortServerWait = null;
          resolve();
          return;
        }
        if (!done) setTimeout(ping, POLL_INTERVAL_MS);
      });

      req.on('error', (err) => {
        if (settled) return;
        settled = true;
        if (!done) setTimeout(ping, POLL_INTERVAL_MS);
      });

      req.setTimeout(1000, () => {
        if (settled) return;
        settled = true;
        req.destroy(); // destroy triggers error, but settled already guards it.
        if (!done) setTimeout(ping, POLL_INTERVAL_MS);
      });
    }

    ping();
  });
}

// Splash window
function createSplashWindow() {
  try {
    splashWindow = new BrowserWindow({
      width: 820,
      height: 360,
      frame: false,
      transparent: false,
      alwaysOnTop: true,
      resizable: false,
      center: true,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
      },
      backgroundColor: '#0f172a',
      icon: APP_ICON,
    });

    splashWindow.loadFile(path.join(__dirname, 'splash.html'), {
      query: { banner: SPLASH_BANNER },
    });
    splashWindow.on('closed', () => { splashWindow = null; });

    splashWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
      console.error(`[Electron] Splash failed to load: ${errorCode} ${errorDescription}`);
      splashWindow.close();
    });
  } catch (err) {
    console.error('[Electron] Failed to create splash window:', err);
    dialog.showErrorBox('Startup failed', `Could not display the startup screen.\n\nError: ${err.message}`);
  }
}

// Main window
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,          // Show only after ready-to-show.
    frame: true,
    title: 'Task Hounds',
    backgroundColor: '#0f172a',
    icon: APP_ICON,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      webSecurity: true,
    },
  });

  // Open external links in the browser instead of navigating inside the app.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Close the splash and show the main window once.
  let splashClosed = false;
  function showMain() {
    try {
      if (splashClosed) return;
      splashClosed = true;
      console.log('[Electron] Showing main window and closing splash');
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.close();
      }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        mainWindow.focus();
      }
    } catch (err) {
      console.error('[Electron] showMain failed:', err);
    }
  }

  // Primary path: wait for rendering before showing, avoiding a white flash.
  mainWindow.on('ready-to-show', () => {
    console.log('[Electron] ready-to-show fired');
    showMain();
  });

  // Fallback path if ready-to-show does not fire.
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[Electron] did-finish-load fired');
    // Give ready-to-show 500ms priority, then force showing.
    setTimeout(showMain, 500);
  });

  // Close the splash even when the page fails to load.
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`[Electron] Page failed to load: ${errorCode} ${errorDescription}`);
    dialog.showErrorBox(
      'Page load failed',
      `Could not load the main window content (error code: ${errorCode}).\n\n${errorDescription}`
    );
    showMain();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.on('unresponsive', () => {
    console.warn('[Electron] Main window is unresponsive');
    dialog.showErrorBox(
      'Window is unresponsive',
      'The main window has stopped responding. A process may be busy.'
    );
  });

  mainWindow.on('responsive', () => {
    console.log('[Electron] Main window is responsive again');
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    console.error('[Electron] Renderer process ended:', details.reason);
    dialog.showErrorBox(
      'Renderer process crashed',
      `The renderer process closed unexpectedly (${details.reason}).\nTask Hounds will restart.`
    );
    app.exit(1);
  });

  console.log(`[Electron] loadURL: ${serverUrl()}`);
  mainWindow.loadURL(serverUrl());
}

ipcMain.handle('dialog:pick-folder', async () => {
  const owner = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
  const result = await dialog.showOpenDialog(owner, {
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

// Verify frontend dist exists.
const FRONTEND_DIST = path.join(APP_ROOT, 'ui', 'web', 'dist');
if (!fs.existsSync(FRONTEND_DIST)) {
  const msg = `Cannot find the frontend build output directory.\nPath: ${FRONTEND_DIST}\n\nPlease build the frontend first:\ncd ui/web && npm run build`;
  console.error('[Electron]', msg);
  dialog.showErrorBox('Frontend build output missing', msg);
  app.quit();
  return;
}

// App lifecycle
app.whenReady().then(async () => {
  SERVER_PORT = await chooseServerPort();
  if (SERVER_PORT === null) return;
  console.log(`[Electron] selected backend port: ${SERVER_PORT}`);

  // Show the splash first.
  createSplashWindow();

  if (!hasOpenCodeCommand()) {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    showOpenCodeInstallDialog();
    app.quit();
    return;
  }

  // Start Python.
  startPythonServer();

  try {
    // Wait for the server to become ready.
    await waitForServer(MAX_WAIT_MS);
    console.log('[Electron] server is ready; loading main window...');

    // Preflight: confirm frontend/dist exists.
    const distPath = path.join(APP_ROOT, 'ui', 'web', 'dist', 'index.html');
    if (!fs.existsSync(distPath)) {
      console.error('[Electron] Frontend build not found:', distPath);
      dialog.showErrorBox(
        'Frontend Build Missing',
        `Cannot find frontend build at:\n${distPath}\n\nPlease run: cd ui/web && npm run build`
      );
      app.exit(1);
    }

    createMainWindow();
  } catch (err) {
    console.error('[Electron]', err.message);
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    // Distinguish a timeout from a Python process crash.
    const isPythonCrash = err.message.includes('exited unexpectedly');
    dialog.showErrorBox(
      isPythonCrash ? 'Python server startup failed' : 'Server startup timed out',
      isPythonCrash
        ? `The backend server exited during startup.\n\n${err.message}\n\nPlease confirm:\n- All Python dependencies are installed (pip install -r requirements.txt)\n- The [Python ERR] console output has been checked for details`
        : `The Task Hounds runtime did not start within ${MAX_WAIT_MS / 1000} seconds.\n\n` +
          'Please confirm:\n' +
          '- Python 3.9+ is installed\n' +
          '- All Python dependencies are installed (pip install -r requirements.txt)\n' +
          `- Port ${SERVER_PORT} is not used by another process`
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createMainWindow();
  }
});

// Clean up the Python process before quitting.
app.on('before-quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Stopping runtime supervisor...');
    if (process.platform === 'win32') {
      // Windows needs taskkill to terminate the full process tree.
      spawn('taskkill', ['/PID', String(pythonProcess.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
    } else {
      pythonProcess.kill('SIGTERM');
    }
    pythonProcess = null;
  }
});

process.on('exit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    pythonProcess.kill();
  }
});
