"""把已上架 Skill 导出为**官方 skill-creator 格式**的 Agent Skill(.agents/skills/<name>/)。

用法:
  python -m dano.export.agent_skills --tenant demo-oa --out <pi仓库>/.agents/skills

每个 skill = 一个文件夹(skill-creator 规范:渐进式披露 + 脚本 + references):
  SKILL.md           —— frontmatter(精确触发场景)+ 能力小结 + 核心工作流
  agents/openai.yaml —— 业务展示名与默认提示词
  references/CAPABILITIES.md / CONTRACT.json / OPTIONS.md —— 人读能力参考 + 无损契约 + 选择项参考
  scripts/dano_call.py  —— 真逻辑:能力级参数校验 + --confirm + --diagnose,POST Dano capability invoke,末行打印稳定 JSON 状态
  scripts/submit.sh / submit.ps1     —— 转发到 dano_call.py 的薄壳
  scripts/format_list.py / format_list.ps1 —— 跨平台把列表结果格式化为稳定 Markdown 表格

真执行(Dano→目标系统 + 三模型闸门 + 事实核查)都在 Dano 侧;本端无业务逻辑、不碰 OA 凭证,
只带 X-Tenant-Key 调 Dano。密钥经环境变量(DANO_URL / DANO_TENANT_KEY),不写进文件。
打包成 .skill:用 skill-creator 的 `python -m scripts.package_skill <此文件夹>`。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

import structlog

from dano.assets.repository import AssetRepository
from dano.business_packs import business_subsystems
from dano.catalog.manifest import (
    SkillManifest,
    _ask_user_question_interaction_protocol,
    build_manifests,
    tool_name_of,
)
from dano.config import get_settings
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import Subsystem

log = structlog.get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _configured_reference_dir() -> Path:
    """Resolve one portable path relative to the installed project root."""
    settings = get_settings()
    configured = str(settings.skill_reference_dir or "").strip()
    if not configured:
        raise ValueError("DANO_SKILL_REFERENCE_DIR 不能为空")
    relative = Path(configured.replace("\\", "/"))
    if (
        relative.is_absolute()
        or configured.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:[/\\]", configured)
    ):
        raise ValueError("DANO_SKILL_REFERENCE_DIR 必须是相对仓库根目录的路径")
    configured_root = str(getattr(settings, "skill_reference_root", "") or "").strip()
    root = Path(configured_root).expanduser() if configured_root else _PROJECT_ROOT
    if configured_root and not root.is_absolute():
        raise ValueError("DANO_SKILL_REFERENCE_ROOT 必须是绝对路径")
    project_root = root.resolve()
    resolved = (project_root / relative).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("DANO_SKILL_REFERENCE_DIR 不得超出项目根目录") from exc
    return resolved


def _load_reference_markdown(source_dir: Path) -> list[tuple[Path, str]]:
    """Read every Markdown file recursively, preserving its relative path."""
    source = source_dir.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Skill 参考目录不存在或不是文件夹: {source}")
    files = sorted(
        (path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".md"),
        key=lambda path: path.relative_to(source).as_posix().casefold(),
    )
    if not files:
        raise ValueError(f"Skill 参考目录中没有 Markdown 文件: {source}")
    try:
        return [(path.relative_to(source), path.read_text(encoding="utf-8")) for path in files]
    except UnicodeDecodeError as exc:
        raise ValueError(f"Skill 参考 Markdown 必须使用 UTF-8 编码: {exc.object!r}") from exc


def _validate_reference_markdown(reference_docs: list[tuple[Path, str]]) -> None:
    """Validate the complete configured reference set before rendering Skills."""
    combined = "\n\n".join(content for _, content in reference_docs)
    required_contracts = {
        "原生提问工具": ("ask_user_question",),
        "分组表单标题": ("title",),
        "多字段 questions 数组": ("questions",),
        "推荐默认值": ("default",),
        "必填规则": ("required",),
        "日期格式": ("dateFormat",),
        "远程选项来源": ("dataSource",),
        "最终确认": ("confirm",),
        "已提交表单确认": ("formIds",),
        "确认结果状态": ("confirmed",),
        "取消结果": ("cancelled",),
        "参数校验错误处理": (
            "validation error",
            "question_validation_failed",
            "invalid_question_arguments",
        ),
    }
    missing = [
        label
        for label, alternatives in required_contracts.items()
        if not any(term in combined for term in alternatives)
    ]
    if missing:
        names = ", ".join(path.as_posix() for path, _ in reference_docs) or "<空>"
        raise ValueError(f"Skill 参考 Markdown 缺少必要的提问契约（{', '.join(missing)}）: {names}")


def _stage_folder(out_dir: Path, slug: str) -> Path:
    """Build a complete export beside its target so failed writes never corrupt it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{slug}-", dir=out_dir))


def _make_export_tree_readable(root: Path) -> None:
    """Generated Skills are read by a runtime container that may use another UID."""
    for path in (root, *root.rglob("*")):
        if path.is_dir():
            mode = 0o755
        else:
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
        os.chmod(path, mode)


def _publish_folder(stage: Path, target: Path, slug: str, skill_name: str | None = None) -> Path:
    """Validate then atomically replace one exporter-owned Skill folder."""
    _validate_generated_skill(stage, skill_name or slug)
    _make_export_tree_readable(stage)
    backup = target.with_name(f".{target.name}.old-{uuid.uuid4().hex}")
    had_target = target.exists()
    if had_target:
        target.rename(backup)
    try:
        stage.rename(target)
    except Exception:
        if had_target and backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    return target


def _abort_stage(stage: Path) -> None:
    if stage.exists():
        shutil.rmtree(stage)


async def _tenant_subsystems(repo: AssetRepository, tenant: str) -> list[Subsystem]:
    """发现租户实际系统；无资产时读取该租户可选业务包。

    与网关 `_tenant_subsystems` 一致:任意系统接入发布后自动被发现并导出,不必在代码里预先登记。
    """
    try:
        subs = await repo.distinct_subsystems(tenant)
    except Exception as e:  # noqa: BLE001 —— DB 不可用时仍可读取可选配置
        log.warning("export.discover_subsystems_failed", tenant=tenant, error=str(e))
        subs = []
    return subs or [Subsystem(value) for value in business_subsystems(tenant)]


def _upgrade_recorded_skill_for_export(skill: SkillSpec) -> SkillSpec:
    """Rebuild a recorded Skill from its immutable release evidence.

    Older published page assets kept the full request facts in
    ``_release_snapshot.flow_spec`` but persisted a lossy top-level capability
    projection (no defaults, empty record item schema, stale required flags).
    Re-running the current deterministic compiler over that same snapshot is a
    compatibility migration, not new inference: enums/defaults/response fields
    still come only from recorded evidence.
    """
    api_request = dict(getattr(skill, "api_request", {}) or {})
    release = dict(api_request.get("_release_snapshot") or {})
    frozen_flow = release.get("flow_spec")
    if not isinstance(frozen_flow, dict) or not frozen_flow.get("steps"):
        return skill
    try:
        from dano.execution.page.flow_spec import (
            FlowSpec,
            flow_spec_to_api_request,
            prepare_flow_spec_for_publish,
        )

        prepared = prepare_flow_spec_for_publish(FlowSpec.model_validate(frozen_flow))
        rebuilt, errors = flow_spec_to_api_request(prepared)
    except Exception as exc:  # noqa: BLE001 - legacy asset stays exportable via its stored contract
        log.warning("export.release_contract_upgrade_failed", skill_id=skill.skill_id, error=str(exc))
        return skill
    if rebuilt is None or errors:
        log.warning(
            "export.release_contract_upgrade_rejected",
            skill_id=skill.skill_id,
            errors=list(errors or []),
        )
        return skill

    rebuilt = dict(rebuilt)
    rebuilt["_release_snapshot"] = release
    capabilities = [item for item in (rebuilt.get("capabilities") or []) if isinstance(item, dict)]
    if not capabilities:
        return skill

    rebuilt_steps = [item for item in (rebuilt.get("steps") or []) if isinstance(item, dict)]
    step_by_id = {
        str(step.get("step_id") or ""): step
        for step in rebuilt_steps
        if str(step.get("step_id") or "")
    }
    for capability in capabilities:
        capability_steps = [
            step_by_id[step_id]
            for step_id in (str(value) for value in (capability.get("step_ids") or []))
            if step_id in step_by_id
        ]
        cap_has_fact_check = any(bool(step.get("fact_check")) for step in capability_steps)
        cap_has_success_rule = any(bool(step.get("success_rule")) for step in capability_steps)
        capability["verification_status"] = "partially_verified"
        capability["verification_basis"] = (
            "fact_check_configured" if cap_has_fact_check
            else "success_rule_configured" if cap_has_success_rule
            else "structure_only"
        )
        capability["verify_required"] = bool(
            cap_has_fact_check
            and str(capability.get("kind") or "")
            not in {"query", "query_status", "list_options", "validate"}
        )

    write_step_ids = {
        str(step_id)
        for capability in capabilities
        if str(capability.get("kind") or "")
        not in {"query", "query_status", "list_options", "validate"}
        for step_id in (capability.get("step_ids") or [])
    }
    write_steps = [
        step for step in rebuilt_steps
        if str(step.get("step_id") or "") in write_step_ids
    ]
    # Skill-level verification describes side-effecting business work. A GET
    # response success rule can validate the query capability itself, but must
    # not make an unrelated submit/withdraw capability look verified.
    has_fact_check = bool(
        rebuilt.get("fact_check")
        or any(step.get("fact_check") for step in write_steps)
    )
    has_success_rule = bool(
        rebuilt.get("success_rule")
        or any(step.get("success_rule") for step in write_steps)
    )
    verification_basis = (
        "fact_check_configured" if has_fact_check
        else "success_rule_configured" if has_success_rule
        else "structure_only"
    )

    required: list[str] = []
    all_fields: list[str] = []
    field_types = dict(getattr(skill, "field_types", {}) or {})
    for capability in capabilities:
        schema = capability.get("input_schema") or capability.get("parameters") or {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for name, prop in props.items():
            if name not in all_fields:
                all_fields.append(name)
            if isinstance(prop, dict) and prop.get("type"):
                field_types[name] = str(prop["type"])
        for name in schema.get("required") or []:
            if name in props and name not in required:
                required.append(name)

    upgraded = skill.model_copy(deep=True)
    upgraded.api_request = rebuilt
    upgraded.capabilities = capabilities
    upgraded.capability_relations = list(rebuilt.get("capability_relations") or [])
    upgraded.required_fields = required
    upgraded.optional_fields = [name for name in all_fields if name not in required]
    upgraded.field_types = field_types
    upgraded.verification_status = "partially_verified"
    upgraded.verification_basis = verification_basis
    upgraded.recording_mode = str(rebuilt.get("recording_mode") or upgraded.recording_mode or "unknown")
    upgraded.call_metadata = {
        **dict(upgraded.call_metadata or {}),
        "recording_mode": upgraded.recording_mode,
        "verification_status": upgraded.verification_status,
        "verification_basis": upgraded.verification_basis,
        "capabilities": capabilities,
    }
    if not has_fact_check:
        upgraded.fact_check_query = None
        upgraded.fact_check_expr = None
    return upgraded


def _slug(skill_id: str) -> str:
    """skill_id(如 workflow.submit_entry)→ 文件夹名(kebab,如 dano-workflow-submit-entry)。

    动作名含非 ASCII(中文)时 ASCII 化会塌成只剩子系统前缀、多个 skill 撞同一目录互相覆盖 →
    补 skill_id 短哈希保唯一(动作名建议用英文,中文放标题)。
    """
    s = ("dano-" + skill_id).lower().replace(".", "-").replace("_", "-")
    s = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]+", "-", s).strip("-"))
    if re.search(r"[^\x00-\x7f]", skill_id):                # 含中文等非 ASCII → 加哈希后缀防撞目录
        import hashlib
        h = hashlib.md5(skill_id.encode("utf-8")).hexdigest()[:6]
        s = (f"{s}-{h}".strip("-")) if s else f"dano-{h}"
    if len(s) > 64:
        import hashlib
        suffix = hashlib.sha256(skill_id.encode("utf-8")).hexdigest()[:8]
        s = f"{s[:55].rstrip('-')}-{suffix}"
    return s


def _skill_name(title: str, fallback: str) -> str:
    """Use the user-facing Chinese business title; keep ``fallback`` only for untitled assets."""
    return str(title or "").strip() or fallback


def _agents_openai_yaml(slug: str, display_name: str, short_description: str) -> str:
    """Render the standard UI metadata without adding product-specific guesses."""
    short = short_description.strip()
    if len(short) < 25:
        short += "，支持参数收集、用户确认和执行结果处理"
    short = short[:64]
    prompt = f"使用 ${slug} 完成“{display_name}”对应的已发布业务能力。"
    return (
        "interface:\n"
        f"  display_name: {json.dumps(display_name, ensure_ascii=False)}\n"
        f"  short_description: {json.dumps(short, ensure_ascii=False)}\n"
        f"  default_prompt: {json.dumps(prompt, ensure_ascii=False)}\n"
    )


def _trigger_description(m: SkillManifest, contracts: dict[str, dict]) -> str:
    """Frontmatter description is an invocation index, not a second README."""
    object_name = str(getattr(m, "business", "") or m.title or m.action).strip()
    titles = list(dict.fromkeys(
        str(contract.get("title") or name).strip()
        for name, contract in contracts.items()
        if str(contract.get("title") or name).strip()
    ))
    triggers = "、".join(titles) or (m.title or m.action)
    return (
        f"用于“{object_name}”业务。用户明确要求执行“{triggers}”中的任一已发布操作时使用；"
        "负责选择正确能力、一次性收集表单参数、确认写操作并返回执行结果。"
        "仅咨询、业务对象不一致或要求未列出的操作时不要触发。"
    )


def _frontend_output_protocol() -> dict:
    """Stable renderer contract for successful generated-Skill calls."""
    return {
        "format": "markdown",
        "success": {
            "title": "操作成功",
            "body": "按 output_schema 展示业务结果；数组使用 Markdown 表格且表格行之间不得插入空行",
            "request_link": {
                "source": "request_link",
                "markdown_source": "request_markdown",
                "label": "打开原系统页面",
                "target": "_blank",
                "rel": "noopener noreferrer",
                "only_when_status": "succeeded",
            },
        },
        "failure": {
            "title": "操作未完成",
            "body_source": "reason",
            "show_request_link": False,
        },
    }


def _validate_generated_skill(folder: Path, expected_name: str) -> None:
    """Fail export before publication when the generated package is incomplete."""
    if not expected_name.strip() or "\n" in expected_name or "\r" in expected_name:
        raise ValueError("Skill name 必须是非空单行标题")
    skill_path = folder / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md 缺少 YAML frontmatter")
    frontmatter = parts[1]
    name_line = next((line for line in frontmatter.splitlines() if line.startswith("name:")), "")
    raw_name = name_line.partition(":")[2].strip()
    try:
        actual_name = json.loads(raw_name)
    except json.JSONDecodeError:
        actual_name = raw_name
    if actual_name != expected_name:
        raise ValueError(f"Skill name 与预期业务名不一致: {actual_name!r} != {expected_name!r}")
    if len(text.splitlines()) > 500:
        raise ValueError("SKILL.md 超过 500 行，违反渐进式披露约束")
    if not (folder / "agents" / "openai.yaml").is_file():
        raise ValueError("Skill 缺少 agents/openai.yaml")
    if "references/CAPABILITIES.md" in text and not (folder / "references" / "CAPABILITIES.md").is_file():
        raise ValueError("Skill 缺少 references/CAPABILITIES.md")


def _fields(m: SkillManifest) -> tuple[list[str], set[str], dict]:
    props = (m.parameters or {}).get("properties", {}) or {}
    required = set((m.parameters or {}).get("required", []) or [])
    return list(props), required, props


def _flags(m: SkillManifest) -> str:
    keys, _, _ = _fields(m)
    return " ".join(f"--{k} <{k}>" for k in keys)


def _capability(m: SkillManifest) -> str:
    return (getattr(m, "capability", "") or m.name).strip()


_READ_CAPABILITY_KINDS = {"query", "query_status", "list_options", "validate", "validate_batch", "preview", "inspect"}
_ROUTING_FIELD_RE = re.compile(r"(?:approv|assignee|reviewer|leader|manager|hr|cc|审批|审核|领导|人力|抄送)", re.I)


def _schema_option_fields(schema: dict) -> list[str]:
    """List selectable field leaves, including fields nested under batch entries."""
    fields: list[str] = []

    def visit(node: dict) -> None:
        for name, prop in ((node or {}).get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            if prop.get("format") == "name-ref" or item.get("format") == "name-ref" or prop.get("x-options-source"):
                if name not in fields:
                    fields.append(name)
            visit(prop)
            if item:
                visit(item)

    visit(schema or {})
    return fields


def _wire_leaf(parameter: str, schema: dict) -> str:
    path = str((schema or {}).get("x-flow-path") or parameter).strip()
    leaf = path.rsplit(".", 1)[-1]
    return re.sub(r"\[\d+\]$", "", leaf)


def _business_label(parameter: str, schema: dict) -> str:
    label = str((schema or {}).get("label") or (schema or {}).get("title") or "").strip()
    if not label:
        return ""
    if label == parameter and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\]-]*", label):
        return ""
    return label


def _output_field_is_internal_transport(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    return normalized in {
        "processinstanceid", "workflowinstanceid", "flowinstanceid",
        "userid", "deptid", "departmentid", "organizationid", "orgid",
        "tenantid", "creatorid", "updaterid", "billtype",
        "processdefkey", "processdefinitionkey",
    }


def _enrich_output_schemas(contracts: dict[str, dict]) -> None:
    """Reuse grounded field metadata by exact wire identity across the recorded flow."""
    sources: dict[str, dict[str, list[tuple[str, dict]]]] = {}
    global_sources: dict[str, list[tuple[str, dict]]] = {}
    for capability, contract in contracts.items():
        for parameter, schema in ((contract.get("parameters") or {}).get("properties") or {}).items():
            if isinstance(schema, dict):
                wire = _wire_leaf(parameter, schema)
                sources.setdefault(capability, {}).setdefault(
                    wire, [],
                ).append(
                    (parameter, schema),
                )
                global_sources.setdefault(wire, []).append((parameter, schema))

    option_keys = (
        "x-enum-options", "x-options", "x-options-snapshot", "x-enum-value-map",
    )
    copied_keys = (
        "x-dano-display", "x-dano-internal", "x-dano-visibility",
        "x-dano-display-order", "x-dano-identifier-role",
    )
    for capability, contract in contracts.items():
        output_schema = copy.deepcopy(contract.get("output_schema") or {"type": "object"})
        properties = output_schema.get("properties") or {}
        row_properties = properties
        for field_schema in properties.values():
            if isinstance(field_schema, dict) and field_schema.get("type") == "array":
                row_properties = ((field_schema.get("items") or {}).get("properties") or {})
                break
        for output_field, output_field_schema in row_properties.items():
            if not isinstance(output_field_schema, dict):
                continue
            if (
                _output_field_is_internal_transport(output_field)
                and "x-dano-display" not in output_field_schema
            ):
                output_field_schema["x-dano-display"] = False
            local_sources = (sources.get(capability) or {}).get(output_field) or []
            normalized_output = re.sub(
                r"[^a-z0-9]+", "", str(output_field or "").casefold(),
            )
            ordered = (
                local_sources
                or ([] if normalized_output.endswith("id") else global_sources.get(output_field))
                or []
            )
            if not ordered:
                continue
            labels = list(dict.fromkeys(
                label for parameter, schema in ordered
                if (label := _business_label(parameter, schema))
            ))
            if (
                len(labels) == 1
                and not (output_field_schema.get("title") or output_field_schema.get("label"))
            ):
                output_field_schema["title"] = labels[0]
            for key in option_keys:
                if key in output_field_schema:
                    continue
                source_value = next(
                    (schema.get(key) for _parameter, schema in ordered if schema.get(key)),
                    None,
                )
                if source_value:
                    output_field_schema[key] = copy.deepcopy(source_value)
            for key in copied_keys:
                if key in output_field_schema:
                    continue
                values = {
                    json.dumps(schema.get(key), ensure_ascii=False, sort_keys=True)
                    for _parameter, schema in ordered
                    if schema.get(key) is not None
                }
                if len(values) == 1:
                    output_field_schema[key] = copy.deepcopy(next(
                        schema[key] for _parameter, schema in ordered if key in schema
                    ))
            if (
                output_field_schema.get("type") in {"integer", "number"}
                and "x-dano-value-format" not in output_field_schema
                and any(
                    schema.get("x-dano-business-type") in {"date", "datetime"}
                    or schema.get("format") in {"date", "date-time"}
                    for _parameter, schema in ordered
                )
            ):
                output_field_schema["x-dano-value-format"] = "epoch-auto"
        contract["output_schema"] = output_schema


def _capability_contracts(m: SkillManifest) -> dict[str, dict]:
    """Return the authoritative per-capability caller contracts used by exports."""
    contracts: dict[str, dict] = {}
    for raw in getattr(m, "capabilities", []) or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("kind") or raw.get("capability_id") or "").strip()
        if not name:
            continue
        kind = str(raw.get("kind") or name)
        schema = copy.deepcopy(
            raw.get("parameters") or raw.get("input_schema")
            or {"type": "object", "properties": {}, "required": []}
        )
        props = dict((schema or {}).get("properties") or {})
        required = list((schema or {}).get("required") or [])
        field_labels: dict[str, str] = {}
        for field in [*(raw.get("inputs") or []), *(raw.get("fields") or [])]:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or field.get("path") or "").split(".")[-1]
            label = str(field.get("display_name") or field.get("label") or "").strip()
            if key and label:
                field_labels[key] = label
        contracts[name] = {
            "name": name,
            "title": str(raw.get("title") or name),
            "kind": kind,
            "fields": list(props),
            "required": required,
            "numeric": _numeric_fields(props),
            "parameters": schema,
            "option_fields": _schema_option_fields(schema),
            "output_schema": raw.get("output_schema") or {"type": "object"},
            "requires_confirmation": raw.get("requires_confirmation") is True,
            "verify_required": (
                (raw.get("validation_requirements") or {}).get(
                    "verification_required",
                    (m.flow or {}).get("verify") is True and kind not in _READ_CAPABILITY_KINDS,
                ) is True
            ),
            "validation_requirements": dict(raw.get("validation_requirements") or {}),
            "caller_responsibilities": list(raw.get("caller_responsibilities") or []),
            "field_labels": field_labels,
        }
    if contracts:
        _enrich_output_schemas(contracts)
        return contracts
    keys, required, props = _fields(m)
    name = _capability(m)
    fallback = {name: {
        "name": name,
        "title": m.title,
        "kind": name,
        "fields": keys,
        "required": [key for key in keys if key in required],
        "numeric": _numeric_fields(props),
        "parameters": m.parameters,
        "option_fields": _schema_option_fields(m.parameters),
        "output_schema": m.output_schema,
        "requires_confirmation": bool(m.requires_confirmation),
        "verify_required": bool((m.flow or {}).get("verify")),
        "validation_requirements": {},
        "caller_responsibilities": [],
    }}
    _enrich_output_schemas(fallback)
    return fallback


def _export_contract(m: SkillManifest) -> dict:
    """Project recorded evidence into the exact contract exposed to callers."""
    contracts = _capability_contracts(m)
    interaction = _ask_user_question_interaction_protocol()
    raw_capabilities = {
        str(raw.get("name") or raw.get("kind") or raw.get("capability_id") or "").strip(): raw
        for raw in copy.deepcopy(getattr(m, "capabilities", []) or [])
        if isinstance(raw, dict)
    }
    exported_capabilities: list[dict] = []
    for name, contract in contracts.items():
        raw = raw_capabilities.get(name) or {}
        schema = copy.deepcopy(contract["parameters"])
        call_protocol = copy.deepcopy(raw.get("call_protocol") or {})
        call_protocol["interaction_protocol"] = copy.deepcopy(interaction)
        call_protocol["frontend_output"] = _frontend_output_protocol()
        call_protocol["input_schema"] = copy.deepcopy(schema)
        call_protocol["output_schema"] = copy.deepcopy(contract["output_schema"])
        exported_capabilities.append({
            "name": name,
            "title": contract["title"],
            "kind": contract["kind"],
            **({"capability_id": raw.get("capability_id")} if raw.get("capability_id") else {}),
            "input_schema": schema,
            "output_schema": copy.deepcopy(contract["output_schema"]),
            "required": list(contract.get("required") or []),
            "requires_confirmation": bool(contract.get("requires_confirmation")),
            "validation_requirements": copy.deepcopy(contract.get("validation_requirements") or {}),
            "caller_responsibilities": list(contract.get("caller_responsibilities") or []),
            "call_protocol": call_protocol,
        })
    payload = {
        "protocol": "dano.skill_contract.v1",
        "name": m.name,
        "title": m.title,
        "description": m.description,
        "subsystem": str(m.subsystem),
        "action": m.action,
        "risk_level": str(m.risk_level),
        "requires_confirmation": bool(m.requires_confirmation),
        "recording_mode": m.recording_mode,
        "verification_status": m.verification_status,
        "verification_basis": m.verification_basis,
        "source_page_url": str((m.flow or {}).get("source_page_url") or ""),
        "capability": m.capability,
        "capabilities": exported_capabilities,
        "capability_relations": copy.deepcopy(getattr(m, "capability_relations", []) or []),
        "parameters": {},
        "output_schema": {},
        "call_protocol": copy.deepcopy(m.call_protocol or {}),
    }
    root_protocol = payload["call_protocol"]
    root_protocol["interaction_protocol"] = copy.deepcopy(interaction)
    root_protocol["frontend_output"] = _frontend_output_protocol()
    orchestration = _relation_orchestration_policy(m, contracts)
    if orchestration:
        payload["relation_orchestration"] = copy.deepcopy(orchestration)
        root_protocol["relation_orchestration"] = copy.deepcopy(orchestration)
        targets = {
            str(rule.get("target_capability") or "")
            for rule in orchestration.get("rules") or []
            if isinstance(rule, dict)
        }
        for capability in exported_capabilities:
            if capability.get("name") in targets:
                capability["call_protocol"]["relation_orchestration"] = copy.deepcopy(
                    orchestration
                )

    if len(contracts) == 1:
        name, contract = next(iter(contracts.items()))
        payload["capability"] = name
        payload["parameters"] = copy.deepcopy(contract["parameters"])
        payload["output_schema"] = copy.deepcopy(contract["output_schema"])
        root_protocol["default_capability"] = name
        root_protocol["requires_explicit_capability"] = False
        root_protocol.setdefault("payload", {})["capability"] = name
    else:
        capability_names = list(contracts)
        payload["capability"] = None
        payload["parameters"] = {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "enum": capability_names},
                "input": {
                    "type": "object",
                    "description": "必须符合所选 capability 在 capabilities[].input_schema 中的定义",
                },
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["capability", "input"],
            "additionalProperties": False,
            "x-dano-capability-input-schemas": {
                name: f"#/capabilities/{index}/input_schema"
                for index, name in enumerate(capability_names)
            },
        }
        payload["output_schema"] = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["succeeded", "partial_success", "need_select", "need_confirm", "failed"],
                },
                "capability": {"type": "string", "enum": capability_names},
                "output": {"type": "object"},
                "reason": {"type": "string"},
                "request_url": {"type": "string", "format": "uri"},
                "request_markdown": {"type": "string"},
                "request_link": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "url": {"type": "string", "format": "uri"},
                        "target": {"type": "string", "const": "_blank"},
                        "rel": {"type": "string", "const": "noopener noreferrer"},
                        "markdown": {"type": "string"},
                    },
                    "required": ["label", "url", "target", "rel", "markdown"],
                },
            },
            "required": ["status"],
        }
        root_protocol["capability"] = None
        root_protocol["default_capability"] = None
        root_protocol["requires_explicit_capability"] = True
        root_protocol.setdefault("payload", {})["capability"] = None

    root_protocol["input_schema"] = copy.deepcopy(payload["parameters"])
    root_protocol["output_schema"] = copy.deepcopy(payload["output_schema"])
    return payload


def _export_default_capability(m: SkillManifest) -> str | None:
    contracts = _capability_contracts(m)
    # With multiple public abilities, silently defaulting to a write ability can run
    # the wrong business operation. The caller must choose explicitly.
    if len(contracts) != 1:
        return None
    return next(iter(contracts))


def _export_contract_errors(m: SkillManifest) -> list[str]:
    """Fail closed when a published contract is structurally unsafe to export."""
    errors: list[str] = []

    def validate_option_defaults(name: str, schema: dict, prefix: str = "") -> None:
        for field, prop in ((schema or {}).get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            path = f"{prefix}.{field}" if prefix else str(field)
            options = _question_options(prop)
            default = prop.get("default")
            if options and default not in (None, ""):
                id_matches = [
                    option for option in options
                    if str(option.get("id")) == str(default)
                ]
                label_matches = [
                    option for option in options
                    if str(option.get("label")) == str(default)
                ]
                if not id_matches and len(label_matches) > 1:
                    errors.append(
                        f"{name}.{path}: 默认值 {default!r} 对应多个同名候选，"
                        "缺少可唯一映射的稳定 option id"
                    )
                elif not id_matches and not label_matches:
                    errors.append(
                        f"{name}.{path}: 默认值 {default!r} 不在静态候选 id/label 中"
                    )
            validate_option_defaults(name, prop, path)
            item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            if item:
                validate_option_defaults(name, item, f"{path}[]")

    for name, contract in _capability_contracts(m).items():
        schema = contract.get("parameters") or {}
        validate_option_defaults(name, schema)
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = [str(item) for item in (schema.get("required") or [])]
        missing_required = [item for item in required if item not in props]
        if missing_required:
            errors.append(f"{name}: required 字段不在 properties: {', '.join(missing_required)}")
        if contract.get("kind") in _CAPABILITY_PUBLIC_KINDS and not str(name or "").strip():
            errors.append(f"{contract.get('kind')}: capability name 不能为空")
        if contract.get("kind") in {"submit", "submit_batch", "validate_batch"}:
            entries = props.get("entries") if isinstance(props, dict) else None
            items = entries.get("items") if isinstance(entries, dict) and isinstance(entries.get("items"), dict) else {}
            item_props = items.get("properties") if isinstance(items.get("properties"), dict) else {}
            if contract.get("kind") in {"submit_batch", "validate_batch"} and not item_props:
                errors.append(f"{name}: 批量能力缺少 entries 条目字段")
            if contract.get("kind") == "submit" and entries is not None:
                errors.append(f"{name}: submit 不能伪装成批量契约；请使用 submit_batch + entries[]")
            if item_props and all(_ROUTING_FIELD_RE.search(str(field or "")) for field in item_props):
                errors.append(f"{name}: entries 只有审批/路由字段，疑似把人员列表误判为批量业务条目")
    return errors


_CAPABILITY_PUBLIC_KINDS = _READ_CAPABILITY_KINDS | {"submit", "submit_batch"}
_MUTATION_CAPABILITY_KINDS = {
    "submit", "create", "save", "update", "approve", "reject",
    "withdraw", "delete", "operation",
}


def _relation_orchestration_policy(
    m: SkillManifest,
    contracts: dict[str, dict] | None = None,
) -> dict:
    """Describe safe caller-side repetition of a recorded single-record mutation.

    Some systems expose only a single-record withdraw/delete endpoint. The
    caller may still satisfy an explicit plural request by querying authoritative
    records, resolving the exact relation field, confirming the complete scope
    once, and invoking that same published capability sequentially.
    """
    capability_contracts = contracts or _capability_contracts(m)
    rules: list[dict] = []
    for relation in getattr(m, "capability_relations", []) or []:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("from_capability") or "").strip()
        target = str(relation.get("to_capability") or "").strip()
        source_output = str(relation.get("from_output") or "").strip()
        target_input = str(relation.get("to_input") or "").strip()
        source_contract = capability_contracts.get(source) or {}
        target_contract = capability_contracts.get(target) or {}
        if not (
            source
            and target
            and source_output
            and target_input
            and source_contract.get("kind") in _READ_CAPABILITY_KINDS
            and target_contract.get("kind") in _MUTATION_CAPABILITY_KINDS
        ):
            continue
        rules.append({
            "source_capability": source,
            "source_output": source_output,
            "target_capability": target,
            "target_input": target_input,
            "operation_kind": target_contract.get("kind"),
            "identifier_mapping": "exact_value_only",
            "preflight": {
                "query_current_record": True,
                "eligibility_source": "published_preconditions_or_explicit_current_state_only",
                "ineligible_record": "do_not_invoke",
            },
            "single_reference": {
                "resolve_from": "last_successful_write_then_current_query",
                "require_unique_record": True,
                "zero_or_multiple_matches": "ask_user_to_select",
            },
            "plural_reference": {
                "requires_explicit_plural_intent": True,
                "query_every_page": True,
                "selection_scope": "all_records_matching_explicit_user_scope",
                "confirmation": "one_combined_form_for_all_selected_records",
                "execution": "sequential_single_capability_invocations",
                "automatic_retry": False,
                "result": "per_record_with_partial_success",
            },
        })
    related_targets = {
        str(rule.get("target_capability") or "")
        for rule in rules
    }
    for target, target_contract in capability_contracts.items():
        kind = str(target_contract.get("kind") or "")
        if (
            target in related_targets
            or kind in _READ_CAPABILITY_KINDS
            or kind in {"submit_batch", "validate_batch"}
            or not (
                target_contract.get("requires_confirmation") is True
                or kind in _MUTATION_CAPABILITY_KINDS
            )
        ):
            continue
        rules.append({
            "target_capability": target,
            "operation_kind": kind,
            "input_collection": "one_grouped_form_with_repeated_entries",
            "plural_reference": {
                "requires_explicit_plural_intent": True,
                "selection_scope": "all_entries_explicitly_provided_or_selected_by_user",
                "confirmation": "one_combined_form_for_all_selected_records",
                "execution": "sequential_single_capability_invocations",
                "automatic_retry": False,
                "result": "per_record_with_partial_success",
            },
        })
    if not rules:
        return {}
    return {
        "protocol": "dano.mutation_orchestration.v1",
        "mode": "caller_orchestrated",
        "rules": rules,
    }


def _capability_relationship_section(m: SkillManifest) -> str:
    relations = [r for r in (getattr(m, "capability_relations", []) or []) if isinstance(r, dict)]
    if not relations:
        contracts = _capability_contracts(m)
        if len(contracts) <= 1:
            return ""
        current_value_fields = [
            f"`{name}.{field}`"
            for name, contract in contracts.items()
            for field, schema in ((contract.get("parameters") or {}).get("properties") or {}).items()
            if isinstance(schema, dict) and schema.get("x-dano-require-current-value") is True
        ]
        lines = [
            "## 能力关系",
            "",
            "当前发布契约未声明 `capability_relations`，因此这些能力按**独立能力**处理；"
            "不得自行编造自动串联、字段映射或执行顺序。",
        ]
        if current_value_fields:
            lines.append(
                "- " + "、".join(current_value_fields)
                + " 必须使用本次会话中用户明确提供或当前系统结果里选择的值；"
                "录制样本只能用于辨认字段，不能作为执行值。"
            )
        return "\n".join(lines)
    lines = [
        "## 能力关系",
        "",
        "能力关系不会自行执行；调用方可以据此编排单条或批量操作，"
        "但每一次真实调用都必须显式选择 capability。",
    ]
    for relation in relations:
        source_ref = str(relation.get("from_capability") or "")
        target_ref = str(relation.get("to_capability") or "")
        if relation.get("from_output"):
            source_ref += f".{relation['from_output']}"
        if relation.get("to_input"):
            target_ref += f".{relation['to_input']}"
        source = f"`{source_ref}`"
        target = f"`{target_ref}`"
        lines.append(f"- {source} → {target}（`{relation.get('type') or 'suggested_call_chain'}`）")
        lines.append(f"  调用方责任：{relation.get('caller_responsibility') or '根据输出和用户意图决定是否继续调用。'}")
    return "\n".join(lines)


def _related_mutation_sop(m: SkillManifest) -> str:
    policy = _relation_orchestration_policy(m)
    rules = policy.get("rules") if isinstance(policy, dict) else []
    if not rules:
        return ""
    lines = [
        "## 单条与批量关联操作",
        "",
        "以下是对已发布查询能力和单条撤回/删除能力的调用方编排，"
        "不是虚构新的批量 capability，也不改变任何能力输入契约。",
    ]
    for rule in rules:
        source = str(rule.get("source_capability") or "")
        source_output = str(rule.get("source_output") or "")
        target = rule["target_capability"]
        target_input = str(rule.get("target_input") or "")
        action = {
            "submit": "提交", "create": "新增", "save": "保存",
            "update": "更新", "approve": "审批", "reject": "驳回",
            "withdraw": "撤回", "delete": "删除",
        }.get(str(rule.get("operation_kind") or ""), "执行")
        lines.append("")
        if source:
            lines += [
                f"- 标识映射固定为 `{source}.{source_output}` → `{target}.{target_input}`。"
                "只复制同一条记录的原值，禁止改用该记录的其他 ID、单据号、列表序号或录制样本。",
                "- 执行前必须以查询结果复核同一条记录的当前状态；只有发布前置条件或当前状态证据"
                "明确允许时才调用目标能力。明确不符合条件时直接说明原因，不得仍然调用后再伪报成功。",
                f"- 用户说“{action}这个提交”“{action}刚刚那条”“{action}上一条”时："
                "先读取本会话最后一次成功写操作的原始结果，再调用查询能力定位同一条记录；"
                f"只有唯一匹配时才能取上述来源字段执行单条{action}。"
                "匹配为零或多条时，必须让用户选择，禁止默认第一条。",
                f"- 用户明确说“{action}这些”“{action}全部”“{action}所有提交”时："
                f"调用 `{source}` 查询用户明确范围内的全部记录，并遍历所有分页，"
                "不能只处理当前页或默认前 10 条。没有用户筛选条件时使用空查询 input，"
                "不得带入录制筛选值。",
                "- 批量选择与共享字段必须放进一次 `ask_user_question` 分组表单："
                "记录选择使用 `checkbox` 与稳定 option id，其他字段继续使用"
                f"`{target}` 的原参数名。将本地选择字段移除后，逐条构造合法的单条能力 input。",
            ]
        else:
            lines += [
                f"- 用户明确要求批量{action}时，把全部业务条目和共享字段放进一次 "
                "`ask_user_question` 分组表单；每条业务条目都必须符合"
                f"`{target}` 的单条输入契约，禁止把条目数组直接传给单条能力。",
                f"- 用户只提供一条记录时正常执行单条{action}；未表达复数意图时不得自行扩展为批量。",
            ]
        lines += [
            "- 整批只确认一次：用户一次确认的是表单中列出的完整记录集合，"
            "不得对每条记录重复发起确认。确认后按表单顺序逐条调用"
            f"`{target}`，每条都显式带 `--capability {target} --confirm`；"
            "不得并发、不得自动重试、不得因一条失败而重复已成功记录。",
            "- 最终逐条报告业务标识、成功或失败及原因：全部成功才报告成功，"
            "部分成功返回 `partial_success`，全部失败返回 `failed`。",
        ]
    return "\n".join(lines)


def _schema_type_text(schema: dict) -> str:
    schema = schema or {}
    if schema.get("format") == "name-ref":
        return "枚举·名字→ID"
    if schema.get("format") == "date-time":
        return "datetime"
    if schema.get("format") == "date":
        return "date"
    if schema.get("type") == "array":
        item = schema.get("items") or {}
        return f"array<{item.get('type') or 'object'}>"
    return str(schema.get("type") or "string")


def _schema_example_value(name: str, schema: dict):  # noqa: ANN001
    schema = schema or {}
    if "default" in schema and schema.get("default") not in (None, ""):
        return schema.get("default")
    if "const" in schema:
        return schema.get("const")
    if schema.get("type") == "array":
        item = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        if item.get("type") == "object":
            return [{field: _schema_example_value(field, field_schema)
                     for field, field_schema in (item.get("properties") or {}).items()}]
        return [f"<{name}>"]
    if schema.get("type") == "object":
        return {field: _schema_example_value(field, field_schema)
                for field, field_schema in (schema.get("properties") or {}).items()}
    if schema.get("type") in {"number", "integer"}:
        return 0
    if schema.get("type") == "boolean":
        return False
    return f"<{name}>"


def _capability_example_input(contract: dict) -> dict:
    """Never teach callers to turn recorded query samples into live filters."""
    props = ((contract.get("parameters") or {}).get("properties") or {})
    if contract.get("kind") in _READ_CAPABILITY_KINDS:
        return {
            field: schema.get("default")
            for field, schema in props.items()
            if isinstance(schema, dict)
            and schema.get("x-dano-apply-default") is True
            and "default" in schema
        }
    required = set(contract.get("required") or [])
    return {
        field: _schema_example_value(field, schema)
        for field, schema in props.items()
        if field in required
    }


def _schema_default_text(schema: dict) -> str:
    """Keep recorded samples as recommendations while marking silent-safe defaults."""
    schema = schema or {}
    if schema.get("x-dano-require-current-value") is True:
        return "必须来自当前查询或用户选择；禁止复用录制标识"
    if "default" not in schema or schema.get("default") in (None, ""):
        return "运行时按用户上下文给出非空推荐值"
    label = "安全默认值" if schema.get("x-dano-apply-default") is True else "录制推荐值，需用户确认"
    return f"`{json.dumps(schema.get('default'), ensure_ascii=False)}`（{label}）"


def _query_default_text(schema: dict) -> str:
    if schema.get("x-dano-apply-default") is True:
        return _schema_default_text(schema)
    if "default" not in schema or schema.get("default") in (None, ""):
        return "无；仅在用户明确指定该筛选条件时传入"
    return (
        f"`{json.dumps(schema.get('default'), ensure_ascii=False)}`"
        "（录制参考值，禁止自动作为查询条件）"
    )


def _capability_contract_section(m: SkillManifest) -> str:
    lines = [
        "## 能力小结",
        "",
        "先按用户目标选择一项，再读取 `references/CAPABILITIES.md` 中对应小节；"
        "机器契约以 `references/CONTRACT.json` 为准。",
    ]
    for name, contract in _capability_contracts(m).items():
        required = "、".join(f"`{field}`" for field in (contract.get("required") or [])) or "无"
        output_fields = list(((contract.get("output_schema") or {}).get("properties") or {}))
        output_hint = "、".join(f"`{field}`" for field in output_fields) or "按机器契约"
        kind = contract.get("kind") or "operation"
        confirm = "执行前确认" if contract.get("requires_confirmation") else "直接执行"
        lines.append(
            f"- **{contract.get('title') or name}**（`{name}`，`{kind}`，{confirm}）："
            f"必填 {required}；返回 {output_hint}。"
        )
    return "\n".join(lines)


def _multi_capability_sop(m: SkillManifest) -> str:
    contracts = _capability_contracts(m)
    supports_related_mutation = bool(
        _relation_orchestration_policy(m, contracts)
    )
    lines = [
        "## 操作步骤(SOP)",
        "",
        (
            "1. 根据用户目标选择一个明确的 capability；查询和提交是不同能力，禁止默认选择写能力。"
            "用户明确要求关联撤回或删除时，按“单条与批量关联操作”显式调用来源查询和目标能力。"
            if supports_related_mutation else
            "1. 根据用户目标选择一个明确的 capability；查询和提交是不同能力，禁止默认选择写能力。"
        ),
        "   用户意图必须同时匹配能力的业务对象和动作；实体目录/候选列表不等于业务申请记录，"
        "未发布对应能力时必须说明不支持，不得用最相近的能力代替。",
        "2. 读取 `references/CAPABILITIES.md` 中所选能力小节和该 capability 的完整 `input_schema`；"
        "对动态选择项先运行 "
        "`bash scripts/submit.sh --capability <能力名> --list-options <字段名>` 获取实时候选。",
        "   查询能力不得为可选筛选字段主动提问。查询 input 只能包含用户本轮明确指定的业务筛选条件；"
        "录制推荐值不得作为查询筛选条件自动提交。"
        "没有筛选条件时传空 input，由脚本仅应用 `x-dano-apply-default: true` 的分页等安全默认值。",
        "3. **一次性收集本次所需字段。** 写能力必须收集全部必填表单项；查询能力只收集必填字段和"
        "用户明确要求的可选筛选条件，不得为其他可选筛选字段主动提问。原生调用 `ask_user_question` "
        "且本轮只调用一次，使用顶层 `title` 与 `questions[]`（`questions` 数组），把所需字段放在同一个分组表单；"
        "多个表单也必须先一次性汇总，不得在普通文本中提问，"
        "不得按表单、字段或分区拆成多轮追问。",
        "   每个问题必须使用所选能力参考小节给出的参数名作为 `id`、业务标签作为 `question`，并设置对应的 "
        "`inputType`、`required`、非空推荐 `default` 及 `options`/`dataSource`。录制默认值只作推荐，"
        "除非契约标记 `x-dano-apply-default: true`，否则必须等待用户回答。",
        "   字段标记 `x-dano-derived-from-query: true` 时，不得让用户猜测或自由填写标识："
        "必须先按 `x-dano-source-capability` 查询并定位用户所指的同一条记录，再把"
        "`x-dano-source-output` 的原值保存在内部调用参数中；该字段不得进入 `questions[]`，"
        "不得展示成需要用户确认或编辑的表单项，也不得使用同一记录的其他 `id`、单据号或录制样本。",
        "   所选能力参考小节是唯一表单来源，`questions[].id` 必须与参数名逐字一致，禁止翻译、改名或改成 snake_case。"
        "用户值优先；否则把能力参考小节“推荐默认值”列的主值逐字复制为表单 `default`；"
        "括号内录制值只用于溯源。候选项必须逐字来自能力参考小节或 `--list-options`，"
        "禁止自行生成、替换、增删候选项；枚举默认值必须等于候选的稳定 `id`，禁止回落为候选第一项。"
        "只有业务上确实必填的字段设置 `required: true`；"
        "工具校验失败时修正参数后静默重试，不在普通文本中模拟提问。",
        "   每次回复最多调用一次表单工具；多题按 `questions[].id` 映射答案，只有只收集一个非确认字段时"
        "才可使用顶层 `question`。录制样例必须保留为推荐值，但推荐默认值只用于 "
        "`ask_user_question` 展示，不得静默执行。",
    ]
    lines += [
        "4. `ask_user_question` 返回 `status=answered` 后，保存 `formId`，并按 `answer` 对象的 `id` "
        "映射回所选 capability 参数；name-ref 选择项按稳定 id 找回同一候选的 label 后提交，"
        "日期按 `dateFormat` 转换，数值转 JSON 数字，"
        "数组/复合字段按 input_schema 组装。返回 `cancelled`（用户取消）时立即停止。",
        (
            "5. 校验必填字段、类型和候选值。单条写操作按本次表单确认；"
            "批量写操作把完整记录集合放在同一表单中，整批只调用一次 "
            "`ask_user_question({confirm: true, formIds: [<answered.formId>]})`，"
            "只带 `formIds[]` 与 `confirm: true`，不得对每条记录重复发起确认。"
            "仅返回 `status=confirmed` 后继续，"
            "并以确认结果的 `answer` 为准。"
            if supports_related_mutation else
            "5. 校验必填字段、类型和候选值。写能力必须在同一 Assistant Turn 内再次调用 "
            "`ask_user_question({confirm: true, formIds: [<answered.formId>]})`；"
            "只带 `formIds[]` 与 `confirm: true`，仅返回 `status=confirmed` 后继续，"
            "并以确认结果的 `answer` 为准。"
        ),
        "6. Linux/macOS 使用 `bash scripts/submit.sh --capability <能力名> --json '<能力输入 JSON>'`；"
        "Windows PowerShell 使用 `scripts/submit.ps1` 传入同样参数。写能力同时带 `--confirm`。"
        + (
            "单条操作一次调用；关联批量撤回/删除按已确认的记录集合逐条调用，不得把数组塞进单条输入。"
            if supports_related_mutation else
            "一次调用由 Dano 完成内部接口编排。"
        ),
        "7. 按末行 JSON 的 `status` 处理结果。列表结果必须先运行 "
        "`python3 scripts/format_list.py --json '<output JSON>'`；Windows PowerShell 使用 "
        "`scripts/format_list.ps1 '<output JSON>'`。"
        "再以 Markdown 表格呈现；"
        "不要重复输出原始 JSON。",
    ]
    has_batch = any(
        contract.get("kind") in {"submit_batch", "validate_batch"}
        for contract in contracts.values()
    )
    if has_batch:
        lines.append(
            "8. 批量输入按 `entries[]` 逐项校验；任一条失败都要保留原索引和原因，"
            "不得把部分成功折叠成全部成功。"
        )
    return "\n".join(lines)


def _multi_capability_quality_section(m: SkillManifest) -> str:
    lines = ["## 质量标准(怎样算做好)", ""]
    for name, contract in _capability_contracts(m).items():
        required = "、".join(f"`{field}`" for field in (contract.get("required") or [])) or "无"
        verdict = "事实核查通过后才可报告成功" if contract.get("verify_required") else "返回值必须符合该能力 output_schema"
        lines.append(f"- `{name}`:只校验本能力必填输入 {required}；{verdict}。")
    lines.append("- 能力未明确、输入缺失或需要确认但未确认时不得执行；验证不通过时不得报告成功。")
    return "\n".join(lines)


# ─────────────────────────── 语义抽取(供丰富 SKILL.md)───────────────────────────
def _numeric_fields(props: dict) -> list[str]:
    """数值字段:manifest 的 type 优先(已按信源/语义判定),再退按名字/描述。与契约层同一判据。

    用途:① SKILL.md 标注「必须是 JSON 数字」② dano_call.py 提交前 str→number 强转
    (审批分支按数值比较,字符串会让网关条件失效)。
    """
    from dano.shared.std_fields import is_numeric_field
    return [k for k, v in (props or {}).items()
            if is_numeric_field(k, str((v or {}).get("description") or ""),
                                declared_type=(v or {}).get("type"))]


def _ptype(k: str, props: dict, numeric: set[str]) -> str:
    """SKILL.md 参数表的「类型」列:把 manifest 的 format 还原成对 agent 有意义的语义类型,
    不再把选择型/日期都显示成 string(那会让 agent 不知道该传名字还是 ID、是否日期)。"""
    p = props.get(k) or {}
    fmt = p.get("format")
    if fmt == "name-ref":
        return "枚举·名字→ID"
    if p.get("type") == "array" and ((p.get("items") or {}).get("format") == "name-ref"):
        return "多选·名字列表→记录"                       # 列表多选(参会人):传名字数组
    if fmt == "date-time":
        return "datetime"
    if fmt == "date":
        return "date"
    return "number" if k in numeric else (p.get("type") or "string")


def _is_name_ref(p: dict) -> bool:
    """名字→ID 选择型(单选 name-ref,或**多选** array<name-ref>):agent 传名字,Dano 运行期查内部信息。"""
    p = p or {}
    return p.get("format") == "name-ref" or (
        p.get("type") == "array" and (p.get("items") or {}).get("format") == "name-ref")


def _select_fields(props: dict) -> list[str]:
    """名字→ID 的选择型字段(选领导/字典下拉/参会人多选):agent 传名字,Dano 运行期查内部 ID。"""
    return [k for k, v in (props or {}).items() if _is_name_ref(v)]


def _opts_hint(prop: dict, cap: int = 12) -> str:
    """枚举字段在参数表/SOP 里的"可选值"提示:静态枚举列前 cap 个候选(超出指向 OPTIONS.md);
    **活接口目录**(选人/部门/审批人:有来源、无内置清单)→ 提示运行期实时拉,**不列陈旧快照**。"""
    opts = _option_labels(prop)
    if not opts:
        return "选项来自实时接口:先 `--list-options` 拉当前可选项再传名字" if (prop or {}).get("x-options-source") else ""
    shown = " / ".join(str(o) for o in opts[:cap])
    more = f" …(共 {len(opts)} 项,见 references/OPTIONS.md)" if len(opts) > cap else ""
    return f"可选:{shown}{more}"


def _option_labels(prop: dict) -> list[str]:
    prop = prop or {}
    raw = prop.get("x-options") or prop.get("x-options-snapshot") or prop.get("enum") or []
    if not raw and prop.get("x-enum-options"):
        raw = prop.get("x-enum-options") or []
    out: list[str] = []
    seen: set[str] = set()
    for opt in raw:
        if isinstance(opt, dict):
            label = str(opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value") or "").strip()
        else:
            label = str(opt or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _label(props: dict, k: str) -> str:
    """字段纯语义(SOP/复述用,简洁);无 label 退回 description、再退回 key。
    调用约定(传名字/勿传ID、日期格式)集中在参数表 description 与 SOP 通用提示里说一次,SOP 逐字段不再重复。"""
    p = props.get(k) or {}
    return p.get("label") or p.get("description") or k


_LONG_TEXT_FIELD_RE = re.compile(
    r"(描述|备注|说明|详情|意见|补充信息|description|remark|comment|notes?|memo)",
    re.IGNORECASE,
)


def _question_control(schema: dict, field: str = "") -> str:
    """Map the published field contract to an ask_user_question control."""
    schema = schema or {}
    business_type = str(schema.get("x-dano-business-type") or schema.get("type") or "").lower()
    item = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    selectable = bool(
        schema.get("format") == "name-ref"
        or item.get("format") == "name-ref"
        or schema.get("enum")
        or schema.get("x-options")
        or schema.get("x-options-snapshot")
        or schema.get("x-enum-options")
        or schema.get("x-options-source")
    )
    if selectable:
        return "treeSelect" if schema.get("x-dano-tree") or schema.get("childrenField") else "select"
    if business_type in {"textarea", "rich_text", "array", "object"}:
        return "textarea"
    semantic_text = " ".join(str(value or "") for value in (
        field,
        schema.get("title"),
        schema.get("label"),
        schema.get("description"),
    ))
    if business_type in {"string", "text", ""} and _LONG_TEXT_FIELD_RE.search(semantic_text):
        return "textarea"
    if schema.get("format") in {"date", "date-time"} or business_type in {"date", "datetime"}:
        default = schema.get("default")
        if (
            schema.get("format") == "date-time"
            and isinstance(default, str)
            and default
            and not re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::00)?", default)
        ):
            # ask_user_question 的 dateFormat 不支持秒/时区；无法无损适配时保留文本控件。
            return "text"
        return "date"
    if business_type == "boolean":
        return "radio"
    return "text"


def _question_options(schema: dict) -> list[dict]:
    """Project choices to stable ask_user_question ``{id,label}`` objects."""
    schema = schema or {}
    raw = (
        schema.get("x-options")
        or schema.get("x-options-snapshot")
        or schema.get("x-enum-options")
        or schema.get("enum")
        or []
    )
    if not raw and str(schema.get("type") or "").lower() == "boolean":
        raw = [{"id": "true", "label": "是"}, {"id": "false", "label": "否"}]
    options: list[dict] = []
    seen_ids: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            option_id = item.get("id", item.get("value", item.get("key")))
            label = item.get("label", item.get("text", item.get("name", option_id)))
        else:
            option_id = label = item
        if option_id in (None, "") or label in (None, ""):
            continue
        stable_id = str(option_id)
        if stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)
        option = {"id": option_id, "label": str(label)}
        if isinstance(item, dict) and isinstance(item.get("extra"), dict):
            option["extra"] = item["extra"]
        options.append(option)
    return options


def _question_option_source(schema: dict, field: str = "<字段名>") -> str:
    schema = schema or {}
    data_source = _question_data_source(schema)
    if data_source:
        return f"`dataSource: {json.dumps(data_source, ensure_ascii=False)}`"
    options = _question_options(schema)
    if options:
        return f"`options: {json.dumps(options, ensure_ascii=False)}`"
    if schema.get("x-options-source"):
        return (
            f"先运行 `--list-options {field}`；把返回的 `options` 对象数组原样用于表单，"
            "用户选择后按所选 `id` 找回 `label`，name-ref 参数提交 `label`"
        )
    return "无；自由输入"


def _question_data_source(schema: dict) -> dict | None:
    """Return only a complete ask_user_question remote-option contract."""
    schema = schema or {}
    source = schema.get("x-options-source-meta") if isinstance(schema.get("x-options-source-meta"), dict) else {}
    endpoint = str(
        source.get("endpoint") or source.get("source_url") or source.get("url") or ""
    ).strip()
    result_path = source.get("result_path") or source.get("resultPath")
    value_key = source.get("value_key") or source.get("idField")
    label_key = source.get("label_key") or source.get("labelField")
    if (
        schema.get("x-options-source")
        and endpoint
        and result_path
        and value_key
        and label_key
    ):
        data_source = {"type": "api", "endpoint": endpoint}
        method = str(source.get("method") or source.get("source_method") or "GET").upper()
        if method in {"GET", "POST"}:
            data_source["method"] = method
        params = dict(source.get("params") or {})
        category_key = str(source.get("category_key") or "").strip()
        if category_key and source.get("category_value") not in (None, ""):
            params[category_key] = source.get("category_value")
        if params:
            data_source["params"] = params
        for source_key, target_key in (
            ("result_path", "resultPath"), ("value_key", "idField"),
            ("label_key", "labelField"), ("children_key", "childrenField"),
        ):
            if source.get(source_key):
                data_source[target_key] = source[source_key]
        return data_source
    return None


def _question_default_text(schema: dict, *, query: bool, control: str) -> str:
    """Render a form-valid recommendation without changing the capability contract."""
    if schema.get("x-dano-derived-from-query") is True:
        source = ".".join(filter(None, (
            str(schema.get("x-dano-source-capability") or ""),
            str(schema.get("x-dano-source-output") or ""),
        )))
        return (
            f"无固定默认值；必须使用本次查询结果 `{source}` 中用户所选记录的原值，"
            "禁止使用录制样本或其他 ID"
        )
    if schema.get("x-dano-require-current-value") is True:
        return _schema_default_text(schema)
    if "default" not in schema or schema.get("default") in (None, ""):
        return _query_default_text(schema) if query else _schema_default_text(schema)
    recorded = schema.get("default")
    form_value = recorded
    note = ""
    options: list[dict] = []
    if control == "radio" and isinstance(recorded, bool):
        form_value = "true" if recorded else "false"
        note = "；提交前转换为 JSON 布尔值"
    elif schema.get("type") in {"array", "object"}:
        form_value = json.dumps(recorded, ensure_ascii=False, separators=(",", ":"))
        note = f"；提交前解析为 JSON {schema.get('type')}"
    else:
        options = _question_options(schema)
    if options:
        match = next(
            (
                option
                for option in options
                if recorded == option.get("id") or str(recorded) == str(option.get("label"))
            ),
            None,
        )
        if match is not None:
            form_value = match["id"]
            if form_value != recorded:
                note = f"；能力值 `{json.dumps(recorded, ensure_ascii=False)}`"
    if (
        control == "date"
        and schema.get("format") == "date-time"
        and isinstance(recorded, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:00", recorded)
    ):
        form_value = recorded[:-3]
    if form_value == recorded:
        return _query_default_text(schema) if query else _schema_default_text(schema)
    if query:
        return (
            f"`{json.dumps(form_value, ensure_ascii=False)}`"
            f"（表单推荐值；录制值 `{json.dumps(recorded, ensure_ascii=False)}`，"
            f"禁止自动作为查询条件{note}）"
        )
    safe = "安全默认值" if schema.get("x-dano-apply-default") is True else "录制推荐值，需用户确认"
    return (
        f"`{json.dumps(form_value, ensure_ascii=False)}`"
        f"（{safe}；录制值 `{json.dumps(recorded, ensure_ascii=False)}`{note}）"
    )


def _question_form_default(schema: dict, control: str):  # noqa: ANN001
    """Convert a recorded value to the exact value accepted by the form control."""
    if schema.get("x-dano-derived-from-query") is True:
        source = ".".join(filter(None, (
            str(schema.get("x-dano-source-capability") or ""),
            str(schema.get("x-dano-source-output") or ""),
        )))
        return f"<调用前替换为 {source} 中用户所选记录的原值>"
    if "default" not in schema or schema.get("default") in (None, ""):
        return "<调用前替换为基于用户上下文的非空推荐值>"
    recorded = schema.get("default")
    if control == "radio" and isinstance(recorded, bool):
        return "true" if recorded else "false"
    if schema.get("type") in {"array", "object"} and control == "textarea":
        return json.dumps(recorded, ensure_ascii=False, separators=(",", ":"))
    options = _question_options(schema)
    match = next(
        (
            option for option in options
            if recorded == option.get("id") or str(recorded) == str(option.get("label"))
        ),
        None,
    )
    if match is not None:
        return match["id"]
    if (
        control == "date"
        and schema.get("format") == "date-time"
        and isinstance(recorded, str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:00", recorded)
    ):
        return recorded[:-3]
    return recorded


def _question_request_template(name: str, contract: dict) -> dict:
    """Render a schema-valid grouped form request for the selected capability."""
    schema = contract.get("parameters") or {}
    required = set(schema.get("required") or [])
    query = contract.get("kind") in _READ_CAPABILITY_KINDS
    questions: list[dict] = []
    for field, prop in (schema.get("properties") or {}).items():
        if (
            not isinstance(prop, dict)
            or prop.get("x-dano-derived-from-query") is True
            or (query and field not in required)
        ):
            continue
        control = _question_control(prop, field)
        question = {
            "id": field,
            "question": str(
                prop.get("title") or prop.get("label")
                or (contract.get("field_labels") or {}).get(field)
                or prop.get("description") or field
            ),
            "inputType": control,
            "required": field in required,
            "default": _question_form_default(prop, control),
        }
        data_source = _question_data_source(prop)
        options = _question_options(prop)
        if data_source:
            question["dataSource"] = data_source
        elif options:
            question["options"] = options
        elif prop.get("x-options-source"):
            question["options"] = [{
                "id": "<调用前替换为 --list-options 返回的稳定 id>",
                "label": "<调用前替换为同一候选的 label>",
            }]
        if control in {"select", "treeSelect"}:
            question["multiple"] = bool(prop.get("multiple") or prop.get("type") == "array")
        if control == "date":
            question["dateFormat"] = str(
                prop.get("dateFormat")
                or ("yyyy-MM-dd HH:mm" if prop.get("format") == "date-time" else "yyyy-MM-dd")
            )
        questions.append(question)
    return {"title": str(contract.get("title") or name), "questions": questions}


def _question_rows(schema: dict, *, prefix: str = "") -> list[tuple[str, dict, bool]]:
    rows: list[tuple[str, dict, bool]] = []
    required = set((schema or {}).get("required") or [])
    for field, prop in ((schema or {}).get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        path = f"{prefix}.{field}" if prefix else str(field)
        rows.append((path, prop, field in required))
    return rows


def _nested_field_rows(schema: dict, *, prefix: str = "") -> list[tuple[str, dict, bool]]:
    """Describe nested leaves without pretending they are separate top-level form questions."""
    rows: list[tuple[str, dict, bool]] = []
    required = set((schema or {}).get("required") or [])
    for field, prop in ((schema or {}).get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        path = f"{prefix}.{field}" if prefix else str(field)
        if prop.get("type") == "object" and isinstance(prop.get("properties"), dict):
            rows.extend(_nested_field_rows(prop, prefix=path))
            continue
        item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        if prop.get("type") == "array" and isinstance(item.get("properties"), dict):
            rows.extend(_nested_field_rows(item, prefix=f"{path}[]"))
            continue
        if prefix:
            rows.append((path, prop, field in required))
    return rows


def _question_collection_block(name: str, contract: dict) -> list[str]:
    """Render the exact parameter-to-question mapping for one capability."""
    schema = contract.get("parameters") or {}
    rows = _question_rows(schema)
    suffix = (
        "可用查询字段（可选筛选条件仅在用户明确指定时加入问题）"
        if contract.get("kind") in _READ_CAPABILITY_KINDS else
        "字段配置"
    )
    lines = [f"   `{name}`（{contract.get('title') or name}）{suffix}："]
    if not rows:
        lines.append("   - 无需收集业务字段。")
        return lines
    lines += [
        "",
        "   | 参数名 / `id` | label / `question` | 控件 `inputType` | 参数类型 | 必填 | 推荐默认值 | 候选配置 |",
        "   |---|---|---|---|---|---|---|",
    ]
    for field, prop, required in rows:
        field_key = field.removesuffix("[]").split(".")[-1]
        label = str(
            prop.get("title") or prop.get("label")
            or (contract.get("field_labels") or {}).get(field_key)
            or prop.get("description") or field
        ).replace("|", "\\|")
        control = _question_control(prop, field)
        date_format = (
            f" / `{prop.get('dateFormat') or ('yyyy-MM-dd HH:mm' if prop.get('format') == 'date-time' else 'yyyy-MM-dd')}`"
            if control == "date" else ""
        )
        default_text = _question_default_text(
            prop,
            query=contract.get("kind") in _READ_CAPABILITY_KINDS,
            control=control,
        )
        lines.append(
            f"   | `{field}` | {label} | `{control}`{date_format} | {_schema_type_text(prop)} | "
            f"{'是' if required else '否'} | "
            f"{default_text} | {_question_option_source(prop, field)} |"
        )
    return lines


def _question_request_block(name: str, contract: dict, heading: str) -> list[str]:
    """Add one copyable request shape without inventing fields outside the contract."""
    request = _question_request_template(name, contract)
    lines = ["", heading, ""]
    if not request["questions"]:
        lines += [
            "该查询没有必填筛选项：用户未明确给筛选条件时不要调用表单，直接提交空 `input`；"
            "用户明确给出可选筛选条件时，只从上表选取这些字段并按同一 `title + questions[]` 结构组装。",
        ]
        return lines
    lines += [
        "以下 JSON 是调用 `ask_user_question` 的固定结构。"
        "出现“调用前替换”占位值时，必须先根据当前上下文或 `--list-options` 结果完成替换，禁止原样发送：",
        "",
        "```json",
        json.dumps(request, ensure_ascii=False, indent=2),
        "```",
        "",
        "前端必须按 `questions[]` 原顺序和 `inputType` 渲染为一个分组表单；"
        "不得展示原始 JSON，不得拆成逐字段聊天问答。",
    ]
    return lines


def _capability_reference_md(m: SkillManifest) -> str:
    """Human-readable per-capability reference; CONTRACT.json remains authoritative."""
    lines = [
        "# 能力参数参考",
        "",
        "只读取当前选中能力的小节。字段名、类型、必填性和默认值均来自已发布能力契约；"
        "本文件只把它们整理成可执行表单视图。",
        "",
        "选择控件返回 option `id`；不得丢弃 id 或按 label 合并不同记录。"
        "若能力字段格式为 `name-ref`，按所选 id 找回同一候选的 label 后提交 label。",
    ]
    for name, contract in _capability_contracts(m).items():
        title = contract.get("title") or name
        lines += [
            "",
            f"## {title}",
            "",
            f"- capability：`{name}`",
            f"- 类型：`{contract.get('kind') or 'operation'}`",
            f"- 写前确认：{'是' if contract.get('requires_confirmation') else '否'}",
        ]
        block = _question_collection_block(name, contract)
        if len(block) == 2:
            lines.append("- 表单字段：无")
            continue
        lines += ["", *[
            line[3:] if line.startswith("   ") else line
            for line in block[1:]
        ]]
        lines += _question_request_block(name, contract, "### 固定表单请求")
        nested_rows = _nested_field_rows(contract.get("parameters") or {})
        if nested_rows:
            lines += [
                "",
                "### 批量/嵌套字段结构",
                "",
                "这些路径用于解释 textarea 中 JSON 的结构，不应拆成多次提问。",
                "",
                "| 路径 | 类型 | 必填 | 推荐默认值 |",
                "|---|---|---|---|",
            ]
            query = contract.get("kind") in _READ_CAPABILITY_KINDS
            for field, prop, required in nested_rows:
                lines.append(
                    f"| `{field}` | {_schema_type_text(prop)} | {'是' if required else '否'} | "
                    f"{_question_default_text(prop, query=query, control=_question_control(prop, field))} |"
                )
    return "\n".join(lines) + "\n"


def _business_capability_reference_md(manifests: list[SkillManifest]) -> str:
    """Group detailed capability forms by exported operation."""
    lines = [
        "# 能力参数参考",
        "",
        "先选择操作，再只读取该操作下当前 capability 的小节。机器契约以 `CONTRACT.json` 为准。",
    ]
    for manifest in manifests:
        lines += ["", f"## {manifest.title or manifest.action}"]
        for name, contract in _capability_contracts(manifest).items():
            lines += [
                "",
                f"### {contract.get('title') or name}",
                "",
                f"- capability：`{name}`",
                f"- 类型：`{contract.get('kind') or 'operation'}`",
                f"- 写前确认：{'是' if contract.get('requires_confirmation') else '否'}",
            ]
            block = _question_collection_block(name, contract)
            if len(block) == 2:
                lines.append("- 表单字段：无")
                continue
            lines += ["", *[
                line[3:] if line.startswith("   ") else line
                for line in block[1:]
            ]]
            lines += _question_request_block(name, contract, "#### 固定表单请求")
            nested_rows = _nested_field_rows(contract.get("parameters") or {})
            if nested_rows:
                lines += [
                    "",
                    "#### 批量/嵌套字段结构",
                    "",
                    "以下路径属于同一个 textarea JSON，不拆成多次提问：",
                ]
                for field, prop, required in nested_rows:
                    lines.append(
                        f"- `{field}`：{_schema_type_text(prop)}，"
                        f"{'必填' if required else '可选'}"
                    )
    return "\n".join(lines) + "\n"


def _approval_section(meta: dict) -> str:
    """从 business_meta(x-flow)渲染审批链 / 金额阈值;没有就返回空(不臆造)。"""
    if not isinstance(meta, dict):
        return ""
    chain = meta.get("approvalChain") or meta.get("approval_chain") or []
    thresholds = meta.get("thresholds") or []
    if not chain and not thresholds:
        return ""
    steps: list[str] = []
    for c in chain:
        if isinstance(c, dict) and c.get("step"):
            cond = c.get("condition")
            steps.append(f"{c['step']}" + (f"〔{cond}〕" if cond else ""))
        elif isinstance(c, str):
            steps.append(c)
    lines = ["## 审批路径(服务端按规则执行,以下为预测)", ""]
    if steps:
        lines += ["```text", "发起人 → " + " → ".join(steps) + " → 结束", "```"]
    if thresholds:
        lines.append("\n金额边界规则:")
        for t in thresholds:
            if not isinstance(t, dict):
                continue
            fld = t.get("field", "amount")
            adds = t.get("adds", "")
            if "gt" in t:
                lines.append(f"- `{fld}` 大于 {t['gt']} → 追加「{adds}」(等于不触发)")
            elif "gte" in t:
                lines.append(f"- `{fld}` 大于等于 {t['gte']} → 追加「{adds}」")
    lines.append("\n> 这是按当前规则做的**预测**;最终审批节点以 OA 工作流引擎实际执行为准。\n")
    return "\n".join(lines)


def _sop_section(m: SkillManifest, flags: str, cflag: str) -> str:
    """Render one executable, schema-grounded SOP for a single capability."""
    f = m.flow or {}
    n = int(f.get("step_count", 1) or 1)
    write = m.requires_confirmation
    contracts = _capability_contracts(m)
    name, contract = next(iter(contracts.items()))
    L: list[str] = [
        "## 操作步骤(SOP)",
        "",
        f"1. 确认用户要执行 `{name}`（{contract.get('title') or m.title or name}），"
        "而不是未发布的查询、撤回或审批动作。",
        "   用户意图必须同时匹配业务对象和动作；实体目录/候选列表不等于业务申请记录，"
        "未发布对应能力时必须说明不支持，不得用最相近的能力代替。",
        "2. 读取 `references/CAPABILITIES.md` 中该能力小节和完整 `input_schema`；动态选择项先运行 "
        f"`bash scripts/submit.sh --capability {name} --list-options <字段名>` 获取实时候选，"
        "不得猜测选项名称或内部 ID。",
        (
            "3. **只收集本次所需查询字段。** 必填字段必须收集；可选筛选条件仅在用户明确指定时收集，"
            "不得主动补入、提问或提交其他录制筛选值。原生调用 `ask_user_question` 且本轮只调用一次，"
            if contract.get("kind") in _READ_CAPABILITY_KINDS else
            "3. **一次性收集全部表单项。** 原生调用 `ask_user_question` 且本轮只调用一次，"
        ) + (
            "使用顶层 `title` 与 `questions[]`（`questions` 数组），把所有字段放进同一个分组表单；"
            "多个表单也必须先一次性汇总，不得在普通文本中提问，"
            "不得按表单、字段或分区拆成多轮追问。每项都必须设置字段名 `id`、业务 `label/question`、"
            "控件 `inputType`、`required`、非空推荐 `default`，以及适用的 `options`、"
            "`dataSource`、`multiple`、`dateFormat`。"
        ),
        "   所选能力参考小节是唯一表单来源，`questions[].id` 必须与参数名逐字一致，禁止翻译、改名或改成 "
        "snake_case。用户值优先；否则把能力参考小节“推荐默认值”列的主值逐字复制为表单 `default`；"
        "括号内录制值只用于溯源。候选项必须逐字来自字段配置或 "
        "`--list-options`，禁止自行生成、替换、增删候选项；枚举默认值必须等于候选的稳定 `id`，"
        "禁止回落为候选第一项。只有业务上确实必填的字段设置 `required: true`；"
        "工具校验失败时修正参数后静默重试。",
        "   每次回复最多调用一次表单工具；多题按 `questions[].id` 映射答案，只有只收集一个非确认字段时"
        "才可使用顶层 `question`。录制样例必须保留为推荐值，但推荐默认值只用于 "
        "`ask_user_question` 展示，不得静默执行。",
    ]
    if contract.get("kind") in _READ_CAPABILITY_KINDS:
        L += [
            "   查询能力不得为可选筛选字段主动提问。查询 input 只能包含用户本轮明确指定的业务筛选条件；"
            "录制推荐值不得作为查询筛选条件自动提交。"
            "没有筛选条件时传空 input，由脚本仅应用 `x-dano-apply-default: true` 的分页等安全默认值。",
        ]
    L += [
        "4. `ask_user_question` 返回 `status=answered` 后，保存 `formId`，按 `answer` 对象的 `id` "
        "映射为能力参数；name-ref 选择项按稳定 id 找回同一候选的 label 后提交，日期按 `dateFormat` "
        "转换，数值转 JSON 数字，数组/复合字段按 "
        "`input_schema` 组装并再次校验。返回 `cancelled`（用户取消）时立即停止。",
    ]
    if write:
        L.append(
            "5. 在同一 Assistant Turn 内调用 "
            "`ask_user_question({confirm: true, formIds: [<answered.formId>]})`；"
            "只带 `formIds[]` 与 `confirm: true`，仅返回 `status=confirmed` 后执行，"
            "并以确认结果的 `answer` 为最终参数。"
        )
    else:
        L.append("5. 这是只读能力，不需要最终 `confirm: true`；参数齐全后直接执行。")
    L += [
        f"6. Linux/macOS 运行 `bash scripts/submit.sh {flags}{cflag}`；Windows PowerShell 运行对应 "
        "`scripts/submit.ps1` 并传入相同参数。一次调用由 Dano 完成内部接口编排。",
    ]
    pre = f.get("preconditions") or []
    if pre:
        L.append("   执行前必须满足：")
        for p in pre:
            msg = (p.get("message") or "").strip() or p.get("check")
            L.append(f"   - {msg}（`{p.get('check')}`）")
    sp = f.get("step_paths") or []
    if n > 1 and sp:
        L.append(f"   Dano 将按序执行以下 {n} 个接口，调用方不得拆开执行：")
        for i, s in enumerate(sp, 1):
            L.append(f"   {i}. `{s['method']} {s['path']}`")
    L += [
        "7. 读取脚本末行 JSON：`succeeded` 才报告成功；`need_select` 补充候选；"
        "`need_confirm` 重新确认；`failed` 按 `reason` 处理。列表结果必须运行 "
        "`python3 scripts/format_list.py --json '<output JSON>'`；Windows PowerShell 使用 "
        "`scripts/format_list.ps1 '<output JSON>'`。"
        "最终只用 Markdown 表格呈现，"
        "不要重复粘贴原始 JSON。写操作超时或结果不明时不得自动重试。",
    ]
    return "\n".join(L)


def _quality_section(m: SkillManifest) -> str:
    """质量标准(怎样算做好):**纯函数、grounded、零业务/框架字面量**。

    输入合格 ← preconditions/computes/parameters;落点正确 ← business_meta;
    结果合格 ← flow.verify / judged_by_code;达成目标 ← goal.success_criteria;红线 ← goal.forbidden_steps。
    源空即省略该项;只读类给轻量"如实反映"。任意业务/框架自适配。
    """
    keys, required, props = _fields(m)
    numset = set(_numeric_fields(props))
    f = m.flow or {}
    g = m.goal or {}
    bm = m.business_meta or {}
    write = m.requires_confirmation
    # 只读查询(非写、无前置、无成功标准)→ 轻量验收
    if not write and not (f.get("preconditions") or g.get("success_criteria")):
        return ("## 质量标准(怎样算做好)\n\n"
                "- 结果应**如实反映系统数据**;查不到 / 为空就如实告知,**不要编造**记录或字段。")
    L = ["## 质量标准(怎样算做好)", "", "逐条自检;不全过就**不算做好**,不要对用户报成功。", ""]

    # ① 输入合格
    L.append("**① 输入合格(提交前)**")
    reqs = [k for k in keys if k in required]
    L.append(f"- 必填字段齐全:{'、'.join('`' + k + '`' for k in reqs)}。" if reqs
             else "- 用户给定的字段已逐项确认,无臆造。")
    num = [k for k in keys if k in numset]
    if num:
        L.append(f"- 数值字段({'、'.join('`' + k + '`' for k in num)})为数字。")
    sel = _select_fields(props)
    if sel:
        L.append(f"- 选择型字段({'、'.join('`' + k + '`' for k in sel)})传名字/选项文字,**非内部 ID**。")
    for c in (f.get("computes") or []):
        L.append(f"- `{c['out']}` 与 `{c['expr']}` 的计算结果一致(给了不一致先与用户确认)。")
    for p in (f.get("preconditions") or []):
        msg = (p.get("message") or "").strip() or p.get("check")
        L.append(f"- 满足前置:{msg}。")

    # ② 落点正确
    L += ["", "**② 落点正确**",
          "- 返回 `succeeded` 且带**业务标识**(单号/实例号)= 已真正进入业务流程。"]
    chain = bm.get("approvalChain") or bm.get("approval_chain") or []
    if chain:
        steps = " → ".join((c.get("step", "") if isinstance(c, dict) else str(c)) for c in chain if c)
        L.append(f"- 已进入正确审批链:{steps}。")
    if bm.get("thresholds"):
        L.append("- 达到阈值时按规则自动加签(见上方「审批路径」)。")

    # ③ 结果合格
    L += ["", "**③ 结果合格(真生效)**"]
    if f.get("verify"):
        L.append("- `status=succeeded` **且事实核查通过**(Dano 回查确认)才算成功;"
                 "回查未过 / 接口 200 但空操作 → **不算成功**,原样返回给用户,**勿谎报**。")
    elif f.get("judged_by_code"):
        L.append("- 以**业务返回码**判成功(非 HTTP 字面);失败码即不算成功,**勿谎报**。")
    else:
        L.append("- `status=succeeded` 才算成功;`failed` 据 `reason` 处置,**勿把失败说成成功**。")
    for sc in (g.get("success_criteria") or []):
        L.append(f"- 达成:{sc}。")

    # 红线
    L += ["", "**红线(命中即不合格)**"]
    L.append("- 不重复提交(超时/结果不明 → 先核对,别重跑);不绕过 `--confirm` / 不伪造身份或结果。"
             if write else "- 不伪造数据或结果;不绕过平台闸门。")
    forb = g.get("forbidden_steps") or []
    if forb:
        L.append(f"- 不执行越权/破坏动作:{'、'.join('`' + s + '`' for s in forb[:8])}。")
    return "\n".join(L)


def _interaction_section(m: SkillManifest) -> str:
    """Render only the non-negotiable ask_user_question rules used by the SOP."""
    contracts = _capability_contracts(m)
    lines = [
        "### 表单工具硬约束",
        "",
        "- 填表或补字段时原生调用 `ask_user_question`；每次回复最多调用一次，多个字段必须使用顶层 `title`，"
        "并放入同一 `questions` 数组（`questions[]`）一次提交，不要逐字段拆成多轮。",
        "- 查询能力不得为可选筛选字段主动提问、自动使用录制推荐值或补造条件；没有用户筛选条件时直接使用空 input。",
        "- 多个表单、分区或连续步骤先一次性汇总；不得按表单、分区、步骤或字段分别提问。只收集一个非确认字段时才使用顶层 `question`。",
        "- `questions[].id` 必须与能力参数名逐字一致，禁止翻译、改名或改成 snake_case；多题按 questions 的 `id` 映射答案。",
        "- SOP 第3步的字段配置表是唯一表单来源；`id`、`question`、`inputType`、`required`、"
        "`default`、`options`/`dataSource` 必须逐项照抄，任一不一致都必须在展示前修正。",
        "- 用户值优先；否则把能力参考小节“推荐默认值”列的主值逐字复制为表单 `default`；"
        "括号内录制值只用于溯源。普通问题提供非空 `default`，"
        "禁止使用“请填写…”“例如…”“待确认”等占位内容。",
        "- `options`/`dataSource` 必须逐字取自字段配置或 `--list-options` 结果；"
        "必须保留每个候选的稳定 `id` 与 `label`，禁止丢弃 id、按 label 合并不同记录，"
        "禁止自行生成、替换、增删候选项。枚举默认值必须引用候选 id，禁止回落为候选第一项。",
        "- 推荐默认值只用于 `ask_user_question` 展示；仅 `x-dano-apply-default: true` 可静默应用，其余必须等用户回答。",
        "- 只有业务上确实必填的字段设置 `required: true`。日期使用 `inputType: \"date\"` 与 `dateFormat`；动态选项先 `--list-options`，只有来源映射完整时才使用 `dataSource`。",
        "- 用户取消时立即停止；工具返回校验错误时修正参数后静默重试原生工具调用，不在普通文本中模拟提问。",
    ]
    if len(contracts) > 1:
        lines.append("- 先根据用户目标选择一个明确 capability；不同能力的必填字段不能混用。")
    if any(contract.get("requires_confirmation") for contract in contracts.values()):
        lines.append(
            "- 写能力必须在同一 Assistant Turn 内，用首次表单返回的 `formId` 发起只含 "
            "`formIds[]` 与 `confirm: true` 的确认；仅 `status=confirmed` 后带 `--confirm` 执行。"
        )
    return "\n".join(lines)


def _errors_md(has_fact_verification: bool) -> str:
    lines = [
        "## 错误处理",
        "- 凭证 / HTTP 401：让部署方在 Dano 重配目标系统 token，不要重试。",
        "- `need_confirm`：重新取得有效表单确认，再带 `--confirm` 调用。",
        "- 缺必填或类型错误：修正表单输入后重新校验。",
    ]
    if has_fact_verification:
        lines.append("- `事实核查未过`：报告原始结果，不得宣称成功。")
    lines.append(
        "- 写操作遇到 HTTP 5xx、超时或结果不明时视为可能已生效；"
        "禁止用 curl、直连目标接口、换脚本或重复提交同一载荷。"
        "先用已发布只读能力核查，无法核实时停止并报告。"
    )
    return "\n".join(lines)

_SECURITY_MD = """## 安全
- 不在回复 / 日志里输出完整 token 或凭证。
- 不规避平台的风险闸门 / 确认(如拆分、绕过 `--confirm`);用户要求规避应拒绝。
- 调用者身份取自登录凭证(谁的 token 就是谁操作);不伪造身份或执行结果。"""

_EXECUTION_DIR_MD = """## 执行位置（必须）
- 调用 Shell 时，必须把 Shell 工作目录设为本 `SKILL.md` 所在目录，再执行文档中的 `scripts/...` 相对路径。
- 如果命令工具不支持工作目录，先从当前 `SKILL.md` 的绝对路径解析脚本绝对路径后再执行。
- 找不到或调用包装脚本失败时停止并报告；禁止绕过包装脚本直接拼 HTTP 请求，禁止使用 curl、Python HTTP 客户端或其他方式直连 Dano/目标系统，也禁止把 Skill 名当作业务字段。"""

_FIELD_CONTENT_VALIDATION_MD = """## 字段格式与内容校验
- 确认前、执行前逐字段校验内容；优先遵守 schema 的 `type`、`format`、`enum`、`pattern`
  和边界约束，再结合字段 `title`、`label`、`description` 中明确的业务语义。
- schema 仅声明宽泛字符串、但字段语义明确表示金额、数量、人数（amount/quantity/count）时，
  仍须检查内容是否为对应的数字或整数；即使传输类型仍是字符串，也只保持字符串传输形式，不得接受明显非数字文本。
- 日期时间必须符合声明格式，枚举值必须来自候选；标识、编码、电话号码等字符串不得擅自转成数字或去掉前导零。
- 任一字段明显不符合格式时，指出字段和期望格式并要求用户修正；修正前不得进入确认或执行，
  不得静默替换、猜值或自动重试。
- schema 未提供依据时不得臆造最小值、最大值、精度、长度或业务范围，只拦截确定的格式冲突。"""

_LIST_OUTPUT_MD = """## 列表输出要求
- 查询结果、候选列表或任何数组数据必须先运行 `python3 scripts/format_list.py --json '<output JSON>'` 格式化。
- Windows PowerShell 使用 `scripts/format_list.ps1 '<output JSON>'`，避免管道编码破坏中文。
- 最终回复使用脚本生成的 Markdown 表格；无数据时明确显示“无数据”，不要重复粘贴原始 JSON。
- Markdown 表头、分隔行和数据行之间不得插入空行；单元格内换行统一使用 `<br>`。
- 非列表对象仍按能力的 `output_schema` 解读，不要为了套表格丢失业务字段。"""

_IDENTIFIER_OUTPUT_MD = """## 标识字段规则
- 标识语义只认 `output_schema` 字段的 `x-dano-identifier-role`：
  `record`、`process_instance`、`business_document` 分别表示记录标识、流程实例标识和业务单据标识。
- 脚本成功结果包含 `business_identifiers` 时，展示和后续操作必须按其中的 `label` 与 `value`
  原样使用；不存在 `document_number` 时不得把 `record_id` 或 `process_instance_id` 写成“单据编号”。
- 契约没有声明标识角色时保留原字段名，禁止根据字段名、值形状或当前业务场景猜测标识含义。
- 后续操作需要哪一个参数，就只使用同名字段或 `capability_relations` 明确映射的字段；
  用户给出另一类编号时，先用已发布查询能力定位同一条记录，再取目标能力要求的字段，禁止直接改名代入。
- 输入字段标记 `x-dano-derived-from-query: true` 时，`x-dano-source-capability` 与
  `x-dano-source-output` 是唯一允许的数据来源；先定位记录再写入内部调用参数，
  禁止把该字段放进 `questions[]` 或让用户猜内部标识。
- 面向用户隐藏哪些字段、字段顺序和标题均由 `output_schema` 的展示元数据决定；
  包装脚本原始 `output` 始终保留完整结果供后续能力准确取值。"""

_FRONTEND_OUTPUT_MD = """## 固定返回展示
- `succeeded`：查询结果按 `output_schema` 展示，数组只显示格式化后的 Markdown 表格；
  写操作按下一条规则展示。
- 成功的写操作使用能力标题给出业务化完成结论（例如“酒店申请已提交”“酒店申请已撤回”
  “酒店申请记录已删除”）；不得逐项展示
  `result.code`、`result.data`、`result.msg`，也不得向用户展示裸 `true`、内部 ID 或空消息。
  技术响应仍保留在脚本 `output` 中供后续能力串联；只有带明确业务标题或
  `x-dano-identifier-role` 的字段才补充展示。
- 成功结果含 `request_markdown` 时，必须把该 Markdown 原样单独输出为可点击链接；禁止输出 `<a>` HTML
  或把 Markdown 放进代码块。链接的 `target="_blank"`、`rel="noopener noreferrer"` 由结构化
  `request_link` 提供给支持这些属性的宿主。
- 不得猜测、改写或自行拼接链接；没有 `request_markdown` 时不显示链接。
- `presentation.forbid_inferred_labels=true` 时，未类型化结果只能称“接口返回值”，禁止把其中的
  `id`、`data` 或任意字符串擅自命名为申请编号、单据编号、流程编号等业务字段。
- 非成功状态不显示成功链接，展示脚本返回的原因或下一步。"""


# ─────────────────────────── SKILL.md ───────────────────────────
def _skill_md(m: SkillManifest, slug: str) -> str:
    confirm = m.requires_confirmation
    contracts = _capability_contracts(m)
    cap_line = ", ".join(contracts)
    has_fact_verification = any(contract.get("verify_required") for contract in contracts.values())
    has_batch_capability = any(
        contract.get("kind") in {"submit_batch", "validate_batch"}
        for contract in contracts.values()
    ) or bool(_relation_orchestration_policy(m, contracts))
    multi_capability = len(contracts) > 1
    flags = _flags(m)
    cflag = " --confirm" if confirm else ""
    confirm_note = ("\n> ⚠ 高风险写操作:**执行前必须向用户复述将提交内容并取得同意**,确认后再带 `--confirm` 调用。\n"
                    if confirm else "")
    desc = _trigger_description(m, contracts)
    # 审批路径(有 business_meta 才出,grounded);放在 SOP 前,供阶段3 引用
    approval = _approval_section(getattr(m, "business_meta", {}) or {})
    approval_md = (approval + "\n\n") if approval else ""
    parameter_md = _capability_contract_section(m)
    sop = _multi_capability_sop(m) if multi_capability else _sop_section(m, flags, cflag)
    relationships = _capability_relationship_section(m)
    withdrawal_orchestration = _related_mutation_sop(m)
    default_capability = _export_default_capability(m)
    if multi_capability:
        protocol_default = "本 Skill 有多个独立能力，调用时必须显式指定 `--capability`"
    else:
        protocol_default = f"默认 capability:`{default_capability}`"
    protocol_example = (
        {"capability": "<capability>", "input": {}, "confirm": False}
        if multi_capability
        else {
            "capability": default_capability,
            "input": _capability_example_input(next(iter(contracts.values()))),
            "confirm": confirm,
        }
    )
    platform_guards = "业务编排与风险闸门"
    if has_fact_verification:
        platform_guards += "、已配置的事实核查"
    success_meaning = (
        "所选能力执行完成；要求事实核查的写能力已核查通过"
        if has_fact_verification else
        "所选能力已按业务成功规则完成，并通过输出合同校验"
    )
    partial_status_row = (
        "| `partial_success` | 批量能力仅部分条目成功 | 逐项报告成功/失败及原索引；"
        "不得笼统宣称全部成功，也不得自动重试成功项 |\n"
        if has_batch_capability else ""
    )
    return f"""---
name: {json.dumps(_skill_name(m.title, slug), ensure_ascii=False)}
description: {json.dumps(desc, ensure_ascii=False)}
---

# {m.title}

这是 Dano **已上架 Skill 的代理**:{platform_guards}都在 Dano 侧。本端负责**收集参数、本地校验、提交前确认**,再调用 Dano,**不接触目标系统凭证、不自行裁定结果**。
{confirm_note}
## 运行前置与调用协议
- 部署方提供 `DANO_URL` 与 `DANO_TENANT_KEY`，不要写入 Skill、回复或日志。
- 可用 capability：`{cap_line}`。
- {protocol_default}；`--json` 使用以下 envelope：

```json
{json.dumps(protocol_example, ensure_ascii=False)}
```

- 能力字段和表单控件见 `references/CAPABILITIES.md`；完整机器契约见 `references/CONTRACT.json`；
  选择型字段存在时读取 `references/OPTIONS.md`。
- 流程句柄、调用者身份与凭证由 Dano 运行期注入，不要向用户索取。

{_EXECUTION_DIR_MD}

{approval_md}{sop}

{parameter_md}

{relationships}

{withdrawal_orchestration}

{_LIST_OUTPUT_MD}

{_FIELD_CONTENT_VALIDATION_MD}

{_IDENTIFIER_OUTPUT_MD}

{_FRONTEND_OUTPUT_MD}

## 输出契约(脚本末行 JSON)
| status | 含义 | 你应做的 |
|---|---|---|
| `succeeded` | {success_meaning} | 按该能力的 output_schema 解读 `output` |
{partial_status_row}| `need_select` | 复合流程消歧:有多个候选待选 | 把 `candidates` 给用户选,再用 `--json` 把选中项的 `bind` 值带上重跑 |
| `need_confirm` | 写操作未确认被拦 | 向用户确认后,**带 `--confirm` 重跑** |
| `failed` | 失败(见 `reason`) | 按错误处理处置，勿谎报成功 |

{_errors_md(has_fact_verification)}

{_SECURITY_MD}

## 限制
只支持本文“能力小结”列出的 capability；任何未列出的业务动作都必须明确说明不支持，不得选择相近能力代替。
"""


# ─────────────────────────── references ───────────────────────────
def _options_md(m: SkillManifest) -> str | None:
    """references/OPTIONS.md:选择型字段的**候选值清单**(快照进 skill,让 agent 从真实选项里选,不凭空猜)。
    无任何选择型候选 → 返回 None(不产生空文件)。提交时 Dano 仍按名字现查内部 ID(选项更新以运行期为准)。"""
    contracts = _capability_contracts(m)
    blocks: list[str] = []
    has_live_source = False
    has_snapshot = False

    def walk(capability: str, node: dict, prefix: str = "") -> None:
        nonlocal has_live_source, has_snapshot
        for key, prop in ((node or {}).get("properties") or {}).items():
            if not isinstance(prop, dict):
                continue
            path = f"{prefix}.{key}" if prefix else key
            item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
            selectable = prop.get("format") == "name-ref" or item.get("format") == "name-ref" or prop.get("x-options-source")
            if selectable:
                opts = _question_options(prop)
                has_live_source = has_live_source or bool(prop.get("x-options-source"))
                has_snapshot = has_snapshot or bool(opts)
                command = f"bash scripts/submit.sh --capability {capability} --list-options {key}"
                label = str(prop.get("label") or prop.get("title") or key)
                if prop.get("x-options-source") and not opts:
                    blocks.append(
                        f"## {label}(`{path}`) · `{capability}` — **实时接口**\n\n"
                        f"运行 `{command}` 拉当前可选项，再传显示名；录制快照不构成有效值限制。"
                    )
                elif opts:
                    head = f"## {label}(`{path}`) · `{capability}` — 共 {len(opts)} 项"
                    values = "\n".join(
                        f"- `{json.dumps(option['id'], ensure_ascii=False)}` → {option['label']}"
                        for option in opts
                    )
                    blocks.append(
                        f"{head}\n\n表单保留稳定 id 与显示 label；name-ref 参数按 id 找回 label 后提交:\n{values}"
                    )
            walk(capability, prop, path)
            if item:
                walk(capability, item, f"{path}[]")

    for capability, contract in contracts.items():
        walk(capability, contract.get("parameters") or {})
    if not blocks:
        return None
    source_note = (
        "带有可信 `x-options-source` 的字段会在运行期调用其真实来源接口；没有该证据的字段不会伪装成动态来源。\n"
        if has_live_source else
        "本产物没有声明可验证的动态选项接口；以下候选仅来自已确认的页面/录制证据。\n"
    )
    snapshot_note = (
        "下面是录制时抓取的**离线快照**(可能过时,仅供快速参考);提交时按字段契约映射显示名与真实值。\n"
        if has_snapshot else ""
    )
    return ("# 可选值参考\n\n选择型字段的候选值。多能力 Skill 必须同时指定 `--capability`。\n"
            + source_note + snapshot_note + "\n"
            + "\n\n".join(blocks) + "\n")


# ─────────────────────────── scripts ───────────────────────────
_PY_TEMPLATE = r'''#!/usr/bin/env python3
"""由 Dano 自动生成:调用已上架 Skill「__TITLE__」(真实执行在 Dano 侧)。

按 capability 组装并校验 input -> POST Dano 能力调用端点；最后一行打印 JSON 状态供 agent 解析。
凭证 / 模板 / base_url / 调用者身份由 Dano 注入,本端不接触。
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SKILL_ID = "__SKILL_ID__"
TOOL = "__TOOL__"
CAPABILITY = __CAPABILITY__
SOURCE_PAGE_URL = __SOURCE_PAGE_URL__
PROTOCOL = "dano.capability_call.v1"
CAPABILITIES = __CAPABILITIES__
FIELDS = __FIELDS__
REQUIRED = __REQUIRED__
NUMERIC = __NUMERIC__          # 数值字段:提交前 str->number,避免审批分支按字符串误判


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _strict_boolean(value):
    """Only JSON booleans are booleans; strings/numbers must never open a gate."""
    return value if isinstance(value, bool) else None


def _original_request_link(api_audit, source_page_url=""):
    """Prefer the recorded business page; fall back to the successful API URL."""
    def link(label, url):
        markdown_url = urllib.parse.quote(url, safe=":/?#[]@!$&'*+,;=%")
        return {
            "label": label,
            "url": url,
            "target": "_blank",
            "rel": "noopener noreferrer",
            "markdown": "[%s](%s)" % (label, markdown_url),
        }

    if isinstance(source_page_url, str) and re.match(r"^https?://", source_page_url.strip(), re.I):
        return link("打开原系统页面", source_page_url.strip())
    if not isinstance(api_audit, dict):
        return None
    containers = [api_audit]
    if isinstance(api_audit.get("api"), dict):
        containers.append(api_audit["api"])
    candidates = []
    for container in containers:
        for key in ("final", "step_result"):
            if isinstance(container.get(key), dict):
                candidates.append(container[key])
        candidates.append(container)
        steps = container.get("step_results")
        if isinstance(steps, list):
            candidates.extend(item for item in reversed(steps) if isinstance(item, dict))
        if isinstance(container.get("raw"), dict):
            candidates.append(container["raw"])
    for item in candidates:
        url = item.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url.strip(), re.I):
            continue
        return link("查看原始请求", url.strip())
    return None


def _business_identifiers(output, output_schema):
    """Expose only identifier semantics explicitly declared by the capability."""
    roles = {
        "record": ("record_id", "记录ID"),
        "process_instance": ("process_instance_id", "流程实例ID"),
        "business_document": ("document_number", "业务编号"),
    }
    found = {}

    def visit(value, schema):
        if not isinstance(value, dict):
            return
        properties = (schema or {}).get("properties") or {}
        for key, item in value.items():
            field_schema = properties.get(key) or {}
            role = str(field_schema.get("x-dano-identifier-role") or "").strip().lower()
            mapped = roles.get(role)
            if mapped and item not in (None, "") and mapped[0] not in found:
                label = str(field_schema.get("title") or field_schema.get("label") or mapped[1])
                found[mapped[0]] = {"label": label, "value": item}
        for key, item in value.items():
            if isinstance(item, dict):
                visit(item, properties.get(key) or {})

    visit(output, output_schema or {})
    return found


def _presentation_policy(output_schema):
    """Tell the caller when the response has no grounded field semantics."""
    properties = (output_schema or {}).get("properties") or {}
    untyped = any(
        isinstance(schema, dict) and schema.get("x-dano-untyped-response") is True
        for schema in properties.values()
    )
    if untyped:
        return {
            "schema_grounded": False,
            "forbid_inferred_labels": True,
            "fallback_label": "接口返回值",
        }
    unlabeled = []

    def visit(schema, path=""):
        for name, field_schema in ((schema or {}).get("properties") or {}).items():
            if not isinstance(field_schema, dict):
                continue
            field_path = "%s.%s" % (path, name) if path else name
            nested = field_schema
            if field_schema.get("type") == "array":
                nested = field_schema.get("items") or {}
            if (nested.get("properties") or {}):
                visit(nested, field_path)
            elif not (
                field_schema.get("title")
                or field_schema.get("label")
                or field_schema.get("x-dano-identifier-role")
            ):
                unlabeled.append(field_path)

    visit(output_schema or {})
    grounded = not unlabeled
    return {
        "schema_grounded": grounded,
        "forbid_inferred_labels": not grounded,
        **({
            "fallback_label": "接口返回值",
            "unlabeled_fields": unlabeled,
        } if unlabeled else {}),
    }


def _coerce_arguments(obj):
    if isinstance(obj, str):
        obj = json.loads(obj or "{}")
    if not isinstance(obj, dict):
        raise ValueError("arguments/input 必须是 JSON 对象")
    return obj


def _is_envelope(obj):
    if not isinstance(obj, dict):
        return False
    if not any(k in obj for k in ("input", "arguments")):
        return False
    return any(k in obj for k in ("protocol", "capability", "name", "confirm"))


def _choose_capability(requested, field=None):
    if requested is not None:
        if not isinstance(requested, str) or not requested.strip():
            raise ValueError("capability 必须是非空字符串")
        return requested.strip()
    if field:
        matches = [name for name, contract in CAPABILITIES.items()
                   if field in contract.get("option_fields", []) or field in contract.get("fields", [])]
        if len(matches) == 1:
            return matches[0]
    return CAPABILITY


def _coerce_cli_values(arguments, contract):
    properties = (contract.get("parameters") or {}).get("properties") or {}
    for field, schema in properties.items():
        value = arguments.get(field)
        if not isinstance(value, str) or value == "":
            continue
        field_type = (schema or {}).get("type")
        if field_type == "boolean" and value.lower() in {"true", "false"}:
            arguments[field] = value.lower() == "true"
            continue
        if (
            (schema or {}).get("format") == "date-time"
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", value)
        ):
            arguments[field] = value + ":00"
            continue
        if (schema or {}).get("format") == "name-ref":
            raw_options = (
                schema.get("x-options")
                or schema.get("x-options-snapshot")
                or schema.get("x-enum-options")
                or schema.get("enum")
                or []
            )
            option = next(
                (
                    item for item in _question_options(raw_options)
                    if str(item.get("id")) == value
                ),
                None,
            )
            if option is not None:
                arguments[field] = option.get("label")
                continue
        if field_type in {"array", "object"}:
            try:
                parsed = json.loads(value)
            except Exception as exc:
                raise ValueError("字段 %s 需为 JSON %s: %s" % (field, field_type, exc)) from exc
            if field_type == "array" and not isinstance(parsed, list):
                raise ValueError("字段 %s 需为 JSON 数组" % field)
            if field_type == "object" and not isinstance(parsed, dict):
                raise ValueError("字段 %s 需为 JSON 对象" % field)
            arguments[field] = parsed
    return arguments


def _apply_safe_defaults(arguments, contract):
    """Apply only defaults explicitly marked safe for silent invocation.

    Ordinary field defaults are question-card recommendations and must still be
    reviewed by the user. Pagination defaults are deterministic transport
    controls and may be applied when omitted while remaining caller-overridable.
    """
    properties = (contract.get("parameters") or {}).get("properties") or {}
    for field, schema in properties.items():
        if field in arguments or not isinstance(schema, dict):
            continue
        if schema.get("x-dano-apply-default") is True and "default" in schema:
            arguments[field] = schema.get("default")
    return arguments


def _question_options(options):
    """Return stable ask_user_question options without collapsing duplicate labels."""
    normalized = []
    seen_ids = set()
    for option in options or []:
        if isinstance(option, dict):
            option_id = option.get("id", option.get("value", option.get("key")))
            label = option.get("label") or option.get("text") or option.get("name") or option_id
        else:
            option_id = label = option
        if option_id in (None, ""):
            continue
        stable_id = str(option_id)
        label = str(label or "").strip()
        if not label or stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)
        normalized.append({"id": option_id, "label": label})
    return normalized


def _validate_schema(value, schema, path="input"):
    schema = schema or {}
    if "const" in schema and value != schema.get("const"):
        raise ValueError("字段 %s 必须等于 %r" % (path, schema.get("const")))
    alternatives = schema.get("oneOf") or schema.get("anyOf")
    if alternatives:
        failures = []
        matched = 0
        for alternative in alternatives:
            try:
                _validate_schema(value, alternative, path)
                matched += 1
            except ValueError as exc:
                failures.append(str(exc))
        if not matched or (schema.get("oneOf") and matched != 1):
            raise ValueError("字段 %s 不符合候选 schema: %s" % (path, "; ".join(failures[:3])))
        return
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError("字段 %s 需为 JSON 对象" % path)
        properties = schema.get("properties") or {}
        missing = [name for name in (schema.get("required") or [])
                   if name not in value or value[name] in (None, "")]
        if missing:
            raise ValueError("%s 缺必填: %s" % (path, ", ".join(missing)))
        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in value if name not in properties)
            if extra:
                raise ValueError("%s 含未声明字段: %s" % (path, ", ".join(extra)))
        for name, child in properties.items():
            if name in value and value[name] is not None:
                _validate_schema(value[name], child, "%s.%s" % (path, name))
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError("字段 %s 需为 JSON 数组" % path)
        if schema.get("minItems") is not None and len(value) < int(schema.get("minItems")):
            raise ValueError("字段 %s 至少需要 %s 项" % (path, schema.get("minItems")))
        if schema.get("maxItems") is not None and len(value) > int(schema.get("maxItems")):
            raise ValueError("字段 %s 最多允许 %s 项" % (path, schema.get("maxItems")))
        if schema.get("uniqueItems") and len({json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value}) != len(value):
            raise ValueError("字段 %s 不允许重复项" % path)
        for index, item in enumerate(value):
            _validate_schema(item, schema.get("items") or {}, "%s[%s]" % (path, index))
    elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("字段 %s 需为整数" % path)
    elif expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ValueError("字段 %s 需为数字" % path)
    elif expected == "boolean" and not isinstance(value, bool):
        raise ValueError("字段 %s 需为布尔值" % path)
    elif expected == "string" and not isinstance(value, str):
        raise ValueError("字段 %s 需为字符串" % path)
    if isinstance(value, str):
        if schema.get("minLength") is not None and len(value) < int(schema.get("minLength")):
            raise ValueError("字段 %s 长度至少为 %s" % (path, schema.get("minLength")))
        if schema.get("maxLength") is not None and len(value) > int(schema.get("maxLength")):
            raise ValueError("字段 %s 长度最多为 %s" % (path, schema.get("maxLength")))
        if schema.get("pattern") and re.search(schema.get("pattern"), value) is None:
            raise ValueError("字段 %s 不符合格式约束" % path)
        if schema.get("format") == "date":
            try:
                datetime.date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("字段 %s 需为有效日期 YYYY-MM-DD" % path) from exc
        elif schema.get("format") == "date-time":
            try:
                datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("字段 %s 需为有效日期时间 ISO 8601" % path) from exc
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if schema.get("minimum") is not None and value < schema.get("minimum"):
            raise ValueError("字段 %s 不能小于 %s" % (path, schema.get("minimum")))
        if schema.get("maximum") is not None and value > schema.get("maximum"):
            raise ValueError("字段 %s 不能大于 %s" % (path, schema.get("maximum")))
        if schema.get("exclusiveMinimum") is not None and value <= schema.get("exclusiveMinimum"):
            raise ValueError("字段 %s 必须大于 %s" % (path, schema.get("exclusiveMinimum")))
        if schema.get("exclusiveMaximum") is not None and value >= schema.get("exclusiveMaximum"):
            raise ValueError("字段 %s 必须小于 %s" % (path, schema.get("exclusiveMaximum")))
    allowed = schema.get("enum")
    if allowed and value not in allowed:
        raise ValueError("字段 %s 必须是: %s" % (path, ", ".join(map(str, allowed))))


def main():
    ap = argparse.ArgumentParser(description="调用 Dano skill " + TOOL)
    for f in FIELDS:
        ap.add_argument("--" + f, default=None)
    ap.add_argument("--json", dest="raw", default=None,
                    help="旧格式:arguments JSON;新格式:调用 envelope,如 {\"capability\":\"...\",\"input\":{...}}")
    ap.add_argument("--json-base64", dest="raw_base64", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--capability", default=None,
                    help="显式选择 capability；只有单能力 Skill 才允许省略")
    # 写操作默认**未确认**:不带 --confirm 调用会被 Dano 拦成 need_confirm(确认闸门不被绕过)。
    ap.add_argument("--confirm", action="store_true", default=False)
    ap.add_argument("--diagnose", action="store_true")
    # 选某选择型字段前,**实时**拉它当前可选项(直接调来源接口):--list-options 字段名 → 从返回里选准确名字再提交。
    ap.add_argument("--list-options", dest="list_options", default=None, metavar="字段",
                    help="实时列出某选择型字段的当前可选项(Dano 调来源接口),再从中选准确名字")
    args = ap.parse_args()

    try:
        capability = _choose_capability(args.capability, args.list_options)
    except ValueError as e:
        _emit({"status": "failed", "reason": str(e)})
        sys.exit(2)
    confirm = args.confirm is True

    raw_obj = None
    raw_text = args.raw
    if args.raw_base64:
        if raw_text:
            _emit({"status": "failed", "reason": "--json 与 --json-base64 不能同时使用"})
            sys.exit(2)
        try:
            raw_text = base64.b64decode(args.raw_base64, validate=True).decode("utf-8")
        except Exception as e:
            _emit({"status": "failed", "reason": "--json-base64 不是合法 UTF-8 JSON: %s" % e})
            sys.exit(2)
    if raw_text:
        try:
            raw_obj = json.loads(raw_text)
        except Exception as e:
            _emit({"status": "failed", "reason": "--json 不是合法 JSON: %s" % e})
            sys.exit(2)
        if _is_envelope(raw_obj):
            try:
                if "capability" in raw_obj:
                    envelope_capability = raw_obj.get("capability")
                    if envelope_capability is None:
                        raise ValueError("capability 必须是非空字符串")
                    capability = _choose_capability(envelope_capability)
                if "confirm" in raw_obj:
                    envelope_confirm = _strict_boolean(raw_obj.get("confirm"))
                    if envelope_confirm is None:
                        raise ValueError("confirm 必须是 JSON 布尔值 true/false，字符串或数字不被接受")
                    confirm = confirm or envelope_confirm is True
            except ValueError as e:
                _emit({"status": "failed", "reason": str(e)})
                sys.exit(2)

    url = os.environ.get("DANO_URL")
    key = os.environ.get("DANO_TENANT_KEY")
    if not url or not key:
        _emit({"status": "failed", "reason": "DANO_URL/DANO_TENANT_KEY 未设置(部署方配置,勿写进文件)"})
        sys.exit(2)
    url = url.rstrip("/")

    if args.list_options:                       # 实时拉某字段可选项(选择型)→ agent 从中选准确名字
        if not capability:
            _emit({"status": "need_select", "reason": "该字段属于多个能力，请同时指定 --capability",
                   "candidates": list(CAPABILITIES)})
            return
        if capability not in CAPABILITIES:
            _emit({"status": "failed", "reason": "未知 capability: %s" % capability})
            sys.exit(1)
        if args.list_options not in (CAPABILITIES[capability].get("option_fields") or []):
            _emit({"status": "failed", "reason": "字段 %s 不是 capability %s 的动态选项字段" %
                   (args.list_options, capability)})
            sys.exit(1)
        payload = json.dumps({"protocol": PROTOCOL, "name": TOOL, "capability": capability,
                              "field": args.list_options}).encode("utf-8")
        req = urllib.request.Request(
            url + "/v1/tools/options", data=payload, method="POST",
            headers={"Content-Type": "application/json", "X-Tenant-Key": key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            _emit({"status": "failed", "reason": "拉可选项失败: %s" % e})
            sys.exit(1)
        options = _question_options(res.get("options"))
        _emit({"status": "options", "field": res.get("field"), "count": len(options),
               "options": options,
               "input_mapping": "selected id -> same option label for name-ref input",
               "note": res.get("note")})
        return

    if args.diagnose:
        try:
            with urllib.request.urlopen(url + "/health", timeout=10) as r:
                ok = r.status == 200
            _emit({"status": "diagnose_done", "health_ok": ok, "tenant_key_set": bool(key)})
        except Exception as e:
            _emit({"status": "failed", "reason": "网关不可达: %s" % e})
            sys.exit(2)
        return

    if not capability:
        _emit({"status": "need_select", "reason": "该 Skill 包含多个独立能力，请显式指定 --capability",
               "candidates": [{"name": name, "title": item.get("title"), "kind": item.get("kind")}
                              for name, item in CAPABILITIES.items()]})
        return
    if capability not in CAPABILITIES:
        _emit({"status": "failed", "reason": "未知 capability: %s" % capability,
               "candidates": list(CAPABILITIES)})
        sys.exit(1)
    contract = CAPABILITIES[capability]

    if raw_obj is not None:
        try:
            if _is_envelope(raw_obj):
                if "input" in raw_obj:
                    arguments = _coerce_arguments(raw_obj.get("input") or {})
                else:
                    arguments = _coerce_arguments(raw_obj.get("arguments") or {})
            else:
                arguments = _coerce_arguments(raw_obj)
        except Exception as e:
            _emit({"status": "failed", "reason": "--json 不是合法 JSON: %s" % e})
            sys.exit(2)
    else:
        arguments = {f: getattr(args, f) for f in FIELDS if getattr(args, f) is not None}

    try:
        arguments = _coerce_cli_values(arguments, contract)
        arguments = _apply_safe_defaults(arguments, contract)
    except ValueError as e:
        _emit({"status": "failed", "reason": str(e)})
        sys.exit(1)

    required = contract.get("required") or []
    missing = [f for f in required if f not in arguments or arguments[f] in (None, "")]
    if missing:
        _emit({"status": "failed", "reason": "缺必填: %s" % ", ".join(missing)})
        sys.exit(1)
    for f in (contract.get("numeric") or []):  # 数值字段 str->number(审批分支按数值比较,字符串会误判)
        v = arguments.get(f)
        if isinstance(v, str) and v != "":
            try:
                arguments[f] = int(v) if v.lstrip("-").isdigit() else float(v)
            except ValueError:
                _emit({"status": "failed", "reason": "字段 %s 需为数字,得到: %r" % (f, v)})
                sys.exit(1)
    try:
        _validate_schema(arguments, contract.get("parameters") or {"type": "object"})
    except ValueError as e:
        _emit({"status": "failed", "reason": str(e)})
        sys.exit(1)

    if contract.get("requires_confirmation") and not confirm:
        _emit({"status": "need_confirm", "reason": "写能力执行前需要明确确认",
               "capability": capability})
        return

    payload = json.dumps({"protocol": PROTOCOL, "input": arguments,
                          "confirm": confirm}).encode("utf-8")
    invoke_path = "/v1/skills/%s/capabilities/%s/invoke" % (
        urllib.parse.quote(SKILL_ID, safe=""), urllib.parse.quote(capability, safe=""))
    req = urllib.request.Request(
        url + invoke_path, data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Tenant-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _emit({"status": "failed", "reason": "HTTP %s: %s" % (e.code, e.read().decode("utf-8")[:300])})
        sys.exit(1)
    except Exception as e:
        _emit({"status": "failed", "reason": str(e)})
        sys.exit(1)

    state = res.get("state")
    audit = res.get("audit") or {}
    api_audit = audit.get("api") if isinstance(audit.get("api"), dict) else {}
    raw_api = api_audit.get("raw") if isinstance(api_audit.get("raw"), dict) else {}
    fc = audit.get("fact_check") or api_audit.get("fact_check")
    if fc is None and "fact_check_passed" in raw_api:
        fc = {"passed": _strict_boolean(raw_api.get("fact_check_passed")), "reason": raw_api.get("detail")}
    if fc is None and "fact_check_passed" in api_audit:
        fc = {"passed": _strict_boolean(api_audit.get("fact_check_passed")), "reason": api_audit.get("detail")}
    output = (res.get("exec_result") or {}).get("structured_output")
    if isinstance(output, dict) and {"ok", "skill_id", "capability", "output"}.issubset(output):
        output = output.get("output")
    partial_state = state in {"partially_completed", "partial_success", "completed_with_errors"} or res.get("status") == "partial_success"
    allow_partial = (contract.get("validation_requirements") or {}).get("allow_partial_success") is True
    if partial_state and not allow_partial:
        _emit({"status": "failed", "state": state,
               "reason": "该能力不允许部分成功，不能把不完整结果判为成功",
               "output": output, "fact_check": fc})
        sys.exit(1)
    partial = partial_state and allow_partial
    if state == "completed" or partial:
        fact_passed = fc is True or (isinstance(fc, dict) and fc.get("passed") is True)
        if contract.get("verify_required") and not fact_passed:
            _emit({"status": "failed", "state": state,
                   "reason": "事实核查未通过或缺少核查结果，不能判定写操作成功",
                   "output": output, "fact_check": fc})
            sys.exit(1)
        try:
            _validate_schema(output, contract.get("output_schema") or {}, "output")
        except ValueError as e:
            _emit({"status": "failed", "state": state,
                   "reason": "输出不符合 output_schema: %s" % e, "output": output})
            sys.exit(1)
        result = {"status": "partial_success" if partial else "succeeded", "state": state,
                  "capability": capability, "output": output, "fact_check": fc}
        result["presentation"] = _presentation_policy(contract.get("output_schema") or {})
        identifiers = _business_identifiers(output, contract.get("output_schema") or {})
        if identifiers:
            result["business_identifiers"] = identifiers
        if not partial:
            request_link = _original_request_link(audit, SOURCE_PAGE_URL)
            if request_link:
                result["request_url"] = request_link["url"]
                result["request_link"] = request_link
                result["request_markdown"] = request_link["markdown"]
        _emit(result)
    elif state == "needs_select":
        sel = audit.get("select") or {}
        _emit({"status": "need_select", "state": state, "message": res.get("message"),
               "bind": sel.get("bind"), "candidates": sel.get("candidates")})
    elif state == "cancelled" or "确认" in (res.get("message") or ""):
        _emit({"status": "need_confirm", "state": state, "message": res.get("message")})
    else:
        _emit({"status": "failed", "state": state, "reason": res.get("message"), "fact_check": fc})
        sys.exit(1)


if __name__ == "__main__":
    main()
'''


def _dano_call_py(m: SkillManifest) -> str:
    contracts = _capability_contracts(m)
    keys = list(dict.fromkeys(
        field for contract in contracts.values() for field in (contract.get("fields") or [])
    ))
    required = set(
        field for contract in contracts.values() for field in (contract.get("required") or [])
    )
    numeric = list(dict.fromkeys(
        field for contract in contracts.values() for field in (contract.get("numeric") or [])
    ))
    return (_PY_TEMPLATE
            .replace("__TITLE__", m.title)
            .replace("__SKILL_ID__", m.name)
            .replace("__TOOL__", tool_name_of(m.name))
            .replace("__CAPABILITY__", repr(_export_default_capability(m)))
            .replace("__SOURCE_PAGE_URL__", repr(str((m.flow or {}).get("source_page_url") or "")))
            .replace("__CAPABILITIES__", repr(contracts))
            .replace("__FIELDS__", json.dumps(keys, ensure_ascii=False))
            .replace("__REQUIRED__", json.dumps([k for k in keys if k in required], ensure_ascii=False))
            .replace("__NUMERIC__", json.dumps(numeric, ensure_ascii=False)))


_SUBMIT_SH = """#!/usr/bin/env bash
# 由 Dano 自动生成:转发到 dano_call.py(真逻辑)。python3 不在则回退 python。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" "$DIR/dano_call.py" "$@"
"""

_SUBMIT_PS1 = """# 由 Dano 自动生成:转发到 dano_call.py(真逻辑)。
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$forward = [System.Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "--json" -and $i + 1 -lt $args.Count) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$args[++$i])
        $forward.Add("--json-base64")
        $forward.Add([System.Convert]::ToBase64String($bytes))
    } else {
        $forward.Add([string]$args[$i])
    }
}
python "$dir/dano_call.py" @forward
exit $LASTEXITCODE
"""

_FORMAT_LIST_PY = r'''#!/usr/bin/env python3
"""Convert a Dano result to a compact business-facing Markdown table."""
import argparse
import datetime
import json
import os
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRESENTATIONS = __PRESENTATIONS__
DEFAULT_CAPABILITY = __DEFAULT_CAPABILITY__


def _list_rows(value):
    if isinstance(value, dict) and "output" in value:
        return _list_rows(value["output"])
    if isinstance(value, dict):
        for key in ("records", "rows", "items", "list"):
            if isinstance(value.get(key), list):
                return value[key]
        if isinstance(value.get("data"), (dict, list)):
            nested = _list_rows(value["data"])
            if nested is not None:
                return nested
        return [value]
    return value if isinstance(value, list) else [value]


def _row_schema(schema):
    properties = (schema or {}).get("properties") or {}
    for key in ("records", "rows", "items", "list"):
        field = properties.get(key) or {}
        if field.get("type") == "array":
            return field.get("items") or {}
    for field in properties.values():
        if isinstance(field, dict) and field.get("type") == "array":
            return field.get("items") or {}
    return schema or {}


def _presentation(value, requested):
    capability = requested or (value.get("capability") if isinstance(value, dict) else None)
    if capability in PRESENTATIONS:
        return PRESENTATIONS[capability]
    if DEFAULT_CAPABILITY in PRESENTATIONS:
        return PRESENTATIONS[DEFAULT_CAPABILITY]
    rows = _list_rows(value)
    keys = set(next((row for row in rows if isinstance(row, dict)), {}))
    ranked = []
    for name, presentation in PRESENTATIONS.items():
        props = set((_row_schema(presentation.get("output_schema")).get("properties") or {}))
        ranked.append((len(keys & props), name, presentation))
    return max(ranked, default=(0, "", {}))[2]


def _label(key, schema):
    explicit = str((schema or {}).get("title") or (schema or {}).get("label") or "").strip()
    if explicit and explicit != str(key):
        return explicit
    return str(key)


def _priority(index, schema):
    declared = (schema or {}).get("x-dano-display-order")
    if isinstance(declared, (int, float)) and not isinstance(declared, bool):
        return 0, declared, index
    return 1, index, index


def _hidden(schema):
    if (
        (schema or {}).get("x-dano-display") is False
        or (schema or {}).get("x-dano-internal") is True
        or (schema or {}).get("x-dano-visibility") == "internal"
    ):
        return True
    return False


def _time_value(value, schema):
    value_format = str((schema or {}).get("x-dano-value-format") or "").lower()
    if value_format not in {
        "epoch-milliseconds", "unix-milliseconds", "timestamp-milliseconds",
        "epoch-seconds", "unix-seconds", "timestamp-seconds",
        "epoch-auto",
    }:
        return value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    seconds = value / 1000 if (
        value_format.endswith("milliseconds")
        or value_format == "epoch-auto" and abs(value) >= 100000000000
    ) else value
    try:
        timezone_name = os.getenv("DANO_DISPLAY_TIMEZONE", "").strip()
        timezone = ZoneInfo(timezone_name) if timezone_name else None
        parsed = datetime.datetime.fromtimestamp(seconds, timezone)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError, ZoneInfoNotFoundError):
        return value


def _display_value(key, value, presentation, schema):
    labels = (presentation.get("value_labels") or {}).get(key) or {}
    mapped = labels.get(str(value))
    if mapped is not None:
        return mapped
    return _time_value(value, schema)


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", "<br>")


def format_table(value, capability=None):
    rows = _list_rows(value)
    if not rows:
        return "无数据"
    if not any(isinstance(row, dict) for row in rows):
        rows = [{"值": row} for row in rows]
    else:
        rows = [row if isinstance(row, dict) else {"值": row} for row in rows]
    presentation = _presentation(value, capability)
    schema_properties = (_row_schema(presentation.get("output_schema")).get("properties") or {})
    columns = list(dict.fromkeys(key for row in rows for key in row))
    columns = [
        key for key in columns
        if not _hidden(schema_properties.get(key) or {})
    ]
    columns = [
        key for _index, key in sorted(
            enumerate(columns),
            key=lambda item: _priority(item[0], schema_properties.get(item[1]) or {}),
        )
    ]
    if not columns:
        return "无数据"
    header = "| " + " | ".join(
        _cell(_label(column, schema_properties.get(column) or {}))
        for column in columns
    ) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(
            _cell(_display_value(
                column,
                row.get(column),
                presentation,
                schema_properties.get(column) or {},
            ))
            for column in columns
        ) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def main():
    parser = argparse.ArgumentParser(description="把 JSON 列表格式化为 Markdown 表格")
    parser.add_argument("--json", dest="raw")
    parser.add_argument("--json-base64", dest="raw_base64")
    parser.add_argument("--file")
    parser.add_argument("--capability")
    args = parser.parse_args()
    if args.raw is not None and args.raw_base64 is not None:
        parser.error("--json 与 --json-base64 不能同时使用")
    if args.raw_base64 is not None:
        import base64
        try:
            raw = base64.b64decode(args.raw_base64, validate=True).decode("utf-8")
        except Exception as error:
            print("Base64 JSON 解码失败: %s" % error, file=sys.stderr)
            raise SystemExit(2)
    elif args.raw is not None:
        raw = args.raw
    elif args.file:
        with open(args.file, encoding="utf-8-sig") as handle:
            raw = handle.read()
    else:
        raw = sys.stdin.read()
    try:
        value = json.loads(raw.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        print("JSON 解析失败: %s" % error, file=sys.stderr)
        raise SystemExit(2)
    print(format_table(value, args.capability))


if __name__ == "__main__":
    main()
'''


def _format_list_py(manifests: list[SkillManifest]) -> str:
    presentations: dict[str, dict] = {}

    def row_properties(output_schema: dict) -> dict:
        properties = (output_schema or {}).get("properties") or {}
        for field in properties.values():
            if isinstance(field, dict) and field.get("type") == "array":
                return ((field.get("items") or {}).get("properties") or {})
        return properties

    def enum_labels(schema: dict) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for option in (
            schema.get("x-enum-options")
            or schema.get("x-options")
            or schema.get("x-options-snapshot")
            or []
        ):
            if isinstance(option, dict):
                value = option.get("value", option.get("id", option.get("key")))
                label = option.get("label") or option.get("text") or option.get("name")
            else:
                value = label = option
            if value not in (None, "") and label not in (None, ""):
                mapping[str(value)] = str(label)
        for option in schema.get("oneOf") or []:
            if isinstance(option, dict) and "const" in option:
                label = option.get("title") or option.get("label")
                if label not in (None, ""):
                    mapping[str(option["const"])] = str(label)
        for label, value in (schema.get("x-enum-value-map") or {}).items():
            if value not in (None, "") and label not in (None, ""):
                mapping[str(value)] = str(label)
        return mapping

    for manifest in manifests:
        for name, contract in _capability_contracts(manifest).items():
            output_schema = copy.deepcopy(contract.get("output_schema") or {})
            presentations[name] = {
                "output_schema": output_schema,
                "value_labels": {
                    field: labels
                    for field, schema in row_properties(output_schema).items()
                    if isinstance(schema, dict) and (labels := enum_labels(schema))
                },
            }
    default = (
        _export_default_capability(manifests[0])
        if len(manifests) == 1
        else None
    )
    return (
        _FORMAT_LIST_PY
        .replace("__PRESENTATIONS__", repr(presentations))
        .replace("__DEFAULT_CAPABILITY__", repr(default))
    )

_FORMAT_LIST_PS1 = """# 保留 Windows PowerShell 中的 UTF-8 JSON，再交给格式化脚本。
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$raw = if ($args.Count -gt 0) { [string]$args[0] } else { [Console]::In.ReadToEnd() }
$bytes = [System.Text.Encoding]::UTF8.GetBytes($raw)
$encoded = [System.Convert]::ToBase64String($bytes)
python "$dir/format_list.py" --json-base64 $encoded
exit $LASTEXITCODE
"""


def _chmod_x(path: Path) -> None:
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _write_skill(out_dir: Path, m: SkillManifest,
                 *, reference_docs: list[tuple[Path, str]] | None = None) -> Path:
    docs = reference_docs if reference_docs is not None else _load_reference_markdown(_configured_reference_dir())
    _validate_reference_markdown(docs)
    slug = _slug(m.name)
    target = out_dir / slug
    folder = _stage_folder(out_dir, slug)
    try:
        for child in ("agents", "scripts", "references"):
            (folder / child).mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(_skill_md(m, slug), encoding="utf-8")
        (folder / "agents" / "openai.yaml").write_text(
            _agents_openai_yaml(
                slug, m.title or slug,
                f"调用 Dano 执行“{m.title or slug}”已发布能力，支持参数收集、确认和结果处理",
            ),
            encoding="utf-8",
        )
        (folder / "references" / "CONTRACT.json").write_text(
            json.dumps(_export_contract(m), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (folder / "references" / "CAPABILITIES.md").write_text(
            _capability_reference_md(m),
            encoding="utf-8",
        )
        opts_md = _options_md(m)
        if opts_md:
            (folder / "references" / "OPTIONS.md").write_text(opts_md, encoding="utf-8")
        py = folder / "scripts" / "dano_call.py"
        py.write_text(_dano_call_py(m), encoding="utf-8", newline="\n")
        _chmod_x(py)
        sh = folder / "scripts" / "submit.sh"
        sh.write_text(_SUBMIT_SH, encoding="utf-8", newline="\n")
        _chmod_x(sh)
        (folder / "scripts" / "submit.ps1").write_text(_SUBMIT_PS1, encoding="utf-8")
        formatter = folder / "scripts" / "format_list.py"
        formatter.write_text(_format_list_py([m]), encoding="utf-8", newline="\n")
        _chmod_x(formatter)
        (folder / "scripts" / "format_list.ps1").write_text(_FORMAT_LIST_PS1, encoding="utf-8")
        return _publish_folder(folder, target, slug, _skill_name(m.title, slug))
    except Exception:
        _abort_stage(folder)
        raise


# ─────────────────────────── 业务剧本 skill(多操作合成一本)───────────────────────────
def _op_sh(action: str) -> str:
    return ("#!/usr/bin/env bash\n# 由 Dano 自动生成:转发到 %s.py(真逻辑)。\n"
            "set -euo pipefail\n"
            'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            "if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi\n"
            'exec "$PY" "$DIR/%s.py" "$@"\n' % (action, action))


def _op_ps1(action: str) -> str:
    return ("# 由 Dano 自动生成:转发到 %s.py。\n"
            "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
            "$OutputEncoding = [Console]::OutputEncoding\n"
            "$dir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
            "$forward = [System.Collections.Generic.List[string]]::new()\n"
            "for ($i = 0; $i -lt $args.Count; $i++) {\n"
            '    if ($args[$i] -eq "--json" -and $i + 1 -lt $args.Count) {\n'
            "        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$args[++$i])\n"
            '        $forward.Add("--json-base64")\n'
            "        $forward.Add([System.Convert]::ToBase64String($bytes))\n"
            "    } else {\n"
            "        $forward.Add([string]$args[$i])\n"
            "    }\n"
            "}\n"
            'python "$dir/%s.py" @forward\n' % (action, action))


def _biz_label(business: str, manifests: list[SkillManifest]) -> str:
    """业务展示名:优先用写操作(办理)的标题,退而用业务键清理。"""
    writes = [m for m in manifests if m.requires_confirmation]
    if writes and writes[0].title:
        return writes[0].title
    s = re.sub(r"^(submit|create|apply|demo|do)[_-]+", "", business.lower())
    return s.replace("_", " ").strip() or business


_DIAGNOSE_SH = """#!/usr/bin/env bash
# 由 Dano 自动生成:剧本自检(能不能走这条路)。转发到某操作脚本的 --diagnose。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" "$DIR/__ENTRY__.py" --diagnose
"""

_DIAGNOSE_PS1 = """# 由 Dano 自动生成:剧本自检。转发到某操作脚本的 --diagnose。
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$dir/__ENTRY__.py" --diagnose
"""


def _business_skill_md(subsystem: str, business: str, manifests: list[SkillManifest], slug: str) -> str:
    """Render one compact business router; operation details live in references."""
    label = _biz_label(business, manifests)
    has_fact_verification = any(
        contract.get("verify_required")
        for manifest in manifests
        for contract in _capability_contracts(manifest).values()
    )
    operation_rows = [
        "| 操作 | capability | 类型 | 调用脚本 | 确认 |",
        "|---|---|---|---|---|",
    ]
    for manifest in manifests:
        for name, contract in _capability_contracts(manifest).items():
            operation_rows.append(
                f"| {contract.get('title') or manifest.title or name} | `{name}` | "
                f"`{contract.get('kind') or 'operation'}` | `scripts/{manifest.action}.sh` | "
                f"{'需要' if contract.get('requires_confirmation') else '不需要'} |"
            )
    titles = "、".join(
        dict.fromkeys(
            contract.get("title") or name
            for manifest in manifests
            for name, contract in _capability_contracts(manifest).items()
        )
    )
    description = (
        f"用于“{label}”业务。用户明确要求执行“{titles or label}”中的任一已发布操作时使用；"
        "负责选择正确操作、一次性收集参数、确认写操作并返回结果。"
        "仅咨询或要求未列出的操作时不要触发。"
    )
    return f"""---
name: {json.dumps(_skill_name(label, slug), ensure_ascii=False)}
description: {json.dumps(description, ensure_ascii=False)}
---

# {label}

本 Skill 只负责选择已发布操作、一次性收集参数、确认写操作并调用对应包装脚本。

{_EXECUTION_DIR_MD}

## 操作清单
{chr(10).join(operation_rows)}

## 操作步骤(SOP)
1. 根据用户目标从“操作清单”选择一个明确操作和 capability；未列出的动作不要调用相近能力代替。
2. 读取 `references/CAPABILITIES.md` 对应操作小节。动态选项先用该行脚本执行
   `--capability <能力名> --list-options <字段名>`，不得猜候选。
3. 需要补字段时原生调用一次 `ask_user_question`，用顶层 `title` 和同一 `questions[]`
   一次性收集本次所需字段；字段 id、控件、默认值和候选必须逐项来自参考小节。用户值优先，
   否则使用“推荐默认值”列的主值；查询能力不得主动询问或自动提交可选筛选项。
4. `status=answered` 后按 question id 组装所选 capability 的 input。选项保留稳定 id，
   name-ref 按 id 找回同一候选 label 后提交；日期、数字和 textarea JSON 按字段类型转换。
   写操作须在同一 Assistant Turn 用
   `ask_user_question({{confirm: true, formIds: [<answered.formId>]}})` 确认，
   仅 `status=confirmed` 后执行；`cancelled` 立即停止。
5. 调用清单中的真实脚本：`bash scripts/<action>.sh --capability <能力名> --json '<输入 JSON>'`；
   写操作确认后增加 `--confirm`。Windows 使用同名 `.ps1`。
6. 只认脚本末行 JSON 的 `status`。列表结果先用 `scripts/format_list.py` 转成 Markdown 表格；
   `failed`、`partial_success` 或结果不明时不得宣称全部成功或自动重复提交。

{_LIST_OUTPUT_MD}

{_FIELD_CONTENT_VALIDATION_MD}

{_IDENTIFIER_OUTPUT_MD}

{_FRONTEND_OUTPUT_MD}

{_errors_md(has_fact_verification)}

{_SECURITY_MD}
"""


def _write_business_skill(out_dir: Path, subsystem: str, business: str,
                          manifests: list[SkillManifest], *, md_text: str | None = None,
                          reference_docs: list[tuple[Path, str]] | None = None) -> Path:
    """同业务多操作 → 一本剧本 skill(多操作脚本 + 六段式剧本 SKILL.md)。

    md_text 给定则用它;否则用本模块的确定性渲染。
    """
    docs = reference_docs if reference_docs is not None else _load_reference_markdown(_configured_reference_dir())
    _validate_reference_markdown(docs)
    slug = _slug(f"{subsystem}.{business}")
    target = out_dir / slug
    folder = _stage_folder(out_dir, slug)
    try:
        for child in ("agents", "scripts", "references"):
            (folder / child).mkdir(parents=True, exist_ok=True)
        if md_text is None:
            md_text = _business_skill_md(subsystem, business, manifests, slug)
        label = _biz_label(business, manifests)
        (folder / "SKILL.md").write_text(md_text, encoding="utf-8")
        (folder / "agents" / "openai.yaml").write_text(
            _agents_openai_yaml(
                slug, label,
                f"调用 Dano 办理或查询“{label}”业务，按已发布操作收集参数并处理结果",
            ),
            encoding="utf-8",
        )
        bundle_contract = {
            "protocol": "dano.skill_bundle.v1",
            "subsystem": subsystem,
            "business": business,
            "skills": [_export_contract(manifest) for manifest in manifests],
        }
        (folder / "references" / "CONTRACT.json").write_text(
            json.dumps(bundle_contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (folder / "references" / "CAPABILITIES.md").write_text(
            _business_capability_reference_md(manifests),
            encoding="utf-8",
        )
        entry = (manifests[0].action if manifests else "diagnose")
        (folder / "scripts" / "diagnose.sh").write_text(
            _DIAGNOSE_SH.replace("__ENTRY__", entry), encoding="utf-8", newline="\n")
        _chmod_x(folder / "scripts" / "diagnose.sh")
        (folder / "scripts" / "diagnose.ps1").write_text(
            _DIAGNOSE_PS1.replace("__ENTRY__", entry), encoding="utf-8")
        for m in manifests:
            py = folder / "scripts" / f"{m.action}.py"
            py.write_text(_dano_call_py(m), encoding="utf-8", newline="\n")
            _chmod_x(py)
            sh = folder / "scripts" / f"{m.action}.sh"
            sh.write_text(_op_sh(m.action), encoding="utf-8", newline="\n")
            _chmod_x(sh)
            (folder / "scripts" / f"{m.action}.ps1").write_text(_op_ps1(m.action), encoding="utf-8")
        formatter = folder / "scripts" / "format_list.py"
        formatter.write_text(_format_list_py(manifests), encoding="utf-8", newline="\n")
        _chmod_x(formatter)
        (folder / "scripts" / "format_list.ps1").write_text(_FORMAT_LIST_PS1, encoding="utf-8")
        return _publish_folder(folder, target, slug, _skill_name(label, slug))
    except Exception:
        _abort_stage(folder)
        raise


async def write_skills(tenant: str, out_dir: str, *, rich: bool = True,
                       exclude_skill_ids: set[str] | None = None) -> list[str]:
    """核心:读该租户已上架 Skill 写成官方格式 skill;**不管连接池**(供已持有池的网关复用)。

    带 business 标签的操作**按业务归组成一本自包含剧本 skill**(多操作);其余各自一个单动作 skill。
    rich 参数保留兼容旧调用;当前导出只做确定性渲染。每业务独立 try/except,一个失败不连累其它。
    """
    from collections import defaultdict
    repo = AssetRepository()
    subs = await _tenant_subsystems(repo, tenant)   # 发现该租户真实系统(任意系统),与网关一致
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subs)
    reference_docs = _load_reference_markdown(_configured_reference_dir())
    _validate_reference_markdown(reference_docs)
    excluded = set(exclude_skill_ids or set())
    export_skills = [_upgrade_recorded_skill_for_export(skill) for skill in reg.skills]
    manifests = [m for m in build_manifests(export_skills) if m.name not in excluded]
    valid_manifests: list[SkillManifest] = []
    for manifest in manifests:
        errors = _export_contract_errors(manifest)
        if errors:
            # A legacy broken Skill must not be exported, but it must not block a
            # newly published valid Skill in the same tenant either.
            log.warning(
                "export.skill_contract_rejected",
                skill_id=manifest.name,
                errors=errors,
            )
            continue
        valid_manifests.append(manifest)
    manifests = valid_manifests
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stale_index = out / "dano-business-index"
    if stale_index.is_symlink() or stale_index.is_file():
        stale_index.unlink()
    elif stale_index.is_dir():
        shutil.rmtree(stale_index)
    log.info("export.target", out_abs=str(out.resolve()), tenant=tenant)   # 落盘绝对路径(排查"看不到文件")
    groups: dict = defaultdict(list)
    standalone: list[SkillManifest] = []
    for m in manifests:
        (groups[(m.subsystem, m.business)].append(m) if getattr(m, "business", "")
         else standalone.append(m))
    written: list[str] = []
    for (sub, biz), ms in groups.items():
        try:                                                 # 每业务独立:一个崩不连累其它
            slug = _slug(f"{sub}.{biz}")
            md = _business_skill_md(sub, biz, ms, slug)
            folder = _write_business_skill(
                out, sub, biz, ms, md_text=md, reference_docs=reference_docs)
            log.info("export.business_skill", business=biz, subsystem=sub,
                     ops=[m.action for m in ms], folder=folder.name)
            written.append(folder.name)
        except Exception as e:  # noqa: BLE001
            log.warning("export.business_skill_failed", business=biz, subsystem=sub, error=str(e))
    for m in standalone:
        try:
            folder = _write_skill(out, m, reference_docs=reference_docs)
            written.append(folder.name)
        except Exception as e:  # noqa: BLE001
            log.warning("export.standalone_failed", action=m.action, error=str(e))
    log.info("export.agent_skills", tenant=tenant, out=str(out),
             count=len(written), businesses=len(groups), standalone=len(standalone))
    return written


async def write_exports(
    tenant: str,
    out_dir: str,
    *,
    mode: str = "both",
    exclude_skill_ids: set[str] | None = None,
) -> list[str]:
    """Write proxy packages, self-contained packages, or both without collisions."""
    if mode not in {"proxy", "package", "both"}:
        raise ValueError("mode 必须是 proxy/package/both")
    excluded = set(exclude_skill_ids or set())
    written: list[str] = []
    if mode in {"proxy", "both"}:
        written.extend(await write_skills(tenant, out_dir, exclude_skill_ids=excluded))
    if mode in {"package", "both"}:
        from dano.export.skill_package.renderer import write_skill_packages

        selected: list[str] | None = None
        if excluded:
            repo = AssetRepository()
            subs = await _tenant_subsystems(repo, tenant)
            registry = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=subs)
            selected = [
                skill.skill_id for skill in registry.skills
                if skill.recording_asset_id is not None and skill.skill_id not in excluded
            ]
        written.extend(await write_skill_packages(tenant, out_dir, skill_ids=selected))
    return written


async def export(tenant: str, out_dir: str, *, mode: str = "both") -> list[str]:
    """命令行入口:自管连接池(init→write→close);返回写出的文件夹名列表。"""
    from dano.infra.db import close_pool, init_pool
    await init_pool()
    try:
        return await write_exports(tenant, out_dir, mode=mode)
    finally:
        await close_pool()


def main() -> None:
    ap = argparse.ArgumentParser(description="导出已上架 Skill 为官方 skill-creator 格式 skill(.agents/skills/)")
    ap.add_argument("--tenant", required=True, help="租户名,如 demo-oa")
    ap.add_argument("--out", required=True, help="输出目录,通常是 <pi仓库>/.agents/skills")
    ap.add_argument("--mode", choices=("proxy", "package", "both"), default="both", help="导出代理包、自包含包或两者")
    args = ap.parse_args()
    written = asyncio.run(export(args.tenant, args.out, mode=args.mode))
    print(f"已导出 {len(written)} 个 skill 到 {args.out}:")
    for w in written:
        print("  -", w)
    if not written:
        print("  (该租户没有已上架 Skill;先在「接入系统」生成并上架)")


if __name__ == "__main__":
    main()
