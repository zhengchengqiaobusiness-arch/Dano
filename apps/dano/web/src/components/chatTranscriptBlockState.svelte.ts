import { buildToolDetailModel, buildToolInlineModel } from "../utils/toolBlock";
import type { ImageContentBlock, ToolContentBlock } from "../utils/transcript";

// ---------------------------------------------------------------------------
// Tool block expand/collapse state
// ---------------------------------------------------------------------------

export function createChatTranscriptBlockState() {
  let expandedToolBlocks = $state(new Set<string>());
  let expandedThinking = $state(new Set<string>());

  const toolBlockModelCache = new WeakMap<
    ToolContentBlock,
    ReturnType<typeof buildToolInlineModel>
  >();
  const toolBlockDetailCache = new WeakMap<
    ToolContentBlock,
    ReturnType<typeof buildToolDetailModel>
  >();

  function toggleToolBlock(blockKey: string) {
    const next = new Set(expandedToolBlocks);
    if (next.has(blockKey)) next.delete(blockKey);
    else next.add(blockKey);
    expandedToolBlocks = next;
  }

  function toggleThinking(blockKey: string) {
    const next = new Set(expandedThinking);
    if (next.has(blockKey)) next.delete(blockKey);
    else next.add(blockKey);
    expandedThinking = next;
  }

  function isToolBlockExpanded(blockKey: string): boolean {
    return expandedToolBlocks.has(blockKey);
  }

  function isThinkingExpanded(blockKey: string): boolean {
    return expandedThinking.has(blockKey);
  }

  function toolBlockModel(block: ToolContentBlock) {
    const cached = toolBlockModelCache.get(block);
    if (cached) return cached;
    const model = buildToolInlineModel(block);
    toolBlockModelCache.set(block, model);
    return model;
  }

  function toolBlockDetail(block: ToolContentBlock) {
    const cached = toolBlockDetailCache.get(block);
    if (cached) return cached;
    const detail = buildToolDetailModel(block);
    toolBlockDetailCache.set(block, detail);
    return detail;
  }

  return {
    get expandedToolBlocks() {
      return expandedToolBlocks;
    },
    get expandedThinking() {
      return expandedThinking;
    },
    toggleToolBlock,
    toggleThinking,
    isToolBlockExpanded,
    isThinkingExpanded,
    toolBlockModel,
    toolBlockDetail,
  };
}

// ---------------------------------------------------------------------------
// Image lightbox state
// ---------------------------------------------------------------------------

export function createChatTranscriptLightboxState() {
  let lightboxImages = $state<ImageContentBlock[]>([]);
  let lightboxIndex = $state(0);

  function openImageLightbox(
    images: readonly ImageContentBlock[],
    idx: number = 0,
  ) {
    if (images.length === 0) return;
    lightboxImages = [...images];
    lightboxIndex = Math.min(Math.max(idx, 0), images.length - 1);
  }

  function closeImageLightbox() {
    lightboxImages = [];
    lightboxIndex = 0;
  }

  function showPreviousLightboxImage() {
    if (lightboxImages.length <= 1) return;
    lightboxIndex =
      (lightboxIndex + lightboxImages.length - 1) % lightboxImages.length;
  }

  function showNextLightboxImage() {
    if (lightboxImages.length <= 1) return;
    lightboxIndex = (lightboxIndex + 1) % lightboxImages.length;
  }

  return {
    get lightboxImages() {
      return lightboxImages;
    },
    get lightboxIndex() {
      return lightboxIndex;
    },
    openImageLightbox,
    closeImageLightbox,
    showPreviousLightboxImage,
    showNextLightboxImage,
  };
}
