export function registerBridgePageLifecycle(
  target: EventTarget,
  disconnect: () => void,
): () => void {
  const handlePageHide = (event: Event) => {
    if ((event as PageTransitionEvent).persisted) return;
    disconnect();
  };

  target.addEventListener("pagehide", handlePageHide);
  return () => target.removeEventListener("pagehide", handlePageHide);
}
