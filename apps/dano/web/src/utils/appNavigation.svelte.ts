const HELP_PATH = "/help";
const RETURN_KEY = "danoHelpReturnTo";

/** Browser history owns the page; the mounted chat keeps its in-progress state. */
export function createAppNavigation() {
  let pathname = $state(window.location.pathname);
  const sync = () => { pathname = window.location.pathname; };
  function openHelp(href = HELP_PATH) {
    if (pathname === HELP_PATH) return;
    const returnTo = window.location.pathname + window.location.search + window.location.hash;
    window.history.pushState({ ...window.history.state, [RETURN_KEY]: returnTo }, "", href);
    sync();
  }
  function returnToChat() {
    if (typeof window.history.state?.[RETURN_KEY] === "string") {
      window.history.back();
    } else {
      window.history.replaceState(window.history.state, "", "/");
      sync();
    }
  }
  function handleLinkClick(event: MouseEvent) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!(anchor instanceof HTMLAnchorElement) || anchor.hasAttribute("download") || (anchor.target && anchor.target !== "_self")) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== HELP_PATH) return;
    event.preventDefault();
    openHelp(url.pathname + url.search + url.hash);
  }
  return { get isHelp() { return pathname === HELP_PATH; }, openHelp, returnToChat, sync, handleLinkClick };
}
