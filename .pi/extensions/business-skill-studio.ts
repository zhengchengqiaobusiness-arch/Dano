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

  const startBrowser = (url: string, name?: string, expectedOperations: any[] = [], completeFieldCoverage = false) => browserServiceUrl
    ? browserRequest<any>("/start", { url, name, expectedOperations, completeFieldCoverage })
    : studio.startRecording(url, name, expectedOperations, completeFieldCoverage);
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
    if (!browserServiceUrl && studio.recorder.isActive()) await studio.stopRecording().catch(() => {});
  });

  pi.registerTool({
    name: "business_skill_record_start",
    label: "Start business recording",
    description: "Start the embedded Playwright browser and record real UI actions plus XHR/fetch/document requests and responses. When the user names required operations, always pass every one in expectedOperations so review cannot export a partial Skill. When the user requires every field except upload/attachment, pass completeFieldCoverage=true so review also rejects blank visible fields and unexercised business detail rows.",
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
      }
    }, ["url"]),
    async execute(_toolCallId, params: any) {
      const session = await startBrowser(params.url, params.name, params.expectedOperations || [], params.completeFieldCoverage === true);
      if (session?.id) lastRecordingSessionId = session.id;
      return {
        content: [{ type: "text", text: `Recording started: ${session.id}. The live page is visible in the Studio browser panel.` }],
        details: session
      };
    }
  });

  pi.registerTool({
    name: "business_skill_record_stop",
    label: "Stop business recording",
    description: "Stop only after every expected operation has a real business-success response. If any required operation is missing or only returned a business error inside HTTP 200, this tool keeps the current browser session open and returns the exact unfinished operations so automation can continue instead of exporting a partial Skill.",
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
    description: "Control the active embedded browser with goto/snapshot/click/fill/select/choose/press/wait/screenshot/exercise-form/submit-form. A detected login page stops before any automatic action with loginRequired and waits for the person to complete login through the Studio manual-takeover card. Snapshot includes operationInventory and enabled availableOperations across the page and visible iframes, so complete every requested page operation rather than only opening its route. Clicked operations and correlated HTTP requests remain recorded evidence; only expectedOperations supplied at recording start define the completion contract. The first exercise-form call is authoritative and fills every currently visible eligible field in one pass; later calls may repair only returned todoFields, fields newly revealed by that pass, format constraints, or a returned failure. Use choose for dropdowns and tree/cascader nodes. Only actual failures consume the per-page/form repair budget; a successful automatic operation clears the consecutive-failure streak. The first or second failure must be repaired from refreshed page evidence, not treated as a stop. On the third consecutive failed automatic operation this same tool call pauses for the Studio manual-takeover card; wait until it returns resumedAfterManualTakeover and continue from its snapshot. A snapshot never requests takeover by itself. Never blindly replay an unchanged failed selector or record_stop+analyze a planned write with no successful response. Required number 0 is empty.",
    parameters: parameters({
      action: { type: "string", enum: ["goto", "snapshot", "click", "fill", "select", "choose", "press", "wait", "screenshot", "exercise-form", "submit-form"] },
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
    description: "Infer atomic business capabilities from recorded evidence. OpenAI refinement is used only when configured.",
    parameters: parameters({
      sessionId: { type: "string" },
      noLlm: { type: "boolean" }
    }),
    async execute(_id, params: any) {
      const caps = await studio.analyze(params.sessionId || lastRecordingSessionId, !params.noLlm);
      const summary = summarizeCatalog(caps);
      const primaryTitles = summary.primary.map(item => item.title).join("、") || "无";
      return {
        content: [{
          type: "text",
          text: `本次会话识别主能力 ${summary.primary.length} 项：${primaryTitles}。字段候选接口 ${summary.lookups.length} 个，后台轮询 ${summary.noise.length} 项不会进入 Skill。主能力只统计本次会话页面的查询/新建/修改/审核/删除，不要把其它页已有能力当成这次的补录对象。用户分页、产品下拉、库存带出不是主能力。分析后必须验证，验证未通过不能导出。`
        }],
        details: caps
      };
    }
  });

  pi.registerTool({
    name: "business_skill_validate",
    label: "Validate business capabilities",
    description: "Review inferred capabilities against evidence. Returns 审核通过 or 审核未通过. Export is allowed only when the review passes.",
    parameters: parameters({
      sessionId: { type: "string" }
    }),
    async execute(_id, params: any) {
      const { capabilities, review } = await studio.review(params.sessionId || lastRecordingSessionId);
      return {
        content: [{
          type: "text",
          text: review.summary
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
    description: "Export verified capabilities as a self-contained Agent Skill package with manual, contracts, routing, forms, candidates, and executable scripts.",
    parameters: parameters({
      name: { type: "string" },
      sessionId: { type: "string" }
    }, ["name"]),
    async execute(_id, params: any) {
      const result = await studio.exportManaged(params.name, true, params.sessionId || lastRecordingSessionId);
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
