# Pi Business Skill Studio

从真实业务操作中生产、验证、组合和管理 Agent Skill 的本地平台。

它不是普通 RPA、接口文档生成器或业务说明书系统。核心链路是：

> 真实页面操作与请求证据 → 统一导出闭环（分析 → 审核 → 自动修复 → 复审）→ 已确认组合路线 → 自包含 Python Skill

## 直接启动

双击项目根目录的 `start.bat`。

这个文件只负责启动项目并用系统浏览器打开前端，不安装依赖、不下载浏览器、不进入 Pi 命令行。启动前会清掉占用同一端口的残留进程；关闭这个 CMD 窗口会立刻结束服务和相关进程，不留后台。启动失败时会保留错误窗口。项目自动读取根目录已有的 `.env`，支持 OpenAI 格式的兼容服务，不要求官方地址：

```dotenv
PI_BASE_URL=https://your-compatible-service.example/v1
PI_API_KEY=your-key
PI_MODEL=your-model
```

前端分为四个工作区：

- **录制工作台**：可明确切换“手动录制”和“Pi 自动点击”；两种方式都操作项目内置的同一个 Playwright 浏览器，同时保存页面与网络证据。
- **能力目录**：区分查询、新建、修改、审核、删除等原子能力；人工修改业务名称和描述；检查字段类型、来源、必填性和处理方；查看审核诊断。
- **Skill 目录**：导出、重新导出、冻结、可恢复删除和自然语言调用 Python Skill。每次导出使用唯一标识和独立目录，不覆盖上一份。
- **运行日志**：显示与 CMD 同源的启动、Pi、工具和内置浏览器日志；密钥、令牌和密码配置不会进入日志。

Pi 的页面读取、点击、填写、选择和截图全部作用于前端显示的同一个 Playwright 会话，不控制本机已经打开的 Chrome 或 Edge。

## 能力与字段规则

每个能力必须来自真实请求和返回证据。字段至少包含：

- 合同路径与实际字段名；
- 业务名称；
- 字符串、整数、数字、布尔、数组、对象等类型；
- 调用方、固定规则、会话、运行时生成、计算、上游绑定或系统处理等来源；
- 必填性及其证据依据；
- 由调用方提供还是由系统自动处理；
- 静态或动态候选规则。

真实页面中观察到的输入才会自动标为“调用方提供”。请求中存在、但没有用户输入证据的字段默认按“系统处理”管理；系统必填字段没有明确处理规则或已确认绑定时，验证不会放行。

## 审核、自动修复与组合安全

产出 Skill 只需要调用统一导出入口。它接收原始录制，依次完成确定性分析、审核、证据内自动修复、复审和导出；`analyze`、`validate` 仅用于查看中间诊断，不要求调用方手工串联。审核发现字段归属、枚举、候选、完成条件或请求合同问题时，平台使用已有证据修复；只有真实主操作、成功响应或全字段证据缺失时，才自动回到页面补齐该缺口。

登录、连续三次页面操作失败、证据无法唯一决定以及写操作/组合确认仍保留人工边界。平台不会把生成文件交给调用方修改，也不会为了通过审核而编造接口、字段、枚举或证据。

只有 `validation.status = "verified"` 且使用当前验证规则的能力可以导出。

验证至少检查：

- 真实网络证据和成功返回；
- 请求方法一致；
- 完成断言有成功证据支持；
- 原子能力类型明确；
- 写操作有真实页面关联；
- 字段元数据、来源和处理方一致；
- 调用方字段有页面输入证据；
- 系统必填字段有可执行处理规则；
- 绑定引用真实能力和字段，并保留确认记录。

自动组合只允许使用 `approved: true` 的绑定。没有绑定、目标不唯一、上游结果为空或存在多个候选时必须停下来询问。新建、修改、审核、删除在每次执行前必须单独确认；结果不明确的写操作不得自动重试。

## 导出的 Python Skill

Linux 环境默认导出到 `/opt/dano/runtime-data/.agents/skills/<skill-name>/`；其它环境默认导出到项目内 `dist/skills/<skill-name>/`。CLI 显式传入 `--out` 时仍使用指定目录：

```text
<skill-name>/
├── SKILL.md
├── references/
│   ├── CONTRACT.json
│   ├── CAPABILITIES.md
│   ├── INPUT_FORMS.md
│   ├── OPTIONS.md
│   ├── PLAYBOOK.md
│   └── routes/
└── scripts/
    ├── execute.py
    ├── candidates.py
    └── format_list.py
```

`SKILL.md` 是路由手册：何时用、何时不用、能力怎么组合，以及规划结束后的可执行约定——何时走哪条原子操作、何时可以按 `approved: true` 绑定串联、何时必须停下来问人。它不是业务说明书，也不写生成过程或代码结构。

`references/CONTRACT.json` 是能力、字段、绑定、路线和完成条件的唯一机器事实来源。能力索引、当前表单、候选项、规划例子与组合路线按需加载。产品、供应商、账户、人员等目录接口只作为字段候选，不作为独立业务能力。

导出的 Skill 不包含生成器实现过程、项目代码结构、录制证据清单、凭据、Cookie 或 secret-bearing headers。录制器从已登录业务请求中提取认证头，脱离证据流保存；导出时在 Skill 目录外生成同名运行时凭据文件。Linux 默认位置为 `/opt/dano/runtime-data/.agents/credentials/<skill-name>.json`，文件权限为 `0600`。导出的 Python 执行器会自动读取，也可通过 `SKILL_AUTH_HEADERS` 或 `SKILL_AUTH_FILE` 显式覆盖。

## 修改 Skill 还是修改平台

- 业务名称、业务描述、触发措辞和结果展示方式变化：在能力目录修改，重新验证并导出 Skill。
- 页面结构、接口、字段、候选来源、认证、运行时生成规则或安全门禁变化：重新录制真实证据；需要新解析能力时修改平台代码。不能只改 Skill 文本掩盖真实系统变化。

## 开发验证

首次开发安装需要 Node.js 22+、Python 3：

```bash
npm install
npm run browser:install
```

日常质量检查：

```bash
npm run typecheck
npm test
```

测试覆盖字段来源区分、系统必填字段门禁、已确认绑定顺序、渐进披露结构、Python 脚本语法和 Skill 包校验。

## 设计参考

- [Alibaba Cloud AIOps Skills](https://github.com/aliyun/alibabacloud-aiops-skills/tree/master/skills)：入口手册、引用资料和确定性脚本的目录分层，以及明确的任务路由与失败处理。
- [Writing Great Skills](https://www.skills.sh/mattpocock/skills/writing-great-skills)：触发描述、单一事实来源、可检查完成条件和渐进披露。
- [程序员 Carl：Skill 不是更长的 Prompt](https://mp.weixin.qq.com/s/upEf0dCi3qwvpwLkRIwyWA)：Skill、Tool、MCP、Memory 和 Harness 的职责边界，以及安全门禁应由运行时强制执行。
