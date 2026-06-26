import DOMPurify from "dompurify";
import {
  createBundledHighlighter,
  createSingletonShorthands,
} from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import {
  readThemeModeFromDom,
  readThemePairFromDom,
  resolveShikiTheme,
  type ThemeMode,
  type ThemePair,
} from "../themes";

export type { ThemeMode } from "../themes";

type SupportedLanguage = keyof typeof LANGUAGE_LOADERS;

const LANGUAGE_LOADERS = {
  bash: () => import("shiki/dist/langs/bash.mjs"),
  css: () => import("shiki/dist/langs/css.mjs"),
  diff: () => import("shiki/dist/langs/diff.mjs"),
  docker: () => import("shiki/dist/langs/docker.mjs"),
  html: () => import("shiki/dist/langs/html.mjs"),
  javascript: () => import("shiki/dist/langs/javascript.mjs"),
  json: () => import("shiki/dist/langs/json.mjs"),
  jsx: () => import("shiki/dist/langs/jsx.mjs"),
  cpp: () => import("shiki/dist/langs/cpp.mjs"),
  go: () => import("shiki/dist/langs/go.mjs"),
  java: () => import("shiki/dist/langs/java.mjs"),
  make: () => import("shiki/dist/langs/make.mjs"),
  markdown: () => import("shiki/dist/langs/markdown.mjs"),
  python: () => import("shiki/dist/langs/python.mjs"),
  rust: () => import("shiki/dist/langs/rust.mjs"),
  sql: () => import("shiki/dist/langs/sql.mjs"),
  toml: () => import("shiki/dist/langs/toml.mjs"),
  tsx: () => import("shiki/dist/langs/tsx.mjs"),
  typescript: () => import("shiki/dist/langs/typescript.mjs"),
  vue: () => import("shiki/dist/langs/vue.mjs"),
  xml: () => import("shiki/dist/langs/xml.mjs"),
  yaml: () => import("shiki/dist/langs/yaml.mjs"),
  svelte: () => import("shiki/dist/langs/svelte.mjs"),
} as const;

const createReadHighlighter = createBundledHighlighter({
  langs: LANGUAGE_LOADERS,
  themes: {},
  engine: () => createJavaScriptRegexEngine(),
});

const { codeToHtml } = createSingletonShorthands(createReadHighlighter);

const LANGUAGE_ALIASES: Record<string, SupportedLanguage | "text"> = {
  js: "javascript",
  cjs: "javascript",
  mjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  json: "json",
  html: "html",
  css: "css",
  scss: "css",
  sass: "css",
  less: "css",
  vue: "vue",
  svelte: "svelte",
  md: "markdown",
  mdx: "markdown",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  fish: "bash",
  py: "python",
  python: "python",
  rb: "text",
  go: "go",
  rs: "rust",
  rust: "rust",
  java: "java",
  kt: "text",
  c: "cpp",
  h: "cpp",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  cs: "text",
  php: "text",
  sql: "sql",
  xml: "xml",
  svg: "xml",
  diff: "diff",
  patch: "diff",
};

function isSupportedLanguage(value: string): value is SupportedLanguage {
  return Object.hasOwn(LANGUAGE_LOADERS, value);
}

function appendClassName(value: unknown, className: string): string {
  const classNames = new Set(
    Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string")
      : typeof value === "string"
        ? value.split(/\s+/).filter(Boolean)
        : [],
  );
  classNames.add(className);
  return [...classNames].join(" ");
}

function sanitizeHighlightedHtml(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ["pre", "code", "span"],
    ALLOWED_ATTR: ["class", "style", "data-line"],
  });
}

function resolveHighlighterThemes(themePair?: ThemePair) {
  const pair = themePair ?? readThemePairFromDom();
  return {
    light: resolveShikiTheme(pair.light),
    dark: resolveShikiTheme(pair.dark),
  };
}

export async function highlightCodeHtml(
  code: string,
  pathOrLanguage?: string,
  themePair?: ThemePair,
): Promise<string> {
  const html = await codeToHtml(code, {
    lang: detectLanguageFromPath(pathOrLanguage),
    themes: resolveHighlighterThemes(themePair),
  });
  return sanitizeHighlightedHtml(html);
}

export async function highlightCodeLinesHtml(
  code: string,
  pathOrLanguage?: string,
  themePair?: ThemePair,
  highlightedLine?: number,
): Promise<string> {
  const html = await codeToHtml(code, {
    lang: detectLanguageFromPath(pathOrLanguage),
    themes: resolveHighlighterThemes(themePair),
    transformers: [
      {
        line(node, line) {
          node.properties.class = appendClassName(
            node.properties.class,
            "code-line",
          );
          node.properties["data-line"] = String(line);
          if (highlightedLine === line) {
            node.properties.class = appendClassName(
              node.properties.class,
              "code-line-target",
            );
          }
          return node;
        },
      },
    ],
  });
  return sanitizeHighlightedHtml(html);
}

export function detectLanguageFromPath(
  path?: string,
): SupportedLanguage | "text" {
  if (!path) return "text";
  const cleanPath = path.trim().split(/[?#]/, 1)[0] ?? path;
  const fileName = cleanPath.split(/[\\/]/).pop()?.toLowerCase() ?? "";
  if (fileName === "dockerfile") return "docker";
  if (fileName === "makefile") return "make";
  if (isSupportedLanguage(fileName)) return fileName;
  const extension = fileName.includes(".")
    ? (fileName.split(".").pop() ?? "")
    : fileName;
  return LANGUAGE_ALIASES[extension] ?? "text";
}

export function readThemeMode(): ThemeMode {
  return readThemeModeFromDom();
}
