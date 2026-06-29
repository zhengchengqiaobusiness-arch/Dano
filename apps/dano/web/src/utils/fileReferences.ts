export type InlineFileReference = {
  path: string;
  lineNumber: number;
  columnNumber?: number;
};

const FILE_REFERENCE_PATTERN =
  /^(?<path>.+?):(?<line>\d+)(?::(?<column>\d+))?$/;
const SPECIAL_FILE_NAMES = new Set([
  "dockerfile",
  "makefile",
  "readme",
  "license",
  "package.json",
  "cargo.toml",
  "go.mod",
  "go.sum",
  "tsconfig.json",
]);

function looksLikeExternalAddress(path: string): boolean {
  const trimmedPath = path.trim();
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmedPath)) {
    return true;
  }
  if (/^(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9.-]+\.[a-z]{2,})$/i.test(trimmedPath)) {
    return true;
  }
  return false;
}

function looksLikeFilePath(path: string): boolean {
  const trimmedPath = path.trim();
  if (!trimmedPath || trimmedPath === "." || trimmedPath === "..") {
    return false;
  }

  const fileName = trimmedPath.split(/[/\\]/).pop()?.toLowerCase() ?? "";
  if (!fileName) {
    return false;
  }

  if (SPECIAL_FILE_NAMES.has(fileName)) {
    return true;
  }
  if (looksLikeExternalAddress(trimmedPath)) {
    return false;
  }

  if (trimmedPath.includes("/") || trimmedPath.includes("\\")) {
    return true;
  }

  const dotIndex = fileName.lastIndexOf(".");
  return dotIndex > 0 && dotIndex < fileName.length - 1;
}

export function parseInlineFileReference(
  value: string,
): InlineFileReference | null {
  const trimmedValue = value.trim();
  if (!trimmedValue) {
    return null;
  }

  const match = FILE_REFERENCE_PATTERN.exec(trimmedValue);
  if (!match?.groups) {
    return null;
  }

  const path = match.groups.path?.trim() ?? "";
  const lineNumber = Number.parseInt(match.groups.line ?? "", 10);
  const columnNumber = match.groups.column
    ? Number.parseInt(match.groups.column, 10)
    : undefined;
  if (
    !looksLikeFilePath(path) ||
    !Number.isInteger(lineNumber) ||
    lineNumber < 1
  ) {
    return null;
  }

  const normalizedColumnNumber =
    typeof columnNumber === "number" &&
    Number.isInteger(columnNumber) &&
    columnNumber > 0
      ? columnNumber
      : undefined;

  return {
    path,
    lineNumber,
    columnNumber: normalizedColumnNumber,
  };
}
