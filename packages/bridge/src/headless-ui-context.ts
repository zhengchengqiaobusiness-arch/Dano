import type { ExtensionUIContext } from "@earendil-works/pi-coding-agent";

export function createHeadlessUIContext(): ExtensionUIContext {
  const noop = () => {};

  return {
    select: async () => undefined,
    confirm: async () => false,
    input: async () => undefined,
    editor: async () => undefined,
    notify: noop,
    setStatus: noop,
    setWidget: noop,
    setTitle: noop,
    setEditorText: noop,
    getEditorText: () => "",
    onTerminalInput: () => () => {},
    setWorkingMessage: noop,
    setHiddenThinkingLabel: noop,
    setFooter: noop,
    setHeader: noop,
    custom: async <T>() => undefined as T,
    pasteToEditor: noop,
    setEditorComponent: noop,
    theme: {} as ExtensionUIContext["theme"],
    getAllThemes: () => [],
    getTheme: () => undefined,
    setTheme: () => ({ success: false, error: "Not supported" }),
    getToolsExpanded: () => false,
    setToolsExpanded: noop,
    setWorkingVisible: noop,
    setWorkingIndicator: noop,
    addAutocompleteProvider: noop,
    getEditorComponent: () => undefined,
  };
}
