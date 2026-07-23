"""把已上架 Skill 导出为**官方 skill-creator 格式**的 Agent Skill(.agents/skills/<name>/)。

用法:
  python -m dano.export.agent_skills --tenant demo-oa --out <pi仓库>/.agents/skills

每个 skill = 一个文件夹(skill-creator 规范:渐进式披露 + 脚本 + references):
  SKILL.md           —— frontmatter(pushy description/触发场景)+ 逐字段参数表 + 输出契约 + 确认工作流 + 示例 + 故障排除
  agents/openai.yaml —— 业务展示名与默认提示词
  references/CONTRACT.json / OPTIONS.md —— 无损契约 + 选择项参考
  scripts/dano_call.py  —— 真逻辑:能力级参数校验 + --confirm + --diagnose,POST Dano capability invoke,末行打印稳定 JSON 状态
  scripts/submit.sh / submit.ps1     —— 转发到 dano_call.py 的薄壳

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
import sys
import tempfile
import uuid
from pathlib import Path, PureWindowsPath

import structlog

from dano.assets.repository import AssetRepository
from dano.catalog.manifest import SkillManifest, build_manifests, tool_name_of
from dano.config import get_settings
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import SkillSpec
from dano.shared.enums import Subsystem

log = structlog.get_logger(__name__)
# 原型常量仅作空租户 / 无 DB 兜底;真实系统由 _tenant_subsystems 从该租户已发布资产发现(任意系统,不写死)。
_PROTOTYPE_SUBSYSTEMS = [Subsystem.OA, Subsystem.TICKET, Subsystem.REIMBURSE]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LINUX_PROJECT_ROOT = Path("/opt/skillmanner/Dano")


def _configured_reference_dir() -> Path:
    """Resolve the configured reference directory for local and Linux layouts."""
    configured = str(get_settings().skill_reference_dir or "").strip()
    if not configured:
        raise ValueError("DANO_SKILL_REFERENCE_DIR 不能为空")
    relative = Path(configured.replace("\\", "/"))
    windows_absolute = PureWindowsPath(configured).is_absolute()
    if sys.platform.startswith("linux"):
        if windows_absolute:
            raise ValueError("Linux 的 DANO_SKILL_REFERENCE_DIR 不能使用 Windows 绝对路径")
        project_root = _LINUX_PROJECT_ROOT.resolve()
        resolved = relative.resolve() if relative.is_absolute() else (project_root / relative).resolve()
    else:
        if relative.is_absolute() or windows_absolute:
            raise ValueError("DANO_SKILL_REFERENCE_DIR 必须是相对仓库根目录的路径")
        project_root = _PROJECT_ROOT.resolve()
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
        "多字段 questions 数组": ("questions",),
        "推荐默认值": ("default",),
        "必填规则": ("required",),
        "日期格式": ("dateFormat",),
        "远程选项来源": ("dataSource",),
        "最终确认": ("confirm",),
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
    """该租户**实际拥有**的系统(发现式,支持任意系统);发现为空 / DB 不可用才退回原型常量。

    与网关 `_tenant_subsystems` 一致:任意系统接入发布后自动被发现并导出,不必在代码里预先登记。
    """
    try:
        subs = await repo.distinct_subsystems(tenant)
    except Exception as e:  # noqa: BLE001 —— DB 不可用时退原型,不致导出整体失败
        log.warning("export.discover_subsystems_failed", tenant=tenant, error=str(e))
        subs = []
    return subs or _PROTOTYPE_SUBSYSTEMS


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
    """skill_id(如 A-OA.submit_leave)→ 文件夹名(kebab,如 dano-a-oa-submit-leave)。

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
    """Use the business title in frontmatter while keeping a portable folder slug."""
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


def _validate_generated_skill(folder: Path, expected_name: str) -> None:
    """Fail export before publication when the generated package is not portable."""
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
        raise ValueError(f"Skill name 与业务标题不一致: {actual_name!r} != {expected_name!r}")
    if len(text.splitlines()) > 500:
        raise ValueError("SKILL.md 超过 500 行，违反渐进式披露约束")
    if not (folder / "agents" / "openai.yaml").is_file():
        raise ValueError("Skill 缺少 agents/openai.yaml")


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
_INTERNAL_CALLER_FIELD_RE = re.compile(
    r"(?:processdefinitionid|processdefid|activityid|processdefkey|billtype|formtype|templateid)$",
    re.I,
)


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


def _capability_contracts(m: SkillManifest) -> dict[str, dict]:
    """Return the authoritative per-capability caller contracts used by exports."""
    contracts: dict[str, dict] = {}
    for raw in getattr(m, "capabilities", []) or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("kind") or raw.get("capability_id") or "").strip()
        if not name:
            continue
        schema = copy.deepcopy(
            raw.get("parameters") or raw.get("input_schema")
            or {"type": "object", "properties": {}, "required": []}
        )
        props = dict((schema or {}).get("properties") or {})
        field_labels: dict[str, str] = {}
        for field in [*(raw.get("inputs") or []), *(raw.get("fields") or [])]:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or field.get("path") or "").split(".")[-1]
            label = str(field.get("display_name") or field.get("label") or "").strip()
            if key and label:
                field_labels[key] = label
        kind = str(raw.get("kind") or name)
        contracts[name] = {
            "name": name,
            "title": str(raw.get("title") or name),
            "kind": kind,
            "fields": list(props),
            "required": list((schema or {}).get("required") or []),
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
            "call_protocol": dict(raw.get("call_protocol") or {}),
            "caller_responsibilities": list(raw.get("caller_responsibilities") or []),
            "field_labels": field_labels,
        }
    if contracts:
        return contracts
    keys, required, props = _fields(m)
    name = _capability(m)
    return {name: {
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
        "call_protocol": dict(m.call_protocol or {}),
        "caller_responsibilities": [],
    }}


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
    for name, contract in _capability_contracts(m).items():
        schema = contract.get("parameters") or {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = [str(item) for item in (schema.get("required") or [])]
        missing_required = [item for item in required if item not in props]
        if missing_required:
            errors.append(f"{name}: required 字段不在 properties: {', '.join(missing_required)}")
        if contract.get("kind") in _CAPABILITY_PUBLIC_KINDS and not str(name or "").strip():
            errors.append(f"{contract.get('kind')}: capability name 不能为空")
        exposed_internal = [
            field for field in props
            if _INTERNAL_CALLER_FIELD_RE.search(re.sub(r"[^a-z0-9]+", "", str(field).lower()))
        ]
        if exposed_internal:
            errors.append(f"{name}: 内部流程字段不能暴露给调用方: {', '.join(exposed_internal)}")
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


def _capability_relationship_section(m: SkillManifest) -> str:
    relations = [r for r in (getattr(m, "capability_relations", []) or []) if isinstance(r, dict)]
    if not relations:
        return ""
    lines = ["## 能力关系", "", "能力关系只描述数据流建议，不会触发自动串联；每一步都必须显式选择 capability。"]
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
    blocks = ["## 能力调用契约"]
    for name, contract in _capability_contracts(m).items():
        schema = contract.get("parameters") or {}
        props = schema.get("properties") or {}
        required = set(contract.get("required") or [])
        confirm = "需要最终确认" if contract.get("requires_confirmation") else "只读，无需写操作确认"
        blocks += ["", f"### `{name}` · {contract.get('title') or name}",
                   f"类型:`{contract.get('kind')}` · {confirm}"]
        if not props:
            blocks.append("\n(无业务输入参数)")
        else:
            rows = ["| 参数 | 类型 | 必填 | 推荐默认值 | 说明 |", "|---|---|---|---|---|"]
            for field, prop in props.items():
                desc = str((prop or {}).get("description") or (prop or {}).get("label") or field).replace("|", "\\|")
                default_text = (
                    _query_default_text(prop)
                    if contract.get("kind") in _READ_CAPABILITY_KINDS
                    else _schema_default_text(prop)
                )
                rows.append(
                    f"| `{field}` | {_schema_type_text(prop)} | {'是' if field in required else '否'} | "
                    f"{default_text} | {desc} |"
                )
                item_props = (((prop or {}).get("items") or {}).get("properties") or {})
                item_required = set((((prop or {}).get("items") or {}).get("required") or []))
                for item_name, item_schema in item_props.items():
                    item_desc = str((item_schema or {}).get("description") or item_name).replace("|", "\\|")
                    rows.append(
                        f"| `  {field}[].{item_name}` | {_schema_type_text(item_schema)} | "
                        f"{'是' if item_name in item_required else '否'} | "
                        f"{_schema_default_text(item_schema)} | {item_desc} |"
                    )
            blocks.extend(rows)
        requirements = contract.get("validation_requirements") or {}
        verification = "需要事实核查" if contract.get("verify_required") else "按 output_schema 校验"
        batch_note = (
            "；`entries[]` 逐项校验并保留顺序，允许部分成功但必须逐项报告"
            if requirements.get("validate_batch_items_individually") else ""
        )
        blocks.append(f"\n输出与验证:{verification}{batch_note}。完整 output schema:")
        blocks += ["```json", json.dumps(contract.get("output_schema") or {}, ensure_ascii=False, indent=2), "```"]
        payload = {"capability": name, "input": _capability_example_input(contract)}
        if contract.get("requires_confirmation"):
            payload["confirm"] = True
        blocks += ["", "调用示例:", "```bash",
                   "bash scripts/submit.sh --json '" + json.dumps(payload, ensure_ascii=False) + "'",
                   "```"]
    return "\n".join(blocks)


def _multi_capability_sop(m: SkillManifest) -> str:
    contracts = _capability_contracts(m)
    lines = [
        "## 操作步骤(SOP)",
        "",
        "1. 根据用户目标选择一个明确的 capability；查询和提交是不同能力，禁止默认选择写能力。",
        "   用户意图必须同时匹配能力的业务对象和动作；实体目录/候选列表不等于业务申请记录，"
        "未发布对应能力时必须说明不支持，不得用最相近的能力代替。",
        "2. 读取所选 capability 的完整 `input_schema`；对动态选择项先运行 "
        "`bash scripts/submit.sh --capability <能力名> --list-options <字段名>` 获取实时候选。",
        "   查询 input 只能包含用户本轮明确指定的业务筛选条件；录制推荐值不得作为查询筛选条件自动提交。"
        "没有筛选条件时传空 input，由脚本仅应用 `x-dano-apply-default: true` 的分页等安全默认值。",
        "3. **一次性收集本次所需字段。** 写能力必须收集全部必填表单项；查询能力只收集必填字段和"
        "用户明确要求的可选筛选条件，不得为其他可选筛选字段主动提问。原生调用 `ask_user_question` "
        "且本轮只调用一次，把所需字段放在同一个 `questions[]`；多个表单也必须合并，不得在普通文本中提问，"
        "不得逐字段、逐分区或逐表单多轮追问。",
        "   每个问题必须使用下表给出的参数名作为 `id`、业务标签作为 `question`，并设置对应的 "
        "`inputType`、`required`、非空推荐 `default` 及 `options`/`dataSource`。录制默认值只作推荐，"
        "除非契约标记 `x-dano-apply-default: true`，否则必须等待用户回答。",
    ]
    for name, contract in contracts.items():
        lines.extend(_question_collection_block(name, contract))
    lines += [
        "4. `ask_user_question` 返回 `status=answered` 后，按 `answer` 对象的 `id` "
        "映射回所选 capability 参数；日期按 `dateFormat` 转换，数值转 JSON 数字，"
        "数组/复合字段按 input_schema 组装。返回 `cancelled` 时立即停止。",
        "5. 校验必填字段、类型和候选值。写能力需要把完整参数摘要再用一次独立的 "
        "`ask_user_question({question, confirm: true})` 确认；只有 `answer=true` 才能继续。",
        "6. 使用 `bash scripts/submit.sh --capability <能力名> --json '<能力输入 JSON>'` 调用；"
        "写能力同时带 `--confirm`。一次调用由 Dano 完成内部接口编排。",
        "7. 按末行 JSON 的 `status` 处理结果。列表结果必须先运行 "
        "`python scripts/format_list.py --json '<output JSON>'`，再以 Markdown 表格呈现；"
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


def _question_control(schema: dict) -> str:
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
    if business_type in {"textarea", "rich_text"}:
        return "textarea"
    if schema.get("format") in {"date", "date-time"} or business_type in {"date", "datetime"}:
        return "date"
    if business_type == "boolean":
        return "radio"
    return "text"


def _question_option_source(schema: dict) -> str:
    schema = schema or {}
    source = schema.get("x-options-source-meta") if isinstance(schema.get("x-options-source-meta"), dict) else {}
    endpoint = str(
        source.get("endpoint") or source.get("source_url") or source.get("url") or ""
    ).strip()
    if schema.get("x-options-source") and endpoint:
        data_source = {"type": "api", "endpoint": endpoint}
        method = str(source.get("method") or source.get("source_method") or "GET").upper()
        if method in {"GET", "POST"}:
            data_source["method"] = method
        for source_key, target_key in (
            ("result_path", "resultPath"), ("value_key", "idField"),
            ("label_key", "labelField"), ("children_key", "childrenField"),
        ):
            if source.get(source_key):
                data_source[target_key] = source[source_key]
        return f"`dataSource: {json.dumps(data_source, ensure_ascii=False)}`"
    options = _option_labels(schema)
    if options:
        return f"`options: {json.dumps(options, ensure_ascii=False)}`"
    if schema.get("x-options-source"):
        return "先运行 `--list-options <字段名>`，再把返回候选放入 `options`"
    return "无；自由输入"


def _question_rows(schema: dict, *, prefix: str = "") -> list[tuple[str, dict, bool]]:
    rows: list[tuple[str, dict, bool]] = []
    required = set((schema or {}).get("required") or [])
    for field, prop in ((schema or {}).get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        path = f"{prefix}.{field}" if prefix else str(field)
        item = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        if prop.get("type") == "array" and isinstance(item.get("properties"), dict):
            rows.extend(_question_rows(item, prefix=f"{path}[]"))
        else:
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
        "   | 参数名 / `id` | label / `question` | 控件 `inputType` | 必填 | 推荐默认值 | 候选配置 |",
        "   |---|---|---|---|---|---|",
    ]
    for field, prop, required in rows:
        field_key = field.removesuffix("[]").split(".")[-1]
        label = str(
            (contract.get("field_labels") or {}).get(field_key)
            or prop.get("label") or prop.get("description") or field
        ).replace("|", "\\|")
        control = _question_control(prop)
        date_format = (
            f" / `{prop.get('dateFormat') or ('yyyy-MM-dd HH:mm' if prop.get('format') == 'date-time' else 'yyyy-MM-dd')}`"
            if control == "date" else ""
        )
        lines.append(
            f"   | `{field}` | {label} | `{control}`{date_format} | {'是' if required else '否'} | "
            f"{_schema_default_text(prop)} | {_question_option_source(prop)} |"
        )
    return lines


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
        "2. 读取该能力完整 `input_schema`；动态选择项先运行 "
        f"`bash scripts/submit.sh --capability {name} --list-options <字段名>` 获取实时候选，"
        "不得猜测选项名称或内部 ID。",
        (
            "3. **只收集本次所需查询字段。** 必填字段必须收集；可选筛选条件仅在用户明确指定时收集，"
            "不得主动补入、提问或提交其他录制筛选值。原生调用 `ask_user_question` 且本轮只调用一次，"
            if contract.get("kind") in _READ_CAPABILITY_KINDS else
            "3. **一次性收集全部表单项。** 原生调用 `ask_user_question` 且本轮只调用一次，"
        ) + (
            "把所有字段放进同一个 `questions[]`；多个表单也必须合并，不得在普通文本中提问，"
            "不得逐字段、逐分区或逐表单多轮追问。每项都必须设置字段名 `id`、业务 `label/question`、"
            "控件 `inputType`、`required`、非空推荐 `default`，以及适用的 `options`、"
            "`dataSource`、`multiple`、`dateFormat`。"
        ),
    ]
    if contract.get("kind") in _READ_CAPABILITY_KINDS:
        L += [
            "   查询 input 只能包含用户本轮明确指定的业务筛选条件；录制推荐值不得作为查询筛选条件自动提交。"
            "没有筛选条件时传空 input，由脚本仅应用 `x-dano-apply-default: true` 的分页等安全默认值。",
        ]
    L.extend(_question_collection_block(name, contract))
    L += [
        "4. `ask_user_question` 返回 `status=answered` 后，按 `answer` 对象的 `id` "
        "映射为能力参数；日期按 `dateFormat` 转换，数值转 JSON 数字，数组/复合字段按 "
        "`input_schema` 组装并再次校验。返回 `cancelled` 时立即停止。",
    ]
    if write:
        L.append(
            "5. 逐项汇总用户回答，再单独调用一次 "
            "`ask_user_question({question: <完整提交摘要>, confirm: true})`；"
            "只有返回 `status=answered` 且 `answer=true` 才允许执行。"
        )
    else:
        L.append("5. 这是只读能力，不需要最终 `confirm: true`；参数齐全后直接执行。")
    L += [
        f"6. 运行 `bash scripts/submit.sh {flags}{cflag}`，或在 Windows 运行对应 "
        "`scripts/submit.ps1`。一次调用由 Dano 完成内部接口编排。",
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
        "`python scripts/format_list.py --json '<output JSON>'`，最终只用 Markdown 表格呈现，"
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
    keys, required, props = _fields(m)
    reqs = [k for k in keys if k in required]
    write = m.requires_confirmation
    lines = [
        "### SOP 第3—5步的表单工具硬约束",
        "",
        "- 填表或补字段时必须原生调用 `ask_user_question`；每次回复最多一次，多个字段放入同一 `questions` 数组，不要逐字段拆成多轮。",
        "- 查询能力不得为可选筛选字段主动提问、自动使用录制推荐值或补造条件；没有用户筛选条件时直接使用空 input。",
        "- 多个表单、分区或连续步骤先一次性汇总；不得按表单、分区、步骤或字段分别提问。只收集一个非确认字段时才使用顶层 `question`。",
        "- `questions[]` 每项内放自己的 `id`、`question`、`default` 及适用的 `options`、`inputType`、`dateFormat`、`required`、`dataSource`、`multiple`。",
        "- `questions[].id` 必须与所选 capability 的参数名逐字一致；禁止翻译、改名或改成 snake_case，回答也必须按原始 id 映射。",
        "- SOP 第3步的字段配置表是唯一表单来源；`id`、`question`、`inputType`、`required`、"
        "`default`、`options`/`dataSource` 必须逐项照抄，任一不一致都必须在展示前修正。",
        "- 用户已明确提供字段值时可用该值作为 `default`；否则，契约存在录制推荐值时必须逐字复制，"
        "禁止自行改成另一个城市、金额、日期、枚举或业务示例。契约没有默认值时才按真实上下文给出非空推荐值。",
        "- 禁止把“请填写…”“例如…”“待确认”或其他提示语当作 `default` 或最终业务值。",
        "- `options`/`dataSource` 必须逐字取自字段配置或 `--list-options` 结果；禁止自行生成、替换、增删候选项。"
        "枚举默认值必须与候选项逐字一致，禁止回落为候选第一项。",
        "- 录制样例必须保留为推荐值，禁止空字符串或 `<字段>` 占位。",
        "- 推荐默认值只用于 `ask_user_question` 展示；仅 `x-dano-apply-default: true` 可静默应用，其余必须等用户回答。",
        "- 只有业务上确实必填的字段设置 `required: true`。日期使用 `inputType: \"date\"` 和 `dateFormat`；动态选项使用真实 `dataSource` 或先 `--list-options`。",
        "- 返回 `status=answered` 后，单题取 `answer`，多题按 questions 的 `id` 合并参数；用户取消时立即停止。",
        "- 工具返回校验错误时修正参数后静默重试原生工具调用，不在普通文本中模拟提问。",
    ]
    if len(contracts) > 1:
        lines.append("- 先根据用户目标选择一个明确 capability；不同能力的必填字段不能混用。")
        for name, contract in contracts.items():
            cap_required = [str(k) for k in (contract.get("required") or [])]
            lines.append(
                f"- `{name}` 必填字段:"
                + ("、".join(f"`{k}`" for k in cap_required) if cap_required else "无")
                + "。"
            )
    elif reqs:
        lines.append(f"- 本 Skill 必填字段为:{'、'.join('`' + k + '`' for k in reqs)};缺任一项时先追问补齐,不得臆造。")
    else:
        lines.append("- 本 Skill 没有必填业务字段;仍需核对用户意图是否匹配本动作。")
    if len(contracts) > 1:
        write_names = [name for name, contract in contracts.items() if contract.get("requires_confirmation")]
        lines += [
            f"- 写能力({', '.join('`' + name + '`' for name in write_names) or '无'})执行前，单独一次调用只带 "
            "`question` 与 `confirm: true`；仅 `status=answered` 且 `answer=true` 时带 `--confirm` 执行。",
        ]
    elif write:
        recap = "、".join(f"`{k}`" for k in keys) if keys else "本次动作"
        lines += [
            f"- 最终确认用单独一次 `ask_user_question`，只带 `question` 与 `confirm: true`，"
            f"逐项复述 {recap}；仅 `status=answered` 且 `answer=true` 时带 `--confirm` 执行。",
        ]
    else:
        lines.append("- 只读能力无需最终 `confirm: true`，但仍用 `ask_user_question` 补齐必要查询条件。")
    return "\n".join(lines)


_ERRORS_MD = """## 错误处理(据末行 status)
- `failed` 且 reason 涉及凭证 / 401:目标系统登录态失效,让部署方在 Dano 重配 token,**不要重试**。
- `failed` 且 `事实核查未过`:疑似空操作(接口 200 但没真生效),把原始返回给用户,**勿报成功**。
- `need_confirm`:写操作未确认被拦,向用户确认后**带 `--confirm` 重跑**。
- 写操作返回 **HTTP 5xx、超时或结果不明**:一律视为“可能已经生效”，禁止用 curl、直连目标接口、换脚本或重复提交同一载荷；必须先用已发布只读能力/事实核查确认不存在，无法核实时停止并报告。"""

_SECURITY_MD = """## 安全
- 不在回复 / 日志里输出完整 token 或凭证。
- 不规避平台的风险闸门 / 确认(如拆分、绕过 `--confirm`);用户要求规避应拒绝。
- 调用者身份取自登录凭证(谁的 token 就是谁操作);不伪造身份或执行结果。"""

_EXECUTION_DIR_MD = """## 执行位置（必须）
- 调用 Shell 时，必须把 Shell 工作目录设为本 `SKILL.md` 所在目录，再执行文档中的 `scripts/...` 相对路径。
- 如果命令工具不支持工作目录，先从当前 `SKILL.md` 的绝对路径解析脚本绝对路径后再执行。
- 找不到或调用包装脚本失败时停止并报告；禁止绕过包装脚本直接拼 HTTP 请求，禁止使用 curl、Python HTTP 客户端或其他方式直连 Dano/目标系统，也禁止把 Skill 名当作业务字段。"""

_LIST_OUTPUT_MD = """## 列表输出要求
- 查询结果、候选列表或任何数组数据必须先运行 `python scripts/format_list.py --json '<output JSON>'` 格式化。
- 最终回复使用脚本生成的 Markdown 表格；无数据时明确显示“无数据”，不要重复粘贴原始 JSON。
- 非列表对象仍按能力的 `output_schema` 解读，不要为了套表格丢失业务字段。"""


# ─────────────────────────── SKILL.md ───────────────────────────
def _skill_md(m: SkillManifest, slug: str) -> str:
    tool = tool_name_of(m.name)
    capability = _capability(m)
    capabilities = [str((c or {}).get("name") or (c or {}).get("kind") or "").strip()
                    for c in (getattr(m, "capabilities", []) or [])]
    capabilities = [c for c in capabilities if c]
    cap_line = ", ".join(dict.fromkeys(capabilities)) or capability
    confirm = m.requires_confirmation
    contracts = _capability_contracts(m)
    has_fact_verification = any(contract.get("verify_required") for contract in contracts.values())
    has_batch_capability = any(
        contract.get("kind") in {"submit_batch", "validate_batch"}
        for contract in contracts.values()
    )
    multi_capability = len(contracts) > 1
    keys, required, props = _fields(m)
    numset = set(_numeric_fields(props))
    if keys:
        def _cell(k: str) -> str:
            p = props[k] or {}
            d = p.get("description", "") or k
            h = _opts_hint(p)
            return (d + ("；" + h if h else "")).replace("\n", " ").replace("|", "\\|")
        rows = "\n".join(
            f"| `{k}` | {_ptype(k, props, numset)} | {'是' if k in required else '否'} | "
            f"{_schema_default_text(props[k])} | {_cell(k)} |"
            for k in keys)
        table = "| 参数 | 类型 | 必填 | 默认值 | 说明 |\n|---|---|---|---|---|\n" + rows
        ex_args = "{" + ", ".join(
            (f'"{k}": <{k}>' if k in numset else f'"{k}": "<{k}>"') for k in keys) + "}"
    else:
        table, ex_args = "(无业务参数)", "{}"
    flags = _flags(m)
    cflag = " --confirm" if confirm else ""
    confirm_note = ("\n> ⚠ 高风险写操作:**执行前必须向用户复述将提交内容并取得同意**,确认后再带 `--confirm` 调用。\n"
                    if confirm else "")
    supported_titles = list(dict.fromkeys(
        str(contract.get("title") or name).strip()
        for name, contract in contracts.items()
        if str(contract.get("title") or name).strip()
    ))
    supported_scope = "、".join(f"「{title}」" for title in supported_titles) or f"「{m.title}」"
    desc = (
        f"{m.description}。仅用于这些已发布能力:{supported_scope};"
        "只有用户意图明确匹配其中一项时才使用,不把同一业务域的其他动作视为已支持。"
    )
    # 审批路径(有 business_meta 才出,grounded);放在 SOP 前,供阶段3 引用
    approval = _approval_section(getattr(m, "business_meta", {}) or {})
    approval_md = (approval + "\n\n") if approval else ""
    parameter_md = _capability_contract_section(m) if multi_capability else f"## 参数\n{table}"
    sop = _multi_capability_sop(m) if multi_capability else _sop_section(m, flags, cflag)
    interaction = _interaction_section(m)
    quality = _multi_capability_quality_section(m) if multi_capability else _quality_section(m)
    relationships = _capability_relationship_section(m)
    default_capability = _export_default_capability(m)
    if multi_capability:
        example_section = (
            "## 示例\n"
            "先根据用户目标选择能力，再使用该能力在“能力调用契约”中的 JSON 示例；"
            "不得把查询能力的输入套用到提交能力，也不得在能力不明确时默认执行写操作。"
        )
        protocol_default = "本 Skill 有多个独立能力，调用时必须显式指定 `--capability`"
    else:
        example_section = (
            f"## 示例\n**Input:** 用户说\"帮我办理一条{m.title}\"。\n"
            f"**调用:** `bash scripts/submit.sh {flags}{cflag}`\n"
            f"**参数 JSON(等价):** `{ex_args}`"
        )
        protocol_default = f"默认 capability:`{default_capability}`"
    has_read_capability = any(c.get("kind") in _READ_CAPABILITY_KINDS for c in contracts.values())
    has_write_capability = any(c.get("kind") not in _READ_CAPABILITY_KINDS for c in contracts.values())
    if has_read_capability and has_write_capability:
        not_use = (
            "用户只是咨询制度或闲聊时不要调用；查询需求只调用只读 capability，"
            "没有明确办理意图或未取得最终确认时不得调用写 capability；禁止代他人审批、驳回。"
        )
    elif has_read_capability:
        not_use = "用户只是咨询制度或闲聊、问题不属于本 Skill 查询范围时不要调用；不得把查询结果当成写操作成功。"
    else:
        not_use = "用户只是咨询制度/查询状态、没有明确办理意图、必填信息未确认或要求代他人审批/驳回时不要调用。"
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
    success_example = {"status": "succeeded", "state": "completed", "output": {}}
    if has_fact_verification:
        success_example["fact_check"] = {"passed": True}
    partial_status_row = (
        "| `partial_success` | 批量能力仅部分条目成功 | 逐项报告成功/失败及原索引；"
        "不得笼统宣称全部成功，也不得自动重试成功项 |"
        if has_batch_capability else ""
    )
    return f"""---
name: {json.dumps(_skill_name(m.title, slug), ensure_ascii=False)}
description: {json.dumps(desc, ensure_ascii=False)}
---

# {m.title}

这是 Dano **已上架 Skill 的代理**:{platform_guards}都在 Dano 侧。本端负责**收集参数、本地校验、提交前确认**,再调用 Dano,**不接触目标系统凭证、不自行裁定结果**。
{confirm_note}
## 何时使用
当用户明确需要{supported_scope}之一时使用本 skill,即使没说出 skill 名或接口名；未列出的新建、提交、查询、撤回或审批动作不在本 Skill 范围内。

**不该直接使用**:{not_use}

{_EXECUTION_DIR_MD}

{approval_md}{sop}

{interaction}

{parameter_md}

{relationships}

> 流程句柄/模板、调用者身份(取自登录凭证)、调用凭证等由 Dano 运行期注入,**不需要也不应**由你提供。

{quality}

{_LIST_OUTPUT_MD}

## 输出契约(脚本末行 JSON)
| status | 含义 | 你应做的 |
|---|---|---|
| `succeeded` | {success_meaning} | 按该能力的 output_schema 解读 `output` |
{partial_status_row}
| `need_select` | 复合流程消歧:有多个候选待选 | 把 `candidates` 给用户选,再用 `--json` 把选中项的 `bind` 值带上重跑 |
| `need_confirm` | 写操作未确认被拦 | 向用户确认后,**带 `--confirm` 重跑** |
| `failed` | 失败(见 `reason`) | 把 reason 告知用户;缺参/凭证按故障排除处理,**勿谎报成功** |

示例:
```json
{json.dumps(success_example, ensure_ascii=False)}
{{"status": "failed", "reason": "缺必填: <字段>"}}
```

{_ERRORS_MD}

{_SECURITY_MD}

## 限制
本 skill 只支持“能力调用契约”中列出的 capability。未列出的查询、撤回、审批、历史等动作，**不要声称支持**。

{example_section}

## 故障排除
| 现象 | 处理 |
|---|---|
| `DANO_URL/DANO_TENANT_KEY 未设置` | 让部署方配好这两个环境变量(勿写进文件) |
| `HTTP 401` / 凭证无效 | Dano「运行配置」里该租户目标系统 token 失效,重配 |
| `缺必填: …` / `字段 … 需为数字` | 补齐参数表里的必填项 / 把数值字段填成数字再调 |
| `事实核查未过` | Dano 判定没真生效(疑似空操作),把原始返回给用户,**勿报成功** |

## 运行前置(环境变量,部署方配置,勿写进文件)
- `DANO_URL`:Dano 网关地址,如 `http://localhost:8077`
- `DANO_TENANT_KEY`:本租户 api_key(作 `X-Tenant-Key`)

## 调用协议草案
可用 capability:`{cap_line}`。
{protocol_default}。脚本按旧工具名 `{tool}` 兼容调用，同时始终携带明确的 capability。`--json` 使用能力 envelope:
```json
{json.dumps(protocol_example, ensure_ascii=False)}
```

完整机器契约见 `references/CONTRACT.json`；存在选择型字段时参考 `references/OPTIONS.md`。
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
                opts = _option_labels(prop)
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
                    values = "\n".join(f"- {value}" for value in opts)
                    blocks.append(f"{head}\n\n传显示名（勿传内部 ID）:\n{values}")
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
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SKILL_ID = "__SKILL_ID__"
TOOL = "__TOOL__"
CAPABILITY = __CAPABILITY__
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
    allowed = schema.get("enum")
    if allowed and value not in allowed:
        raise ValueError("字段 %s 必须是: %s" % (path, ", ".join(map(str, allowed))))


def main():
    ap = argparse.ArgumentParser(description="调用 Dano skill " + TOOL)
    for f in FIELDS:
        ap.add_argument("--" + f, default=None)
    ap.add_argument("--json", dest="raw", default=None,
                    help="旧格式:arguments JSON;新格式:调用 envelope,如 {\"capability\":\"...\",\"input\":{...}}")
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
    if args.raw:
        try:
            raw_obj = json.loads(args.raw)
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
        _emit({"status": "options", "field": res.get("field"), "count": res.get("count"),
               "options": res.get("options"), "note": res.get("note")})
        return

    if args.diagnose:
        try:
            with urllib.request.urlopen(url + "/health", timeout=10) as r:
                ok = r.status == 200
            _emit({"status": "diagnose_done", "dano_url": url, "health_ok": ok, "tenant_key_set": bool(key)})
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
        _emit({"status": "partial_success" if partial else "succeeded", "state": state,
               "output": output, "fact_check": fc})
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
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$dir/dano_call.py" @args
exit $LASTEXITCODE
"""

_FORMAT_LIST_PY = r'''#!/usr/bin/env python3
"""Convert a Dano result or ordinary JSON list to a Markdown table."""
import argparse
import json
import sys


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


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).replace("|", r"\|").replace("\r", " ").replace("\n", "<br>")


def format_table(value):
    rows = _list_rows(value)
    if not rows:
        return "无数据"
    if not any(isinstance(row, dict) for row in rows):
        rows = [{"值": row} for row in rows]
    else:
        rows = [row if isinstance(row, dict) else {"值": row} for row in rows]
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        return "无数据"
    header = "| " + " | ".join(_cell(column) for column in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_cell(row.get(column)) for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def main():
    parser = argparse.ArgumentParser(description="把 JSON 列表格式化为 Markdown 表格")
    parser.add_argument("--json", dest="raw")
    parser.add_argument("--file")
    args = parser.parse_args()
    if args.raw is not None:
        raw = args.raw
    elif args.file:
        with open(args.file, encoding="utf-8") as handle:
            raw = handle.read()
    else:
        raw = sys.stdin.read()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        print("JSON 解析失败: %s" % error, file=sys.stderr)
        raise SystemExit(2)
    print(format_table(value))


if __name__ == "__main__":
    main()
'''


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
            json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
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
        formatter.write_text(_FORMAT_LIST_PY, encoding="utf-8", newline="\n")
        _chmod_x(formatter)
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
            "$dir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
            'python "$dir/%s.py" @args\n' % (action, action))


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
    """确定性渲染业务剧本 SKILL.md,不依赖已删除的 generation/playbook 包。"""
    label = _biz_label(business, manifests)
    ops = "\n".join(
        f"- `{m.action}`: {m.title or m.action}"
        f"({'写操作,需 --confirm' if m.requires_confirmation else '查询/只读'})"
        for m in manifests
    ) or "- 暂无操作"
    script_lines: list[str] = []
    for m in manifests:
        contracts = _capability_contracts(m)
        if len(contracts) == 1:
            script_lines.append(
                f"bash scripts/{m.action}.sh {_flags(m)}"
                f"{' --confirm' if m.requires_confirmation else ''}"
            )
        else:
            script_lines.extend(
                f"bash scripts/{m.action}.sh --capability {name} --json '<能力输入 JSON>'"
                f"{' --confirm' if contract.get('requires_confirmation') else ''}"
                for name, contract in contracts.items()
            )
    scripts = "\n".join(script_lines)
    fields = "\n\n".join(
        f"### {m.title or m.action}\n"
        f"{_multi_capability_sop(m) if len(_capability_contracts(m)) > 1 else _sop_section(m, _flags(m), ' --confirm' if m.requires_confirmation else '')}"
        f"\n\n{_interaction_section(m)}"
        f"\n\n{_capability_contract_section(m)}"
        f"\n\n{_multi_capability_quality_section(m) if len(_capability_contracts(m)) > 1 else _quality_section(m)}"
        for m in manifests
    )
    description = f"{label}: Dano 导出的业务剧本,包含 {len(manifests)} 个已上架操作。用于办理或查询该业务时按脚本调用 Dano。"
    return f"""---
name: {json.dumps(_skill_name(label, slug), ensure_ascii=False)}
description: {json.dumps(description, ensure_ascii=False)}
---

# {label}

这是 Dano 导出的业务剧本。所有真实执行都在 Dano 服务端完成;本 skill 只负责收集参数、确认风险、调用脚本。

{_EXECUTION_DIR_MD}

## 操作清单
{ops}

## 快速调用
```bash
{scripts}
```

## 通用规则
- 缺必填字段先追问,不要臆造。
- 写操作必须取得用户明确确认并带 `--confirm`。
- 批量操作按条目逐项报告；`partial_success` 不得表述成全部成功，也不得自动重试成功项。
- 结果只认脚本末行 JSON 的 `status`;失败、部分成功或回查未通过时不要报全部成功。
- `DANO_URL` 和 `DANO_TENANT_KEY` 由部署环境提供,不要写进文件。

## 操作细则
{fields}

{_LIST_OUTPUT_MD}
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
            "skills": [manifest.model_dump(mode="json") for manifest in manifests],
        }
        (folder / "references" / "CONTRACT.json").write_text(
            json.dumps(bundle_contract, ensure_ascii=False, indent=2) + "\n",
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
        formatter.write_text(_FORMAT_LIST_PY, encoding="utf-8", newline="\n")
        _chmod_x(formatter)
        return _publish_folder(folder, target, slug, _skill_name(label, slug))
    except Exception:
        _abort_stage(folder)
        raise


# ─────────────────────────── index 路由(总台,自动生成)───────────────────────────
def _index_md(entries: list[dict], slug: str) -> str:
    """业务总台:列出所有业务剧本 + 触发词,把用户意图路由到对应剧本。无业务专属逻辑。"""
    rows = "\n".join(f"| {e['label']} | `{e['folder']}` | {e['ops']} 个操作 |" for e in entries)
    table = "| 业务 | 剧本目录 | 规模 |\n|---|---|---|\n" + rows
    names = "、".join(e["label"] for e in entries) or "(暂无)"
    description = f"OA 业务总台:统一入口,把用户意图路由到具体业务剧本({names})。当用户提到办理/查询任一 OA 业务时,先看本目录选对剧本。"
    return f"""---
name: "Dano OA 业务总台"
description: {json.dumps(description, ensure_ascii=False)}
---

# OA 业务剧本总台

这是所有已生成业务剧本的**路由目录**。用户说要办什么,在下表里找到对应业务,
打开它的剧本目录(各自一本自包含 skill),按那本剧本的六段流程办。

## 业务目录
{table}

> 每本剧本都含:①自检 ②办理前校验 ③办理(需确认) ④错误处置 ⑤事后确认 ⑥缺失恢复。
> 找不到对应业务就如实告知用户"没有这个业务的 skill",**不要臆造**。
"""


def _write_index(out_dir: Path, entries: list[dict],
                 *, reference_docs: list[tuple[Path, str]] | None = None) -> str:
    docs = reference_docs if reference_docs is not None else _load_reference_markdown(_configured_reference_dir())
    _validate_reference_markdown(docs)
    slug = "dano-oa-index"
    target = out_dir / slug
    folder = _stage_folder(out_dir, slug)
    try:
        (folder / "agents").mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(_index_md(entries, slug), encoding="utf-8")
        (folder / "agents" / "openai.yaml").write_text(
            _agents_openai_yaml(
                slug, "Dano OA 业务总台",
                "根据用户目标选择正确的 Dano OA 业务 Skill，并进入对应已发布业务流程",
            ),
            encoding="utf-8",
        )
        _publish_folder(folder, target, slug, "Dano OA 业务总台")
        return slug
    except Exception:
        _abort_stage(folder)
        raise


async def write_skills(tenant: str, out_dir: str, *, rich: bool = True,
                       exclude_skill_ids: set[str] | None = None) -> list[str]:
    """核心:读该租户已上架 Skill 写成官方格式 skill;**不管连接池**(供已持有池的网关复用)。

    带 business 标签的操作**按业务归组成一本自包含剧本 skill**(多操作);其余各自一个单动作 skill。
    rich 参数保留兼容旧调用;当前导出只做确定性渲染。每业务独立 try/except,一个失败不连累其它。
    最后自动生成 index 路由总台。
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
    log.info("export.target", out_abs=str(out.resolve()), tenant=tenant)   # 落盘绝对路径(排查"看不到文件")
    groups: dict = defaultdict(list)
    standalone: list[SkillManifest] = []
    for m in manifests:
        (groups[(m.subsystem, m.business)].append(m) if getattr(m, "business", "")
         else standalone.append(m))
    written: list[str] = []
    index_entries: list[dict] = []
    for (sub, biz), ms in groups.items():
        try:                                                 # 每业务独立:一个崩不连累其它
            slug = _slug(f"{sub}.{biz}")
            md = _business_skill_md(sub, biz, ms, slug)
            folder = _write_business_skill(
                out, sub, biz, ms, md_text=md, reference_docs=reference_docs)
            log.info("export.business_skill", business=biz, subsystem=sub,
                     ops=[m.action for m in ms], folder=folder.name)
            written.append(folder.name)
            index_entries.append({"label": _biz_label(biz, ms), "folder": folder.name, "ops": len(ms)})
        except Exception as e:  # noqa: BLE001
            log.warning("export.business_skill_failed", business=biz, subsystem=sub, error=str(e))
    for m in standalone:
        try:
            written.append(_write_skill(out, m, reference_docs=reference_docs).name)
        except Exception as e:  # noqa: BLE001
            log.warning("export.standalone_failed", action=m.action, error=str(e))
    if index_entries:                                        # 自动生成 index 路由总台
        written.append(_write_index(out, index_entries, reference_docs=reference_docs))
    log.info("export.agent_skills", tenant=tenant, out=str(out),
             count=len(written), businesses=len(groups), standalone=len(standalone))
    return written


async def export(tenant: str, out_dir: str) -> list[str]:
    """命令行入口:自管连接池(init→write→close);返回写出的文件夹名列表。"""
    from dano.infra.db import close_pool, init_pool
    await init_pool()
    try:
        return await write_skills(tenant, out_dir)
    finally:
        await close_pool()


def main() -> None:
    ap = argparse.ArgumentParser(description="导出已上架 Skill 为官方 skill-creator 格式 skill(.agents/skills/)")
    ap.add_argument("--tenant", required=True, help="租户名,如 demo-oa")
    ap.add_argument("--out", required=True, help="输出目录,通常是 <pi仓库>/.agents/skills")
    args = ap.parse_args()
    written = asyncio.run(export(args.tenant, args.out))
    print(f"已导出 {len(written)} 个 skill 到 {args.out}:")
    for w in written:
        print("  -", w)
    if not written:
        print("  (该租户没有已上架 Skill;先在「接入系统」生成并上架)")


if __name__ == "__main__":
    main()
