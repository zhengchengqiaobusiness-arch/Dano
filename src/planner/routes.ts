import type { CapabilityContract, CapabilityRoute } from "../domain.js";
import { slugify } from "../utils.js";

export function buildApprovedRoutes(capabilities: CapabilityContract[]): CapabilityRoute[] {
  const verified = capabilities.filter(capability => capability.validation.status === "verified");
  const byId = new Map(verified.map(capability => [capability.id, capability]));
  const targets = verified.filter(capability => capability.bindings.some(binding => binding.approved));

  return targets.map(target => {
    const ordered: CapabilityContract[] = [];
    const visiting = new Set<string>();
    const visited = new Set<string>();

    const visit = (capability: CapabilityContract) => {
      if (visiting.has(capability.id)) throw new Error(`已确认绑定形成循环，无法导出路线：${capability.id}`);
      if (visited.has(capability.id)) return;
      visiting.add(capability.id);
      for (const binding of capability.bindings.filter(item => item.approved)) {
        const source = byId.get(binding.fromCapabilityId);
        if (!source) throw new Error(`绑定 ${binding.id} 引用了未验证或不存在的来源能力`);
        visit(source);
      }
      visiting.delete(capability.id);
      visited.add(capability.id);
      ordered.push(capability);
    };

    visit(target);
    const bindingIds = ordered.flatMap(capability => capability.bindings.filter(binding => binding.approved).map(binding => binding.id));
    return {
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
  });
}
