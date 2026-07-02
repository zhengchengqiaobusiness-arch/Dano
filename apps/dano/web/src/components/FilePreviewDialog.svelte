<script lang="ts">
  import Maximize from "lucide-svelte/icons/maximize";
  import Maximize2 from "lucide-svelte/icons/maximize-2";
  import Minimize2 from "lucide-svelte/icons/minimize-2";
  import X from "lucide-svelte/icons/x";
  import ZoomIn from "lucide-svelte/icons/zoom-in";
  import ZoomOut from "lucide-svelte/icons/zoom-out";
  import { t } from "../i18n";

  export interface FilePreviewData {
    name: string;
    src?: string;
    content?: string;
    loading: boolean;
    error: string;
  }

  let {
    preview = null as FilePreviewData | null,
    onClose = () => {},
  }: {
    preview?: FilePreviewData | null;
    onClose?: () => void;
  } = $props();

  let imageFit = $state(true);
  let imageScale = $state(1);
  let imageNaturalWidth = $state(0);
  let imageNaturalHeight = $state(0);
  let imageElement = $state<HTMLImageElement | undefined>();
  let maximized = $state(false);
  let bodyElement = $state<HTMLDivElement | undefined>();
  let dragging = $state(false);
  let dragStart:
    | { x: number; y: number; scrollLeft: number; scrollTop: number }
    | null = null;
  let pinchStart:
    | { distance: number; scale: number }
    | null = null;

  function resetImageZoom() {
    imageFit = true;
    imageScale = 1;
    imageNaturalWidth = 0;
    imageNaturalHeight = 0;
  }

  function setImageFit() {
    imageFit = true;
    imageScale = 1;
  }

  function setImageOriginalSize() {
    imageFit = false;
    imageScale = 1;
  }

  function clampImageScale(scale: number): number {
    return Math.min(8, Math.max(0.1, scale));
  }

  function currentImageScale(): number {
    if (!imageFit || !imageElement || !imageNaturalWidth)
      return imageScale;
    return clampImageScale(
      imageElement.getBoundingClientRect().width / imageNaturalWidth,
    );
  }

  function zoomImage(multiplier: number) {
    const baseScale = currentImageScale();
    imageFit = false;
    imageScale = clampImageScale(baseScale * multiplier);
  }

  function handleImageLoad(event: Event) {
    const image = event.currentTarget as HTMLImageElement;
    imageNaturalWidth = image.naturalWidth;
    imageNaturalHeight = image.naturalHeight;
  }

  function startPan(event: MouseEvent) {
    if (!preview?.src || !bodyElement || event.button !== 0) return;
    dragging = true;
    dragStart = {
      x: event.clientX,
      y: event.clientY,
      scrollLeft: bodyElement.scrollLeft,
      scrollTop: bodyElement.scrollTop,
    };
  }

  function movePan(event: MouseEvent) {
    if (!dragging || !dragStart || !bodyElement) return;
    event.preventDefault();
    bodyElement.scrollLeft =
      dragStart.scrollLeft - (event.clientX - dragStart.x);
    bodyElement.scrollTop =
      dragStart.scrollTop - (event.clientY - dragStart.y);
  }

  function endPan() {
    dragging = false;
    dragStart = null;
  }

  function touchDistance(touches: TouchList): number {
    const [first, second] = [touches[0], touches[1]];
    return Math.hypot(first.clientX - second.clientX, first.clientY - second.clientY);
  }

  function startPinch(event: TouchEvent) {
    if (!preview?.src || event.touches.length !== 2) return;
    event.preventDefault();
    endPan();
    pinchStart = {
      distance: touchDistance(event.touches),
      scale: currentImageScale(),
    };
  }

  function movePinch(event: TouchEvent) {
    if (!pinchStart || event.touches.length !== 2) return;
    event.preventDefault();
    const distance = touchDistance(event.touches);
    if (!pinchStart.distance) return;
    imageFit = false;
    imageScale = clampImageScale(
      pinchStart.scale * (distance / pinchStart.distance),
    );
  }

  function endPinch(event: TouchEvent) {
    if (event.touches.length < 2) pinchStart = null;
  }

  function imageStyle(): string {
    if (imageFit || !imageNaturalWidth || !imageNaturalHeight)
      return "";
    return `width: ${Math.round(imageNaturalWidth * imageScale)}px; height: ${Math.round(imageNaturalHeight * imageScale)}px;`;
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!preview) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  $effect(() => {
    void preview?.src;
    resetImageZoom();
    maximized = false;
    endPan();
    pinchStart = null;
  });

  $effect(() => {
    if (typeof document === "undefined") return;
    if (preview) {
      document.addEventListener("keydown", handleKeydown);
      document.body.style.overflow = "hidden";
      return () => {
        document.removeEventListener("keydown", handleKeydown);
        document.body.style.removeProperty("overflow");
      };
    }
  });
</script>

{#if preview}
  <div class="file-preview-shell">
    <button
      type="button"
      class="file-preview-backdrop"
      aria-label={t("common.cancel")}
      onclick={onClose}
    ></button>
    <div
      class="file-preview-dialog"
      class:maximized={maximized}
      role="dialog"
      aria-modal="true"
      aria-label={preview.name}
      tabindex="-1"
    >
      <header class="file-preview-header">
        <div class="file-preview-title">{preview.name}</div>
        {#if preview.src}
          <div class="file-preview-controls">
            <button
              type="button"
              class="file-preview-control"
              aria-label="Zoom out"
              title="Zoom out"
              onclick={() => zoomImage(1 / 1.25)}
            >
              <ZoomOut aria-hidden="true" size={16} />
            </button>
            <button
              type="button"
              class="file-preview-control"
              aria-label="Original size"
              title="Original size"
              onclick={setImageOriginalSize}
            >
              1:1
            </button>
            <button
              type="button"
              class="file-preview-control"
              aria-label="Fit to view"
              title="Fit to view"
              onclick={setImageFit}
            >
              <Maximize2 aria-hidden="true" size={16} />
            </button>
            <button
              type="button"
              class="file-preview-control"
              aria-label="Zoom in"
              title="Zoom in"
              onclick={() => zoomImage(1.25)}
            >
              <ZoomIn aria-hidden="true" size={16} />
            </button>
          </div>
        {/if}
        <button
          type="button"
          class="file-preview-control"
          aria-label={maximized ? "Restore dialog" : "Maximize dialog"}
          title={maximized ? "Restore dialog" : "Maximize dialog"}
          onclick={() => (maximized = !maximized)}
        >
          {#if maximized}
            <Minimize2 aria-hidden="true" size={16} />
          {:else}
            <Maximize aria-hidden="true" size={16} />
          {/if}
        </button>
        <button
          type="button"
          class="file-preview-close"
          aria-label={t("common.cancel")}
          onclick={onClose}
        >
          <X aria-hidden="true" size={18} />
        </button>
      </header>
      <!-- svelte-ignore a11y_no_static_element_interactions, a11y_no_noninteractive_element_interactions: drag-to-pan is mouse-only sugar; native scrolling still works -->
      <div
        bind:this={bodyElement}
        class="file-preview-body"
        class:pannable={Boolean(preview.src)}
        class:panning={dragging}
        onmousedown={startPan}
        onmousemove={movePan}
        onmouseup={endPan}
        onmouseleave={endPan}
        ontouchstart={startPinch}
        ontouchmove={movePinch}
        ontouchend={endPinch}
        ontouchcancel={endPinch}
      >
        {#if preview.src}
          <img
            bind:this={imageElement}
            class="file-preview-image"
            class:fit={imageFit}
            src={preview.src}
            alt={preview.name}
            style={imageStyle()}
            onload={handleImageLoad}
          />
        {:else if preview.loading}
          <div class="file-preview-state">{t("fileViewer.loading")}</div>
        {:else if preview.error}
          <div class="file-preview-state error">{preview.error}</div>
        {:else if !(preview.content ?? "")}
          <div class="file-preview-state">{t("fileViewer.empty")}</div>
        {:else}
          <pre class="file-preview-text">{preview.content ?? ""}</pre>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .file-preview-shell {
    position: fixed;
    inset: 0;
    z-index: 80;
    display: grid;
    place-items: center;
    padding: 24px;
  }

  .file-preview-backdrop {
    position: absolute;
    inset: 0;
    border: 0;
    background: color-mix(in srgb, #000 42%, transparent);
    cursor: default;
  }

  .file-preview-dialog {
    position: relative;
    z-index: 1;
    display: flex;
    flex-direction: column;
    width: min(860px, 100%);
    height: min(720px, calc(100dvh - 48px));
    border: 1px solid color-mix(in srgb, var(--border) 78%, transparent);
    border-radius: 14px;
    background: var(--panel);
    box-shadow: var(--shadow-floating);
    overflow: hidden;
  }

  .file-preview-dialog.maximized {
    width: calc(100dvw - 48px);
    height: calc(100dvh - 48px);
  }

  .file-preview-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
  }

  .file-preview-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.85rem;
    font-weight: 700;
  }

  .file-preview-controls {
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }

  .file-preview-control,
  .file-preview-close {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
  }

  .file-preview-control {
    font-size: 0.72rem;
    font-weight: 700;
  }

  .file-preview-control:hover,
  .file-preview-control:focus-visible,
  .file-preview-close:hover,
  .file-preview-close:focus-visible {
    background: var(--surface-hover);
    color: var(--text);
  }

  .file-preview-body {
    display: grid;
    place-items: center;
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 14px;
  }

  .file-preview-body.pannable {
    cursor: grab;
  }

  .file-preview-body.panning {
    cursor: grabbing;
    user-select: none;
  }

  .file-preview-image {
    display: block;
    margin: 0 auto;
    object-fit: contain;
    max-width: none;
    max-height: none;
    user-select: none;
    -webkit-user-drag: none;
    touch-action: none;
  }

  .file-preview-image.fit {
    max-width: 100%;
    max-height: 100%;
  }

  .file-preview-text {
    align-self: start;
    justify-self: stretch;
    margin: 0;
    font-family: var(--pi-font-mono);
    font-size: 0.78rem;
    line-height: 1.55;
    white-space: pre-wrap;
    color: var(--text);
  }

  .file-preview-state {
    color: var(--text-muted);
    font-size: 0.85rem;
  }

  .file-preview-state.error {
    color: var(--danger);
  }

  @media (max-width: 720px) {
    .file-preview-shell {
      place-items: end stretch;
      padding: 0;
    }

    .file-preview-dialog {
      width: 100%;
      height: 82dvh;
      border-right: 0;
      border-bottom: 0;
      border-left: 0;
      border-radius: 16px 16px 0 0;
    }

    .file-preview-dialog.maximized {
      width: 100%;
      height: 100dvh;
      border-radius: 0;
    }

    .file-preview-header {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }

    .file-preview-controls {
      gap: 0;
    }
  }
</style>
