"""Render published page recordings as self-contained, direct-API skill packages."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import structlog

from dano.export.skill_package.validator import (
    flow_spec_unverified_capability_names,
    flow_spec_verification_ids,
    validate_skill_documents,
    validate_skill_package,
)


log = structlog.get_logger(__name__)
_SECRET_KEY_RE = re.compile(r"(?i)(authorization|cookie|token|secret|password|session|credential)")
_INLINE_SECRET_RE = re.compile(
    r"(?i)\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/-]{8,}|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)


def _slug(value: str) -> str:
    raw = str(value or "skill")
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", raw.casefold().replace(".", "-").replace("_", "-"))).strip("-")
    if not slug or slug in {"skill", "dano"}:
        slug = "skill-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return slug[:80]


def package_slug(skill_id: str) -> str:
    """Use a stable suffix so package and proxy exports can coexist."""
    return f"dano-{_slug(skill_id)}-package"


def _script_slug(value: str) -> str:
    raw = str(value or "capability")
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", raw.casefold().replace("-", "_"))).strip("_")
    return slug or "capability_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _scrub(node: Any, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "<runtime-auth>"
    if isinstance(node, dict):
        return {str(k): _scrub(v, str(k)) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(value, key) for value in node]
    if isinstance(node, str):
        return _INLINE_SECRET_RE.sub("<runtime-auth>", node)
    return node


def _safe_text(value: Any) -> str:
    return str(_scrub(str(value or ""))).replace("\r", " ").strip()


def _flow_spec(skill):  # noqa: ANN001, ANN202
    release = dict((skill.api_request or {}).get("_release_snapshot") or {})
    raw = release.get("flow_spec")
    if not isinstance(raw, dict) or not raw.get("steps"):
        return None
    from dano.execution.page.flow_spec import FlowSpec

    try:
        spec = FlowSpec.model_validate(raw)
        trusted_write_ids = {
            str(item.get("verification_id"))
            for item in ((raw.get("meta") or {}).get("verification_log") or [])
            if isinstance(item, dict)
            and item.get("kind") == "write_execute"
            and item.get("status") == "passed"
            and item.get("verification_id")
        }
        # Legacy model normalization may prune a fact_check that was bound by
        # the executor after recording. Restore only evidence present in the
        # frozen executor-owned verification log; never trust an orphaned id.
        raw_steps = {
            str(step.get("step_id") or ""): step
            for step in raw.get("steps") or []
            if isinstance(step, dict)
        }
        for step in spec.steps:
            fact_check = (raw_steps.get(step.step_id) or {}).get("fact_check") or {}
            if (
                fact_check.get("verified") is True
                and str(fact_check.get("verification_id") or "") in trusted_write_ids
            ):
                step.fact_check = dict(fact_check)
        return spec
    except Exception as exc:  # noqa: BLE001 - legacy assets still render from api_request
        log.warning("export.package_flow_spec_invalid", skill_id=skill.skill_id, error=str(exc))
        return None


def _compiled_request(skill, spec) -> dict:  # noqa: ANN001
    if spec is not None:
        from dano.execution.page.flow_spec import flow_spec_to_api_request

        compiled, errors = flow_spec_to_api_request(spec)
        if compiled is not None and not errors:
            raw = dict(((skill.api_request or {}).get("_release_snapshot") or {}).get("flow_spec") or {})
            trusted_write_ids = {
                str(item.get("verification_id"))
                for item in ((raw.get("meta") or {}).get("verification_log") or [])
                if isinstance(item, dict)
                and item.get("kind") == "write_execute"
                and item.get("status") == "passed"
                and item.get("verification_id")
            }
            raw_steps = {
                str(step.get("step_id") or ""): step
                for step in raw.get("steps") or []
                if isinstance(step, dict)
            }
            compiled_steps = compiled.get("steps") if isinstance(compiled.get("steps"), list) else [compiled]
            for step in compiled_steps:
                if not isinstance(step, dict):
                    continue
                fact_check = (raw_steps.get(str(step.get("step_id") or "")) or {}).get("fact_check") or {}
                if (
                    fact_check.get("verified") is True
                    and str(fact_check.get("verification_id") or "") in trusted_write_ids
                ):
                    step["fact_check"] = dict(fact_check)
            return compiled
    return dict(skill.api_request or {})


def _steps(api_request: dict) -> list[dict]:
    if isinstance(api_request.get("steps"), list):
        return [dict(step) for step in api_request["steps"] if isinstance(step, dict)]
    if api_request.get("method"):
        return [dict(api_request)]
    return []


def _capabilities(skill, spec, api_request: dict) -> list[dict]:  # noqa: ANN001
    raw = (
        [cap.model_dump(mode="json", exclude_none=True) for cap in spec.capabilities]
        if spec is not None and spec.capabilities
        else list(api_request.get("capabilities") or skill.capabilities or [])
    )
    out = [dict(cap) for cap in raw if isinstance(cap, dict)]
    if out:
        return out
    params = list(api_request.get("params") or [])
    field_types = dict(api_request.get("field_types") or {})
    return [{
        "name": skill.action,
        "title": skill.title or skill.action,
        "kind": "query" if all((step.get("method") or "GET").upper() in {"GET", "HEAD"} for step in _steps(api_request)) else "submit",
        "step_ids": [str(step.get("step_id") or "") for step in _steps(api_request)],
        "input_schema": {
            "type": "object",
            "properties": {name: {"type": field_types.get(name, "string")} for name in params},
            "required": list(skill.required_fields or []),
        },
        "output_schema": {"type": "object"},
    }]


def _base_url(steps: list[dict]) -> str:
    for step in steps:
        parsed = urlparse(str(step.get("url") or ""))
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _safe_step(step: dict) -> dict:
    keep = {
        "step_id", "step_name", "method", "url", "url_template", "path",
        "content_type", "body_template", "query_template", "params", "success_rule",
        "sample_inputs", "field_types", "wire_formats", "runtime_fields",
        "selects", "system_values", "fact_check",
    }
    projected = {key: step.get(key) for key in keep if step.get(key) is not None}
    projected["selects"] = [
        {
            key: item.get(key)
            for key in ("param", "path", "option_map", "multi", "element_template", "field_projections")
            if item.get(key) is not None
        }
        for item in step.get("selects") or [] if isinstance(item, dict)
    ]
    return _scrub(projected)


def _verified_links(spec, step_ids: list[str]) -> list[dict]:  # noqa: ANN001
    if spec is None:
        return []
    allowed = set(step_ids)
    positions = {step_id: index for index, step_id in enumerate(step_ids)}
    links: list[dict] = []
    for link in spec.links:
        verification_id = str((link.meta or {}).get("verification_id") or "")
        if (
            (link.meta or {}).get("verified") is not True
            or not verification_id
            or link.source_step_id not in allowed
            or link.target_step_id not in allowed
            or positions[link.source_step_id] >= positions[link.target_step_id]
        ):
            continue
        declared_kind = str(link.kind or "")
        legacy_kind = str((link.meta or {}).get("kind") or "")
        link_kind = legacy_kind if legacy_kind and declared_kind in {"", "value"} else declared_kind or legacy_kind or "value"
        links.append({
            "link_id": link.link_id,
            "kind": link_kind,
            "source_step": positions[link.source_step_id],
            "source_path": link.source_path,
            "target_step": positions[link.target_step_id],
            "target_path": link.target_path,
            "param_name": link.param_name or "",
            "verification_id": verification_id,
            "source_collection_path": link.source_collection_path,
            "source_key_path": link.source_key_path,
            "source_label_path": link.source_label_path,
            "target_container_path": link.target_container_path,
            "value_binding": dict(link.value_binding or {}),
        })
    return links


def _capability_plans(skill, spec, api_request: dict) -> list[dict]:  # noqa: ANN001
    all_steps = _steps(api_request)
    by_id = {str(step.get("step_id") or f"step-{index}"): step for index, step in enumerate(all_steps)}
    plans: list[dict] = []
    used_scripts: set[str] = set()
    trusted_ids = flow_spec_verification_ids(spec) if spec is not None else set()
    for index, cap in enumerate(_capabilities(skill, spec, api_request), 1):
        name = str(cap.get("name") or cap.get("capability_id") or f"capability_{index}")
        script = _script_slug(name)
        if script in used_scripts:
            script += "_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
        used_scripts.add(script)
        step_ids = [
            str(value) for value in (
                cap.get("compiled_step_ids") or cap.get("step_ids") or []
            ) if str(value) in by_id
        ]
        if not step_ids:
            step_ids = list(by_id)
        cap_steps = [_safe_step(by_id[step_id]) for step_id in step_ids]
        is_write = any(
            str(step.get("method") or "GET").upper() not in {"GET", "HEAD"}
            for step in cap_steps
        )
        raw_risk = getattr(skill, "risk_level", "")
        risk = str(raw_risk.value if hasattr(raw_risk, "value") else raw_risk or "").upper()
        fact_checks = []
        for step in cap_steps:
            fact_check = step.get("fact_check")
            if (
                isinstance(fact_check, dict)
                and fact_check.get("verified") is True
                and str(fact_check.get("verification_id") or "") in trusted_ids
            ):
                fact_checks.append({"step_id": step.get("step_id"), **_scrub(fact_check)})
        plans.append({
            "name": name,
            "title": str(cap.get("title") or name),
            "kind": str(cap.get("kind") or "operation"),
            "script": script,
            "input_schema": dict(cap.get("input_schema") or cap.get("parameters") or {"type": "object", "properties": {}}),
            "output_schema": dict(cap.get("output_schema") or {"type": "object"}),
            "preconditions": list(cap.get("preconditions") or []),
            "caller_responsibilities": list(cap.get("caller_responsibilities") or []),
            "skill_responsibilities": list(cap.get("skill_responsibilities") or []),
            "steps": cap_steps,
            "links": _verified_links(spec, step_ids),
            "fact_checks": fact_checks,
            "requires_confirmation": bool(
                is_write
                and (cap.get("requires_human_confirm") is True or risk in {"L3", "L4", "L5"})
            ),
            "requires_verify": is_write,
        })
    return plans


def _evidence_for_plan(plan: dict, spec) -> list[str]:  # noqa: ANN001
    ids = [str(link.get("verification_id") or "") for link in plan["links"]]
    ids.extend(
        str(item.get("verification_id") or "")
        for item in plan["fact_checks"]
        if item.get("verified") is True
    )
    return list(dict.fromkeys(value for value in ids if value))


def _fallback_skill_md(skill, slug: str, plans: list[dict], spec) -> str:  # noqa: ANN001
    description = _safe_text(skill.title or skill.action or skill.skill_id)
    lines = [
        "---", f"name: {slug}", f"description: {json.dumps(description, ensure_ascii=False)}", "---", "",
        f"# {description}", "",
        "这是录制后发布的自包含 Skill。它保留既有 Skill 的能力选择、一次性收参、字段校验、写前确认、执行后验证和结果处理规则；业务请求由包内脚本直接调用目标系统。", "",
        "## Transport", "",
        "- 使用 `references/CONTRACT.json` 选择 capability，并调用其中声明的 `scripts/*.py`。",
        "- 运行期需要 Python 与 `httpx`；业务执行不依赖 Dano 运行时或 LLM。",
        "- 鉴权只从运行期 `DANO_AUTH_HEADERS`、本地会话缓存或已配置的 Dano token fallback 获取；不得把凭证写进 Skill、参数、回复或日志。", "",
        "## Preconditions", "",
        "- `references/CONTRACT.json` 是能力、字段名、类型、必填性、枚举和输出结构的唯一机器契约；不得按录制样例或字段外观猜值。",
        "- 只执行契约中列出的 capability；用户目标与业务对象或动作不一致时停止，不得用相近能力代替。",
        "- 写能力必须在执行前取得用户明确确认；查询能力只带用户明确要求的可选筛选条件。", "",
        "## Steps", "",
        "1. 根据用户目标选择一个明确的 capability；查询和写入是不同能力，禁止默认选择写能力。",
        "   Done when: 已选 capability 的业务对象和动作与用户目标完全一致。",
        "2. 读取 `references/CONTRACT.json` 中该 capability 的 `input_schema`、脚本路径、`requires_confirmation` 和 `requires_verify`；需要人读说明时同时读取 `references/CAPABILITIES.md`，选择项读取 `references/OPTIONS.md`。",
        "   Done when: 已确定全部调用方字段、必填字段、类型、枚举、默认值和内部字段，且没有使用录制样例补空值。",
        "3. 一次性收集本次需要的调用方字段。使用一次 `ask_user_question`，顶层传 `title` 与 `questions[]`；每个 `questions[].id` 必须与 `input_schema.properties` 的字段名逐字一致。写能力收集全部必填字段；查询能力只收集必填字段和用户明确指定的可选筛选条件。",
        "   Done when: 返回 `status=answered`，答案已按字段 id 映射，或返回 `cancelled` 并立即停止。",
        "4. 按 schema 校验 required、type、format、enum、pattern 和边界；日期时间、数字、数组与对象按声明转换。内部字段、常量、上游响应和计算字段不得放进 `questions[]`，不得让用户猜内部 ID。",
        "   Done when: 输入完整且逐字段满足契约；任何不确定值均未被猜测或静默替换。",
        "5. 若 `requires_confirmation=true`，使用 `ask_user_question({confirm: true, formIds: [<answered.formId>]})` 对完整输入只确认一次；只有返回 `status=confirmed` 才能继续，并在执行脚本时带 `--confirm`。",
        "   Done when: 写能力已有有效确认，或当前能力不需要确认。",
        "6. 把输入作为 JSON 对象传给对应脚本：`python scripts/<capability>.py --input-json '<JSON>'`；需要确认时追加 `--confirm`。同一写请求不得并发、不得在结果不明时自动重试。",
        "   Done when: stdout 最后一行是 JSON，且 `status=succeeded`、`ok=true`；否则按 Branch exit 停止。",
        "7. 若 `requires_verify=true`，用完全相同的输入调用 `verify_script`；写操作只有执行和验证都 `ok=true` 才能报告成功。",
        "   Done when: 验证脚本返回 `ok=true`，或只读能力明确不需要验证。",
        "8. 按 `output_schema` 解读结果。数组用 Markdown 表格展示；不得把内部 ID、裸 `data` 或未声明字段擅自命名为业务编号。",
        "   Done when: 最终回复准确区分成功、取消、待确认和失败，且未泄露凭证或内部字段。", "",
        "## Capability summary", "",
    ]
    for plan in plans:
        schema = plan.get("input_schema") or {}
        required = ", ".join(f"`{name}`" for name in schema.get("required") or []) or "无"
        lines.append(
            f"- **{_safe_text(plan['title'])}** (`{plan['name']}`): "
            f"脚本 `scripts/{plan['script']}.py`；必填 {required}；"
            f"写前确认 {'是' if plan['requires_confirmation'] else '否'}；"
            f"执行后验证 {'是' if plan['requires_verify'] else '否'}。"
        )
    lines.extend([
        "", "## Result contract", "",
        "- `succeeded`: 能力执行成功；写能力还必须通过 `verify_script` 后才能向用户报告完成。",
        "- `need_confirm`: 写能力尚未确认；取得确认后带 `--confirm` 重跑，禁止绕过。",
        "- `failed`: 停止并报告 `reason`/失败步骤；写操作遇到超时、HTTP 5xx 或结果不明时禁止重复提交，先用只读能力核查。",
        "- `cancelled`: 用户取消，立即停止。", "",
        "## Branch exit", "",
        "- capability 不匹配、字段缺失或校验失败：停止，补齐或修正后再执行。",
        "- `ask_user_question` 返回 `cancelled`：立即停止。",
        "- 写能力未确认或脚本返回 `need_confirm`：不得执行，取得有效确认后才可带 `--confirm`。",
        "- 任一请求、执行脚本或验证脚本返回 `ok=false`：立即停止；不得宣称完成。",
        "- 写结果不明：不得自动重试同一载荷，先用已发布只读能力核查；无法核实时报告不确定。", "",
        "## Pitfalls", "",
        "- 不得把录制样例值当作调用方默认值、固定业务值或本次用户输入。",
        "- 不得翻译、改名或猜测参数名；`questions[].id` 和提交 JSON 的键必须与契约逐字一致。",
        "- 不得向用户询问常量、会话头、分页上下文、上游响应、计算值或动态结构键。",
        "- 不得跳过写前确认或写后验证，也不得因一个写请求失败而自动重试。",
        "- 不得复用录制凭证、内部 ID 或动态流程节点标识。",
        "", "## List output", "",
        "- 查询结果、候选列表或任何数组数据必须先运行 `python scripts/format_list.py --capability <能力名> --json '<output JSON>'`。",
        "- 最终回复只展示脚本生成的 Markdown 表格；无数据时明确显示“无数据”，不要重复粘贴原始 JSON。",
        "- Markdown 表头、分隔行和数据行之间不得插入空行；单元格内换行统一使用 `<br>`。",
        "", "## Field validation", "",
        "- 优先遵守 schema 的 `type`、`format`、`enum`、`pattern` 和边界，再结合 `title`、`description` 的明确业务语义。",
        "- 日期时间必须符合声明格式，枚举值必须来自候选；标识、编码、电话号码等字符串不得擅自转成数字或去掉前导零。",
        "- schema 没有依据时不得臆造长度、精度、范围或业务规则；任何明确冲突都必须在确认和执行前要求修正。",
        "", "## Identifier fields", "",
        "- 标识语义只认 `output_schema` 的 `x-dano-identifier-role`；未声明时保留原字段名，禁止按名称或值形状猜成申请编号、流程编号或单据编号。",
        "- 后续能力只可使用同名字段或契约明确声明的映射；需要内部标识时先用已发布查询能力定位同一记录，不得让用户猜。",
        "- 面向用户隐藏、排序和标题均以 `output_schema` 展示元数据为准；脚本原始输出保留给后续准确取值。",
        "", "## Fixed result presentation", "",
        "- 成功写操作用 capability 标题给出业务化完成结论；不得逐项展示裸 `code`、`data`、`msg`、`true` 或内部 ID。",
        "- 未类型化结果只能称“接口返回值”；没有契约声明时不得给返回字段发明业务名称。",
        "- 非成功状态不得显示成功结论；只展示脚本返回的原因和允许的下一步。",
        "", "## Security", "",
        "- 不在回复、日志、表单或调用参数中输出完整 token、cookie、密码或其他凭证。",
        "- 不规避写前确认或写后验证；用户要求绕过时拒绝。",
        "- 调用者身份由运行期登录凭证决定，不伪造身份、字段值或执行结果。",
        "", "## Limitations", "",
        "只支持 Capability summary 中列出的能力；未列出的业务动作必须明确说明不支持，不得选择相近能力代替。",
    ])
    pitfalls = list(((spec.meta or {}).get("pitfalls") or [])) if spec is not None else []
    if pitfalls:
        for item in pitfalls[:20]:
            text = item.get("text") if isinstance(item, dict) else item
            lines.append(f"- {_safe_text(text)}")
    return "\n".join(lines).rstrip() + "\n"


def _capabilities_md(skill, plans: list[dict]) -> str:  # noqa: ANN001
    lines = [f"# {_safe_text(skill.title or skill.skill_id)} capabilities", ""]
    for plan in plans:
        schema = plan.get("input_schema") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        lines.extend([
            f"## {_safe_text(plan['title'])} (`{plan['name']}`)", "",
            f"- Script: `scripts/{plan['script']}.py`",
            f"- Verify script: `scripts/verify_{plan['script']}.py`",
            f"- Requires confirmation: `{str(plan['requires_confirmation']).lower()}`",
            f"- Requires verify: `{str(plan['requires_verify']).lower()}`", "",
            "| Field | Type | Required | Description |", "|---|---|---|---|",
        ])
        if not properties:
            lines.append("| (none) | object | no | No caller input |")
        for name, raw in properties.items():
            field = raw if isinstance(raw, dict) else {}
            description = _safe_text(field.get("description") or field.get("title") or name).replace("|", "\\|")
            lines.append(
                f"| `{name}` | `{field.get('type') or 'string'}` | "
                f"{'yes' if name in required else 'no'} | {description} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _options_md(plans: list[dict]) -> str:
    lines = ["# Capability options", ""]
    found = False
    for plan in plans:
        for name, raw in ((plan.get("input_schema") or {}).get("properties") or {}).items():
            field = raw if isinstance(raw, dict) else {}
            values = list(field.get("enum") or [])
            labels = dict(field.get("x-enum-value-map") or {})
            if not values and not labels:
                continue
            found = True
            lines.extend([f"## `{plan['name']}.{name}`", "", "| Label | Value |", "|---|---|"])
            if labels:
                lines.extend(f"| {_safe_text(label)} | `{value}` |" for label, value in labels.items())
            else:
                lines.extend(f"| `{value}` | `{value}` |" for value in values)
            lines.append("")
    if not found:
        lines.append("No static enum options are declared. Do not invent candidates.")
    return "\n".join(lines).rstrip() + "\n"


def _format_list_py(plans: list[dict]) -> str:
    schemas = {
        str(plan["name"]): dict(plan.get("output_schema") or {})
        for plan in plans
    }
    return f'''from __future__ import annotations

import argparse
import json
import sys

SCHEMAS = json.loads({json.dumps(json.dumps(schemas, ensure_ascii=False), ensure_ascii=False)})


def list_rows(value):
    if isinstance(value, dict) and "output" in value:
        return list_rows(value["output"])
    if isinstance(value, dict):
        for key in ("records", "rows", "items", "list"):
            if isinstance(value.get(key), list):
                return value[key]
        if isinstance(value.get("data"), (dict, list)):
            return list_rows(value["data"])
        return [value]
    return value if isinstance(value, list) else [value]


def row_schema(schema):
    properties = (schema or {{}}).get("properties") or {{}}
    for field in properties.values():
        if isinstance(field, dict) and field.get("type") == "array":
            return field.get("items") or {{}}
    return schema or {{}}


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).replace("|", r"\\|").replace("\\r", " ").replace("\\n", "<br>")


def main():
    parser = argparse.ArgumentParser(description="format capability output as Markdown table")
    parser.add_argument("--json")
    parser.add_argument("--capability", required=True, choices=sorted(SCHEMAS))
    args = parser.parse_args()
    raw = args.json if args.json is not None else sys.stdin.read()
    value = json.loads(raw)
    rows = list_rows(value)
    if not rows:
        print("无数据")
        return
    rows = [row if isinstance(row, dict) else {{"值": row}} for row in rows]
    properties = (row_schema(SCHEMAS[args.capability]).get("properties") or {{}})
    columns = []
    for row in rows:
        for key in row:
            field = properties.get(key) or {{}}
            if field.get("x-dano-display") is False or field.get("x-dano-internal") is True:
                continue
            if key not in columns:
                columns.append(key)
    if not columns:
        print("无数据")
        return
    labels = [str((properties.get(key) or {{}}).get("title") or key) for key in columns]
    print("| " + " | ".join(cell(label) for label in labels) + " |")
    print("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        print("| " + " | ".join(cell(row.get(key)) for key in columns) + " |")


if __name__ == "__main__":
    main()
'''


def _fallback_reference_md(skill, plans: list[dict], spec) -> str:  # noqa: ANN001
    lines = [f"# {_safe_text(skill.title or skill.skill_id)} reference", "", "## API chain", ""]
    for plan in plans:
        chain = " -> ".join(
            f"{str(step.get('method') or 'GET').upper()} {step.get('path') or urlparse(str(step.get('url') or '')).path or '/'}"
            for step in plan["steps"]
        ) or "GET /"
        evidence = _evidence_for_plan(plan, spec)
        markers = [f"verification_id: {value}" for value in evidence]
        if plan["requires_verify"] and not plan["fact_checks"]:
            markers.append("unverified write read-back")
        marker = "; ".join(markers) if markers else "unverified"
        lines.append(f"- `{plan['name']}`: {chain}; {marker}")
    lines.extend(["", "## Business hard rules", ""])
    forbidden = list(((spec.goal or {}).get("forbidden_actions") or [])) if spec is not None else []
    if forbidden:
        lines.extend(f"- {_safe_text(value)}" for value in forbidden)
    else:
        lines.append("- Execute only the selected capability and stop on the first failed request.")
    lines.extend(["", "## Fallback browser steps", ""])
    events = list((spec.request_facts.page_events or [])) if spec is not None else []
    semantic = []
    for event in events[-50:]:
        if not isinstance(event, dict):
            continue
        kind = _safe_text(event.get("kind") or event.get("op") or event.get("type"))
        role = _safe_text(event.get("role"))
        name = _safe_text(event.get("name") or event.get("label") or event.get("text"))
        if kind or role or name:
            semantic.append(" / ".join(value for value in (kind, role, name) if value))
    if semantic:
        lines.extend(f"{index}. {value}" for index, value in enumerate(semantic[:20], 1))
    else:
        lines.append("1. Open the recorded business page and follow the visible role/name labels matching the capability; do not use coordinates.")
    return "\n".join(lines).rstrip() + "\n"


_CLIENT_TEMPLATE = r'''from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin
from uuid import uuid4

import httpx
from wire_format import apply_wire_formats, date_span_days

CONFIG = json.loads(__CONFIG__)
BASE_URL = os.environ.get("DANO_BUSINESS_BASE_URL", CONFIG["base_url"]).rstrip("/")
_PLACEHOLDER = re.compile(r"^\{\{([^{}]+)\}\}$")


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def _json_object(raw, label):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return value


def _cache_headers():
    path = Path.home() / ".dano" / "sessions" / f"{CONFIG['tenant']}__{CONFIG['subsystem'].replace('/', '_')}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("headers"), dict) and data["headers"]:
        return data["headers"]
    headers = {}
    cookies = data.get("cookies") if isinstance(data, dict) else []
    pairs = [f"{item.get('name')}={item.get('value')}" for item in cookies or [] if item.get("name") and item.get("value")]
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    for origin in data.get("origins") or []:
        for item in origin.get("localStorage") or []:
            name = str(item.get("name") or "").casefold()
            value = str(item.get("value") or "").strip().strip('"')
            if value and any(hint in name for hint in ("access_token", "accesstoken", "auth_token", "authorization")):
                headers.setdefault("Authorization", value if value.lower().startswith("bearer ") else f"Bearer {value}")
    return headers


def auth_headers():
    raw = os.environ.get("DANO_AUTH_HEADERS")
    if raw:
        return _json_object(raw, "DANO_AUTH_HEADERS")
    cached = _cache_headers()
    if cached:
        return cached
    dano_url = os.environ.get("DANO_URL", "").rstrip("/")
    tenant_key = os.environ.get("DANO_TENANT_KEY", "")
    if dano_url and tenant_key:
        response = httpx.get(
            dano_url + "/v1/settings/token/raw",
            params={"tenant": CONFIG["tenant"], "subsystem": CONFIG["subsystem"]},
            headers={"X-Tenant-Key": tenant_key}, timeout=20,
        )
        response.raise_for_status()
        headers = response.json().get("headers") or {}
        if headers:
            return headers
    raise RuntimeError("authentication unavailable: set DANO_AUTH_HEADERS or configure a session/Dano token source")


def get_path(node, path):
    text = str(path or "").removeprefix("response.").removeprefix("$.")
    if text in {"", "$", "response"}:
        return node
    tokens = [token for token in re.split(r"\.|\[|\]", text) if token]
    current = node
    for token in tokens:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def deep_set(node, path, value):
    tokens = [token for token in re.split(r"\.|\[|\]", str(path or "")) if token]
    current = node
    for token in tokens[:-1]:
        if isinstance(current, list) and token.isdigit():
            while len(current) <= int(token):
                current.append({})
            current = current[int(token)]
        else:
            current = current.setdefault(token, {})
    if not tokens:
        return value
    last = tokens[-1]
    if isinstance(current, list) and last.isdigit():
        while len(current) <= int(last):
            current.append(None)
        current[int(last)] = value
    else:
        current[last] = value
    return node


def render(node, values):
    if isinstance(node, dict):
        return {key: render(value, values) for key, value in node.items()}
    if isinstance(node, list):
        return [render(value, values) for value in node]
    if not isinstance(node, str):
        return copy.deepcopy(node)
    match = _PLACEHOLDER.fullmatch(node)
    if match:
        key = match.group(1)
        if key not in values:
            raise RuntimeError(f"missing input: {key}")
        return copy.deepcopy(values[key])
    return re.sub(r"\{\{([^{}]+)\}\}", lambda match: str(values[match.group(1)]), node)


def _business_ok(data, rule):
    if isinstance(data, dict) and rule and rule.get("field") in data:
        return str(data[rule["field"]]) in {str(value) for value in rule.get("ok_values") or []}
    if isinstance(data, dict):
        for key in ("code", "status", "errcode", "errCode", "resultCode", "rspCode", "retCode", "flag"):
            if key in data and not isinstance(data[key], (dict, list)):
                return str(data[key]).casefold() in {"200", "0", "00000", "true", "success", "ok", "1"}
        if "success" in data:
            return bool(data["success"])
    return True


def http_json(method, path="", *, url="", query=None, body=None, content_type="application/json", success_rule=None):
    target = url or path
    if not str(target).startswith(("http://", "https://")):
        if not BASE_URL:
            raise RuntimeError("DANO_BUSINESS_BASE_URL is required because the recording has no absolute origin")
        target = urljoin(BASE_URL + "/", str(target).lstrip("/"))
    kwargs = {"params": query or None, "headers": auth_headers(), "timeout": 30}
    if body is not None:
        if "form-urlencoded" in str(content_type).casefold():
            kwargs["data"] = body
        else:
            kwargs["json"] = body
    response = httpx.request(str(method or "GET").upper(), target, **kwargs)
    try:
        data = response.json()
    except ValueError:
        data = response.text
    ok = response.is_success and _business_ok(data, success_rule)
    return {"ok": ok, "status": response.status_code, "data": data, "method": str(method).upper(), "url": str(response.url)}


def settle(seconds=0.25):
    time.sleep(max(0.0, min(float(seconds), 5.0)))


def _apply_selects(step, values):
    current = dict(values)
    for binding in step.get("selects") or []:
        param = str(binding.get("param") or "")
        if not param or param not in current:
            continue
        option_map = binding.get("option_map") or {}
        raw = current[param]
        if isinstance(raw, list):
            current[param] = [option_map.get(str(value), value) for value in raw]
        else:
            current[param] = option_map.get(str(raw), raw)
    return current


def _system_values(step, body):
    for item in step.get("system_values") or []:
        kind = str(item.get("kind") or "")
        value = (
            int(time.time() * 1000) if kind == "now_ms" else
            time.strftime("%Y-%m-%d") if kind == "now_date" else
            time.strftime("%Y-%m-%dT%H:%M:%S") if kind == "now_iso" else
            str(uuid4())
        )
        deep_set(body, item.get("path") or "", value)
    return body


def _runtime_values(step, inputs):
    values = dict(inputs)
    for field in step.get("runtime_fields") or []:
        name = str(field.get("name") or "")
        if not name or name in values:
            continue
        if str(field.get("kind") or "") != "date_span_days_json":
            continue
        start_name = str(field.get("start_field") or "")
        end_name = str(field.get("end_field") or "")
        if start_name not in values or end_name not in values:
            raise RuntimeError(f"computed field {name} is missing {start_name or end_name}")
        values[name] = json.dumps(
            {str(field.get("output_key") or "days"): date_span_days(values[start_name], values[end_name])},
            ensure_ascii=False, separators=(",", ":"),
        )
    return apply_wire_formats(values, step.get("wire_formats") or {})


def _response_key_map(link, source, body, values):
    collection = get_path(source, link.get("source_collection_path") or link.get("source_path"))
    binding = link.get("value_binding") or {}
    input_field = str(binding.get("input_field") or "")
    caller_map = values.get(input_field)
    if not isinstance(collection, list) or not collection:
        raise RuntimeError(f"dynamic structure source unavailable: {link.get('link_id')}")
    if binding.get("kind") != "caller_map_by_label" or not isinstance(caller_map, dict):
        raise RuntimeError(f"dynamic structure input {input_field} must be an object")
    rows = []
    for item in collection:
        key = get_path(item, link.get("source_key_path"))
        label = get_path(item, link.get("source_label_path"))
        if key in (None, "") or label in (None, ""):
            raise RuntimeError(f"dynamic structure node lacks id/name: {link.get('link_id')}")
        rows.append((str(key), str(label)))
    keys = [key for key, _label in rows]
    labels = [label for _key, label in rows]
    if len(set(keys)) != len(keys) or len(set(labels)) != len(labels):
        raise RuntimeError(f"dynamic structure node id/name is duplicated: {link.get('link_id')}")
    missing = [label for label in labels if label not in caller_map]
    extra = [label for label in caller_map if label not in set(labels)]
    if missing or extra:
        raise RuntimeError(f"dynamic structure labels changed: missing={missing!r}, extra={extra!r}")
    wrap = str(binding.get("value_shape") or "direct") == "single_item_list"
    rebuilt = {
        key: (value if isinstance(value, list) else [value]) if wrap else value
        for key, label in rows
        for value in [caller_map[label]]
    }
    return deep_set(body or {}, link.get("target_container_path") or link.get("target_path"), rebuilt)


def execute_plan(plan, inputs):
    outputs = []
    for index, step in enumerate(plan.get("steps") or []):
        values = _apply_selects(step, _runtime_values(step, inputs))
        body = render(step.get("body_template"), values) if step.get("body_template") is not None else None
        query = render(step.get("query_template"), values) if step.get("query_template") is not None else None
        url = render(step.get("url_template") or step.get("url") or step.get("path") or "", values)
        for link in plan.get("links") or []:
            if int(link.get("target_step", -1)) != index:
                continue
            source_index = int(link.get("source_step", -1))
            if source_index < 0 or source_index >= len(outputs):
                raise RuntimeError(f"verified dependency source unavailable: {link.get('link_id')}")
            if str(link.get("kind") or "") == "response_key_map":
                body = _response_key_map(link, outputs[source_index]["data"], body, values)
                continue
            value = get_path(outputs[source_index]["data"], link.get("source_path"))
            if value is None:
                raise RuntimeError(f"verified dependency value missing: {link.get('verification_id')}")
            target = str(link.get("target_path") or "")
            if target.startswith("query."):
                query = deep_set(query or {}, target[6:], value)
            elif target.startswith("path."):
                values[target[5:]] = value
                url = render(step.get("url_template") or step.get("url") or step.get("path") or "", values)
            else:
                body = deep_set(body or {}, target.removeprefix("body."), value)
        if isinstance(body, (dict, list)):
            body = _system_values(step, body)
        result = http_json(
            step.get("method") or "GET", step.get("path") or "", url=url,
            query=query, body=body, content_type=step.get("content_type") or "application/json",
            success_rule=step.get("success_rule"),
        )
        outputs.append(result)
        if not result["ok"]:
            return {"ok": False, "failed_step": step.get("step_id"), "results": outputs}
        if str(step.get("method") or "GET").upper() not in {"GET", "HEAD"}:
            settle()
    return {"ok": True, "results": outputs, "data": outputs[-1]["data"] if outputs else None}


def evaluate_assertion(response, assertion, inputs):
    actual = get_path(response, assertion.get("path") or assertion.get("response_path") or "")
    expected = assertion.get("equals", assertion.get("value"))
    input_path = assertion.get("equals_input") or assertion.get("input_path")
    if input_path:
        expected = get_path(inputs, input_path)
    operator = assertion.get("operator") or ("equals" if expected is not None else "truthy")
    if operator in {"equals", "eq"}:
        return actual == expected
    if operator in {"not_equals", "ne"}:
        return actual != expected
    if operator == "contains":
        return expected in actual if isinstance(actual, (str, list, tuple, set, dict)) else False
    if operator == "exists":
        return actual is not None
    return bool(actual)


def list_items(node):
    if isinstance(node, list):
        return node
    if not isinstance(node, dict):
        return []
    for key in ("list", "records", "rows", "items", "content"):
        if isinstance(node.get(key), list):
            return node[key]
    for key in ("data", "result", "payload"):
        nested = list_items(node.get(key))
        if nested:
            return nested
    return []


def main():
    parser = argparse.ArgumentParser(description="Self-contained business API client")
    parser.add_argument("--show-config", action="store_true")
    args = parser.parse_args()
    emit({"ok": True, "tenant": CONFIG["tenant"], "subsystem": CONFIG["subsystem"], "base_url_configured": bool(BASE_URL)} if args.show_config else {"ok": True})


if __name__ == "__main__":
    main()
'''


_CAPABILITY_TEMPLATE = r'''from __future__ import annotations

import argparse
import datetime
import json
import re
import sys

from client import emit, execute_plan

PLAN = json.loads(__PLAN__)


def _coerce(value, schema):
    kind = str((schema or {}).get("type") or "string")
    if kind in {"object", "array"}:
        return json.loads(value)
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    if kind == "boolean":
        lowered = str(value).casefold()
        if lowered not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("boolean must be true/false")
        return lowered in {"true", "1", "yes"}
    return value


def _validate(value, schema, path="input"):
    schema = schema or {}
    if "const" in schema and value != schema.get("const"):
        raise ValueError(f"{path} must equal {schema.get('const')!r}")
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if alternatives:
        matched = 0
        for alternative in alternatives:
            try:
                _validate(value, alternative, path)
                matched += 1
            except (TypeError, ValueError):
                pass
        if not matched or (schema.get("oneOf") and matched != 1):
            raise ValueError(f"{path} does not match its alternative schemas")
        return
    allowed = schema.get("enum")
    if allowed and value not in allowed:
        raise ValueError(f"{path} must be one of: {', '.join(map(str, allowed))}")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties") or {}
        missing = [
            name for name in schema.get("required") or []
            if name not in value or value[name] in (None, "")
        ]
        if missing:
            raise ValueError(f"{path} missing required: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in value if name not in properties)
            if extra:
                raise ValueError(f"{path} has undeclared fields: {', '.join(extra)}")
        for name, child in properties.items():
            if name in value and value[name] is not None:
                _validate(value[name], child, f"{path}.{name}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        if schema.get("minItems") is not None and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path} has too few items")
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path} has too many items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ValueError(f"{path} must not contain duplicate items")
        for index, item in enumerate(value):
            _validate(item, schema.get("items") or {}, f"{path}[{index}]")
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"{path} must be an integer")
    elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ValueError(f"{path} must be a number")
    elif expected == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    elif expected == "string" and not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path} is too short")
        if schema.get("maxLength") is not None and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path} is too long")
        if schema.get("pattern") and re.search(str(schema["pattern"]), value) is None:
            raise ValueError(f"{path} does not match its pattern")
        if schema.get("format") == "date":
            datetime.date.fromisoformat(value)
        elif schema.get("format") == "date-time":
            datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
        if schema.get("maximum") is not None and value > schema["maximum"]:
            raise ValueError(f"{path} is above maximum")
        if schema.get("exclusiveMinimum") is not None and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} must be greater than exclusiveMinimum")
        if schema.get("exclusiveMaximum") is not None and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"{path} must be less than exclusiveMaximum")


def parser():
    command = argparse.ArgumentParser(description=PLAN.get("title") or PLAN["name"])
    command.add_argument("--input-json", default="{}", help="JSON object merged before named arguments")
    command.add_argument("--confirm", action="store_true", help="confirm an explicitly reviewed write")
    for name, schema in (PLAN.get("input_schema", {}).get("properties") or {}).items():
        command.add_argument(f"--{name}", dest=name, help=str(schema.get("description") or schema.get("title") or name))
    return command


def inputs_from_args(args, command):
    try:
        values = json.loads(args.input_json)
        if not isinstance(values, dict):
            raise ValueError("--input-json must be an object")
        properties = PLAN.get("input_schema", {}).get("properties") or {}
        for name, schema in properties.items():
            raw = getattr(args, name, None)
            if raw is not None:
                values[name] = _coerce(raw, schema)
        missing = [name for name in PLAN.get("input_schema", {}).get("required") or [] if name not in values]
        if missing:
            command.error("missing required inputs: " + ", ".join(missing))
        _validate(values, PLAN.get("input_schema") or {"type": "object"})
        return values
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        command.error(str(exc))


def main():
    command = parser()
    args = command.parse_args()
    inputs = inputs_from_args(args, command)
    if PLAN.get("requires_confirmation") and not args.confirm:
        emit({
            "capability": PLAN["name"], "ok": False, "status": "need_confirm",
            "reason": "write capability requires explicit confirmation",
        })
        return 0
    result = execute_plan(PLAN, inputs)
    emit({
        "capability": PLAN["name"],
        "status": "succeeded" if result.get("ok") else "failed",
        **result,
    })
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
'''


_VERIFY_TEMPLATE = r'''from __future__ import annotations

import sys

from client import emit, evaluate_assertion, http_json, list_items, settle
from __CAP_MODULE__ import PLAN, inputs_from_args, parser


def verify(inputs):
    issues = []
    checks = PLAN.get("fact_checks") or []
    if PLAN.get("requires_verify") and not checks:
        issues.append({"step_id": None, "verification_id": "unverified", "reason": "no verified read-back is available"})
    for check in checks:
        settle(check.get("backoff_s", 0.25))
        response = http_json("GET", check.get("endpoint") or "")
        passed = bool(response.get("ok"))
        assertion = check.get("assertion")
        if passed and isinstance(assertion, dict) and assertion:
            passed = evaluate_assertion(response.get("data"), assertion, inputs)
        elif passed and check.get("match_field") and check.get("param"):
            target = inputs.get(check["param"])
            passed = target is not None and any(
                isinstance(item, dict) and str(item.get(check["match_field"])) == str(target)
                for item in list_items(response.get("data"))
            )
        if not passed:
            issues.append({"step_id": check.get("step_id"), "verification_id": check.get("verification_id") or "unverified", "reason": "read-back assertion failed"})
    return {"ok": not issues, "issues": issues, "checks": len(checks)}


def main():
    command = parser()
    inputs = inputs_from_args(command.parse_args(), command)
    result = verify(inputs)
    emit({"capability": PLAN["name"], **result})
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _render_folder(skill, folder: Path, *, tenant: str) -> tuple[list[dict], bool]:  # noqa: ANN001
    spec = _flow_spec(skill)
    api_request = _compiled_request(skill, spec)
    steps = _steps(api_request)
    if not steps:
        raise ValueError(f"{skill.skill_id} has no executable page request")
    plans = _capability_plans(skill, spec, api_request)
    if not plans:
        raise ValueError(f"{skill.skill_id} has no capability")
    slug = package_slug(skill.skill_id)
    docs = dict(((spec.meta or {}).get("skill_docs") or {})) if spec is not None else {}
    model_skill_md = str(docs.get("skill_md") or "")
    reference_md = str(docs.get("reference_md") or "")
    docs_valid = validate_skill_documents(
        model_skill_md,
        reference_md,
        allowed_verification_ids=flow_spec_verification_ids(spec),
        required_chain_names={str(plan["name"]) for plan in plans},
        required_unverified_chains=flow_spec_unverified_capability_names(spec),
    )["ok"]
    # The model may enrich business facts in reference.md, but it must never
    # replace the deterministic operational contract inherited from the
    # original Skill exporter (collection, validation, confirmation, verify,
    # and result handling). A structurally valid but minimal model document was
    # previously accepted here and silently discarded all of those rules.
    skill_md = _fallback_skill_md(skill, slug, plans, spec)
    if not docs_valid:
        reference_md = _fallback_reference_md(skill, plans, spec)

    scripts = folder / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    references = folder / "references"
    references.mkdir(parents=True, exist_ok=True)
    _write_text(folder / "SKILL.md", skill_md)
    _write_text(folder / "reference.md", reference_md)
    _write_text(references / "CAPABILITIES.md", _capabilities_md(skill, plans))
    _write_text(references / "OPTIONS.md", _options_md(plans))
    config = {
        "tenant": tenant,
        "subsystem": str(skill.subsystem.value if hasattr(skill.subsystem, "value") else skill.subsystem),
        "base_url": _base_url(steps),
    }
    _write_text(scripts / "client.py", _CLIENT_TEMPLATE.replace("__CONFIG__", repr(json.dumps(config, ensure_ascii=False))))
    from dano.execution.page import wire_format as wire_format_module

    _write_text(
        scripts / "wire_format.py",
        Path(wire_format_module.__file__).read_text(encoding="utf-8"),
    )
    _write_text(scripts / "format_list.py", _format_list_py(plans))
    contract = {
        "protocol": "dano.skill_package.contract.v1",
        "skill": {"id": skill.skill_id, "name": slug, "title": skill.title or skill.action},
        "capabilities": [
            {
                "name": plan["name"],
                "title": plan["title"],
                "kind": plan["kind"],
                "script": f"scripts/{plan['script']}.py",
                "verify_script": f"scripts/verify_{plan['script']}.py",
                "requires_confirmation": plan["requires_confirmation"],
                "requires_verify": plan["requires_verify"],
                "input_schema": plan["input_schema"],
                "output_schema": plan["output_schema"],
                "preconditions": plan["preconditions"],
                "caller_responsibilities": plan["caller_responsibilities"],
                "skill_responsibilities": plan["skill_responsibilities"],
            }
            for plan in plans
        ],
        "capability_relations": [
            relation.model_dump(mode="json", exclude_none=True)
            for relation in (spec.capability_relations if spec is not None else [])
        ],
    }
    _write_text(
        references / "CONTRACT.json",
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    for plan in plans:
        plan_payload = {**plan, "fact_checks": plan["fact_checks"]}
        module = plan["script"]
        _write_text(
            scripts / f"{module}.py",
            _CAPABILITY_TEMPLATE.replace("__PLAN__", repr(json.dumps(plan_payload, ensure_ascii=False))),
        )
        _write_text(
            scripts / f"verify_{module}.py",
            _VERIFY_TEMPLATE.replace("__CAP_MODULE__", module),
        )
    return plans, not docs_valid


def render_skill_package(skill, out_dir: str, *, tenant: str) -> str:  # noqa: ANN001
    """Render one SkillSpec atomically and return its package folder name."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    slug = package_slug(skill.skill_id)
    stage = Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=root))
    try:
        _plans, fallback_used = _render_folder(skill, stage, tenant=tenant)
        validation = validate_skill_package(stage)
        if not validation["ok"] and not fallback_used:
            spec = _flow_spec(skill)
            plans = _capability_plans(skill, spec, _compiled_request(skill, spec))
            _write_text(stage / "SKILL.md", _fallback_skill_md(skill, slug, plans, spec))
            _write_text(stage / "reference.md", _fallback_reference_md(skill, plans, spec))
            fallback_used = True
            validation = validate_skill_package(stage)
        if not validation["ok"]:
            raise ValueError(f"skill package validation failed: {validation['issues']}")
        target = root / slug
        backup = root / f".{slug}.old-{uuid4().hex}"
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except Exception:
            if backup.exists():
                backup.rename(target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        log.info("export.skill_package", skill_id=skill.skill_id, folder=slug, fallback_used=fallback_used)
        return slug
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def validate_flow_spec_package(spec, *, tenant: str = "release-check") -> dict:  # noqa: ANN001
    """Render and validate one frozen FlowSpec without publishing files.

    This is the release gate's package-level check.  It deliberately uses the
    same renderer and validator as the real export path, including script
    ``--help`` checks and plaintext credential detection.
    """
    from dano.execution.page.flow_spec import (
        flow_spec_release_payload,
        flow_spec_required_params,
        flow_spec_to_api_request,
    )

    api_request, build_errors = flow_spec_to_api_request(spec)
    if api_request is None or build_errors:
        return {
            "ok": False,
            "issues": [{
                "severity": "error",
                "code": "flow_spec_build",
                "path": "FlowSpec",
                "message": "; ".join(build_errors or ["FlowSpec cannot be compiled"]),
            }],
        }
    api_request = dict(api_request)
    api_request["_release_snapshot"] = {"flow_spec": flow_spec_release_payload(spec)}
    skill = SimpleNamespace(
        skill_id=f"release-check.{spec.flow_id}",
        action=(spec.capabilities[0].name if spec.capabilities else "recording"),
        title=spec.title or (spec.capabilities[0].title if spec.capabilities else "Recording"),
        subsystem="release-check",
        api_request=api_request,
        capabilities=api_request.get("capabilities") or [],
        required_fields=flow_spec_required_params(spec),
    )
    with tempfile.TemporaryDirectory(prefix="dano-release-check-") as folder:
        stage = Path(folder)
        try:
            _render_folder(skill, stage, tenant=tenant)
        except Exception as exc:  # noqa: BLE001 - converted into a stable gate result
            return {
                "ok": False,
                "issues": [{
                    "severity": "error",
                    "code": "package_render",
                    "path": str(stage),
                    "message": str(exc),
                }],
            }
        return validate_skill_package(stage)


async def write_skill_packages(
    tenant: str,
    out_dir: str,
    *,
    skill_ids: list[str] | None = None,
) -> list[str]:
    """Render every published PAGE_SCRIPT skill selected for one tenant."""
    from dano.assets.repository import AssetRepository
    from dano.orchestrator.skills import SkillRegistry

    repo = AssetRepository()
    try:
        subsystems = await repo.distinct_subsystems(tenant)
    except Exception as exc:  # noqa: BLE001
        log.warning("export.package_discovery_failed", tenant=tenant, error=str(exc))
        subsystems = []
    registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subsystems)
    selected = None if skill_ids is None else set(skill_ids)
    page_skills = [
        skill for skill in registry.skills
        if skill.recording_asset_id is not None and (selected is None or skill.skill_id in selected)
    ]
    written: list[str] = []
    for skill in page_skills:
        try:
            written.append(render_skill_package(skill, out_dir, tenant=tenant))
        except Exception as exc:  # noqa: BLE001 - one malformed legacy asset cannot block peers
            log.warning("export.skill_package_failed", skill_id=skill.skill_id, error=str(exc))
    return written
