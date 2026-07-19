export interface CenterFocusTarget {
  sessionKey: string;
  toolCallId: string;
  element: HTMLElement;
}

export interface CenterFocusStage {
  show(target: CenterFocusTarget): void;
  hide(toolCallId?: string): void;
  setSession(sessionKey: string | null): void;
  destroy(): void;
}

interface ActivePresentation {
  target: CenterFocusTarget;
  anchor: HTMLElement;
  anchorStyle: string | null;
  cardStyle: string | null;
  composer: HTMLElement | null;
  composerWasInert: boolean;
  backgroundBranches: Array<{ element: HTMLElement; wasInert: boolean }>;
  sourceRect: DOMRect;
}

const DESKTOP_SAFE_MARGIN = 24;
const MOBILE_SAFE_MARGIN = 14;
const WIDTH_SCALE = 1.2;

export function createCenterFocusStage(
  root: HTMLElement,
  onActiveChange: (active: boolean) => void = () => {},
): CenterFocusStage {
  let active: ActivePresentation | null = null;

  function runTransition(update: () => void, after?: () => void): void {
    if (prefersReducedMotion()) {
      update();
      after?.();
      return;
    }
    const startViewTransition = (
      document as Document & {
        startViewTransition?: (callback: () => void) => { finished: Promise<unknown> };
      }
    ).startViewTransition;
    if (typeof startViewTransition === "function") {
      startViewTransition.call(document, update).finished.finally(after);
    } else {
      update();
      after?.();
    }
  }

  function layout(presentation: ActivePresentation | null = active): void {
    if (!presentation) return;
    const { element } = presentation.target;
    const rootRect = root.getBoundingClientRect();
    const sourceRect = presentation.sourceRect;
    presentation.anchor.style.height = `${sourceRect.height}px`;

    if (!isDesktopCenterFocusViewport()) {
      const margin = MOBILE_SAFE_MARGIN;
      element.classList.add("center-focus-mobile-card");
      element.style.removeProperty("left");
      element.style.removeProperty("top");
      element.style.removeProperty("bottom");
      element.style.removeProperty("width");
      element.style.removeProperty("max-height");
      element.style.setProperty(
        "--center-focus-left",
        `calc(${rootRect.left}px + ${margin}px + env(safe-area-inset-left, 0px))`,
      );
      element.style.setProperty(
        "--center-focus-top",
        `calc(max(${rootRect.top}px, var(--mobile-header-offset, calc(env(safe-area-inset-top, 0px) + 50px))) + ${margin}px)`,
      );
      element.style.setProperty(
        "--center-focus-bottom",
        `calc(100dvh - min(${rootRect.bottom}px, 100dvh) + ${margin}px + env(safe-area-inset-bottom, 0px))`,
      );
      element.style.setProperty(
        "--center-focus-width",
        `calc(${rootRect.width}px - ${margin * 2}px - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px))`,
      );
      element.style.setProperty(
        "--center-focus-max-height",
        `calc(min(${rootRect.bottom}px, 100dvh) - max(${rootRect.top}px, var(--mobile-header-offset, calc(env(safe-area-inset-top, 0px) + 50px))) - ${margin * 2}px - env(safe-area-inset-bottom, 0px))`,
      );
      return;
    }

    element.classList.remove("center-focus-mobile-card");
    element.style.removeProperty("--center-focus-left");
    element.style.removeProperty("--center-focus-top");
    element.style.removeProperty("--center-focus-bottom");
    element.style.removeProperty("--center-focus-width");
    element.style.removeProperty("--center-focus-max-height");
    const width = Math.max(
      0,
      Math.min(
        sourceRect.width * WIDTH_SCALE,
        rootRect.width - DESKTOP_SAFE_MARGIN * 2,
      ),
    );
    const maxHeight = Math.max(0, rootRect.height - DESKTOP_SAFE_MARGIN * 2);
    const left = rootRect.left + (rootRect.width - width) / 2;

    element.style.left = `${left}px`;
    element.style.top = `${rootRect.top + DESKTOP_SAFE_MARGIN}px`;
    element.style.bottom = `calc(100dvh - ${rootRect.bottom}px + ${DESKTOP_SAFE_MARGIN}px)`;
    element.style.width = `${width}px`;
    element.style.maxHeight = `${maxHeight}px`;
  }

  function show(target: CenterFocusTarget): void {
    if (
      active?.target.sessionKey === target.sessionKey &&
      active.target.toolCallId === target.toolCallId &&
      active.target.element === target.element
    ) return;
    hide();

    const anchor = target.element.closest<HTMLElement>(".question-card-anchor");
    if (!anchor) return;
    const composer = root.querySelector<HTMLElement>("[data-center-focus-composer]");
    const presentation: ActivePresentation = {
      target,
      anchor,
      anchorStyle: anchor.getAttribute("style"),
      cardStyle: target.element.getAttribute("style"),
      composer,
      composerWasInert: composer?.inert ?? false,
      backgroundBranches: [],
      sourceRect: target.element.getBoundingClientRect(),
    };
    active = presentation;
    target.element.classList.add("center-focus-transition-card");

    runTransition(() => {
      layout(presentation);
      root.dataset.centerFocusActive = "true";
      root.querySelector<HTMLElement>("[data-center-focus-transcript]")
        ?.setAttribute("data-center-focus-locked", "true");
      target.element.classList.add("center-focused-card");
      isolateBackground(presentation, root);
      if (composer) {
        if (composer.contains(document.activeElement)) {
          (document.activeElement as HTMLElement | null)?.blur();
        }
        composer.inert = true;
      }
      onActiveChange(true);
    });
  }

  function hide(toolCallId?: string): void {
    if (!active) return;
    if (toolCallId && active.target.toolCallId !== toolCallId) return;
    const presentation = active;
    active = null;
    runTransition(() => {
      delete root.dataset.centerFocusActive;
      root.querySelector<HTMLElement>("[data-center-focus-transcript]")
        ?.removeAttribute("data-center-focus-locked");
      presentation.target.element.classList.remove("center-focused-card");
      presentation.target.element.classList.remove("center-focus-mobile-card");
      restoreStyle(presentation.target.element, presentation.cardStyle);
      restoreStyle(presentation.anchor, presentation.anchorStyle);
      if (presentation.composer) {
        presentation.composer.inert = presentation.composerWasInert;
      }
      for (const branch of presentation.backgroundBranches) {
        branch.element.inert = branch.wasInert;
      }
      onActiveChange(false);
    }, () => presentation.target.element.classList.remove("center-focus-transition-card"));
  }

  function setSession(sessionKey: string | null): void {
    if (active && active.target.sessionKey !== sessionKey) hide();
  }

  function destroy(): void {
    hide();
    window.removeEventListener("resize", handleResize);
  }

  function handleResize(): void {
    layout();
  }

  window.addEventListener("resize", handleResize);
  return { show, hide, setSession, destroy };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function hasActiveCenterFocusStage(root: ParentNode = document): boolean {
  return Boolean(root.querySelector('[data-center-focus-active="true"]'));
}

export function isDesktopCenterFocusViewport(
  matchMedia: typeof window.matchMedia | undefined =
    typeof window === "undefined" ? undefined : window.matchMedia?.bind(window),
): boolean {
  return matchMedia ? matchMedia("(min-width: 901px)").matches : false;
}

function isolateBackground(
  presentation: ActivePresentation,
  root: HTMLElement,
): void {
  const transcript = root.querySelector<HTMLElement>("[data-center-focus-transcript]");
  if (!transcript) return;
  let branch: HTMLElement | null = presentation.anchor;
  while (branch && branch !== transcript) {
    const parent: HTMLElement | null = branch.parentElement;
    if (!parent) break;
    for (const sibling of parent.children) {
      if (sibling === branch || !(sibling instanceof HTMLElement)) continue;
      presentation.backgroundBranches.push({
        element: sibling,
        wasInert: sibling.inert,
      });
      sibling.inert = true;
    }
    branch = parent;
  }
}

function restoreStyle(element: HTMLElement, style: string | null): void {
  if (style === null) element.removeAttribute("style");
  else element.setAttribute("style", style);
}
