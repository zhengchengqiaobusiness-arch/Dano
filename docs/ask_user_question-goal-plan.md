Goal

在 pi-web 中实现一个真正的 AI Tool：ask_user_question。

该 Tool 允许 Agent 在执行过程中主动向用户提出结构化问题，并在聊天流（Chat Stream）中渲染交互组件，由用户直接在聊天消息内完成选择或输入。

用户回答后，结果必须作为 Tool Result 返回给 Agent，Agent 基于回答继续执行，而不是中断当前任务流程。

目标体验参考：

* OpenCode question tool
* ChatGPT Tool Interactive Card
* Claude Code 中需要用户确认的交互流程

不参考：

* Modal Dialog
* Browser Alert
* Extension Popup
* TUI Terminal UI

⸻

Background

当前 pi-web 已经具备：

* Agent Loop
* Tool Calling
* Chat Message Rendering
* Extension Bridge
* Extension Dialog

但 Extension Dialog 的定位是：

Extension
  -> uiContext.*
  -> Dialog
  -> Extension

属于插件能力。

本次实现的目标是：

LLM
  -> ask_user_question Tool
  -> Chat Interactive Component
  -> User Answer
  -> Tool Result
  -> LLM Continue

属于 Agent Tool 能力。

两者不是同一个系统。

不要把 ask_user_question 实现成 uiContext 的包装器。

⸻

Reference Projects

OpenCode

Repository:

https://github.com/anomalyco/opencode

重点参考：

packages/core/src/tool/question.ts

参考内容：

* Tool Schema
* Question 生命周期
* Tool Result 返回方式
* Agent 如何等待用户回答

重点学习交互模型，不要求复制实现。

⸻

Pi Community Package

Package:

https://pi.dev/packages/@juicesharp/rpiv-ask-user-question

重点参考：

* Tool Schema
* Question 数据结构
* Multiple Choice 设计
* Structured Result 设计

不要参考：

* TUI Renderer
* Terminal Layout
* Keyboard Navigation

⸻

Architecture Requirements

Tool First

ask_user_question 必须是独立 Tool。

必须拥有：

* Tool Definition
* Tool Schema
* Tool Executor
* Tool Result Schema
* Tests

禁止实现为：

uiContext.ask()

或者：

ExtensionDialog.ask()

⸻

Chat Native UI

所有用户交互必须渲染在聊天流中。

正确：

Assistant Message
 └─ Question Tool Card
      ├─ Question
      ├─ Options
      ├─ Input
      └─ Submit

错误：

Modal Dialog
Popup
Overlay
Extension Dialog

⸻

Tool Lifecycle

Step 1

Agent 调用：

ask_user_question(...)

Step 2

系统产生一个 Pending Tool Call

Step 3

聊天流渲染：

Question Tool Card

Step 4

用户回答

Step 5

Tool Call Resolve

Step 6

Tool Result 返回 Agent

Step 7

Agent 继续执行

⸻

MVP Scope

第一阶段仅支持：

Single Choice

{
  question: string,
  options: string[]
}

Text Input

{
  question: string
}

Cancel

用户可以取消。

⸻

Tool Schema

建议：

type AskUserQuestionInput = {
  header?: string
  questions: Array<{
    question: string
    options?: Array<{
      label: string
      description?: string
    }>
    multiSelect?: boolean
  }>
}

MVP 只要求支持：

questions.length === 1
multiSelect === false

未来扩展：

* Multi Select
* Preview
* Other Option
* Rich Content

但本次不要实现。

⸻

Tool Result

成功：

{
  answers: [
    {
      question: string
      answer: string
    }
  ]
  cancelled: false
}

取消：

{
  answers: []
  cancelled: true
}

必须返回结构化结果。

禁止只返回字符串。

⸻

UI Requirements

新增：

QuestionToolCard

组件。

状态：

Pending

显示：

* Question
* Options/Input
* Submit
* Cancel

Submitted

显示：

✓ Answer Submitted

并展示用户答案。

控件禁用。

Cancelled

显示：

Question Cancelled

控件禁用。

⸻

Data Flow

LLM
 ↓
ask_user_question
 ↓
Pending Tool Call
 ↓
QuestionToolCard
 ↓
User Answer
 ↓
Tool Result
 ↓
Agent Loop Resume

禁止：

LLM
 ↓
Tool
 ↓
Dialog
 ↓
User
 ↓
Normal Chat Message

回答必须走 Tool Result 通道。

⸻

Done When

满足以下全部条件：

Tool

* ask_user_question 出现在 Tool Registry 中
* 模型可以主动调用

UI

* Question 在聊天流中显示
* 不使用 Dialog
* 不使用 Modal

Execution

* 用户回答后 Tool Resolve
* Agent 自动继续执行
* 不需要重新发送 Prompt

Cancellation

* 用户可以取消
* Tool 返回 cancelled=true
* Agent 不崩溃

Compatibility

以下能力不受影响：

* read
* write
* edit
* bash

Quality

* TypeScript 类型检查通过
* Lint 通过
* Build 通过

⸻

Validation

Case 1

Prompt:

Ask me which implementation style I prefer before making changes.

期望：

* Agent 调用 ask_user_question
* Chat 中出现选项
* 用户选择
* Agent 继续执行

⸻

Case 2

Prompt:

Ask me for the component name before creating files.

期望：

* Chat 中出现输入框
* 用户输入
* Agent 使用输入结果继续执行

⸻

Case 3

Prompt:

Ask me for confirmation before deleting files.

期望：

* 用户取消
* Tool 返回 cancelled=true
* Agent 安全退出

⸻

Constraints

* 不要使用 Extension Dialog
* 不要使用 Modal
* 不要使用 Browser Popup
* 不要实现成 uiContext wrapper
* 不要依赖 TUI 组件
* 不要修改现有 Tool 行为
* 不要引入大型第三方依赖
* 优先保持现有架构风格

⸻

Output

最后输出：

* 改了哪些文件
* 为什么这样改
* Tool 调用链路
* Chat UI 渲染链路
* Tool Result 回传链路
* 验证结果
* 剩余风险
* 后续可扩展方向