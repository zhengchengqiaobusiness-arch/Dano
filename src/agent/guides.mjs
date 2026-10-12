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
  "skill-generator-workflow.md": "成品只有 SKILL.md、scripts/client.py、references/api.md。命令行必须能被调用方直接跑。python3；本机没有则运行时改用 python。每个 add_parser 必须 set_defaults(func=)。写入子命令必须有 --confirm。基地址只读 DANO_BASE_URL 或 config/runtime.json，不要写死源站。source 只许 caller、current_user、now、previous_response、other_api、constant、unknown。overlay_editable 上的控件是调用方填或选；overlay_editable 没有对应控件的 listed 键才是系统自动。活列表 options 留空，调用时先跑读命令再填；封闭枚举才把 id/label 写进 options。不要把命令行写成 option。inputType 只许 text、textarea、date、select、radio、treeSelect、checkbox。页面日期用 date 和 dateFormat。页面填过不等于调用方必填。手册参数是否必填必须和 argparse 的 required/default 一致。listed 某键这次 empty/[] 只说明这次传了空，不要冻成空数组。读命令不要写死本场 listed 里的业务 id。SKILL.md 里出现的每一条读命令（含表格单元格）都要跑。身份键从本场身份接口现取，不要问人。同一张 overlay 一张 title+questions[]，不同 overlay 可各一张，调用方每轮只用一张。调用方提交后再 confirm+formIds，不要 dataSource。嵌套数组按列写成可重复命令行参数，问句用 textarea 写 JSON 数组。目标只写时不要发明 GET。",
  "skill-generator-auth-and-token.md": "业务请求把 config/auth.local.json 的 headers 整袋带上。有任意鉴权头就发，不要只认某一个键名，也不要因为缺少 credential 对象而退出。只有 credential.url 和 method 时才重放；listed 里的刷新请求不是必须重放。重放带 credential.headers，没有则带 headers 里除 Authorization 外的头。访问凭证写入 Authorization。脚本对单次请求失败或 401 不要 sleep 循环重试。读命令 401 仍写完三文件并 verify_skill，不要 assist、不要把凭证写进对话。DANO_AUTH_HEADERS 是 JSON 对象，json.loads 后只覆盖同名头，不要按逗号拆，不要用环境变量整袋替换本地头。不要把 token 写进三文件。不要把响应里的文件 URL 写成接口 path。",
};

export function guideBody(name) {
  return SHORT[name] || "";
}
