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
_AFTER_NOT_THEN = r"(?<!然)(?<!之)(?<!随)后"
_ORDER_PATTERNS = (
    re.compile(r"先(?P<left>.+?)再(?:对选中的[^做]*做)?(?P<right>.+)"),
    re.compile(r"先(?P<left>.+?)然后(?P<right>.+)"),
    re.compile(r"先(?P<left>.+?)之后(?P<right>.+)"),
    re.compile(
        r"先(?P<left>.+?)(?:，|,|。)?根据(?:返回|结果|查询结果|统计结果)(?:进行|再|后)?(?P<right>.+)"
    ),
    re.compile(
        r"(?P<left>.+?)(?:，|,)?根据(?:返回|结果|查询结果)(?:进行|再|后)(?P<right>.+)"
    ),
    re.compile(rf"先(?P<left>.+?){_AFTER_NOT_THEN}(?:再)?(?P<right>.+)"),
    re.compile(r"完成(?P<left>.+?)方可(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)优先[，,、\s]*(?P<right>.+?)其次"),
    re.compile(r"(?P<left>.+?)在前[，,、\s]*(?P<right>.+?)在后"),
    re.compile(r"(?P<left>.+?)方可(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)继而(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)结束(?:后|了)?(?:就|再|即)(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)紧接着(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)随后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)随即(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)接下来(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)然后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)之后(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)后再(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)后重新(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)[，,]?后续(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>.+?)接着(?P<right>.+)"),
    re.compile(
        r"(?P<left>.+?)(?:完成|完毕|做完|完了)(?:后|了)?(?:就|再|立即|马上|紧接着|帮我)?(?P<right>.+)"
    ),
    re.compile(rf"(?P<left>查询|查看|搜索|筛选|查出).{{0,6}}{_AFTER_NOT_THEN}(?:再)?(?P<right>.+)"),
    re.compile(r"(?P<left>查询|查看|搜索|筛选|查出).{0,8}再(?P<right>.+)"),
)
_SEQUENCE_HINTS = (
    "根据返回",
    "根据结果",
    "根据查询结果",
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
    "后重新",
    "后续",
    "连着",
    "连续办理",
    "衔接",
    "继而",
    "结束就",
    "结束后",
    "结束即",
    "方可",
    "在前",
    "在后",
    "优先",
    "其次",
)
_BETWEEN_SEQUENCE = _SEQUENCE_HINTS + (
    "结束就",
    "结束后",
    "完成就",
    "完毕就",
    "优先",
    "其次",
    "在前",
    "在后",
    "方可",
    "继而",
)
_UNRESOLVED_SEQUENCE = "描述像是要按顺序办理多项操作，但无法唯一确定组合路线。请用「先…再…」写明每一步，不要省略顺序"
_TARGET_GIVEN = ("已指定", "已备齐", "已经提供", "目标已给出", "已给出完整")
_READ_ONLY = ("只要查询", "只要查看", "只查", "只看", "不要写", "不要改", "不要审")
_GENERIC_VERBS = (
    "查询", "查看", "搜索", "筛选", "检索", "列表", "新增", "新建", "创建",
    "编辑", "修改", "更新", "审核", "审批", "反审", "反审核", "删除", "提交",
    "保存", "导出", "打印", "下载", "同步", "作废", "撤回", "撤销",
)
_NARRATIVE_PHRASES = (
    "保存修改",
    "撤回修改",
    "撤回审核",
    "重新更新",
    "重新修改",
    "统计存档",
    "统计归档",
)
_CLAUSE_GLUE = (
    "根据返回",
    "根据结果",
    "根据",
    "返回",
    "结果",
    "进行",
    "办理",
    "操作",
    "然后",
    "之后",
    "随后",
    "接着",
    "接下来",
    "再",
    "后",
    "并",
)
_CONNECTOR_FRAGMENTS = "然之随接"


def _cap_title(cap: FlowCapability) -> str:
    return str(cap.title or cap.name or "")


def _aliases(cap: FlowCapability) -> list[str]:
    title = _cap_title(cap)
    name = str(cap.name or "")
    aliases = [item for item in (title, name) if item]
    family = capability_family(cap)
    kind = str(cap.kind or "").strip().lower()
    if kind == "export" or "导出" in title or "下载" in title:
        aliases.extend(["导出", "下载"])
    elif family == "query" or kind == "query":
        if "详情" in title or "查看" in title:
            aliases.extend(["查看", "详情", "看详情"])
        else:
            aliases.extend(["查询", "搜索", "筛选", "查订单", "查出"])
    if family == "write" or is_write_capability(cap):
        if any(token in title for token in ("新增", "新建", "创建")):
            aliases.extend(["新增", "新建", "创建"])
        elif any(token in title for token in ("编辑", "修改", "更新")):
            aliases.extend(["编辑", "修改", "更新"])
        elif "取消审核" in title or "反审" in title:
            aliases.extend(["取消审核", "反审核", "反审"])
            if "反审核" in title:
                aliases.append(title.replace("反审核", "取消审核"))
        elif any(token in title for token in ("审核", "审批")):
            aliases.extend(["审核", "审批"])
        elif "删除" in title:
            aliases.extend(["删除"])
        elif "提交" in title:
            aliases.extend(["提交"])
        elif "保存" in title:
            aliases.extend(["保存"])
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


def _is_clause_noise(part: str) -> bool:
    """Connectors and leftover fragments like「然」are not page operations."""
    compact = re.sub(r"[\s，,。.!？?、；;：:]+", "", str(part or "")).strip()
    if not compact:
        return True
    rest = compact
    for word in sorted(_CLAUSE_GLUE, key=len, reverse=True):
        rest = rest.replace(word, "")
    rest = re.sub(f"[{_CONNECTOR_FRAGMENTS}]", "", rest)
    return not rest


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
        if _is_clause_noise(part):
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
    """True when the user named a multi-step order, not just a list of actions.

    Degree adverbs such as「马上」or「立即」alone are not order language.
    """
    raw = str(text or "")
    if any(token in raw for token in _SEQUENCE_HINTS):
        return True
    if "接着" in raw:
        return True
    if "优先" in raw and "其次" in raw:
        return True
    if "在前" in raw and "在后" in raw:
        return True
    if "方可" in raw:
        return True
    if re.search(r"(?:结束|完成|完毕|做完|完了).{0,4}(?:就|再|即)", raw):
        return True
    if "先" in raw and any(
        token in raw for token in ("再", "后", "根据返回", "根据结果", "根据查询结果")
    ):
        return True
    return False


def _is_sequence_connector(between: str) -> bool:
    compact = str(between or "")
    if not compact.strip():
        return False
    if any(token in compact for token in _BETWEEN_SEQUENCE):
        return True
    if re.search(r"(?:结束|完成|完毕|做完|完了).{0,4}(?:就|再|即)", compact):
        return True
    if re.search(r"(?<!不)(?<!重)再(?!新)", compact):
        return True
    stripped = compact.strip()
    return stripped in {"马上", "立即"} or stripped.startswith(("马上", "立即"))


def _sequence_pair_between_hits(
    text: str,
    hits: list[tuple[int, int, FlowCapability, str]],
) -> list[FlowCapability]:
    raw = str(text or "")
    if len(hits) < 2:
        return []
    for index in range(len(hits) - 1):
        _start, end, cap, _alias = hits[index]
        nxt_start, nxt_end, nxt_cap, _nxt_alias = hits[index + 1]
        between = raw[end:nxt_start]
        after = raw[nxt_start:nxt_end + 8]
        before = raw[max(0, end - 8):end]
        connected = (
            _is_sequence_connector(between)
            or (("优先" in between or "优先" in before) and "其次" in after)
            or (("在前" in between or "在前" in before) and "在后" in after)
        )
        if connected and capability_ref(cap) != capability_ref(nxt_cap):
            return [cap, nxt_cap]
    return []


def _orders_from_mentions(
    text: str,
    caps: list[FlowCapability],
) -> list[list[FlowCapability]]:
    """Compile the two actions directly joined by sequence language."""
    hits = _alias_hits(text, caps)
    sequence = _sequence_pair_between_hits(text, hits)
    return [sequence] if sequence else []


def looks_like_ordered_multi_step(text: str, caps: list[FlowCapability]) -> bool:
    """True when the utterance names two capabilities and an order, not one action."""
    raw = str(text or "")
    mentioned = _match_caps(raw, caps)
    if len(mentioned) < 2:
        return False
    return description_has_explicit_sequence(raw) or bool(_orders_from_mentions(raw, caps))


def _mask_known_actions(text: str, caps: list[FlowCapability]) -> str:
    """Hide already-mapped operations and narrative collocations before scanning leftovers."""
    raw = str(text or "")
    spans: list[tuple[int, int]] = [(start, end) for start, end, _cap, _alias in _alias_hits(raw, caps)]
    for phrase in sorted(_NARRATIVE_PHRASES, key=len, reverse=True):
        start = 0
        while True:
            index = raw.find(phrase, start)
            if index < 0:
                break
            spans.append((index, index + len(phrase)))
            start = index + len(phrase)
    chars = list(raw)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            chars[index] = "\0"
    return "".join(chars)


def _unknown_actions(text: str, caps: list[FlowCapability]) -> list[str]:
    known = {alias for cap in caps for alias in _aliases(cap)}
    masked = _mask_known_actions(text, caps)
    unknown: list[str] = []
    for verb in sorted(_GENERIC_VERBS, key=len, reverse=True):
        if verb not in masked:
            continue
        if verb in known or any(verb in alias for alias in known):
            continue
        if any(verb != other and verb in other and other in masked for other in _GENERIC_VERBS):
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
    # Narrative clauses often contain earlier context before the real connector,
    # for example「审核后……反审核后重新修改」.  Without an explicit parallel
    # list, the operation nearest the connector is the only safe left-hand side.
    if len(left) > 1 and not _LIST_SPLIT.search(left_text):
        left = left[-1:]
    if len(right) > 1 and not _LIST_SPLIT.search(right_text):
        right = right[:1]
    unresolved = [*left_unresolved, *right_unresolved]
    if unresolved:
        return [], [
            f"无法把「{'、'.join(unresolved)}」映射到当前页面已有操作，请改用已有动作或补充说明"
        ]
    if not left or not right:
        missing = left_text.strip() if not left else right_text.strip()
        if _is_clause_noise(missing):
            return [], []
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


def _narrative_lookup_orders(
    text: str,
    caps: list[FlowCapability],
) -> list[list[FlowCapability]]:
    """Recognize the narrow, common「搜索定位，查看详情」handoff."""

    hits = _alias_hits(text, caps)
    found: list[list[FlowCapability]] = []
    seen: set[tuple[str, str]] = set()
    for index in range(len(hits) - 1):
        _start, end, left, _alias = hits[index]
        right_start, _right_end, right, _right_alias = hits[index + 1]
        left_title = _cap_title(left)
        right_title = _cap_title(right)
        between = str(text or "")[end:right_start]
        pair = (capability_ref(left), capability_ref(right))
        if pair in seen or pair[0] == pair[1]:
            continue
        if is_write_capability(left) or is_write_capability(right):
            continue
        left_is_lookup = any(token in left_title for token in ("查询", "搜索", "筛选", "检索"))
        right_is_detail = any(token in right_title for token in ("详情", "详细"))
        connector_is_lookup = any(token in between for token in ("定位", "选定", "找到", "查到"))
        if left_is_lookup and right_is_detail and connector_is_lookup:
            seen.add(pair)
            found.append([left, right])
    return found


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
        # 「先查再问」描述的是人工交接策略，不是一个可调用的业务动作。
        if "先查再问" in raw:
            continue
        if any(token in raw for token in ("停下来", "停问", "问人", "人工交接", "让用户确认")):
            if len(_match_caps(raw, caps)) < 2:
                continue
        matched = False
        fallback_sequences: list[list[FlowCapability]] = []
        fallback_problems: list[str] = []
        for pattern in _ORDER_PATTERNS:
            match = pattern.search(raw)
            if not match:
                continue
            sequences, problems = _expand_order(match.group("left"), match.group("right"), caps)
            if sequences and not problems:
                found.extend(sequences)
                matched = True
                break
            if sequences and not fallback_sequences:
                fallback_sequences = sequences
            if problems and not fallback_problems:
                fallback_problems = problems
        if matched:
            continue
        if fallback_sequences:
            found.extend(fallback_sequences)
            continue
        if fallback_problems:
            unresolved.extend(fallback_problems)
            continue
        mention_orders = _orders_from_mentions(raw, caps)
        found.extend(mention_orders or _narrative_lookup_orders(raw, caps))
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
                independent=False,
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
            and not looks_like_ordered_multi_step(description, selected)
        ):
            add(_branch(
                branch_id="desc_independent",
                trigger=description,
                caps=mentioned,
                source="description",
                independent=True,
            ))
        if (
            looks_like_ordered_multi_step(description, selected)
            and not any(branch.unresolved or branch.conflicting for branch in branches)
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
                    independent=False,
                    target_given=_target_given(example),
                ))
            continue
        hits = _match_caps(example, selected)
        if looks_like_ordered_multi_step(example, selected) and len(hits) != 1:
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
