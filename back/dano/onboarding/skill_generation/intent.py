"""Extract constrained intent branches from stage-8 natural language."""

from __future__ import annotations

import re
from collections.abc import Iterable

from dano.execution.page.capability_kinds import READ_CAPABILITY_KINDS
from dano.execution.page.flow_spec_core.models import FlowCapability
from dano.onboarding.skill_generation.catalog import capability_ref, is_write_capability
from dano.onboarding.skill_generation.models import IntentBranch, SkillGenerationRequest


def _family(cap: FlowCapability) -> str:
    kind = str(cap.kind or "").strip().lower()
    title = f"{cap.title} {cap.name} {cap.intent}"
    if kind == "list_options" or any(token in title for token in ("选项", "字典", "下拉", "候选")):
        return "option"
    if kind in READ_CAPABILITY_KINDS or any(token in title for token in ("查询", "查看", "列表", "检索", "筛选")):
        return "query"
    if is_write_capability(cap) or any(token in title for token in ("提交", "保存", "审批", "写入", "新建", "编辑", "更新")):
        return "write"
    return kind or "other"

_SEQ_SPLIT = re.compile(r"[；;。.!？?\n]+")
_LIST_SPLIT = re.compile(r"[、,/]|或者|或是|以及|和")
_ORDER_PATTERNS = (
    re.compile(r"先(?P<left>.+?)再(?:对选中的[^做]*做)?(?P<right>.+)"),
    re.compile(r"先(?P<left>.+?)后(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)然后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)之后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)后再(?P<right>.+)"),
    re.compile(r"(?P<left>查询|查看|搜索|筛选|查出).{0,6}后(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>查(?:询|出)?|查看|搜索|筛选).{0,8}再(?P<right>.+)"),
)
_TARGET_GIVEN = ("已指定", "已备齐", "已经提供", "目标已给出", "已给出完整")
_READ_ONLY = ("只要查询", "只要查看", "只查", "只看", "不要写", "不要改", "不要审")
_UNKNOWN_STOP = (
    "办理", "使用", "操作", "处理", "完成", "确认", "结果", "用户", "订单",
    "记录", "字段", "对象", "业务", "页面", "本页", "单独", "创建", "选定",
    "选中", "一条", "一张", "目标", "写入", "只读",
)
_GENERIC_VERBS = (
    "查询", "查看", "搜索", "筛选", "检索", "列表", "新增", "新建", "创建",
    "编辑", "修改", "更新", "审核", "审批", "反审", "反审核", "删除", "提交",
    "保存", "导出", "打印", "下载", "同步", "作废", "撤回", "撤销",
)
_FAMILY_HINTS = {
    "query": ("查询", "搜索", "筛选", "检索", "列表", "查"),
    "detail": ("查看", "详情", "明细"),
    "write": ("提交", "保存", "写入"),
    "create": ("新增", "新建", "创建"),
    "update": ("编辑", "修改", "更新"),
    "approve": ("审核", "审批"),
    "unapprove": ("反审核", "反审"),
    "delete": ("删除", "作废"),
}


def _cap_title(cap: FlowCapability) -> str:
    return str(cap.title or cap.name or "")


def _aliases(cap: FlowCapability) -> list[str]:
    title = _cap_title(cap)
    name = str(cap.name or "")
    aliases = [item for item in (title, name) if item]
    family = _family(cap)
    kind = str(cap.kind or "").strip().lower()
    if family == "query" or kind == "query":
        if "详情" in title or "查看" in title:
            aliases.extend(["查看", "详情", "看详情"])
        else:
            aliases.extend(["查询", "搜索", "筛选", "查订单", "查出"])
    if family == "write" or is_write_capability(cap):
        if any(token in title for token in ("新增", "新建", "创建")):
            aliases.extend(["新增", "新建", "创建"])
        elif any(token in title for token in ("编辑", "修改")):
            aliases.extend(["编辑", "修改"])
        elif "反审" in title:
            aliases.extend(["反审核", "反审"])
        elif any(token in title for token in ("审核", "审批")):
            aliases.extend(["审核", "审批"])
        elif "删除" in title:
            aliases.extend(["删除"])
        elif "提交" in title:
            aliases.extend(["提交"])
    return list(dict.fromkeys(aliases))


def _unique_caps(caps: list[FlowCapability]) -> list[FlowCapability]:
    seen: set[str] = set()
    unique: list[FlowCapability] = []
    for cap in caps:
        key = capability_ref(cap)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(cap)
    return unique


def _match_caps(text: str, caps: list[FlowCapability]) -> list[FlowCapability]:
    scored: list[tuple[int, FlowCapability]] = []
    for cap in caps:
        best = 0
        for alias in _aliases(cap):
            if alias and alias in text:
                best = max(best, len(alias))
        if best:
            scored.append((best, cap))
    if not scored:
        return []
    top = max(score for score, _cap in scored)
    if top >= 4:
        return [cap for score, cap in scored if score >= 4]
    return [cap for score, cap in scored if score == top]


def _parts(text: str) -> list[str]:
    return [item.strip() for item in _LIST_SPLIT.split(text) if item and item.strip()]


def _mutation(caps: list[FlowCapability]) -> str:
    writes = [cap for cap in caps if is_write_capability(cap)]
    if writes and len(writes) == len(caps):
        return "write"
    if writes:
        return "mixed"
    return "read"


def _target_given(text: str) -> bool:
    return any(token in text for token in _TARGET_GIVEN)


def _unknown_actions(text: str, caps: list[FlowCapability]) -> list[str]:
    known = {alias for cap in caps for alias in _aliases(cap)}
    unknown: list[str] = []
    for verb in _GENERIC_VERBS:
        if verb in text and verb not in known and not any(verb in alias for alias in known):
            unknown.append(verb)
    return list(dict.fromkeys(unknown))


def _branch(
    *,
    branch_id: str,
    trigger: str,
    caps: list[FlowCapability],
    source: str,
    unresolved: list[str] | None = None,
    independent: bool = False,
    target_given: bool = False,
    conflicting: bool = False,
    preconditions: list[str] | None = None,
) -> IntentBranch:
    return IntentBranch(
        branch_id=branch_id,
        trigger=trigger,
        capability_sequence=[capability_ref(cap) for cap in caps],
        mutation=_mutation(caps),
        preconditions=list(preconditions or []),
        unresolved=list(unresolved or []),
        source=source,  # type: ignore[arg-type]
        independent=independent,
        target_given=target_given,
        conflicting=conflicting,
    )


def _expand_order(left_text: str, right_text: str, caps: list[FlowCapability]) -> list[list[FlowCapability]]:
    left = _unique_caps(_match_caps(left_text, caps) or [cap for part in _parts(left_text) for cap in _match_caps(part, caps)])
    right = _unique_caps(_match_caps(right_text, caps) or [cap for part in _parts(right_text) for cap in _match_caps(part, caps)])
    if not left or not right:
        return []
    sequences: list[list[FlowCapability]] = []
    for start in left:
        for end in right:
            if capability_ref(start) == capability_ref(end):
                continue
            sequences.append([start, end])
    return sequences


def _sentence_orders(text: str, caps: list[FlowCapability]) -> list[list[FlowCapability]]:
    found: list[list[FlowCapability]] = []
    for sentence in _SEQ_SPLIT.split(text):
        raw = sentence.strip()
        if not raw:
            continue
        for pattern in _ORDER_PATTERNS:
            match = pattern.search(raw)
            if not match:
                continue
            found.extend(_expand_order(match.group("left"), match.group("right"), caps))
            break
    return found


def _orders_conflict(orders: Iterable[list[FlowCapability]]) -> bool:
    seen: set[tuple[str, str]] = set()
    for sequence in orders:
        if len(sequence) < 2:
            continue
        pair = (capability_ref(sequence[0]), capability_ref(sequence[-1]))
        reverse = (pair[1], pair[0])
        if reverse in seen:
            return True
        seen.add(pair)
    return False


def extract_intent_branches(
    request: SkillGenerationRequest,
    selected: list[FlowCapability],
) -> list[IntentBranch]:
    """Propose whitelist-constrained branches. Never invent fields or bindings."""
    description = str(request.business_description or "").strip()
    examples = [str(item).strip() for item in request.example_requests if str(item).strip()]
    branches: list[IntentBranch] = []
    seen: set[tuple[str, ...]] = set()

    def add(branch: IntentBranch) -> None:
        key = tuple(branch.capability_sequence) + tuple(branch.unresolved) + (branch.trigger,)
        if branch.capability_sequence:
            key = tuple(branch.capability_sequence)
        if key in seen and not branch.unresolved:
            for existing in branches:
                if tuple(existing.capability_sequence) == tuple(branch.capability_sequence):
                    if branch.trigger and branch.trigger not in existing.trigger:
                        existing.trigger = f"{existing.trigger}；{branch.trigger}"
                    return
        if key in seen and not branch.unresolved:
            return
        seen.add(key)
        branches.append(branch)

    orders = _sentence_orders(description, selected)
    if _orders_conflict(orders):
        add(_branch(
            branch_id="conflict_order",
            trigger=description,
            caps=[],
            source="description",
            unresolved=["描述中的办理顺序互相冲突，无法安全选择一条"],
            conflicting=True,
        ))
    else:
        for index, sequence in enumerate(orders):
            add(_branch(
                branch_id=f"desc_order_{index + 1}",
                trigger=description,
                caps=sequence,
                source="description",
                independent=_looks_independent(sequence),
            ))

    mentioned = _match_caps(description, selected)
    if not orders:
        if len(mentioned) == 1:
            add(_branch(
                branch_id="desc_single",
                trigger=description,
                caps=mentioned,
                source="description",
                target_given=_target_given(description),
            ))
        elif len(mentioned) >= 2 and _looks_independent(mentioned) and not any(
            token in description for token in ("先", "再", "然后", "之后")
        ):
            add(_branch(
                branch_id="desc_independent",
                trigger=description,
                caps=mentioned,
                source="description",
                independent=True,
            ))

    unknown = _unknown_actions(description, selected)
    if unknown:
        add(_branch(
            branch_id="desc_unknown",
            trigger=description,
            caps=[],
            source="description",
            unresolved=[f"描述提到了当前页面没有的动作：{'、'.join(unknown)}"],
        ))

    if any(token in description for token in _READ_ONLY):
        reads = [cap for cap in selected if not is_write_capability(cap)]
        for index, cap in enumerate(reads):
            if _cap_title(cap) in description or any(alias in description for alias in _aliases(cap)[:3]):
                add(_branch(
                    branch_id=f"desc_readonly_{index + 1}",
                    trigger="只查询或只查看，不要写入",
                    caps=[cap],
                    source="description",
                    preconditions=["只读，不得升级成写入"],
                ))

    for index, example in enumerate(examples):
        example_orders = _sentence_orders(example, selected)
        example_unknown = _unknown_actions(example, selected)
        if example_unknown:
            add(_branch(
                branch_id=f"example_unknown_{index + 1}",
                trigger=example,
                caps=[],
                source="example",
                unresolved=[f"示例提到了当前页面没有的动作：{'、'.join(example_unknown)}"],
            ))
            continue
        if example_orders:
            for extra, sequence in enumerate(example_orders):
                add(_branch(
                    branch_id=f"example_order_{index + 1}_{extra + 1}",
                    trigger=example,
                    caps=sequence,
                    source="example",
                    independent=_looks_independent(sequence),
                    target_given=_target_given(example),
                ))
            continue
        hits = _match_caps(example, selected)
        if len(hits) == 1:
            add(_branch(
                branch_id=f"example_single_{index + 1}",
                trigger=example,
                caps=hits,
                source="example",
                target_given=_target_given(example),
            ))
        elif len(hits) >= 2:
            add(_branch(
                branch_id=f"example_multi_{index + 1}",
                trigger=example,
                caps=hits,
                source="example",
                independent=_looks_independent(hits),
                target_given=_target_given(example),
            ))

    return branches


def _looks_independent(caps: list[FlowCapability]) -> bool:
    if len(caps) < 2:
        return False
    writes = [cap for cap in caps if is_write_capability(cap)]
    reads = [cap for cap in caps if not is_write_capability(cap)]
    if reads and writes:
        return False
    return True


def branch_needs_clarification(branch: IntentBranch) -> bool:
    return bool(branch.unresolved or branch.conflicting or (not branch.capability_sequence and branch.trigger))
