import {
  codeToHtml,
  createBundledHighlighter,
  createCssVariablesTheme,
  getTokenStyleObject,
  normalizeTheme,
  stringifyTokenStyle,
} from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const bundledLanguages = {
  bash: () => import("shiki/dist/langs/bash.mjs"),
  c: () => import("shiki/dist/langs/c.mjs"),
  cpp: () => import("shiki/dist/langs/cpp.mjs"),
  css: () => import("shiki/dist/langs/css.mjs"),
  diff: () => import("shiki/dist/langs/diff.mjs"),
  docker: () => import("shiki/dist/langs/docker.mjs"),
  dockerfile: () => import("shiki/dist/langs/dockerfile.mjs"),
  go: () => import("shiki/dist/langs/go.mjs"),
  html: () => import("shiki/dist/langs/html.mjs"),
  java: () => import("shiki/dist/langs/java.mjs"),
  javascript: () => import("shiki/dist/langs/javascript.mjs"),
  json: () => import("shiki/dist/langs/json.mjs"),
  jsx: () => import("shiki/dist/langs/jsx.mjs"),
  make: () => import("shiki/dist/langs/make.mjs"),
  makefile: () => import("shiki/dist/langs/makefile.mjs"),
  markdown: () => import("shiki/dist/langs/markdown.mjs"),
  php: () => import("shiki/dist/langs/php.mjs"),
  python: () => import("shiki/dist/langs/python.mjs"),
  ruby: () => import("shiki/dist/langs/ruby.mjs"),
  rust: () => import("shiki/dist/langs/rust.mjs"),
  scss: () => import("shiki/dist/langs/scss.mjs"),
  sql: () => import("shiki/dist/langs/sql.mjs"),
  svelte: () => import("shiki/dist/langs/svelte.mjs"),
  toml: () => import("shiki/dist/langs/toml.mjs"),
  tsx: () => import("shiki/dist/langs/tsx.mjs"),
  typescript: () => import("shiki/dist/langs/typescript.mjs"),
  vue: () => import("shiki/dist/langs/vue.mjs"),
  xml: () => import("shiki/dist/langs/xml.mjs"),
  yaml: () => import("shiki/dist/langs/yaml.mjs"),
  yml: () => import("shiki/dist/langs/yml.mjs"),
  zsh: () => import("shiki/dist/langs/zsh.mjs"),
};

const bundledThemes = {};

const createHighlighter = createBundledHighlighter({
  langs: bundledLanguages,
  themes: bundledThemes,
  engine: () => createJavaScriptRegexEngine(),
});

function createOnigurumaEngine(): never {
  throw new Error(
    'The @pierre/diffs Shiki shim only bundles the "shiki-js" highlighter.',
  );
}

export {
  bundledLanguages,
  bundledThemes,
  codeToHtml,
  createCssVariablesTheme,
  createHighlighter,
  createJavaScriptRegexEngine,
  createOnigurumaEngine,
  getTokenStyleObject,
  normalizeTheme,
  stringifyTokenStyle,
};
