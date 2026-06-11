<script lang="ts">
  import ChevronLeft from "lucide-svelte/icons/chevron-left";
  import ChevronRight from "lucide-svelte/icons/chevron-right";
  import X from "lucide-svelte/icons/x";
  import { onMount } from "svelte";
  import type { ImageContentBlock } from "../utils/transcript";

  let {
    open = false,
    images = [] as readonly ImageContentBlock[],
    index = 0,
    onClose = () => {},
    onPrevious = () => {},
    onNext = () => {},
  }: {
    open: boolean;
    images: readonly ImageContentBlock[];
    index: number;
    onClose?: () => void;
    onPrevious?: () => void;
    onNext?: () => void;
  } = $props();

  let currentImage = $derived(images[index] ?? null);
  let hasMultipleImages = $derived(images.length > 1);
  let caption = $derived.by(() => {
    const alt = currentImage?.alt?.trim() ?? "";
    return alt && alt !== "Image attachment" ? alt : "";
  });

  function handleKeydown(event: KeyboardEvent) {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (!hasMultipleImages) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      onPrevious();
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      onNext();
    }
  }

  $effect(() => {
    if (typeof document === "undefined") return;
    if (open) {
      document.addEventListener("keydown", handleKeydown);
      document.body.style.overflow = "hidden";
      return () => {
        document.removeEventListener("keydown", handleKeydown);
        document.body.style.removeProperty("overflow");
      };
    }
  });
</script>

{#if open && currentImage}
  <div class="image-lightbox-shell">
    <div class="image-lightbox-backdrop" role="button" tabindex="0" onclick={onClose} onkeydown={(e) => (e.key === "Enter" || e.key === " ") && onClose()}></div>
    <div
      class="image-lightbox-stage"
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      tabindex="-1"
      onclick={onClose}
      onkeydown={(e) => e.key === "Escape" && onClose()}
    >
      <button
        type="button"
        class="image-lightbox-close"
        aria-label="Close image preview"
        onclick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        <X aria-hidden="true" size={18} />
      </button>

      {#if hasMultipleImages}
        <button
          type="button"
          class="image-lightbox-nav prev"
          aria-label="Previous image"
          onclick={(event) => {
            event.stopPropagation();
            onPrevious();
          }}
        >
          <ChevronLeft aria-hidden="true" size={18} />
        </button>
      {/if}

      <div class="image-lightbox-viewport">
        <figure class="image-lightbox-figure">
          <div class="image-lightbox-frame" role="presentation" onclick={(event) => event.stopPropagation()} onkeydown={(event) => event.stopPropagation()}>
            <img
              class="image-lightbox-image"
              src={currentImage.src}
              alt={currentImage.alt}
            />
          </div>
        </figure>
      </div>

      {#if hasMultipleImages}
        <button
          type="button"
          class="image-lightbox-nav next"
          aria-label="Next image"
          onclick={(event) => {
            event.stopPropagation();
            onNext();
          }}
        >
          <ChevronRight aria-hidden="true" size={18} />
        </button>
      {/if}

      <div
        class="image-lightbox-footer"
        class:empty-footer={!caption && !hasMultipleImages}
        role="presentation"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => event.stopPropagation()}
      >
        {#if caption}
          <span class="image-lightbox-caption">{caption}</span>
        {/if}
        {#if hasMultipleImages}
          <span class="image-lightbox-counter">
            {index + 1}/{images.length}
          </span>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .image-lightbox-shell {
    position: fixed;
    inset: 0;
    z-index: 1600;
    display: grid;
    place-items: center;
    padding: 24px;
  }

  .image-lightbox-backdrop {
    position: absolute;
    inset: 0;
    background:
      radial-gradient(circle at top, rgba(255, 255, 255, 0.08), transparent 28%),
      rgba(5, 5, 8, 0.84);
    backdrop-filter: blur(18px) saturate(1.1);
  }

  .image-lightbox-stage {
    position: relative;
    z-index: 1;
    width: min(100%, 1500px);
    height: min(100%, calc(100dvh - 48px));
    min-height: 0;
  }

  .image-lightbox-viewport {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
  }

  .image-lightbox-figure {
    margin: 0;
    width: 100%;
    height: 100%;
    display: grid;
    place-items: center;
  }

  .image-lightbox-frame {
    display: grid;
    place-items: center;
    max-width: min(92vw, 1400px);
    max-height: min(88vh, calc(100dvh - 140px));
    padding: clamp(10px, 1.4vw, 18px);
    border-radius: 28px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.08),
      rgba(255, 255, 255, 0.03)
    );
    box-shadow:
      0 36px 100px rgba(0, 0, 0, 0.48),
      inset 0 1px 0 rgba(255, 255, 255, 0.12);
  }

  .image-lightbox-image {
    display: block;
    max-width: min(88vw, 1320px);
    max-height: min(82vh, calc(100dvh - 180px));
    object-fit: contain;
    border-radius: 18px;
    background: rgba(0, 0, 0, 0.18);
    box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28);
  }

  .image-lightbox-footer {
    position: absolute;
    left: 50%;
    bottom: max(14px, env(safe-area-inset-bottom));
    transform: translateX(-50%);
    display: inline-flex;
    align-items: center;
    gap: 10px;
    max-width: min(88vw, 880px);
    padding: 10px 14px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    background: rgba(15, 15, 18, 0.58);
    backdrop-filter: blur(14px);
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.28);
  }

  .image-lightbox-footer.empty-footer {
    display: none;
  }

  .image-lightbox-caption {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: rgba(255, 255, 255, 0.9);
    font-size: 0.8rem;
    line-height: 1.45;
  }

  .image-lightbox-counter {
    flex-shrink: 0;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.84);
    font-size: 0.76rem;
    font-weight: 600;
  }

  .image-lightbox-close,
  .image-lightbox-nav {
    position: absolute;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 46px;
    height: 46px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.86);
    backdrop-filter: blur(12px);
    cursor: pointer;
    transition:
      transform 0.16s ease,
      background 0.16s ease,
      border-color 0.16s ease,
      color 0.16s ease,
      box-shadow 0.16s ease;
  }

  .image-lightbox-close:hover,
  .image-lightbox-close:focus-visible,
  .image-lightbox-nav:hover,
  .image-lightbox-nav:focus-visible {
    transform: translateY(-1px) scale(1.01);
    background: rgba(255, 255, 255, 0.14);
    border-color: rgba(255, 255, 255, 0.26);
    color: #fff;
    box-shadow: 0 14px 30px rgba(0, 0, 0, 0.2);
  }

  .image-lightbox-close {
    top: max(8px, env(safe-area-inset-top));
    right: max(8px, env(safe-area-inset-right));
  }

  .image-lightbox-nav {
    top: 50%;
    transform: translateY(-50%);
  }

  .image-lightbox-nav.prev {
    left: max(8px, env(safe-area-inset-left));
  }

  .image-lightbox-nav.next {
    right: max(8px, env(safe-area-inset-right));
  }

  @media (max-width: 900px) {
    .image-lightbox-shell {
      padding: 12px;
    }

    .image-lightbox-stage {
      height: min(100%, calc(100dvh - 24px));
    }

    .image-lightbox-frame {
      max-width: calc(100vw - 24px);
      max-height: calc(100dvh - 120px);
      padding: 10px;
      border-radius: 22px;
    }

    .image-lightbox-image {
      max-width: calc(100vw - 44px);
      max-height: calc(100dvh - 150px);
      border-radius: 14px;
    }

    .image-lightbox-close,
    .image-lightbox-nav {
      width: 42px;
      height: 42px;
    }

    .image-lightbox-nav.prev {
      left: 4px;
    }

    .image-lightbox-nav.next {
      right: 4px;
    }

    .image-lightbox-footer {
      width: min(calc(100vw - 24px), 100%);
      max-width: calc(100vw - 24px);
      justify-content: space-between;
      padding: 9px 12px;
      border-radius: 18px;
    }

    .image-lightbox-caption {
      white-space: normal;
      overflow: visible;
      text-overflow: unset;
    }
  }
</style>
