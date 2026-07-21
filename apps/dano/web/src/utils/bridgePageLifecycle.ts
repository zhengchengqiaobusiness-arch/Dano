export function registerBridgePageLifecycle(
  target: Window,
  disconnect: () => void,
): () => void {
  const handlePageHide = (event: PageTransitionEvent) => {
    if (event.persisted) return;
    disconnect();
  };

  target.addEventListener("pagehide", handlePageHide);
  return () => target.removeEventListener("pagehide", handlePageHide);
}
