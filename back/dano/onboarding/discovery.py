"""阶段一·流程发现(图二步骤2-3):导入 swagger 后,平台自动「了解功能 + 找出合适的流程」。

把解析出的业务动作组织成**可直接生成**的流程提案,用户只需在前端确认/微调,不再手填:
  - 复合流程(多步串成一个业务 skill,如 提交请假 = 发起→存表单→提交):来自匹配到的 OA 模板配方;
  - 连接器(单接口一个 skill):写接口(需测试输入,执行前先确认)/ 只读查询(自动生成)。

只用 spec 客观特征 + 模板知识,不臆造流程;拿不准的留给用户在前端取舍。
"""

from __future__ import annotations

import re
from typing import Any

from dano.capabilities import doc_parser, endpoint_classifier, oa_templates
from dano.generation.strategies.workflow_bpmn import WorkflowBpmnStrategy


def discover_flows(spec: dict, include_tags: list[str] | None = None) -> list[dict[str, Any]]:
    """从 swagger 发现「合适的流程」提案(复合 + 连接器),供前端确认后生成。"""
    tags = set(include_tags or [])
    template = oa_templates.match_template(spec)
    extra = template.infrastructure_patterns() if template else ()
    actions = [a for a in doc_parser.parse_openapi(spec)
               if endpoint_classifier.classify(a, extra_infra=extra) != endpoint_classifier.INFRASTRUCTURE]
    in_scope = [a for a in actions if not tags or (set(a.tags) & tags)]
    action_dicts = [a.model_dump() for a in in_scope]

    proposed: list[dict[str, Any]] = []

    # 1) 复合流程:匹配到 OA 模板且 spec 含该框架工作流信号 → 按 spec 的 templateId **动态**提案
    #    (每个真实模板 = 一个复合业务;审批链从文档解析)。零硬编码业务:没有模板就不提复合流程。
    if template and action_dicts and WorkflowBpmnStrategy().matches(action_dicts):
        for tid in template.template_ids(spec):
            meta = template.parse_approval_chain(spec, tid)
            base = re.sub(r"_template$", "", tid)
            proposed.append({
                "flow": f"submit_{base}",
                "title": meta.get("flow") or tid,
                "kind": "composite",
                "write": True,                              # 写操作,执行前需确认
                "actions": [],                              # 空=交给 workflow_bpmn 策略用全量动作编排
                "method": "POST",
                "endpoint": "(多步编排)",
                "required": [],
                "suggested_test_input": {"templateId": tid, "values": {}},
                "business_meta": meta,                       # 审批链(动态解析,可空)
                "reason": "工作流模板:发起→提交串成一个业务 skill;模板与审批链来自接入材料,动态发现",
                "selected": True,
            })

    # 2) 连接器:每个业务动作各一个 skill(GET=只读自动 / 其它=写接口,需测试输入且执行前确认)
    for a in in_scope:
        method = (a.method or "GET").upper()
        is_write = method != "GET"
        proposed.append({
            "flow": a.name,
            "title": a.summary or a.name,
            "kind": "connector",
            "write": is_write,
            "actions": [a.name],
            "method": method,
            "endpoint": a.endpoint,
            "required": list(a.required_in),
            "suggested_test_input": {f: "" for f in a.required_in} if is_write else {},
            "reason": "写接口(执行前先确认)" if is_write else "只读查询(自动生成)",
            "tags": list(a.tags),
            "selected": not is_write,                       # 读默认选中;写需用户填测试输入后选
        })
    return proposed
