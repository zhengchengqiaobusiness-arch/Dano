export const GUIDE_NAMES = [
  "skill-generator-auth-and-token.md",
  "skill-generator-live-options.md",
  "skill-generator-ask-user-question-guide.md",
  "writing-for-agents.md",
  "writing-for-agents-mechanics.md",
];

const SHORT = {
  "writing-for-agents.md": "SKILL.md 开头写 name 和 description。description 写明调用方在什么意图下启用，以及这份 Skill 能做的事。",
  "writing-for-agents-mechanics.md": "name 用小写短横线。SKILL.md 不写 disable-model-invocation，调用方靠 description 启用。",
};

const CREDENTIAL_FILE = `程序还会写入 credential，只有 method 和 url。证据里的请求标了 issues_credential 时，在可执行代码里这样重放，不要只写在注释里，也不要另拼 path：

credential = auth["credential"]
Request(credential["url"], method=credential["method"])

查询串里的凭证留在 url 里。响应带回新的刷新凭证时，只替换这段 url 里原来的查询值，再写回原文件。`;

export function guideBody(name) {
  return SHORT[name] || "";
}

export function guideText(name, fileText) {
  const base = guideBody(name) || String(fileText || "");
  if (name !== "skill-generator-auth-and-token.md") return base;
  return `${base}\n\n${CREDENTIAL_FILE}`;
}
