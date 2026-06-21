export interface BridgeExitKeybindings {
  matches?: (input: string, action: string) => boolean;
}

const ESCAPE_CHAR = String.fromCharCode(0x1b);
const LOCK_MODIFIER_MASK = 64 + 128;
const CTRL_MODIFIER = 4;
const CTRL_C_CODEPOINT = 99;

function hasOnlyCtrlModifier(modifierValue: number): boolean {
  const modifier = (modifierValue - 1) & ~LOCK_MODIFIER_MASK;
  return modifier === CTRL_MODIFIER;
}

function isCtrlCInput(input: string): boolean {
  if (input === "\u0003") {
    return true;
  }

  // Kitty keyboard protocol CSI-u: ESC [ <codepoint>[...];<modifier>[:event] u
  if (input.startsWith(`${ESCAPE_CHAR}[`) && input.endsWith("u")) {
    const kittyMatch = input
      .slice(2)
      .match(/^(\d+)(?::\d*)?(?::\d+)?(?:;(\d+))?(?::\d+)?u$/);
    if (kittyMatch) {
      const codepoint = Number(kittyMatch[1]);
      const modifierValue = Number(kittyMatch[2] ?? "1");
      return (
        codepoint === CTRL_C_CODEPOINT && hasOnlyCtrlModifier(modifierValue)
      );
    }
  }

  // xterm modifyOtherKeys: ESC [ 27 ; <modifier> ; <codepoint> ~
  if (input.startsWith(`${ESCAPE_CHAR}[27;`) && input.endsWith("~")) {
    const modifyOtherKeysMatch = input.slice(2).match(/^27;(\d+);(\d+)~$/);
    if (modifyOtherKeysMatch) {
      const modifierValue = Number(modifyOtherKeysMatch[1]);
      const codepoint = Number(modifyOtherKeysMatch[2]);
      return (
        codepoint === CTRL_C_CODEPOINT && hasOnlyCtrlModifier(modifierValue)
      );
    }
  }

  return false;
}

export function isBridgeExitInput(
  input: string,
  keybindings?: BridgeExitKeybindings,
): boolean {
  if (isCtrlCInput(input)) {
    return true;
  }

  if (!keybindings?.matches) {
    return false;
  }

  // Pi's keybinding layer maps Ctrl+C through cancel/copy actions rather than
  // always surfacing a raw SIGINT byte in custom views.
  return (
    keybindings.matches(input, "selectCancel") ||
    keybindings.matches(input, "copy")
  );
}
