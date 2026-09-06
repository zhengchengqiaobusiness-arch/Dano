import type { CapabilityContract, CapabilityRoute, EvidenceEvent, NetworkEvidence } from "../domain.js";
import { isPrimaryCapability, sameResource } from "../inference/export-scope.js";
import { isSuccessfulNetworkEvidence, isTriggeredOperationEvidence } from "../inference/heuristics.js";
import { slugify } from "../utils.js";

export interface RouteBuildIssue {
  targetCapabilityId: string;
  reason: string;
}

function issueReason(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (message.startsWith("cycle:")) return `已确认绑定形成循环（${message.slice("cycle:".length)}），已跳过自动组合`;
  if (message.startsWith("missing:")) return `已确认绑定缺少来源能力（${message.slice("missing:".length)}），已跳过自动组合`;
  return message;
}

function visitRoute(target: CapabilityContract, byId: Map<string, CapabilityContract>) {
  const ordered: CapabilityContract[] = [];
  const visiting = new Set<string>();
  const visited = new Set<string>();

  const visit = (capability: CapabilityContract) => {
    if (visiting.has(capability.id)) throw new Error(`cycle:${capability.id}`);
    if (visited.has(capability.id)) return;
    visiting.add(capability.id);
    for (const binding of capability.bindings.filter(item => item.approved)) {
      const source = byId.get(binding.fromCapabilityId);
      if (!source) throw new Error(`missing:${binding.id}`);
      visit(source);
    }
    visiting.delete(capability.id);
    visited.add(capability.id);
    ordered.push(capability);
  };

  visit(target);
  const bindingIds = ordered.flatMap(capability => capability.bindings.filter(binding => binding.approved).map(binding => binding.id));
  const route: CapabilityRoute = {
    id: slugify(`route-${target.id}`),
    title: `${ordered.map(capability => capability.title).join(" → ")}`,
    targetCapabilityId: target.id,
    steps: ordered.map((capability, index) => ({
      order: index + 1,
      capabilityId: capability.id,
      bindingIds: capability.bindings.filter(binding => binding.approved).map(binding => binding.id)
    })),
    approvedBindingIds: bindingIds,
    stopConditions: [
      "目标或能力匹配不唯一时停止并询问用户",
      "上一步结果无法唯一确定绑定值时停止并让用户选择",
      "缺少调用方必填字段时只询问缺失字段",
      "写操作执行前必须单独取得明确确认",
      "执行结果不满足合同完成条件时停止，不猜测、不重试写操作"
    ],
    completion: `最后一步 ${target.id} 必须满足其合同中的完成条件`
  };
  return route;
}

export function buildRouteGraph(capabilities: CapabilityContract[]) {
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  const byId = new Map(verified.map(capability => [capability.id, capability]));
  const targets = verified.filter(capability => capability.bindings.some(binding => binding.approved));
  const routes: CapabilityRoute[] = [];
  const issues: RouteBuildIssue[] = [];
  for (const target of targets) {
    try {
      routes.push(visitRoute(target, byId));
    } catch (error) {
      issues.push({ targetCapabilityId: target.id, reason: issueReason(error) });
    }
  }
  return { routes, issues };
}

export function buildApprovedRoutes(capabilities: CapabilityContract[]): CapabilityRoute[] {
  return buildRouteGraph(capabilities).routes;
}

function successfulOperationEvents(
  capability: CapabilityContract,
  events: EvidenceEvent[],
  evidenceById: Map<string, EvidenceEvent>
) {
  const ids = new Set(capability.evidence.filter(ref => ref.kind === "network").map(ref => ref.eventId));
  return events.filter((event): event is NetworkEvidence =>
    event.kind === "network"
    && ids.has(event.id)
    && isSuccessfulNetworkEvidence(event)
    && isTriggeredOperationEvidence(event, capability.operation, evidenceById)
  );
}

export function buildRecordedWorkflowRoutes(capabilities: CapabilityContract[], events: EvidenceEvent[]): CapabilityRoute[] {
  if (!events.length) return [];
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  const evidenceById = new Map(events.map(event => [event.id, event]));
  const queries = verified.filter(capability =>
    capability.operation === "query" && isPrimaryCapability(capability, verified)
  );
  const creates = verified.filter(capability =>
    capability.operation === "create" && isPrimaryCapability(capability, verified)
  );
  return creates.flatMap(create => {
    const createEvents = successfulOperationEvents(create, events, evidenceById);
    const matches = queries.filter(query => {
      if (query.transport.origin !== create.transport.origin
        || !sameResource(query.transport.pathTemplate, create.transport.pathTemplate)) return false;
      const queryEvents = successfulOperationEvents(query, events, evidenceById);
      return createEvents.some(created => queryEvents.some(queried =>
        queried.sessionId === created.sessionId && Date.parse(queried.at) < Date.parse(created.at)
      ));
    });
    if (matches.length !== 1) return [];
    const query = matches[0]!;
    return [{
      id: slugify(`route-${create.id}`),
      title: `${query.title} → ${create.title}`,
      targetCapabilityId: create.id,
      steps: [
        { order: 1, capabilityId: query.id, bindingIds: [] },
        { order: 2, capabilityId: create.id, bindingIds: [] }
      ],
      approvedBindingIds: [],
      stopConditions: [
        "查询失败时停止，不执行新增",
        "查询结果不自动写入新增字段；两步分别使用调用方提供的字段",
        "缺少调用方必填字段时只询问缺失字段",
        "新增执行前必须单独取得明确确认",
        "执行结果不满足合同完成条件时停止，不猜测、不重试写操作"
      ],
      completion: `查询 ${query.id} 与新增 ${create.id} 都必须满足各自合同中的完成条件`
    }];
  });
}

export function buildExportRoutes(capabilities: CapabilityContract[], events: EvidenceEvent[] = []) {
  const approved = buildApprovedRoutes(capabilities);
  const approvedTargets = new Set(approved.map(route => route.targetCapabilityId));
  return [
    ...approved,
    ...buildRecordedWorkflowRoutes(capabilities, events)
      .filter(route => !approvedTargets.has(route.targetCapabilityId))
  ];
}

export function collectRouteIssues(capabilities: CapabilityContract[]): RouteBuildIssue[] {
  return buildRouteGraph(capabilities).issues;
}
