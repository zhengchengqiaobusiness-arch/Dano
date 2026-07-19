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
  composer: HTMLElement | null;
  composerWasInert: boolean;
  backgroundBranches: Array<{ element: HTMLElement; wasInert: boolean }>;
}

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
      composer,
      composerWasInert: composer?.inert ?? false,
      backgroundBranches: [],
    };
    active = presentation;
    target.element.classList.add("center-focus-transition-card");

    runTransition(() => {
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
  }

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
