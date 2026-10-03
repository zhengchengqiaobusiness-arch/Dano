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

export function guideBody(name) {
  return SHORT[name] || "";
}
