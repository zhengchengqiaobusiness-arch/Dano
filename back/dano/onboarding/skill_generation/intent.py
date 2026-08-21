"""Extract constrained intent branches from stage-8 natural language."""

from __future__ import annotations

import re
from collections.abc import Iterable

from dano.execution.page.flow_spec_core.models import FlowCapability
from dano.onboarding.skill_generation.catalog import (
    capability_family,
    capability_ref,
    is_write_capability,
)
from dano.onboarding.skill_generation.models import IntentBranch, SkillGenerationRequest


_SEQ_SPLIT = re.compile(r"[；;。.!？?\n]+")
_LIST_SPLIT = re.compile(r"或者|或是|以及|和|[、,/]|或")
_ORDER_PATTERNS = (
    re.compile(r"先(?P<left>.+?)再(?:对选中的[^做]*做)?(?P<right>.+)"),
    re.compile(r"先(?P<left>.+?)后(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)紧接着(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)随后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)随即(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)接下来(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)然后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)之后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)后再(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)接着(?P<right>.+)"),
    re.compile(
        r"(?P<left>.+?)(?:完成|完毕|做完|完了)(?:后|了)?(?:就|再|立即|马上|紧接着|帮我)?(?P<right>.+)"
    ),
    re.compile(r"(?P<left>查询|查看|搜索|筛选|查出).{0,6}后(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>查询|查看|搜索|筛选|查出).{0,8}再(?P<right>.+)"),
)
_SEQUENCE_HINTS = (
    "然后",
    "之后",
    "紧接着",
    "随后",
    "随即",
    "接下来",
    "完成后",
    "完毕后",
    "完了就",
    "完了再",
    "做完",
    "下一步",
    "跟着",
    "后再",
    "马上",
    "连着",
    "连续办理",
    "衔接",
)
_UNRESOLVED_SEQUENCE = "描述像是要按顺序办理多项操作，但无法唯一确定组合路线。请用「先…再…」写明每一步，不要省略顺序"
_TARGET_GIVEN = ("已指定", "已备齐", "已经提供", "目标已给出", "已给出完整")
_READ_ONLY = ("只要查询", "只要查看", "只查", "只看", "不要写", "不要改", "不要审")
_GENERIC_VERBS = (
    "查询", "查看", "搜索", "筛选", "检索", "列表", "新增", "新建", "创建",
    "编辑", "修改", "更新", "审核", "审批", "反审", "反审核", "删除", "提交",
    "保存", "导出", "打印", "下载", "同步", "作废", "撤回", "撤销",
)


def _cap_title(cap: FlowCapability) -> str:
    return str(cap.title or cap.name or "")


def _aliases(cap: FlowCapability) -> list[str]:
    title = _cap_title(cap)
    name = str(cap.name or "")
    aliases = [item for item in (title, name) if item]
    family = capability_family(cap)
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


def _alias_hits(text: str, caps: list[FlowCapability]) -> list[tuple[int, int, FlowCapability, str]]:
    """Find non-overlapping alias spans, longest match first."""
    raw = str(text or "")
    candidates: list[tuple[int, int, FlowCapability, str]] = []
    for cap in caps:
        for alias in _aliases(cap):
            if not alias:
                continue
            start = 0
            while True:
                index = raw.find(alias, start)
                if index < 0:
                    break
                candidates.append((index, index + len(alias), cap, alias))
                start = index + len(alias)
    candidates.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    occupied: list[tuple[int, int]] = []
    accepted: list[tuple[int, int, FlowCapability, str]] = []
    for start, end, cap, alias in candidates:
        if any(not (end <= left or start >= right) for left, right in occupied):
            continue
        occupied.append((start, end))
        accepted.append((start, end, cap, alias))
    accepted.sort(key=lambda item: item[0])
    return accepted


def _match_caps(text: str, caps: list[FlowCapability]) -> list[FlowCapability]:
    return _unique_caps([cap for _start, _end, cap, _alias in _alias_hits(text, caps)])


def _parts(text: str) -> list[str]:
    return [item.strip() for item in _LIST_SPLIT.split(text) if item and item.strip()]


def _match_alternatives(text: str, caps: list[FlowCapability]) -> tuple[list[FlowCapability], list[str]]:
    """Parse a parallel list into capabilities. Unmapped items become clarifications."""
    raw = str(text or "").strip()
    if not raw:
        return [], []
    parts = _parts(raw) or [raw]
    found: list[FlowCapability] = []
    unresolved: list[str] = []
    for part in parts:
        hits = _match_caps(part, caps) or _match_prefix_aliases(part, caps)
        if hits:
            found.extend(hits)
            continue
        unresolved.append(part)
    return _unique_caps(found), unresolved


def _match_prefix_aliases(text: str, caps: list[FlowCapability]) -> list[FlowCapability]:
    """Resolve short leftovers like「查」when they uniquely prefix a known alias."""
    part = str(text or "").strip()
    if len(part) < 1:
        return []
    scored: list[tuple[int, FlowCapability]] = []
    for cap in caps:
        aliases = [alias for alias in _aliases(cap) if alias and alias.startswith(part)]
        if aliases:
            scored.append((min(len(alias) for alias in aliases), cap))
    if not scored:
        return []
    shortest = min(length for length, _cap in scored)
    winners = _unique_caps([cap for length, cap in scored if length == shortest])
    return winners if len(winners) == 1 else []


def _mutation(caps: list[FlowCapability]) -> str:
    writes = [cap for cap in caps if is_write_capability(cap)]
    if writes and len(writes) == len(caps):
        return "write"
    if writes:
        return "mixed"
    return "read"


def _target_given(text: str) -> bool:
    return any(token in text for token in _TARGET_GIVEN)


def description_has_explicit_sequence(text: str) -> bool:
    """True when the user named a multi-step order, not just a list of actions."""
    raw = str(text or "")
    if any(token in raw for token in _SEQUENCE_HINTS):
        return True
    if "接着" in raw:
        return True
    return "先" in raw and ("再" in raw or "后" in raw)


def _unknown_actions(text: str, caps: list[FlowCapability]) -> list[str]:
    known = {alias for cap in caps for alias in _aliases(cap)}
    unknown: list[str] = []
    for verb in sorted(_GENERIC_VERBS, key=len, reverse=True):
        if verb not in text:
            continue
        if verb in known or any(verb in alias for alias in known):
            continue
        if any(verb != other and verb in other and other in text for other in _GENERIC_VERBS):
            continue
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


def _expand_order(
    left_text: str,
    right_text: str,
    caps: list[FlowCapability],
) -> tuple[list[list[FlowCapability]], list[str]]:
    left, left_unresolved = _match_alternatives(left_text, caps)
    right, right_unresolved = _match_alternatives(right_text, caps)
    unresolved = [*left_unresolved, *right_unresolved]
    if unresolved:
        return [], [
            f"无法把「{'、'.join(unresolved)}」映射到当前页面已有操作，请改用已有动作或补充说明"
        ]
    if not left or not right:
        missing = left_text.strip() if not left else right_text.strip()
        return [], [f"无法把「{missing}」映射到当前页面已有操作，请改用已有动作或补充说明"]
    sequences: list[list[FlowCapability]] = []
    for start in left:
        for end in right:
            if capability_ref(start) == capability_ref(end):
                continue
            sequences.append([start, end])
    if not sequences:
        return [], [f"「{left_text.strip()} → {right_text.strip()}」没有可执行的能力顺序"]
    return sequences, []


def _sentence_orders(
    text: str,
    caps: list[FlowCapability],
) -> tuple[list[list[FlowCapability]], list[str]]:
    found: list[list[FlowCapability]] = []
    unresolved: list[str] = []
    for sentence in _SEQ_SPLIT.split(text):
        raw = sentence.strip()
        if not raw:
            continue
        for pattern in _ORDER_PATTERNS:
            match = pattern.search(raw)
            if not match:
                continue
            sequences, problems = _expand_order(match.group("left"), match.group("right"), caps)
            unresolved.extend(problems)
            found.extend(sequences)
            break
    return found, unresolved


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

    orders, order_problems = _sentence_orders(description, selected)
    if order_problems:
        add(_branch(
            branch_id="desc_unresolved_order",
            trigger=description,
            caps=[],
            source="description",
            unresolved=list(dict.fromkeys(order_problems)),
        ))
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
        elif (
            len(mentioned) >= 2
            and _looks_independent(mentioned)
            and not description_has_explicit_sequence(description)
        ):
            add(_branch(
                branch_id="desc_independent",
                trigger=description,
                caps=mentioned,
                source="description",
                independent=True,
            ))
        if description_has_explicit_sequence(description) and not any(
            branch.unresolved or branch.conflicting for branch in branches
        ):
            add(_branch(
                branch_id="desc_unresolved_sequence",
                trigger=description,
                caps=[],
                source="description",
                unresolved=[_UNRESOLVED_SEQUENCE],
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
        example_orders, example_problems = _sentence_orders(example, selected)
        example_unknown = _unknown_actions(example, selected)
        if example_problems:
            add(_branch(
                branch_id=f"example_unresolved_{index + 1}",
                trigger=example,
                caps=[],
                source="example",
                unresolved=list(dict.fromkeys(example_problems)),
            ))
            continue
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
        if description_has_explicit_sequence(example) and len(hits) != 1:
            add(_branch(
                branch_id=f"example_unresolved_sequence_{index + 1}",
                trigger=example,
                caps=[],
                source="example",
                unresolved=[_UNRESOLVED_SEQUENCE],
            ))
            continue
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
