import { mount } from "svelte";
import QuestionToolCard from "../components/QuestionToolCard.svelte";
import { ACCENT_COLOR_PRESETS, resolveAppThemeVars } from "../themes";
import { PI_BASE46_LIGHT_THEME } from "../themes/light";
import type { ToolContentBlock } from "../utils/transcript";

const preset = new URLSearchParams(window.location.search).get("accent");
const accent = ACCENT_COLOR_PRESETS[
  preset && preset in ACCENT_COLOR_PRESETS
    ? preset as keyof typeof ACCENT_COLOR_PRESETS
    : "default"
];
for (const [name, value] of Object.entries(
  resolveAppThemeVars(PI_BASE46_LIGHT_THEME, accent),
)) {
  document.documentElement.style.setProperty(name, value);
}

document.body.style.margin = "0";
document.body.style.padding = "24px";
document.body.style.background = "var(--bg)";
document.body.style.color = "var(--text)";
document.body.style.fontFamily = "system-ui, sans-serif";

const groupedForm: ToolContentBlock = {
  kind: "tool",
  toolName: "ask_user_question",
  toolCallId: "question-browser-form",
  toolArgs: {},
  argumentsText: "",
  toolStatus: "pending",
  questionRequest: {
    batch: true,
    title: "浏览器渲染测试表单",
    questions: [
      {
        id: "reason",
        kind: "text",
        question: "申请原因？",
        fieldAssist: false,
      },
      {
        id: "approver",
        kind: "multiple",
        question: "请选择审批人",
        options: [{ id: "zhang-san", label: "张三" }],
      },
      {
        id: "date",
        kind: "date",
        question: "日期",
        dateFormat: "yyyy-MM-dd",
      },
    ],
  },
};

const confirmation: ToolContentBlock = {
  kind: "tool",
  toolName: "ask_user_question",
  toolCallId: "question-browser-confirmation",
  toolArgs: {},
  argumentsText: "",
  toolStatus: "pending",
  questionRequest: {
    batch: false,
    id: "confirmation",
    kind: "confirm",
    title: "确认 2 份表单",
    confirmationOfToolCallId: "form-a",
    questions: [
      { id: "reason", kind: "text", question: "原因？", fieldAssist: false },
    ],
    answer: { reason: "家庭事务" },
    forms: [
      {
        formId: "form-a",
        title: "请假申请",
        questions: [
          { id: "reason", kind: "text", question: "原因？", fieldAssist: false },
          { id: "date", kind: "date", question: "日期", dateFormat: "yyyy-MM-dd" },
        ],
        answer: { reason: "家庭事务", date: "2026-07-22" },
      },
      {
        formId: "form-b",
        title: "出差申请",
        questions: [
          { id: "destination", kind: "text", question: "目的地？", fieldAssist: false },
          { id: "duration", kind: "text", question: "天数？", fieldAssist: false },
        ],
        answer: { destination: "上海", duration: "2" },
      },
    ],
  },
  formInteraction: {
    interactionId: "question-browser-confirmation",
    state: "awaiting_confirmation",
    revision: 1,
    allowedActions: ["cancel", "return_modify", "confirm"],
    forms: [],
  },
};

const respond = async () => ({ success: true } as never);
const app = document.getElementById("app")!;
for (const block of [groupedForm, confirmation]) {
  const target = document.createElement("section");
  app.append(target);
  mount(QuestionToolCard, {
    target,
    props: {
      block,
      active: true,
      onPresent: respond,
      onRespond: respond,
      onRevise: respond,
      onSubmitRevision: respond,
    },
  });
}
