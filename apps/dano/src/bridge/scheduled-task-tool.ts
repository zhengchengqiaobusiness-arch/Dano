import { defineTool } from "@earendil-works/pi-coding-agent";
import * as fs from "node:fs";
import { Type } from "typebox";
import type { CredentialBroker } from "./credential-broker.js";
import {
  ScheduledTaskHttpError,
  type ScheduledTaskScheduler,
} from "./scheduled-task.js";
import type { UserContext } from "./user-context.js";

export function createScheduleTaskTool(options: {
  scheduler: ScheduledTaskScheduler;
  userContext: UserContext;
  credentialBroker?: CredentialBroker;
  credentialBrokerScope?: string;
}) {
  return defineTool({
    name: "schedule_task",
    label: "Schedule Task",
    description:
      "Create a scheduled task bound to the current conversation. The scheduler later prompts this session with the frozen action.",
    promptSnippet:
      "Use schedule_task when the user asks to do something at a future time in this conversation",
    promptGuidelines: [
      "当用户约定定时做事时调用 schedule_task。",
      "做什么和何时都明确时立刻调用，不要确认，不要问去向，绑定当前会话。",
      "userInput 是冻结后要执行的动作，不是排程原话。",
      "nextRunAt 由你算成下一次运行的 ISO 时间（时区 Asia/Shanghai）。例如每天 8 点且现在已过 8 点，则填明天上海 8 点对应的 ISO。",
      "重复间隔没有任何产品限制：每 10 分钟、每 30 秒、每小时、每天都可以。不要说不支持某间隔，也不要为此改成每小时/每天或追问用户。",
      "重复时填 intervalMinutes、intervalSeconds、intervalHours 或 intervalMs。每 10 分钟：intervalMinutes: 10。只做一次不要填间隔，或 repeat: \"none\"。hourly/daily/weekly 仍可作为别名。",
      "时间含糊时用 ask_user_question 只问缺口。何时已明确到具体间隔时不要再问。",
      "用户要求现在立刻做时不要调用本工具。",
    ],
    parameters: Type.Object({
      taskName: Type.String({ description: "Short name for the scheduled task." }),
      userInput: Type.String({
        description: "Frozen action to prompt into the conversation when due.",
      }),
      nextRunAt: Type.String({
        description: "Next run time as an ISO 8601 timestamp.",
      }),
      repeat: Type.Optional(
        Type.Union([
          Type.Literal("none"),
          Type.Literal("hourly"),
          Type.Literal("daily"),
          Type.Literal("weekly"),
        ]),
      ),
      intervalMs: Type.Optional(
        Type.Number({ description: "Repeat every this many milliseconds." }),
      ),
      intervalSeconds: Type.Optional(
        Type.Number({ description: "Repeat every this many seconds." }),
      ),
      intervalMinutes: Type.Optional(
        Type.Number({ description: "Repeat every this many minutes." }),
      ),
      intervalHours: Type.Optional(
        Type.Number({ description: "Repeat every this many hours." }),
      ),
    }),
    executionMode: "sequential",
    async execute(_toolCallId, params, _signal, _onUpdate, context) {
      const sessionFile = context.sessionManager.getSessionFile();
      const sessionId = context.sessionManager.getSessionId();
      const cwd = context.sessionManager.getCwd();
      if (!sessionFile || !fs.existsSync(sessionFile)) {
        return {
          content: [
            { type: "text" as const, text: "Current session file was not found" },
          ],
          details: { error: "SESSION_FILE_NOT_FOUND" },
          isError: true,
        };
      }
      try {
        const loginSessionId =
          options.credentialBroker && options.credentialBrokerScope && sessionId
            ? options.credentialBroker.currentLoginSessionId(
                options.credentialBrokerScope,
                sessionId,
              )
            : undefined;
        const task = await options.scheduler.create(
          {
            taskName: params.taskName,
            userInput: params.userInput,
            nextRunAt: params.nextRunAt,
            repeat: params.repeat,
            intervalMs: params.intervalMs,
            intervalSeconds: params.intervalSeconds,
            intervalMinutes: params.intervalMinutes,
            intervalHours: params.intervalHours,
            destination: "conversation",
            sessionPath: sessionFile,
            conversationId: sessionId,
            workspacePath: cwd || undefined,
          },
          {
            userContext: options.userContext,
            loginSessionId,
            defaultWorkspacePath: cwd || undefined,
          },
        );
        return {
          content: [{ type: "text" as const, text: JSON.stringify(task) }],
          details: task,
        };
      } catch (error) {
        const message =
          error instanceof ScheduledTaskHttpError || error instanceof Error
            ? error.message
            : String(error);
        return {
          content: [{ type: "text" as const, text: message }],
          details: { error: message },
          isError: true,
        };
      }
    },
  });
}
