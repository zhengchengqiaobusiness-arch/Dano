import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StudioService } from "../../src/studio-service.js";
import { isNoiseCapability, isPrimaryCapability, summarizeCatalog } from "../../src/inference/export-scope.js";
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

  const startBrowser = (url: string, name?: string) => browserServiceUrl
    ? browserRequest<any>("/start", { url, name })
    : studio.startRecording(url, name);
  const stopBrowser = () => browserServiceUrl
    ? browserRequest<any>("/stop")
    : studio.stopRecording();
  const controlBrowser = (command: any) => browserServiceUrl
    ? browserRequest<any>("/control", command)
    : studio.recorder.control(command);

  pi.on("session_shutdown", async () => {
    if (!browserServiceUrl && studio.recorder.isActive()) await studio.stopRecording().catch(() => {});
  });

  pi.registerTool({
    name: "business_skill_record_start",
    label: "Start business recording",
    description: "Start the embedded Playwright browser and record real UI actions plus XHR/fetch/document requests and responses.",
    parameters: parameters({
      url: { type: "string", description: "Real business-system URL" },
      name: { type: "string", description: "Optional recording name" }
    }, ["url"]),
    async execute(_toolCallId, params: any) {
      const session = await startBrowser(params.url, params.name);
      return {
        content: [{ type: "text", text: `Recording started: ${session.id}. The live page is visible in the Studio browser panel.` }],
        details: session
      };
    }
  });

  pi.registerTool({
    name: "business_skill_record_stop",
    label: "Stop business recording",
    description: "Stop the active browser recording and persist its evidence.",
    parameters: parameters({}),
    async execute() {
      const session = await stopBrowser();
      return {
        content: [{ type: "text", text: `Recording saved: ${session.id}` }],
        details: session
      };
    }
  });

  pi.registerTool({
    name: "business_browser_control",
    label: "Control recording browser",
    description: "Control the active embedded browser with goto/snapshot/click/fill/select/choose/press/wait/screenshot/exercise-form. Use choose for dropdowns. When the user requires every field filled, call exercise-form or finish snapshot.todoFields before submit.",
    parameters: parameters({
      action: { type: "string", enum: ["goto", "snapshot", "click", "fill", "select", "choose", "press", "wait", "screenshot", "exercise-form"] },
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
      const caps = await studio.analyze(params.sessionId, !params.noLlm);
      const summary = summarizeCatalog(caps);
      const primaryTitles = summary.primary.map(item => item.title).join("、") || "无";
      return {
        content: [{
          type: "text",
          text: `本次录制主能力 ${summary.primary.length} 项：${primaryTitles}。字段候选接口 ${summary.lookups.length} 个，后台轮询 ${summary.noise.length} 项不会进入 Skill。`
        }],
        details: caps
      };
    }
  });

  pi.registerTool({
    name: "business_skill_validate",
    label: "Validate business capabilities",
    description: "Apply the evidence gate. Only capabilities with real successful evidence and required write/UI correlation become verified.",
    parameters: parameters({}),
    async execute() {
      const caps = await studio.validate();
      const verified = caps.filter(item => item.validation.status === "verified");
      const primary = verified.filter(isPrimaryCapability);
      const noise = caps.filter(isNoiseCapability).length;
      return {
        content: [{
          type: "text",
          text: `验证完成：主能力 ${primary.filter(item => item.validation.status === "verified").length} 项已通过（${primary.map(item => item.title).join("、") || "无"}）。后台轮询 ${noise} 项不会导出。`
        }],
        details: caps
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
    description: "Record an approved data binding from one verified capability output path into another verified capability input path.",
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
    parameters: parameters({ name: { type: "string" } }, ["name"]),
    async execute(_id, params: any) {
      const result = await studio.exportManaged(params.name, true);
      return {
        content: [{
          type: "text",
          text: `已导出主能力 ${result.primaryCount} 项、字段候选接口 ${result.lookupCount} 个，版本 v${result.version}。目录 ${result.directory}。候选接口只给调用方选值，不是独立业务操作。`
        }],
        details: result
      };
    }
  });
}
