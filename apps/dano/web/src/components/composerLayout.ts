export function shouldComposerBeMultiline({
  hasText,
  wasMultiline,
  hasExplicitNewline,
  wrapsAtCurrentWidth,
}: {
  hasText: boolean;
  wasMultiline: boolean;
  hasExplicitNewline: boolean;
  wrapsAtCurrentWidth: boolean;
}): boolean {
  return hasText && (wasMultiline || hasExplicitNewline || wrapsAtCurrentWidth);
}
