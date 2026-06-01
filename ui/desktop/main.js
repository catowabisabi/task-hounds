'use strict';

const { app, BrowserWindow, shell, dialog, ipcMain } = require('electron');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const http = require('http');
const fs = require('fs');
const net = require('net');

// ─── 設定 ───────────────────────────────────────────────────────────────────
let SERVER_PORT = Number(process.env.TASK_HOUNDS_PORT || 8766);
const SERVER_HOST = '127.0.0.1';
function serverUrl() {
  return `http://${SERVER_HOST}:${SERVER_PORT}`;
}
// 使用 FastAPI/legacy 都支援的 API endpoint 作為 health check
function healthCheckUrl() {
  return `${serverUrl()}/api/agents`;
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

async function chooseServerPort() {
  const preferred = Number(process.env.TASK_HOUNDS_PORT || 8766);
  if (await canUsePort(preferred)) return preferred;
  if (preferred !== 8766 && await canUsePort(8766)) return 8766;
  if (await canUsePort(8765)) return 8765;
  for (let port = 18765; port <= 18850; port++) {
    if (await canUsePort(port)) return port;
  }
  throw new Error('No free local port is available for Task Hounds backend');
}
const POLL_INTERVAL_MS = 500;   // 每隔多久 ping 一次 server
const MAX_WAIT_MS      = 30000; // 最多等待 30 秒

// ─── 找到可用的 Python 指令 ───────────────────────────────────────────────────
function resolvePythonCmd() {
  // Windows 優先試 'py'（Python Launcher），再試 'python'，最後 'python3'
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3']
    : ['python3', 'python'];

  for (const cmd of candidates) {
    try {
      execFileSync(cmd, ['--version'], { stdio: 'ignore', timeout: 3000, windowsHide: true });
      console.log(`[Electron] 找到 Python 指令: ${cmd}`);
      return cmd;
    } catch (_) {
      // 繼續嘗試下一個
    }
  }
  // 找不到時回傳預設值，讓 spawn error handler 顯示錯誤
  const fallback = process.platform === 'win32' ? 'python' : 'python3';
  console.warn(`[Electron] 找不到可用的 Python 指令，回傳預設值: ${fallback}`);
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

// 找到 app root（開發 vs 打包後路徑不同）
const APP_ROOT = app.isPackaged
  ? process.resourcesPath
  : path.join(__dirname, '..', '..');

const SERVER_SCRIPT = path.join(APP_ROOT, 'core', 'api', 'fastapi_server.py');
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

// ─── 啟動前 debug 資訊 ────────────────────────────────────────────────────────
console.log('[Electron] === 啟動診斷資訊 ===');
console.log('[Electron] app.isPackaged  :', app.isPackaged);
console.log('[Electron] __dirname       :', __dirname);
console.log('[Electron] resourcesPath   :', process.resourcesPath || '(不適用)');
console.log('[Electron] APP_ROOT        :', APP_ROOT);
console.log('[Electron] SERVER_SCRIPT   :', SERVER_SCRIPT);
console.log('[Electron] SERVER_SCRIPT 存在:', fs.existsSync(SERVER_SCRIPT));
console.log('[Electron] healthCheckUrl():', healthCheckUrl());

// ─── 全域變數 ────────────────────────────────────────────────────────────────
let mainWindow   = null;
let splashWindow = null;
let pythonProcess = null;
let _abortServerWait = null; // 用於在 Python 異常退出時立即中止 health check 迴圈

// ─── 全域錯誤處理 ────────────────────────────────────────────────────────────
app.on('uncaughtException', (err, origin) => {
  console.error(`[Electron] uncaughtException at ${origin}:`, err);
  dialog.showErrorBox(
    '應用程式發生未預期錯誤',
    `發生了一個無法處理的錯誤，應用程式將關閉。\n\n錯誤：${err.message}\n\n請回報此問題。`
  );
  app.exit(1);
});

process.on('uncaughtException', (err) => {
  console.error('[Electron] process uncaughtException:', err);
  dialog.showErrorBox(
    '應用程式發生未預期錯誤',
    `發生了一個無法處理的錯誤，應用程式將關閉。\n\n錯誤：${err.message}\n\n請回報此問題。`
  );
  app.exit(1);
});

// ─── 啟動後端伺服器（支援 PyInstaller 封裝模式） ──────────────────────────────
function startPythonServer() {
  // 優先使用 PyInstaller 封裝的 exe（位於 extra-bin/）
  const EXTRA_BIN_DIR = app.isPackaged
    ? path.join(process.resourcesPath, 'extra-bin')
    : path.join(__dirname, 'extra-bin');
  const PACKAGED_EXE = path.join(EXTRA_BIN_DIR, 'task-hounds-fastapi-server.exe');

  let usePackaged = false;

  if (fs.existsSync(PACKAGED_EXE)) {
    console.log(`[Electron] 發現封裝後的 server exe: ${PACKAGED_EXE}`);
    usePackaged = true;
  } else if (!fs.existsSync(SERVER_SCRIPT)) {
    // fastapi_server.py 也不存在，無法啟動
    const msg = `找不到後端伺服器！\n\n已嘗試：\n  1. ${PACKAGED_EXE}\n  2. ${SERVER_SCRIPT}\n\nAPP_ROOT：${APP_ROOT}\n\n請確認已執行過建置流程。`;
    console.error('[Electron]', msg);
    dialog.showErrorBox('找不到後端腳本', msg);
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
    PYTHONIOENCODING: 'utf-8',
  });

  if (usePackaged) {
    // ── PyInstaller 封裝模式：直接執行 exe ────────────────────────────────
    // exe 自包含，不需要 Python 解釋器
    console.log(`[Electron] 啟動封裝 server: "${PACKAGED_EXE}" --port ${SERVER_PORT}`);
    console.log(`[Electron] cwd: ${APP_ROOT}`);

    pythonProcess = spawn(PACKAGED_EXE, ['--port', String(SERVER_PORT)], {
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
      console.error('[Electron] 無法啟動封裝 server:', err.message);
      dialog.showErrorBox(
        'Server 啟動失敗',
        `無法啟動後端伺服器（封裝模式）。\n\n錯誤：${err.message}`
      );
      app.quit();
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Server] 已結束 (code=${code}, signal=${signal})`);
      if (code !== 0 && code !== null) {
        console.error(`[Electron] Server 異常退出，code=${code}`);
        if (_abortServerWait) {
          _abortServerWait(new Error(`Server 異常退出 (exit code=${code})`));
        } else {
          dialog.showErrorBox(
            'Server 異常退出',
            `後端伺服器意外結束 (exit code=${code})。\n應用程式將關閉。`
          );
          app.quit();
        }
      }
    });
  } else {
    // ── 傳統 Python 模式 ─────────────────────────────────────────────────
    const PYTHON_CMD = resolvePythonCmd();
    console.log(`[Electron] 啟動 Python server: ${PYTHON_CMD} "${SERVER_SCRIPT}" --port ${SERVER_PORT}`);
    console.log(`[Electron] cwd: ${APP_ROOT}`);

    pythonProcess = spawn(PYTHON_CMD, [SERVER_SCRIPT, '--port', String(SERVER_PORT)], {
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
      console.error('[Electron] 無法啟動 Python process:', err.message);
      dialog.showErrorBox(
        'Python 啟動失敗',
        `無法啟動後端伺服器。\n請確認已安裝 Python 3.9+。\n\n錯誤：${err.message}`
      );
      app.quit();
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Python] 已結束 (code=${code}, signal=${signal})`);
      if (code !== 0 && code !== null) {
        console.error(`[Electron] Python server 異常退出，code=${code}`);
        if (_abortServerWait) {
          _abortServerWait(new Error(`Python server 異常退出 (exit code=${code})`));
        } else {
          dialog.showErrorBox(
            'Python Server 異常退出',
            `後端伺服器意外結束 (exit code=${code})。\n應用程式將關閉。`
          );
          app.quit();
        }
      }
    });
  }
}

// ─── 等待 server 就緒 ─────────────────────────────────────────────────────────
function waitForServer(timeout) {
  const start = Date.now();
  let pingCount = 0;
  let done = false; // 防止 resolve/reject 被多次呼叫

  return new Promise((resolve, reject) => {
    // 將 abort 函式暴露到模組層，讓 Python exit handler 可以中止迴圈
    _abortServerWait = (err) => {
      if (!done) {
        done = true;
        _abortServerWait = null;
        reject(err);
      }
    };

    function ping() {
      if (done) return; // 已被外部中止，停止 ping

      const elapsed = Date.now() - start;
      if (elapsed >= timeout) {
        done = true;
        _abortServerWait = null;
        return reject(new Error(`Server 未在 ${timeout / 1000} 秒內就緒`));
      }

      pingCount++;
      if (pingCount % 10 === 1) {
        // 每 5 秒 log 一次，避免洗版
        console.log(`[Electron] health check #${pingCount} → ${healthCheckUrl()} (elapsed ${Math.round(elapsed / 1000)}s)`);
      }

      let settled = false; // 防止 error + timeout 雙重觸發 ping

      const req = http.get(healthCheckUrl(), (res) => {
        // 必須消費 response body，否則 socket 不會釋放
        res.resume();
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300 && !done) {
          console.log(`[Electron] server 就緒！HTTP ${res.statusCode}`);
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
        req.destroy(); // destroy 會觸發 error，但 settled flag 已鎖住
        if (!done) setTimeout(ping, POLL_INTERVAL_MS);
      });
    }

    ping();
  });
}

// ─── Splash 視窗 ──────────────────────────────────────────────────────────────
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
      console.error(`[Electron] Splash 載入失敗: ${errorCode} ${errorDescription}`);
      splashWindow.close();
    });
  } catch (err) {
    console.error('[Electron] 建立 Splash 視窗失敗:', err);
    dialog.showErrorBox('啟動失敗', `無法顯示啟動畫面。\n\n錯誤：${err.message}`);
  }
}

// ─── 主視窗 ───────────────────────────────────────────────────────────────────
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,          // 等 ready-to-show 再顯示
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

  // 外部連結用瀏覽器開啟，不在 app 內導航
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // 關閉 splash、顯示主視窗（只做一次）
  let splashClosed = false;
  function showMain() {
    try {
      if (splashClosed) return;
      splashClosed = true;
      console.log('[Electron] 顯示主視窗，關閉 splash');
      if (splashWindow && !splashWindow.isDestroyed()) {
        splashWindow.close();
      }
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.show();
        mainWindow.focus();
      }
    } catch (err) {
      console.error('[Electron] showMain 執行時發生錯誤:', err);
    }
  }

  // 主要路徑：ready-to-show（頁面渲染完成後才顯示，避免白屏閃爍）
  mainWindow.on('ready-to-show', () => {
    console.log('[Electron] ready-to-show 觸發');
    showMain();
  });

  // 備援路徑：did-finish-load（若 ready-to-show 因某種原因未觸發）
  mainWindow.webContents.on('did-finish-load', () => {
    console.log('[Electron] did-finish-load 觸發');
    // 給 ready-to-show 500ms 優先，若還沒觸發則強制顯示
    setTimeout(showMain, 500);
  });

  // 頁面載入失敗時也要關閉 splash
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error(`[Electron] 頁面載入失敗: ${errorCode} ${errorDescription}`);
    dialog.showErrorBox(
      '頁面載入失敗',
      `無法載入主視窗內容 (錯誤碼: ${errorCode})。\n\n${errorDescription}`
    );
    showMain();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.on('unresponsive', () => {
    console.warn('[Electron] 主視窗無回應');
    dialog.showErrorBox(
      '視窗無回應',
      '主視窗似乎已停止回應。可能有一個程序正在忙碌中。'
    );
  });

  mainWindow.on('responsive', () => {
    console.log('[Electron] 主視窗已恢復回應');
  });

  mainWindow.webContents.on('render-process-gone', (event, details) => {
    console.error('[Electron] 渲染程序已終止:', details.reason);
    dialog.showErrorBox(
      '視窗程序崩潰',
      `視窗程序意外關閉 (${details.reason})。\n應用程式將重啟。`
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

// ─── 驗證前端 dist/ 是否存在 ────────────────────────────────────────────────
const FRONTEND_DIST = path.join(APP_ROOT, 'ui', 'web', 'dist');
if (!fs.existsSync(FRONTEND_DIST)) {
  const msg = `找不到前端建構產出目錄！\n路徑：${FRONTEND_DIST}\n\n請先執行以下指令建置前端：\ncd ui/web && npm run build`;
  console.error('[Electron]', msg);
  dialog.showErrorBox('前端建構產出不存在', msg);
  app.quit();
  return;
}

// ─── App 生命週期 ─────────────────────────────────────────────────────────────
app.whenReady().then(async () => {
  SERVER_PORT = await chooseServerPort();
  console.log(`[Electron] selected backend port: ${SERVER_PORT}`);

  // 先顯示 Splash
  createSplashWindow();

  if (!hasOpenCodeCommand()) {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    showOpenCodeInstallDialog();
    app.quit();
    return;
  }

  // 啟動 Python
  startPythonServer();

  try {
    // 等待 server 就緒
    await waitForServer(MAX_WAIT_MS);
    console.log('[Electron] server 已就緒，載入主視窗...');

    // ─── 前置檢查：確認 frontend/dist/ 存在 ─────────────────────────────
    const distPath = path.join(APP_ROOT, 'ui', 'web', 'dist', 'index.html');
    if (!fs.existsSync(distPath)) {
      console.error('[Electron] 找不到 frontend build:', distPath);
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
    // 判斷是逾時還是 Python 異常退出，給出對應的錯誤訊息
    const isPythonCrash = err.message.includes('異常退出');
    dialog.showErrorBox(
      isPythonCrash ? 'Python Server 啟動失敗' : 'Server 啟動逾時',
      isPythonCrash
        ? `後端伺服器在啟動時異常退出。\n\n${err.message}\n\n請確認：\n• 已安裝所有 Python 依賴套件 (pip install -r requirements.txt)\n• 查看 console 中的 [Python ERR] 輸出以了解詳細原因`
        : `後端 Python server 未能在 ${MAX_WAIT_MS / 1000} 秒內啟動。\n\n` +
          '請確認：\n' +
          '• 已安裝 Python 3.9+\n' +
          '• 已安裝所有 Python 依賴套件 (pip install -r requirements.txt)\n' +
          `• Port ${SERVER_PORT} 未被其他程式佔用`
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

// ─── 關閉時清理 Python process ────────────────────────────────────────────────
app.on('before-quit', () => {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] 關閉 Python server...');
    if (process.platform === 'win32') {
      // Windows 需要用 taskkill 才能確保整個 process tree 被終止
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
