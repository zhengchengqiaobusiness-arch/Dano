import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StudioService } from "../../src/studio-service.js";
import { summarizeCatalog } from "../../src/inference/export-scope.js";
import { reviewCatalog } from "../../src/review/catalog-review.js";
import { getByPath, setByPath } from "../../src/utils.js";

const parameters = (properties: Record<string, unknown>, required: string[] = []) => ({
  type: "object",
  properties,
  required,
  additionalProperties: false
}) as any;

export default function businessSkillStudio(pi: ExtensionAPI) {
  const piBaseUrl = process.env.PI_BASE_URL?.trim();
  if (piBaseUrl && process.env.PI_API_KEY) {
    pi.registerProvider("xiaomi-token-plan-cn", {
      baseUrl: piBaseUrl,
      apiKey: "$PI_API_KEY"
    });
  }

  const studio = new StudioService();
  let lastRecordingSessionId: string | undefined;

  const requireConversationSession = (params: { sessionId?: string } = {}) => {
    const sessionId = (typeof params.sessionId === "string" ? params.sessionId.trim() : "") || lastRecordingSessionId;
    if (!sessionId) {
      throw new Error("当前对话还没有录制证据。请先接入业务系统并完成录制；不要使用上一轮会话的录制、链接或分析结果。");
    }
    return sessionId;
  };

  const browserServiceUrl = process.env.BSS_BROWSER_SERVICE_URL?.replace(/\/+$/, "");
  const browserServiceToken = process.env.BSS_BROWSER_SERVICE_TOKEN;

  const browserRequest = async <T>(path: string, body?: unknown): Promise<T> => {
    if (!browserServiceUrl || !browserServiceToken) throw new Error("Embedded browser service is unavailable");
    const response = await fetch(`${browserServiceUrl}/internal/browser${path}`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${browserServiceToken}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body ?? {})
    });
    const payload = await response.json().catch(() => ({})) as any;
    if (!response.ok) throw new Error(payload.error || `Embedded browser request failed: ${response.status}`);
    return payload as T;
  };

  const startBrowser = (url: string, name?: string, expectedOperations: any[] = [], completeFieldCoverage = false, completePageCoverage = false) => browserServiceUrl
    ? browserRequest<any>("/start", { url, name, expectedOperations, completeFieldCoverage, completePageCoverage })
    : studio.startRecording(url, name, expectedOperations, completeFieldCoverage, completePageCoverage);
  const stopBrowser = () => browserServiceUrl
    ? browserRequest<any>("/stop")
    : studio.stopRecording();
  const stopReadiness = () => browserServiceUrl
    ? browserRequest<any>("/stop-readiness")
    : studio.recorder.stopReadiness();
  const controlBrowser = (command: any) => browserServiceUrl
    ? browserRequest<any>("/control", command)
    : studio.recorder.control(command);

  pi.on("session_shutdown", async () => {
    lastRecordingSessionId = undefined;
    if (!browserServiceUrl && studio.recorder.isActive()) await studio.stopRecording().catch(() => {});
  });

  pi.registerTool({
    name: "business_skill_record_start",
    label: "Start business recording",
    description: "Start the embedded Playwright browser and record real UI actions plus XHR/fetch/document requests and responses. When the user names required operations, always pass every one in expectedOperations so review cannot export a partial Skill. When the user requires every field except upload/attachment, pass completeFieldCoverage=true. When the user requires every accessible menu page, pass completePageCoverage=true so discovered but unvisited pages block completion.",
    parameters: parameters({
      url: { type: "string", description: "Real business-system URL" },
      name: { type: "string", description: "Optional recording name" },
      expectedOperations: {
        type: "array",
        items: { type: "string", enum: ["query", "create", "update", "review", "delete", "upload", "download", "action"] },
        description: "Operations explicitly required by the user. Exact mapping: 查询=query, 新增=create, 修改/编辑=update, 审核=review, 删除=delete, 上传=upload, 导出/下载=download. action is only for custom business actions such as 撤回/签章; never map 导出 to action."
      },
      completeFieldCoverage: {
        type: "boolean",
        description: "True only when the user requires every operable field to be filled; upload and attachment controls remain excluded."
      },
      completePageCoverage: {
        type: "boolean",
        description: "True when every accessible menu page must be visited. Snapshot returns grounded navigation coverage and record_stop rejects discovered but unvisited pages."
      }
    }, ["url"]),
    async execute(_toolCallId, params: any) {
      try {
        const session = await startBrowser(params.url, params.name, params.expectedOperations || [], params.completeFieldCoverage === true, params.completePageCoverage === true);
        if (session?.blocked) {
          return {
            content: [{ type: "text", text: session.message || "上次审核未要求补录。禁止对同一业务页重新录制。" }],
            details: session
          };
        }
        if (session?.id) lastRecordingSessionId = session.id;
        return {
          content: [{ type: "text", text: `Recording started: ${session.id}. The live page is visible in the Studio browser panel.` }],
          details: session
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (/禁止|不要重新录制|不要 business_skill_record_start/.test(message)) {
          return {
            content: [{ type: "text", text: message }],
            details: { blocked: true, started: false, message }
          };
        }
        throw error;
      }
    }
  });

  pi.registerTool({
    name: "business_skill_record_stop",
    label: "Stop business recording",
    description: "Stop only after the live recording audit has verified pages, fields, expected operations, business-success responses, and request contracts. If any evidence or contract gap remains, this tool keeps the current browser session open and returns the exact next action.",
    parameters: parameters({}),
    async execute() {
      const readiness = await stopReadiness();
      if (!readiness.ready) {
        return {
          content: [{ type: "text", text: readiness.message }],
          details: { ...readiness, stopped: false }
        };
      }
      const session = await stopBrowser();
      if (session?.id) lastRecordingSessionId = session.id;
      return {
        content: [{ type: "text", text: session.manualStepsFile
          ? `Recording saved: ${session.id}. Manual steps: ${session.manualStepsFile}`
          : `Recording saved: ${session.id}` }],
        details: session
      };
    }
  });

  pi.registerTool({
    name: "business_browser_control",
    label: "Control recording browser",
    description: "Control the active embedded browser with goto/next-page/snapshot/click/fill/select/choose/press/wait/screenshot/exercise-form/submit-form. Every mutating result includes the authoritative recordingAudit, which builds, repairs, validates, and reviews the current request contracts while recording remains open. Follow only recordingAudit.nextAction and continue until recordingAudit.ready=true; do not defer contract review to stop or export. next-page opens the next unvisited URL from the real menu inventory; never guess a route. Snapshot includes navigationCoverage, operationInventory and enabled availableOperations. In complete-field mode exercise-form fills every currently visible eligible field in one pass and is the authoritative whole-form action; direct single-field mutations are rejected until it has run once for that page/form. A detected login page stops before any automatic action. The first or second failure must be repaired automatically from refreshed page evidence; only actual failures consume the budget, and a successful operation clears the consecutive-failure streak. The third consecutive failure pauses with the exact problem location, last error, and manual click instructions.",
    parameters: parameters({
      action: { type: "string", enum: ["goto", "next-page", "snapshot", "click", "fill", "select", "choose", "press", "wait", "screenshot", "exercise-form", "submit-form"] },
      selector: { type: "string" },
      value: {},
      url: { type: "string" },
      key: { type: "string" },
      ms: { type: "number" }
    }, ["action"]),
    async execute(_id, params: any) {
      const result = await controlBrowser(params);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: result
      };
    }
  });

  pi.registerTool({
    name: "business_skill_analyze",
    label: "Analyze recorded capabilities",
    description: "Cluster recorded evidence, then let Pi skills infer-field-contract and judge-primary-capability fill field ownership, rules, and primary/lookup/noise roles. Does not invent endpoints or request keys.",
    parameters: parameters({
      sessionId: { type: "string" },
      noLlm: { type: "boolean" }
    }),
    async execute(_id, params: any) {
      const caps = await studio.analyze(requireConversationSession(params), !params.noLlm);
      const summary = summarizeCatalog(caps);
      const primaryTitles = summary.primary.map(item => item.title).join("、") || "无";
      return {
        content: [{
          type: "text",
          text: `本次会话识别主能力 ${summary.primary.length} 项：${primaryTitles}。字段候选接口 ${summary.lookups.length} 个，后台轮询 ${summary.noise.length} 项不会进入 Skill。主能力只统计本次会话页面的查询/新建/修改/审核/删除，不要把其它页已有能力当成这次的补录对象。用户分页、产品下拉、库存带出不是主能力。该工具仅查看中间结果；正式闭环以录制阶段的 recordingAudit 为准。`
        }],
        details: caps
      };
    }
  });

  pi.registerTool({
    name: "business_skill_infer_fields",
    label: "Infer field contracts with Pi",
    description: "Run the infer-field-contract Pi skill on a recorded session. Binds existing request keys only; does not invent fields. Prefer business_skill_analyze which already includes this step.",
    parameters: parameters({
      sessionId: { type: "string" }
    }),
    async execute(_id, params: any) {
      const caps = await studio.analyze(requireConversationSession(params), true);
      return {
        content: [{ type: "text", text: `已按 infer-field-contract 处理 ${caps.length} 个能力的字段合同。` }],
        details: caps.map(item => ({ id: item.id, role: item.role, fields: item.inputForm.map(field => ({ path: field.path, source: field.source, rule: field.defaultRule })) }))
      };
    }
  });

  pi.registerTool({
    name: "business_skill_judge_primary",
    label: "Judge primary capabilities with Pi",
    description: "Run the judge-primary-capability Pi skill on a recorded session. Marks primary / lookup / noise. Prefer business_skill_analyze which already includes this step.",
    parameters: parameters({
      sessionId: { type: "string" }
    }),
    async execute(_id, params: any) {
      const caps = await studio.analyze(requireConversationSession(params), true);
      const summary = summarizeCatalog(caps);
      return {
        content: [{ type: "text", text: `主能力 ${summary.primary.length}，lookup ${summary.lookups.length}，噪声 ${summary.noise.length}。` }],
        details: caps.map(item => ({ id: item.id, operation: item.operation, role: item.role, title: item.title }))
      };
    }
  });

  pi.registerTool({
    name: "business_skill_validate",
    label: "Validate business capabilities",
    description: "Inspect the review result already produced by the live recordingAudit loop. This is a diagnostic view, not a caller-operated gate and not a repair stage. Recording owns capability build, deterministic contract repair, validation, and review after each action; export only checks consistency and writes the verified package. Never hand generated-file repairs to the user.",
    parameters: parameters({
      sessionId: { type: "string" }
    }),
    async execute(_id, params: any) {
      const { capabilities, review } = await studio.review(requireConversationSession(params));
      const gate = review.next === "re-record"
        ? "自动处理：仅返回页面补齐缺失的主操作或其成功响应，然后继续导出闭环。"
        : review.status === "passed"
          ? "审核通过；完整闭环可继续导出。"
          : "现有证据的自动修复仍未通过；这是平台解析问题，不要求用户补录或修改生成文件。";
      return {
        content: [{
          type: "text",
          text: `${gate}\n${review.summary}`
        }],
        details: {
          review,
          capabilityIds: capabilities.map(item => item.id),
          titles: capabilities.map(item => item.title)
        }
      };
    }
  });

  pi.registerTool({
    name: "business_skill_plan",
    label: "Plan business execution",
    description: "Plan a natural-language business goal using verified atomic capabilities and approved data bindings.",
    parameters: parameters({
      goal: { type: "string" }
    }, ["goal"]),
    async execute(_id, params: any) {
      const result = await studio.plan(params.goal);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: result
      };
    }
  });

  pi.registerTool({
    name: "business_skill_execute",
    label: "Execute verified capability",
    description: "Execute one verified capability immediately. Do not wait for a confirmation dialog.",
    parameters: parameters({
      capabilityId: { type: "string" },
      input: { type: "object", additionalProperties: true }
    }, ["capabilityId", "input"]),
    async execute(_id, params: any, _signal, _onUpdate, ctx) {
      const caps = await studio.capabilities();
      const cap = caps.find(c => c.id === params.capabilityId);
      if (!cap) throw new Error(`Unknown capability: ${params.capabilityId}`);

      const executionInput = structuredClone(params.input || {});
      for (const field of cap.inputForm.filter(item => item.source === "caller" && item.required)) {
        if (getByPath(executionInput, field.path) !== undefined) continue;
        if (field.candidates?.type === "capability") {
          throw new Error(`字段 ${field.label} 需要先通过 ${field.candidates.capabilityId} 获取动态候选，并让用户选择`);
        }
        let value: unknown;
        if (field.candidates?.type === "static") {
          const display = field.candidates.values.map(item => `${item.label}（${String(item.value)}）`);
          const selected = await ctx.ui.select(`请选择${field.label}`, display);
          if (selected === undefined) return { content: [{ type: "text", text: "用户取消了字段选择。" }], details: { cancelled: true } };
          value = field.candidates.values[display.indexOf(selected)]?.value;
        } else {
          value = await ctx.ui.input(`请输入${field.label}`, field.sourceDetail || field.name);
          if (value === undefined) return { content: [{ type: "text", text: "用户取消了字段输入。" }], details: { cancelled: true } };
        }
        setByPath(executionInput, field.path, value);
      }

      const result = await studio.execute(cap.id, executionInput, cap.confirmation.required);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: result
      };
    }
  });

  pi.registerTool({
    name: "business_skill_approve_binding",
    label: "Approve capability binding",
    description: "Record an approved data binding from a recorded query output path into a write capability input path. Both capabilities must exist and the paths must be in the recorded schemas; they do not need to be verified yet.",
    parameters: parameters({
      fromCapabilityId: { type: "string" },
      fromPath: { type: "string" },
      toCapabilityId: { type: "string" },
      toPath: { type: "string" },
      note: { type: "string" }
    }, ["fromCapabilityId", "fromPath", "toCapabilityId", "toPath"]),
    async execute(_id, params: any) {
      const target = await studio.approveBinding(params);
      return { content: [{ type: "text", text: "Binding approved." }], details: target };
    }
  });

  pi.registerTool({
    name: "business_skill_set_dynamic_candidates",
    label: "Set dynamic candidates",
    description: "Configure a verified query capability as the dynamic candidate source for an input field.",
    parameters: parameters({
      targetCapabilityId: { type: "string" },
      inputPath: { type: "string" },
      sourceCapabilityId: { type: "string" },
      valuePath: { type: "string" },
      labelPath: { type: "string" },
      dependsOn: { type: "array", items: { type: "string" } }
    }, ["targetCapabilityId", "inputPath", "sourceCapabilityId", "valuePath", "labelPath"]),
    async execute(_id, params: any) {
      const target = await studio.setDynamicCandidates(params);
      return { content: [{ type: "text", text: "Dynamic candidate source configured." }], details: target };
    }
  });

  pi.registerTool({
    name: "business_skill_export",
    label: "Export business skill",
    description: "Write the Agent Skill package only after the recording-stage audit has already built, repaired, validated, and approved the request contracts. Export performs a final consistency review and never exports a blocked catalog.",
    parameters: parameters({
      name: { type: "string" },
      sessionId: { type: "string" }
    }, ["name"]),
    async execute(_id, params: any) {
      const result = await studio.exportManaged(params.name, true, requireConversationSession(params));
      return {
        content: [{
          type: "text",
          text: `已导出主能力 ${result.primaryCount} 项、字段候选接口 ${result.lookupCount} 个，标识 ${result.name}，版本 v${result.version}。目录 ${result.directory}。本次写入独立目录，不覆盖已有成品。候选接口只给调用方选值，不是独立业务操作。`
        }],
        details: result
      };
    }
  });
}
