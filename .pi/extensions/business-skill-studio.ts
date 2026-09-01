import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { StudioService } from "../../src/studio-service.js";

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

  pi.on("session_shutdown", async () => {
    if (studio.recorder.isActive()) await studio.stopRecording().catch(() => {});
  });

  pi.registerTool({
    name: "business_skill_record_start",
    label: "Start business recording",
    description: "Launch a headed browser and start recording real UI actions plus XHR/fetch/document requests and responses.",
    parameters: parameters({
      url: { type: "string", description: "Real business-system URL" },
      name: { type: "string", description: "Optional recording name" }
    }, ["url"]),
    async execute(_toolCallId, params: any) {
      const session = await studio.startRecording(params.url, params.name);
      return {
        content: [{ type: "text", text: `Recording started: ${session.id}. Operate the headed browser, then call business_skill_record_stop.` }],
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
      const session = await studio.stopRecording();
      return {
        content: [{ type: "text", text: `Recording saved: ${session.id}` }],
        details: session
      };
    }
  });

  pi.registerTool({
    name: "business_browser_control",
    label: "Control recording browser",
    description: "Control the active headed browser with goto/snapshot/click/fill/select/press/wait/screenshot. Use snapshot first to ground selectors in the real page.",
    parameters: parameters({
      action: { type: "string", enum: ["goto", "snapshot", "click", "fill", "select", "press", "wait", "screenshot"] },
      selector: { type: "string" },
      value: {},
      url: { type: "string" },
      key: { type: "string" },
      ms: { type: "number" }
    }, ["action"]),
    async execute(_id, params: any, _signal, _onUpdate, ctx) {
      if ((params.action === "click" || (params.action === "press" && params.key === "Enter")) && params.selector) {
        const target = await studio.recorder.inspectTarget(params.selector);
        const signal = `${target.text || ""} ${target.label || ""} ${target.name || ""} ${target.formText || ""}`;
        const risky = target.type === "submit" || /save|submit|create|add|update|edit|approve|review|reject|delete|remove|保存|提交|新增|新建|创建|修改|编辑|更新|审核|审批|通过|驳回|删除|移除|作废/i.test(signal);
        if (risky) {
          const ok = await ctx.ui.confirm(
            "Confirm real browser write",
            `The target may change real business data:\n${target.text || target.label || params.selector}\n\nContinue?`
          );
          if (!ok) return { content: [{ type: "text", text: "Browser write action cancelled by user." }], details: { cancelled: true, target } };
        }
      }
      const result = await studio.recorder.control(params);
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
      return {
        content: [{ type: "text", text: `Generated ${caps.length} editable capability candidate(s).` }],
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
      const verified = caps.filter(c => c.validation.status === "verified").length;
      return {
        content: [{ type: "text", text: `Validation complete: ${verified}/${caps.length} verified.` }],
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
    description: "Execute one verified capability. Side-effecting operations always require an interactive confirmation.",
    parameters: parameters({
      capabilityId: { type: "string" },
      input: { type: "object", additionalProperties: true }
    }, ["capabilityId", "input"]),
    async execute(_id, params: any, _signal, _onUpdate, ctx) {
      const caps = await studio.capabilities();
      const cap = caps.find(c => c.id === params.capabilityId);
      if (!cap) throw new Error(`Unknown capability: ${params.capabilityId}`);

      let confirmed = false;
      if (cap.confirmation.required) {
        confirmed = await ctx.ui.confirm(
          `Confirm ${cap.operation}`,
          `${cap.title}\n\nThis operation changes business data. Execute it now?`
        );
        if (!confirmed) {
          return {
            content: [{ type: "text", text: "Write operation cancelled by user." }],
            details: { cancelled: true }
          };
        }
      }

      const result = await studio.execute(cap.id, params.input, confirmed);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        details: result
      };
    }
  });

  pi.registerTool({
    name: "business_skill_approve_binding",
    label: "Approve capability binding",
    description: "Human-confirm a data binding from one verified capability output path into another verified capability input path.",
    parameters: parameters({
      fromCapabilityId: { type: "string" },
      fromPath: { type: "string" },
      toCapabilityId: { type: "string" },
      toPath: { type: "string" },
      note: { type: "string" }
    }, ["fromCapabilityId", "fromPath", "toCapabilityId", "toPath"]),
    async execute(_id, params: any, _signal, _onUpdate, ctx) {
      const ok = await ctx.ui.confirm(
        "Approve automatic data binding",
        `${params.fromCapabilityId}:${params.fromPath}\n→ ${params.toCapabilityId}:${params.toPath}\n\nAllow this binding for future automatic composition?`
      );
      if (!ok) return { content: [{ type: "text", text: "Binding approval cancelled." }], details: { cancelled: true } };
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
    async execute(_id, params: any, _signal, _onUpdate, ctx) {
      const ok = await ctx.ui.confirm(
        "Set dynamic candidate source",
        `${params.inputPath} will query ${params.sourceCapabilityId}. Apply this rule?`
      );
      if (!ok) return { content: [{ type: "text", text: "Candidate rule cancelled." }], details: { cancelled: true } };
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
      outputRoot: { type: "string" }
    }, ["name"]),
    async execute(_id, params: any) {
      const result = await studio.export(params.name, params.outputRoot);
      return {
        content: [{ type: "text", text: `Exported ${result.count} verified capabilities to ${result.dir}` }],
        details: result
      };
    }
  });
}
