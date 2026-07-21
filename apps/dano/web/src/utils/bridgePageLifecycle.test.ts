/** @vitest-environment happy-dom */

import { describe, expect, it, vi } from "vitest";
import { registerBridgePageLifecycle } from "./bridgePageLifecycle";

describe("Bridge page lifecycle", () => {
  it("disconnects the Bridge client when the page is permanently hidden", () => {
    const disconnect = vi.fn();
    const unregister = registerBridgePageLifecycle(window, disconnect);
    const cachedPageHide = new Event("pagehide");
    Object.defineProperty(cachedPageHide, "persisted", { value: true });

    window.dispatchEvent(cachedPageHide);
    expect(disconnect).not.toHaveBeenCalled();

    window.dispatchEvent(
      new PageTransitionEvent("pagehide", { persisted: false }),
    );

    expect(disconnect).toHaveBeenCalledOnce();

    unregister();
    window.dispatchEvent(
      new PageTransitionEvent("pagehide", { persisted: false }),
    );
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
