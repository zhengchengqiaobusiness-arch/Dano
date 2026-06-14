/// <reference types="svelte" />
/// <reference types="vite/client" />

declare global {
  const __PI_WEB_DEV_DEBUG__: boolean;

  interface Window {
    __PI_WEB_CONFIG__?: {
      debugModeAvailable?: boolean;
    };
  }
}

export {};
