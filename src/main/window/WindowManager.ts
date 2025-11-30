import { BrowserWindow, screen, nativeTheme, ipcMain, app, systemPreferences } from 'electron';
import { join } from 'path';
import { ZmqBridge } from '../ipc/ZmqBridge';
import { v4 as uuidv4 } from 'uuid';

// UI style for glass effect
let currentUIStyle: 'classic' | 'glass' = 'classic';

// These will be set by the main process
let closeToTrayEnabled = false;
let onTrayVisibilityUpdate: (() => void) | null = null;

export function setCloseToTrayEnabled(enabled: boolean): void {
  closeToTrayEnabled = enabled;
}

export function setTrayVisibilityCallback(callback: () => void): void {
  onTrayVisibilityUpdate = callback;
}

export interface TabData {
  id: string;
  path: string;
  title: string;
  history: string[];
  historyIndex: number;
}

export class WindowManager {
  private windows: Map<string, BrowserWindow> = new Map();
  private zmqBridge: ZmqBridge;

  constructor(zmqBridge: ZmqBridge) {
    this.zmqBridge = zmqBridge;
    this.setupIpcHandlers();
  }

  private setupIpcHandlers(): void {
    // Handle creating new window with tab data
    ipcMain.handle('window:createWithTab', async (_event, tabData: TabData, screenX: number, screenY: number) => {
      const window = await this.createWindow({
        x: screenX - 100,
        y: screenY - 20,
        tabData,
      });
      return window.id;
    });

    // Handle getting all window IDs
    ipcMain.handle('window:getAllIds', () => {
      return Array.from(this.windows.keys());
    });

    // Handle getting window bounds for drop detection
    ipcMain.handle('window:getBounds', (_event, windowId: string) => {
      const window = this.windows.get(windowId);
      if (window) {
        return window.getBounds();
      }
      return null;
    });

    // Handle transferring tab to another window
    ipcMain.on('window:transferTab', (_event, targetWindowId: string, tabData: TabData) => {
      const targetWindow = this.windows.get(targetWindowId);
      if (targetWindow) {
        targetWindow.webContents.send('tab:receive', tabData);
      }
    });

    // Handle showing/hiding drop indicator on target window
    ipcMain.on('window:showDropIndicator', (_event, targetWindowId: string, show: boolean) => {
      const targetWindow = this.windows.get(targetWindowId);
      if (targetWindow) {
        targetWindow.webContents.send('tab:dropIndicator', show);
      }
    });

    // Get the current window ID for a renderer
    ipcMain.handle('window:getId', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender);
      if (window) {
        for (const [id, win] of this.windows.entries()) {
          if (win === window) {
            return id;
          }
        }
      }
      return null;
    });

    // Focus a window
    ipcMain.on('window:focus', (_event, windowId: string) => {
      const window = this.windows.get(windowId);
      if (window) {
        window.focus();
      }
    });

    // Handle UI style changes
    ipcMain.on('window:setUIStyle', (_event, style: 'classic' | 'glass') => {
      const previousStyle = currentUIStyle;
      currentUIStyle = style;

      // Update all windows with the new style
      this.windows.forEach((window) => {
        this.applyWindowStyle(window, style);
      });

      // Note: If switching to/from glass, the window may need to be recreated
      // for full transparency support (transparent: true must be set at creation)
      // For now, we'll apply what we can dynamically
      if (previousStyle !== style) {
        console.log(`UI style changed from ${previousStyle} to ${style}`);
      }
    });

    // Window control handlers
    ipcMain.on('window:minimize', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender);
      if (window) {
        window.minimize();
      }
    });

    ipcMain.on('window:maximize', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender);
      if (window) {
        if (window.isMaximized()) {
          window.unmaximize();
        } else {
          window.maximize();
        }
      }
    });

    ipcMain.on('window:close', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender);
      if (window) {
        window.close();
      }
    });
  }

  async createMainWindow(): Promise<BrowserWindow> {
    return this.createWindow({});
  }

  async createWindow(options: {
    x?: number;
    y?: number;
    tabData?: TabData;
  } = {}): Promise<BrowserWindow> {
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;
    const windowId = uuidv4();

    // Always create windows with transparency enabled on Windows
    // This allows dynamic switching between classic and glass modes
    const isWindows = process.platform === 'win32';

    const window = new BrowserWindow({
      width: Math.min(1400, width * 0.8),
      height: Math.min(900, height * 0.8),
      x: options.x,
      y: options.y,
      minWidth: 800,
      minHeight: 600,
      frame: false,
      title: 'XPLORER', // Explicitly set the window title
      titleBarStyle: 'hidden',
      // Enable transparency for potential glass effect (Windows only)
      transparent: isWindows,
      // Start with solid background - glass effect applied via applyWindowStyle
      backgroundColor: isWindows ? '#00000000' : (nativeTheme.shouldUseDarkColors ? '#1e1e1e' : '#ffffff'),
      show: false,
      webPreferences: {
        preload: join(__dirname, '../preload/index.js'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: false,
        webSecurity: true,
      },
    });

    this.windows.set(windowId, window);

    // Show window when ready
    window.once('ready-to-show', () => {
      window.show();
      // If we have tab data, send it to the new window after it's ready
      if (options.tabData) {
        // Small delay to ensure renderer is ready
        setTimeout(() => {
          window.webContents.send('tab:initWithData', options.tabData);
        }, 100);
      }
    });

    // Forward file system events to all windows
    this.zmqBridge.onEvent((event) => {
      this.windows.forEach((win) => {
        win.webContents.send('xp:event', event);
      });
    });

    // Load the renderer
    if (process.env.NODE_ENV === 'development') {
      await window.loadURL('http://localhost:5173');
      window.webContents.openDevTools();
    } else {
      await window.loadFile(join(__dirname, '../renderer/index.html'));
    }

    // Handle window close - check if we should minimize to tray instead
    window.on('close', (event) => {
      // If close-to-tray is enabled and this is the last window, hide instead of close
      if (closeToTrayEnabled && this.windows.size === 1) {
        event.preventDefault();
        window.hide();
        // Update tray visibility when window is hidden
        if (onTrayVisibilityUpdate) {
          onTrayVisibilityUpdate();
        }
      }
    });

    // Handle window closed
    window.on('closed', () => {
      this.windows.delete(windowId);
      // Update tray visibility when window is closed
      if (onTrayVisibilityUpdate) {
        onTrayVisibilityUpdate();
      }
    });

    // Handle window show - hide tray when window becomes visible
    window.on('show', () => {
      if (onTrayVisibilityUpdate) {
        onTrayVisibilityUpdate();
      }
    });

    return window;
  }

  getMainWindow(): BrowserWindow | null {
    // Return the first window or null
    const windows = Array.from(this.windows.values());
    return windows.length > 0 ? windows[0] : null;
  }

  getWindow(id: string): BrowserWindow | null {
    return this.windows.get(id) || null;
  }

  closeAllWindows(): void {
    this.windows.forEach((window) => window.close());
  }

  /**
   * Apply visual style to a window (classic or glass).
   * For glass style, enables transparency and native blur effects.
   */
  private applyWindowStyle(window: BrowserWindow, style: 'classic' | 'glass'): void {
    if (process.platform !== 'win32') {
      // Non-Windows platforms: glass effect not supported the same way
      return;
    }

    try {
      if (style === 'glass') {
        // Enable transparency for glass effect
        window.setBackgroundColor('#00000000');

        // Try to use Windows 11 Mica/Acrylic effect
        // backgroundMaterial is available in Electron 30+ on Windows 11
        // @ts-ignore - backgroundMaterial may not be in types yet
        if (typeof window.setBackgroundMaterial === 'function') {
          // Options: 'none', 'auto', 'mica', 'acrylic', 'tabbed'
          // 'acrylic' provides the translucent blur effect
          // @ts-ignore
          window.setBackgroundMaterial('acrylic');
          console.log('Applied acrylic background material for glass effect');
        } else {
          console.log('setBackgroundMaterial not available - using CSS-only glass effect');
        }
      } else {
        // Classic style - solid background
        // Since window was created with transparent: true, we need to use
        // a solid color via the web content's CSS
        window.setBackgroundColor('#00000001'); // Nearly transparent but not fully

        // @ts-ignore
        if (typeof window.setBackgroundMaterial === 'function') {
          // @ts-ignore
          window.setBackgroundMaterial('none');
        }
      }
    } catch (error) {
      console.error('Failed to apply window style:', error);
    }
  }
}
