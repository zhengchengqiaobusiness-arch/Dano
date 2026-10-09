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
  "skill-generator-workflow.md": "成品只有 SKILL.md、scripts/client.py、references/api.md。命令行必须能被调用方直接跑。字段来源只能是调用方填写、listed 已录请求再读、上一步响应、系统计算、固定协议常量、空、运行时配置。页面填过不等于调用方必填。手册参数是否必填必须和 argparse 的 required/default 一致。listed 某键这次 empty/[] 只说明这次传了空，同场另有选项请求则再读后让调用方选，不要冻成空数组。基地址用运行时配置或 DANO_BASE_URL。SKILL.md 里出现的每一条读命令（含表格单元格）都要跑。问句不要另造一张更短的表。嵌套数组保持为行。目标只写时不要发明 GET。",
  "skill-generator-auth-and-token.md": "业务请求用 config/auth.local.json 的 headers。不要因为缺少 credential 对象而退出。只有 credential.url 和 method 时才重放。重放带 credential.headers，没有则带 headers 里除 Authorization 外的头。访问凭证写入 Authorization。失败或 401 停止。不要 assist、不要把凭证写进对话。DANO_AUTH_HEADERS 只覆盖同名头。不要把 token 写进三文件。",
  "skill-generator-live-options.md": "选项从 listed 里已录的请求实时再读，不要把录制当时看到的几条冻成默认。listed 某键这次是空数组，只说明这次传了空，不是该键永远空；同场另有选项请求就再读后让调用方选。失败或空列表就停问调用方，不要默默取第一条。",
  "skill-generator-ask-user-question-guide.md": "问句覆盖 overlay 里调用方填的控件，以及 adopted 请求里来源为调用方的键。overlay required 仍空且来源是调用方的要问。只读、系统计算、打开弹层已带上、listed 已能确定的不要问。嵌套数组保持为行，不要压成一个字符串。需要选项时命令先打 listed 里的选项请求再让调用方选。不要把录制示例写成默认答案。手册必填必须和 argparse 一致。",
};

export function guideBody(name) {
  return SHORT[name] || "";
}
