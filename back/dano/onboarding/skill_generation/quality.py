"""Handbook-quality metrics that do not depend on business-API discovery."""

from __future__ import annotations

from pathlib import Path

from dano.onboarding.skill_generation.catalog import is_write_capability
from dano.onboarding.skill_generation.models import SkillPlan, SkillRoute


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


_WRITE_HINTS = ("编辑", "修改", "审核", "反审", "删除", "新增", "提交", "写入")


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
            score += 5
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
        writes = len(route.capability_sequence) > 1 or any(
            cap_id not in {"cap_search", "cap_detail", "cap_query", "cap_option"}
            for cap_id in route.capability_sequence
        )
        if wants_write:
            score += 4 if writes else -3
        else:
            score += 4 if (not writes and len(route.capability_sequence) == 1) else -2
            if ("详情" in text or "只看" in text or "看看" in text) and route.capability_sequence[:1] == ["cap_detail"]:
                score += 3
            elif "查" in text and route.capability_sequence[:1] in (["cap_search"], ["cap_query"]):
                score += 3
        if any(token in text for token in ("查出", "搜索", "筛选", "查订单")) and route.capability_sequence[:1] == ["cap_search"]:
            score += 4
        if any(token in text for token in ("详情", "看看")) and route.capability_sequence[:1] == ["cap_detail"]:
            score += 4
        if score > 0:
            scored.append((score, route))
    scored.sort(key=lambda item: (-item[0], item[1].route_id))
    return [route for _score, route in scored]


def skill_hit(plan: SkillPlan, utterance: str, object_tokens: tuple[str, ...]) -> bool:
    del utterance
    skill_text = f"{plan.summary} {' '.join(plan.trigger_phrases)}"
    return any(token in skill_text for token in object_tokens)


def unnecessary_asks(route: SkillRoute) -> list[str]:
    asked: list[str] = []
    bound = {binding.to_input for binding in route.bindings if binding.to_input}
    for step in route.steps:
        if step.confirm_before_execute and source_is_read(step.capability_id, route):
            asked.append(step.step_key)
        for source in step.input_sources:
            if str(source.source) == "user" and source.field in bound:
                asked.append(source.field)
    return asked


def source_is_read(capability_id: str, route: SkillRoute) -> bool:
    del route
    return capability_id in {"cap_search", "cap_detail", "cap_query", "cap_option"}


def should_stop_but_continued(route: SkillRoute, *, write_ids: set[str]) -> list[str]:
    issues: list[str] = []
    if len(route.capability_sequence) < 2 or route.bindings:
        return issues
    if any(cap_id in write_ids for cap_id in route.capability_sequence[1:]):
        if not route.checkpoints and not any(step.checkpoint for step in route.steps):
            issues.append(route.route_id)
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


def _tokens(text: str) -> list[str]:
    raw = str(text or "")
    return [item for item in (raw, *raw.replace("，", " ").replace("。", " ").split()) if item]
