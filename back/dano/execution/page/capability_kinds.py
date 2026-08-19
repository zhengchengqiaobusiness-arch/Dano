"""Stage 6: capability operation kinds and write/read families."""
from __future__ import annotations

from typing import Any
import hashlib
import json
from urllib.parse import urlparse, parse_qs
import re
from dano.execution.page.flow_spec_core.models import (
    FlowCapability,
    FlowSpec,
    FlowStep,
)
from dano.execution.page.request_capture import (
    _parse_body,
)
from dano.execution.page.recording_facts import (
    _has_query_action_evidence,
    _request_path,
)
from dano.execution.page.flow_materialization.field_contracts.common import (
    _is_missing_wire_placeholder,
)


READ_CAPABILITY_KINDS = frozenset({
    "query", "query_status", "list_options", "validate", "validate_batch",
    "preview", "inspect", "export",
})


WRITE_CAPABILITY_KINDS = frozenset({
    "create", "update", "save_draft", "submit", "submit_batch",
    "approve", "reject", "withdraw", "delete",
})


ALLOWED_CAPABILITY_KINDS = READ_CAPABILITY_KINDS | WRITE_CAPABILITY_KINDS


def _is_write_step(step: FlowStep) -> bool:
    meta = step.source_meta or {}
    role = str(meta.get("role") or step.semantic_role or "").strip().lower()
    if role in {"business_get", "read_context", "read_option", "option_source", "explicit_read_option"}:
        return False
    if role in {"business_write", "submit_anchor"}:
        return True
    return (step.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}


def _looks_batch_step(step: FlowStep) -> bool:
    meta = step.source_meta or {}
    if any(bool(meta.get(key)) for key in ("batch", "is_batch", "batch_intent", "repeated_submission")):
        return True
    text = f"{step.name} {step.path} {step.url} {meta.get('trigger_locator') or ''}".lower()
    if any(x in text for x in ("batch", "bulk", "批量")):
        return True
    try:
        body = _parse_body(step.body_source)
    except Exception:
        body = None
    # A large class of enterprise APIs wraps a single form object in ``[{...}]``.
    # Array shape or ``[0].field`` paths alone are therefore not evidence of a
    # caller-visible batch contract. Multiple recorded rows are grounded evidence;
    # a single row remains a normal submit unless URL/metadata says otherwise.
    return isinstance(body, list) and len(body) > 1


def _write_contract_is_batch(
    spec: FlowSpec,
    write_steps: list[FlowStep],
    cap: FlowCapability | None = None,
) -> bool:
    """Return the single reproducible submit/submit_batch decision."""
    return bool(
        any(_looks_batch_step(step) for step in write_steps)
        or (cap is not None and _capability_has_explicit_batch_intent(cap))
    )


def _repeated_write_command_signature(step: FlowStep) -> tuple[Any, ...] | None:
    """Identify one reusable command without relying on vendor path names."""
    if not _is_write_step(step):
        return None
    meta = step.source_meta or {}
    trigger_op = str(meta.get("trigger_op") or "").lower()
    if trigger_op not in {"click", "submit", "select", "pick"}:
        return None
    raw_path = str(step.path or step.url or "")
    return (
        (step.method or "GET").upper(),
        urlparse(raw_path).path or raw_path.split("?", 1)[0],
        tuple(sorted(param.path for param in step.params)),
        _write_command_discriminators(step),
        str(meta.get("page_id") or meta.get("page_url") or ""),
        str(meta.get("frame_id") or meta.get("frame_url") or ""),
        _locator_action_name(str(meta.get("trigger_locator") or "")).casefold(),
    )


_WRITE_COMMAND_DISCRIMINATOR_RE = re.compile(
    r"(?:^|[_-])(?:op|operation|action|command|event|intent|mode)(?:$|[_-])",
    re.I,
)


def _write_command_discriminators(step: FlowStep) -> tuple[tuple[str, str], ...]:
    """Keep RPC-style commands distinct while ignoring record-specific values."""
    try:
        body = _parse_body(step.body_source)
    except Exception:
        body = None
    found: list[tuple[str, str]] = []

    def visit(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                normalized_key = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).casefold()
                if (
                    _WRITE_COMMAND_DISCRIMINATOR_RE.search(normalized_key)
                    and isinstance(child, (str, int, float, bool))
                ):
                    found.append((path.casefold(), str(child).casefold()))
                else:
                    visit(child, path)
        elif isinstance(value, list):
            for child in value:
                visit(child, prefix)

    visit(body)
    return tuple(sorted(set(found)))


def _write_steps(spec: FlowSpec) -> list[FlowStep]:
    return [
        step for step in spec.steps
        if _is_write_step(step)
        and not (step.source_meta or {}).get("duplicate_observation_of")
    ]


_CAPABILITY_PATH_PREFIXES = frozenset({
    "api", "rest", "gateway", "openapi", "v1", "v2", "v3", "oa", "system", "admin", "admin-api",
})


def _write_operation_key(step: FlowStep) -> str:
    """Group one reusable write command, not one recorded row click.

    Per-click action/transaction ids and row wrappers in the locator are
    samples of the same business operation.  Similar commands stay distinct
    when the HTTP contract, visible action name, or command discriminators
    differ.
    """
    meta = step.source_meta or {}
    raw_path = str(step.path or step.url or "")
    identity = "|".join((
        str(meta.get("page_id") or meta.get("page_url") or meta.get("document_url") or ""),
        str(meta.get("frame_id") or meta.get("frame_url") or ""),
        str(step.method or "POST").upper(),
        urlparse(raw_path).path or raw_path.split("?", 1)[0],
        _capability_operation_kind(step),
        _locator_action_name(str(meta.get("trigger_locator") or "")).casefold(),
        ",".join(sorted(str(param.path or "") for param in step.params or [])),
        json.dumps(_write_command_discriminators(step), ensure_ascii=False),
    ))
    return f"write_{hashlib.sha1(identity.encode('utf-8')).hexdigest()[:10]}"


def _capability_kind_family(kind: str) -> str:
    # Only the legacy single/batch submit pair is interchangeable. Draft,
    # submit, withdraw and delete are separate caller-visible operations.
    return "write" if kind in {"submit", "submit_batch"} else str(kind or "")


_ACTION_LABELS = (
    "撤回", "撤销", "作废", "取消", "删除", "驳回", "同意", "审批",
    "提交", "保存", "新增", "创建", "更新", "编辑", "导出", "查询", "搜索",
)


def _capability_operation_kind(step: FlowStep) -> str:
    """Infer a public business operation from grounded request/action evidence."""
    method = str(step.method or "GET").upper()
    meta = step.source_meta or {}
    locator = _locator_action_name(str(meta.get("trigger_locator") or "")).casefold()
    signature = " ".join((
        locator,
        str(step.name or ""),
        _request_path({"url": step.path or step.url}),
    )).casefold()
    is_query_action = _has_query_action_evidence(
        meta.get("trigger_op"),
        str(meta.get("trigger_locator") or ""),
    )
    if (
        method in {"GET", "HEAD"}
        or str(meta.get("role") or step.semantic_role or "") == "business_get"
    ):
        if re.search(r"(?:^|[/_.\s-])(?:export|download|excel)(?:$|[/_.\s-])|导出|下载", signature):
            return "export"
        if re.search(
            r"(?:detail|inspect|view|progress)|"
            r"(?:^|/)(?:get)(?:$|[/?#])|"
            r"(?:^|/)(?:list|page)-by-[^/?#]*(?:id|key)(?:$|[/?#])|"
            r"详情|查看|进度",
            signature,
        ):
            return "inspect"
        if re.search(r"(?:preview)|预览", signature):
            return "preview"
        if is_query_action:
            return "query_status"
        return "query_status"
    # Specific business verbs must win over generic edit/update markers.
    if re.search(r"(?:withdraw|revoke)|撤回|撤销", signature):
        return "withdraw"
    if re.search(r"(?:delete|remove)|删除", signature):
        return "delete"
    if re.search(r"(?:reject)|驳回", signature):
        return "reject"
    if re.search(r"(?:approve|approval|pass)|审批|同意|通过", signature):
        return "approve"
    context_url = str((meta.get("trigger_page_context") or {}).get("url") or "")
    context_ids = {
        str(value)
        for values in parse_qs(urlparse(context_url).query).values()
        for value in values
        if value not in (None, "") and not _is_missing_wire_placeholder(value)
    }
    request_ids = {
        str(param.value)
        for param in step.params or []
        if re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key).split(".")[-1].casefold(),
        ) == "id"
        and param.value not in (None, "")
        and not _is_missing_wire_placeholder(param.value)
    }
    editable_business_fields = [
        param for param in step.params or []
        if param.exposed_to_user
        and param.source_kind == "user_input"
        and re.sub(
            r"[^a-z0-9]+", "",
            str(param.path or param.key).split(".")[-1].casefold(),
        ) != "id"
    ]
    if context_ids & request_ids and editable_business_fields:
        # Some systems reuse a submit-looking endpoint when an existing record
        # is edited. The selected identity plus caller-edited business fields
        # is stronger operation evidence than that route name.
        return "update"
    if re.search(r"(?:submit|commit)|提交", signature):
        return "submit"
    if re.search(r"(?:draft|save-draft)|草稿|暂存", signature):
        return "save_draft"
    if re.search(r"(?:create|insert|add)|新增|创建", signature):
        return "create"
    if re.search(r"(?:update|edit|modify)|更新|编辑|保存", signature):
        return "update"
    return "submit"


_MUTATING_RECORD_KINDS = frozenset({"update", "approve", "reject", "delete", "withdraw"})

_PENDING_FLOW_SPEC_HELPERS = {'_capability_has_explicit_batch_intent': 'dano.execution.page.capability_contracts', '_locator_action_name': 'dano.execution.page.capability_contracts'}


def _bind_flow_spec_helpers() -> None:
    import sys
    module_globals = globals()
    for name, owner in _PENDING_FLOW_SPEC_HELPERS.items():
        mod = sys.modules.get(owner)
        if mod is None or not hasattr(mod, name):
            continue
        module_globals[name] = getattr(mod, name)


_bind_flow_spec_helpers()
