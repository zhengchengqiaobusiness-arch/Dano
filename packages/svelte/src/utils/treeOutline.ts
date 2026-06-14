import type { RpcTreeEntry, RpcTreeTrackColumn } from "@pi-web/bridge/types";

export type TreeFilterMode =
  | "default"
  | "no-tools"
  | "user-only"
  | "labeled-only"
  | "all";

export interface TreeEntryDisplayParts {
  role: Exclude<RpcTreeEntry["role"], undefined>;
  roleLabel: string;
  labelTag: string | null;
  previewText: string;
  title: string;
}

interface TreeRowGutter {
  position: number;
  show: boolean;
}

interface VisibleTreeNode {
  entry: RpcTreeEntry;
  children: VisibleTreeNode[];
  containsActiveLeaf: boolean;
  order: number;
}

function normalizeRole(
  entry: RpcTreeEntry,
): Exclude<RpcTreeEntry["role"], undefined> {
  if (entry.role) return entry.role;

  if (entry.type === "message") {
    const label = entry.label?.toLowerCase() ?? "";
    if (label.startsWith("user:")) return "user";
    if (label.startsWith("assistant:")) return "assistant";
  }

  return "other";
}

function buildSearchText(entry: RpcTreeEntry): string {
  if (typeof entry.searchText === "string" && entry.searchText.trim()) {
    return entry.searchText.toLowerCase();
  }

  return [
    entry.labelTag,
    entry.previewText,
    entry.label,
    entry.type,
    normalizeRole(entry),
  ]
    .filter(
      (value): value is string =>
        typeof value === "string" && value.trim().length > 0,
    )
    .join(" ")
    .toLowerCase();
}

function buildRoleLabel(entry: RpcTreeEntry): string {
  const role = normalizeRole(entry);
  if (role !== "meta") return role;

  switch (entry.type) {
    case "compaction":
      return "compact";
    case "branch_summary":
      return "summary";
    case "model_change":
      return "model";
    case "thinking_level_change":
      return "thinking";
    case "session_info":
      return "title";
    default:
      return "meta";
  }
}

function buildTitle(entry: RpcTreeEntry, labelTag: string | null): string {
  const previewText =
    entry.previewText?.trim() || entry.label?.trim() || entry.type || entry.id;
  return labelTag ? `[${labelTag}] ${previewText}` : previewText;
}

export function getTreeEntryDisplayParts(
  entry: RpcTreeEntry,
): TreeEntryDisplayParts {
  const labelTag = entry.labelTag?.trim() || null;
  const previewText =
    entry.previewText?.trim() || entry.label?.trim() || entry.type || entry.id;

  return {
    role: normalizeRole(entry),
    roleLabel: buildRoleLabel(entry),
    labelTag,
    previewText,
    title: buildTitle(entry, labelTag),
  };
}

export function matchesTreeSearch(
  entry: RpcTreeEntry,
  tokens: readonly string[],
): boolean {
  if (tokens.length === 0) return true;
  const haystack = buildSearchText(entry);
  return tokens.every(token => haystack.includes(token));
}

function passesTreeFilterMode(
  entry: RpcTreeEntry,
  mode: TreeFilterMode,
): boolean {
  switch (mode) {
    case "user-only":
      return normalizeRole(entry) === "user";
    case "no-tools":
      return !entry.isSettingsEntry && normalizeRole(entry) !== "tool";
    case "labeled-only":
      return entry.isLabeled === true;
    case "all":
      return true;
    default:
      return !entry.isSettingsEntry;
  }
}

export function filterTreeEntries(
  entries: readonly RpcTreeEntry[],
  mode: TreeFilterMode,
  query: string,
): RpcTreeEntry[] {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);

  const filtered = entries.filter(entry => {
    if (entry.isActive) return true;
    if (entry.isToolOnlyAssistant) return false;
    if (!passesTreeFilterMode(entry, mode)) return false;
    return matchesTreeSearch(entry, tokens);
  });

  return recalculateVisibleTrackColumns(filtered, entries);
}

function orderTreeChildren(
  children: readonly VisibleTreeNode[],
): VisibleTreeNode[] {
  const sortByOrder = (left: VisibleTreeNode, right: VisibleTreeNode) =>
    left.order - right.order;
  const activeChildren = children
    .filter(child => child.containsActiveLeaf)
    .sort(sortByOrder);
  const inactiveChildren = children
    .filter(child => !child.containsActiveLeaf)
    .sort(sortByOrder);
  return [...activeChildren, ...inactiveChildren];
}

function buildTrackColumns(
  displayIndent: number,
  connectorPosition: number,
  isLast: boolean,
  gutters: readonly TreeRowGutter[],
): RpcTreeTrackColumn[] {
  const columns: RpcTreeTrackColumn[] = [];

  for (let level = 0; level < displayIndent; level++) {
    const gutter = gutters.find(item => item.position === level);
    if (gutter) {
      columns.push(gutter.show ? "line" : "blank");
      continue;
    }
    if (connectorPosition === level) {
      columns.push(isLast ? "branch-last" : "branch");
      continue;
    }
    columns.push("blank");
  }

  return columns;
}

export function recalculateVisibleTrackColumns(
  filteredEntries: readonly RpcTreeEntry[],
  allEntries: readonly RpcTreeEntry[],
): RpcTreeEntry[] {
  if (filteredEntries.length === 0) return [];

  const allEntriesById = new Map(allEntries.map(entry => [entry.id, entry]));
  const visibleIds = new Set(filteredEntries.map(entry => entry.id));
  const nodeById = new Map<string, VisibleTreeNode>();

  filteredEntries.forEach((entry, order) => {
    nodeById.set(entry.id, {
      entry: { ...entry },
      children: [],
      containsActiveLeaf: Boolean(entry.isOnActivePath),
      order,
    });
  });

  const roots: VisibleTreeNode[] = [];

  filteredEntries.forEach(entry => {
    const node = nodeById.get(entry.id);
    if (!node) return;

    let visibleParentId = entry.parentId ?? null;
    while (visibleParentId) {
      if (visibleIds.has(visibleParentId)) break;
      visibleParentId = allEntriesById.get(visibleParentId)?.parentId ?? null;
    }

    node.entry.parentId = visibleParentId;
    if (visibleParentId) {
      const parentNode = nodeById.get(visibleParentId);
      if (parentNode) {
        parentNode.children.push(node);
        return;
      }
    }
    roots.push(node);
  });

  const markContainsActiveLeaf = (node: VisibleTreeNode): boolean => {
    let containsActiveLeaf = Boolean(node.entry.isOnActivePath);
    for (const child of node.children) {
      if (markContainsActiveLeaf(child)) {
        containsActiveLeaf = true;
      }
    }
    node.containsActiveLeaf = containsActiveLeaf;
    return containsActiveLeaf;
  };

  roots.forEach(markContainsActiveLeaf);

  const orderedRoots = orderTreeChildren(roots);
  const result: RpcTreeEntry[] = [];
  const multipleRoots = orderedRoots.length > 1;
  const stack: Array<{
    node: VisibleTreeNode;
    indent: number;
    justBranched: boolean;
    showConnector: boolean;
    isLast: boolean;
    gutters: TreeRowGutter[];
    isVirtualRootChild: boolean;
    parentId: string | null;
  }> = [];

  for (let index = orderedRoots.length - 1; index >= 0; index--) {
    stack.push({
      node: orderedRoots[index],
      indent: multipleRoots ? 1 : 0,
      justBranched: multipleRoots,
      showConnector: multipleRoots,
      isLast: index === orderedRoots.length - 1,
      gutters: [],
      isVirtualRootChild: multipleRoots,
      parentId: null,
    });
  }

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;

    const {
      node,
      indent,
      justBranched,
      showConnector,
      isLast,
      gutters,
      isVirtualRootChild,
      parentId,
    } = current;
    const displayIndent = multipleRoots ? Math.max(0, indent - 1) : indent;
    const connectorDisplayed = showConnector && !isVirtualRootChild;
    const connectorPosition = connectorDisplayed
      ? Math.max(0, displayIndent - 1)
      : -1;
    const children = orderTreeChildren(node.children);

    result.push({
      ...node.entry,
      parentId,
      depth: displayIndent,
      trackColumns: buildTrackColumns(
        displayIndent,
        connectorPosition,
        isLast,
        gutters,
      ),
    });

    const multipleChildren = children.length > 1;
    const childIndent = multipleChildren
      ? indent + 1
      : justBranched && indent > 0
        ? indent + 1
        : indent;
    const childGutters = connectorDisplayed
      ? [...gutters, { position: connectorPosition, show: !isLast }]
      : gutters;

    for (let index = children.length - 1; index >= 0; index--) {
      stack.push({
        node: children[index],
        indent: childIndent,
        justBranched: multipleChildren,
        showConnector: multipleChildren,
        isLast: index === children.length - 1,
        gutters: childGutters,
        isVirtualRootChild: false,
        parentId: node.entry.id,
      });
    }
  }

  return result;
}
