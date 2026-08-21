"""Handbook-quality metrics that do not depend on business-API discovery."""

from __future__ import annotations

from pathlib import Path

from dano.onboarding.skill_generation.catalog import is_write_capability
from dano.onboarding.skill_generation.intent import (
    description_has_explicit_sequence,
    looks_like_ordered_multi_step,
)
from dano.onboarding.skill_generation.models import SkillPlan, SkillRoute


_WRITE_HINTS = ("编辑", "修改", "审核", "反审", "删除", "新增", "提交", "写入", "审批")
_GENERIC_TOKENS = {
    "帮我", "请", "一下", "那个", "这个", "一条", "一张", "的", "了", "再", "先",
    "查询", "查看", "搜索", "筛选", "提交", "办理", "用户", "可以", "不要",
}
_STOP_CHARS = "，。！？、；：,!?;: "


def combo_pairs(plan: SkillPlan) -> set[tuple[str, ...]]:
    pairs: set[tuple[str, ...]] = set()
    for route in plan.routes:
        sequence = [str(item) for item in route.capability_sequence if str(item)]
        if len(sequence) < 2:
            continue
        if len(sequence) >= 3 and sequence[-1] == sequence[0]:
            sequence = sequence[:-1]
        pairs.add(tuple(sequence))
    return pairs


def silent_branch_drops(plan: SkillPlan) -> list[str]:
    missing: list[str] = []
    for branch in plan.intent_branches:
        if branch.unresolved or branch.conflicting or not branch.capability_sequence:
            continue
        covered = any(
            list(route.capability_sequence)[: len(branch.capability_sequence)] == list(branch.capability_sequence)
            for route in plan.routes
        )
        if not covered:
            missing.append(branch.trigger or branch.branch_id)
    return missing


def silent_sequence_drop(plan: SkillPlan, spec=None) -> bool:  # noqa: ANN001
    """True when explicit order language was neither compiled nor clarified."""
    texts = [plan.summary, *plan.trigger_phrases]
    if spec is not None:
        ordered = any(
            looks_like_ordered_multi_step(str(text), list(spec.capabilities or []))
            for text in texts
            if text
        )
    else:
        ordered = any(description_has_explicit_sequence(str(text)) for text in texts if text)
    if not ordered:
        return False
    has_combo = any(len(route.capability_sequence) > 1 for route in plan.routes)
    has_clarify = bool(plan.clarification_questions) or any(
        branch.unresolved or branch.conflicting for branch in plan.intent_branches
    )
    return not has_combo and not has_clarify


def match_routes(plan: SkillPlan, utterance: str) -> list[SkillRoute]:
    text = str(utterance or "").strip()
    wants_write = any(token in text for token in _WRITE_HINTS)
    scored: list[tuple[int, SkillRoute]] = []
    for route in plan.routes:
        haystack = " ".join(
            [
                route.name,
                route.when_to_use,
                *(example.user_request for example in route.examples),
            ]
        )
        score = sum(1 for token in _tokens(text) if token and token in haystack)
        if any(example.user_request == text for example in route.examples):
            score += 8
        for key, extras in (
            ("编辑", ("编辑", "修改", "更新")),
            ("修改", ("编辑", "修改", "更新")),
            ("审核", ("审核", "审批")),
            ("审批", ("审核", "审批")),
            ("反审", ("反审", "反审核")),
            ("删除", ("删除",)),
            ("新增", ("新增", "新建", "创建")),
            ("查出", ("搜索", "筛选", "查询", "查出")),
        ):
            if key == "审核" and "反审" in text:
                continue
            if key in text and any(item in haystack for item in extras):
                score += 3
        writes = bool(route.requires_confirmation) or len(route.capability_sequence) > 1
        if wants_write:
            score += 4 if writes else -3
        else:
            score += 4 if (not route.requires_confirmation and len(route.capability_sequence) == 1) else -2
        score += _route_affinity(text, haystack)
        if score > 0:
            scored.append((score, route))
    scored.sort(key=lambda item: (-item[0], item[1].route_id))
    return [route for _score, route in scored]


def skill_hit(plan: SkillPlan, utterance: str) -> bool:
    """Whether this Skill is the right handbook for the user's original words."""
    text = str(utterance or "").strip()
    if not text:
        return False
    haystack = f"{plan.summary} {plan.composition_summary} {' '.join(plan.trigger_phrases)}"
    if text in haystack:
        return True
    distinctive = _distinctive_tokens(text)
    return bool(distinctive) and any(token in haystack for token in distinctive)


def unnecessary_asks(route: SkillRoute) -> list[str]:
    asked: list[str] = []
    bound = {binding.to_input for binding in route.bindings if binding.to_input}
    if not route.requires_confirmation:
        for step in route.steps:
            if step.confirm_before_execute:
                asked.append(step.step_key)
    for step in route.steps:
        for source in step.input_sources:
            if str(source.source) == "user" and source.field in bound:
                asked.append(source.field)
    return asked


def should_stop_but_continued(route: SkillRoute, *, write_ids: set[str]) -> list[str]:
    issues: list[str] = []
    if len(route.capability_sequence) < 2 or route.bindings:
        return issues
    if any(cap_id in write_ids for cap_id in route.capability_sequence[1:]):
        if not route.checkpoints and not any(step.checkpoint for step in route.steps):
            issues.append(route.route_id)
    return issues


def tool_invocation_ready(route: SkillRoute) -> list[str]:
    """Generation-time proxy for tool-call success: every step is executable.

    Live tool-success rate needs runtime telemetry and is not scored here.
    """
    issues: list[str] = []
    if not route.steps:
        issues.append(f"{route.route_id}:missing_steps")
        return issues
    for step in route.steps:
        if not step.capability_id:
            issues.append(f"{route.route_id}:{step.step_key}:missing_capability")
        if not str(step.done_when or "").strip():
            issues.append(f"{route.route_id}:{step.step_key}:missing_done_when")
    if route.requires_confirmation and not any(step.confirm_before_execute for step in route.steps):
        issues.append(f"{route.route_id}:missing_write_confirm")
    return issues


def human_correction_ready(route: SkillRoute) -> list[str]:
    """Generation-time proxy for human-correction coverage.

    Live human-correction rate needs runtime telemetry and is not scored here.
    """
    issues: list[str] = []
    write_combo = len(route.capability_sequence) > 1 and route.requires_confirmation
    if write_combo and not route.bindings:
        if not route.checkpoints and not any(step.checkpoint for step in route.steps):
            issues.append(f"{route.route_id}:missing_handoff")
    return issues


def context_load_cost(root: Path, route_id: str) -> dict[str, int]:
    handbook = (root / "SKILL.md").read_text(encoding="utf-8")
    route_path = root / "references" / "routes" / f"{route_id}.md"
    route_text = route_path.read_text(encoding="utf-8") if route_path.is_file() else ""
    all_routes = 0
    routes_dir = root / "references" / "routes"
    if routes_dir.is_dir():
        all_routes = sum(path.stat().st_size for path in routes_dir.glob("*.md"))
    return {
        "handbook_chars": len(handbook),
        "route_chars": len(route_text),
        "all_route_chars": all_routes,
    }


def write_capability_ids(spec) -> set[str]:  # noqa: ANN001
    return {
        str(cap.capability_id or cap.name)
        for cap in spec.capabilities
        if is_write_capability(cap)
    }


def _route_affinity(text: str, haystack: str) -> int:
    """Prefer the route whose business title matches the user's words. No capability IDs."""
    detail_ask = any(token in text for token in ("详情", "看看", "只看"))
    search_ask = any(token in text for token in ("查出", "搜索", "筛选", "查订单")) or (
        "查" in text and not detail_ask
    )
    if detail_ask:
        if any(token in haystack for token in ("详情", "查看")):
            return 5
        if any(token in haystack for token in ("搜索", "筛选")):
            return -2
    if search_ask:
        if any(token in haystack for token in ("搜索", "筛选", "查询")) and "详情" not in haystack:
            return 5
        if "详情" in haystack:
            return -2
    return 0


def _tokens(text: str) -> list[str]:
    raw = str(text or "")
    return [item for item in (raw, *raw.replace("，", " ").replace("。", " ").split()) if item]


def _distinctive_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    current = []
    for char in str(text or ""):
        if char in _STOP_CHARS:
            if current:
                tokens.add("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        tokens.add("".join(current))
    for item in _tokens(text):
        tokens.add(item)
    return {
        token
        for token in tokens
        if len(token) >= 2 and token not in _GENERIC_TOKENS and token not in _WRITE_HINTS
    }
