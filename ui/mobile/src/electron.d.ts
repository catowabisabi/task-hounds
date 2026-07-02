export {};

declare global {
  interface Window {
    electronAPI?: {
      appName?: string;
      platform?: string;
      pickFolder?: () => Promise<string | null>;
    };
  }
}
