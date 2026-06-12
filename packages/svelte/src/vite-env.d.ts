/// <reference types="vite/client" />

declare module "highlight.js/lib/core" {
  import hljs from "highlight.js";

  export default hljs;
}

declare module "highlight.js/lib/languages/*" {
  const language: (hljs?: any) => any;

  export default language;
}
