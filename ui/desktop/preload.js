'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  versions: {
    node: process.versions.node,
    chrome: process.versions.chrome,
    electron: process.versions.electron,
  },

  platform: process.platform,

  appName: 'Task Hounds',

  pickFolder: () => ipcRenderer.invoke('dialog:pick-folder'),

  onStatusUpdate: (callback) => {
    ipcRenderer.on('splash-status', (event, status) => callback(status));
  },
});

console.log('[Preload] Task Hounds preload loaded');
