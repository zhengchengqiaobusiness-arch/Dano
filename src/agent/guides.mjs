export const GUIDE_NAMES = [
  "skill-generator-auth-and-token.md",
  "skill-generator-live-options.md",
  "skill-generator-ask-user-question-guide.md",
  "skill-generator-workflow.md",
  "writing-for-agents.md",
  "writing-for-agents-mechanics.md",
];

const SHORT = {
  "writing-for-agents.md": "SKILL.md 必须以 --- 包住 name 和 description，再写正文。description 写明调用方在什么意图下启用，以及这份 Skill 能做的事。不要写成页面点击步骤。",
  "writing-for-agents-mechanics.md": "name 用小写短横线。第一行是 ---。SKILL.md 不写 disable-model-invocation，调用方靠 description 启用。",
  "skill-generator-workflow.md": "成品只有 SKILL.md、scripts/client.py、references/api.md。命令行必须能被调用方直接跑。字段来源只能是调用方填写、listed 已录请求再读、上一步响应、系统计算、固定协议常量、空、运行时配置。页面填过不等于调用方必填。目标只写时不要发明 GET。",
  "skill-generator-auth-and-token.md": "issues_credential 的请求按 config/auth.local.json 的 credential 在业务请求前重放。重放带上 credential.headers，没有则带 headers 里除 Authorization 外的头，不要另猜头的值。访问凭证写入 Authorization。失败或 401 停止。DANO_AUTH_HEADERS 只覆盖同名头。不要把 token 写进三文件。",
  "skill-generator-live-options.md": "选项从 listed 里已录的请求实时再读，不要把录制当时看到的几条冻成默认。失败或空列表就停问调用方，不要默默取第一条。",
  "skill-generator-ask-user-question-guide.md": "调用方填写的字段写在 SKILL.md 参数和 client.py 命令行。需要选项时命令先打 listed 里的选项请求再让调用方选。答案已能从已录请求确定的不要再问。",
};

export function guideBody(name) {
  return SHORT[name] || "";
}
