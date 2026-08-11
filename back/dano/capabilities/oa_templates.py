"""Generic workflow-dialect adapters configured by optional tenant packs."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import re
from typing import Any

import structlog

from dano.business_packs import dialects_for
from dano.shared.asset_bodies import WorkflowSkillBody


log = structlog.get_logger(__name__)
_CONTROL_TYPES = {
    "el-input-number": ("number", False),
    "el-slider": ("number", False),
    "el-rate": ("number", False),
    "el-switch": ("boolean", False),
    "el-select": ("string", True),
    "el-radio-group": ("string", True),
    "el-radio": ("string", True),
    "el-checkbox-group": ("string", True),
    "el-checkbox": ("string", True),
}


def _dig(node: object, path: list[object]) -> object:
    current = node
    for token in path:
        if isinstance(current, dict) and str(token) in current:
            current = current[str(token)]
        elif isinstance(current, list) and str(token).isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _walk_form_fields(node: object, out: list[dict]) -> None:
    if isinstance(node, dict):
        model = node.get("__vModel__") or node.get("vModel")
        if isinstance(model, str) and model:
            config = node.get("__config__") if isinstance(node.get("__config__"), dict) else {}
            tag = str(config.get("tag") or node.get("tag") or "")
            json_type, is_enum = _CONTROL_TYPES.get(tag, ("string", False))
            out.append({
                "key": model,
                "label": str(config.get("label") or node.get("label") or model),
                "type": tag,
                "json_type": json_type,
                "enum": is_enum,
            })
        for value in node.values():
            _walk_form_fields(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_form_fields(value, out)


class OATemplate(ABC):
    """One deterministic workflow-system dialect."""

    name: str = "generic"

    @abstractmethod
    def matches(self, spec: dict[str, Any]) -> bool:
        """Return whether objective OpenAPI features match this dialect."""

    def success_rule(self) -> str | None:
        return None

    def infrastructure_patterns(self) -> tuple[str, ...]:
        return ()

    def workflows(self) -> list[WorkflowSkillBody]:
        return []

    def contract_tokens(self) -> tuple[str, ...]:
        return ()

    def submit_endpoints(self) -> tuple[str, ...]:
        return ()

    async def discover_contract(
        self,
        template_id: str,
        base_url: str,
        token: str,
        *,
        get=None,
    ):  # noqa: ANN001, ANN201
        return None

    def form_probe_path(self, template_id: str) -> str | None:
        return None

    def parse_form_fields(self, probe_response: object) -> list[dict]:
        return []

    def parse_approval_chain(self, spec: dict[str, Any], template_id: str) -> dict:
        return {}

    def template_list_paths(self) -> tuple[str, ...]:
        return ()

    def parse_template_list(self, payload: object) -> list[dict]:
        return []

    def template_ids(self, spec: dict[str, Any]) -> list[str]:
        return []

    def template_id_in(self, spec: dict[str, Any], body_json: str) -> str:
        return next((value for value in self.template_ids(spec) if value in body_json), "")


class ConfiguredOATemplate(OATemplate):
    """Interpret one data-only dialect declaration from a tenant pack."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.name = str(config.get("name") or "configured")

    def matches(self, spec: dict[str, Any]) -> bool:
        match = self.config.get("match") or {}
        paths = " ".join((spec.get("paths") or {}).keys()).casefold()
        schemas = (spec.get("components") or {}).get("schemas") or {}
        path_matches = any(str(value).casefold() in paths for value in match.get("path_contains") or [])
        schema_matches = any(str(value) in schemas for value in match.get("schema_names") or [])
        return path_matches or schema_matches

    def success_rule(self) -> str | None:
        return str(self.config.get("success_rule") or "") or None

    def infrastructure_patterns(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.config.get("infrastructure_patterns") or [])

    def contract_tokens(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.config.get("contract_tokens") or [])

    def submit_endpoints(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.config.get("submit_endpoints") or [])

    async def discover_contract(
        self,
        template_id: str,
        base_url: str,
        token: str,
        *,
        get=None,
    ):  # noqa: ANN001, ANN201
        if self.config.get("contract_synth") is not True:
            return None
        from dano.onboarding.contract_synth import synthesize_contract

        return await synthesize_contract(template_id, base_url, token, get=get)

    def form_probe_path(self, template_id: str) -> str | None:
        template = str(self.config.get("form_probe_path") or "")
        return template.format(template_id=template_id) if template and template_id else None

    def parse_form_fields(self, probe_response: object) -> list[dict]:
        if not isinstance(probe_response, dict):
            return []
        if probe_response.get("code") not in (None, 200, 0):
            return []
        raw = _dig(probe_response, list(self.config.get("form_data_path") or []))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        schema_key = str(self.config.get("form_schema_key") or "")
        schema = raw.get(schema_key) if schema_key and isinstance(raw, dict) and schema_key in raw else raw
        fields: list[dict] = []
        _walk_form_fields(schema, fields)
        seen: set[str] = set()
        return [field for field in fields if not (field["key"] in seen or seen.add(field["key"]))]

    def template_ids(self, spec: dict[str, Any]) -> list[str]:
        values = _dig(spec, list(self.config.get("template_ids_path") or []))
        return [str(value) for value in values or [] if isinstance(value, str)]

    def template_id_in(self, spec: dict[str, Any], body_json: str) -> str:
        found = super().template_id_in(spec, body_json)
        if found:
            return found
        pattern = str(self.config.get("template_id_pattern") or "")
        match = re.search(pattern, body_json) if pattern else None
        return match.group(1) if match else ""

    def template_list_paths(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.config.get("template_list_paths") or [])

    def parse_template_list(self, payload: object) -> list[dict]:
        if not isinstance(payload, dict) or payload.get("code") not in (None, 200, 0):
            return []
        rows = payload.get("rows") or payload.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("records") or rows.get("list") or []
        out: list[dict] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            identity = item.get("id") or item.get("templateId") or item.get("defKey")
            if identity is None:
                continue
            out.append({
                "templateId": str(identity),
                "name": item.get("name") or item.get("templateName") or str(identity),
                "type": item.get("typeName") or item.get("type") or "",
                "defKey": item.get("defKey") or "",
                "enableFlag": str(item.get("enableFlag", "")),
            })
        seen: set[str] = set()
        return [item for item in out if not (item["templateId"] in seen or seen.add(item["templateId"]))]

    def parse_approval_chain(self, spec: dict[str, Any], template_id: str) -> dict:
        path = str(self.config.get("approval_description_path") or "")
        description = (((spec.get("paths") or {}).get(path) or {}).get("post") or {}).get("description") or ""
        identity = str(template_id or "").strip().strip("`")
        flow_name = ""
        chain_text = ""
        for line in description.splitlines():
            if "|" not in line:
                continue
            cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 3 and cells[1] == identity:
                flow_name, chain_text = cells[0], cells[2]
                break
        if not chain_text:
            return {}
        approval: list[dict] = []
        thresholds: list[dict] = []
        for segment in (value.strip() for value in re.split(r"[→➔➜]", chain_text) if value.strip()):
            condition = None
            condition_match = re.search(r"〔(.+?)〕", segment)
            step = re.sub(r"[(（].*?[)）]", "", re.sub(r"〔.+?〕", "", segment)).strip()
            if condition_match:
                raw = condition_match.group(1).replace("大于等于", "≥").replace("不小于", "≥").replace("大于", ">")
                threshold = re.search(r"([><≥≤]=?)\s*(\d+)", raw)
                if threshold:
                    number = int(threshold.group(2))
                    key = "gte" if "≥" in threshold.group(1) or ">=" in threshold.group(1) else "gt"
                    condition = f"amount{'≥' if key == 'gte' else '>'}{number}"
                    thresholds.append({"field": "amount", key: number, "adds": step})
            if step and step not in {"发起人填表", "发起人", "结束", "系统结束", "填表"}:
                approval.append({"step": step, **({"condition": condition} if condition else {})})
        return {
            "flow": flow_name,
            "templateId": identity,
            "approvalChain": approval,
            "thresholds": thresholds,
        } if approval else {}


_REGISTERED: list[OATemplate] = []


def register_oa_template(template: OATemplate) -> None:
    _REGISTERED.insert(0, template)


def all_templates(tenant: str = "") -> list[OATemplate]:
    return [*_REGISTERED, *(ConfiguredOATemplate(item) for item in dialects_for(tenant))]


def match_template(spec: dict[str, Any], *, tenant: str = "") -> OATemplate | None:
    if not isinstance(spec, dict):
        return None
    for template in all_templates(tenant):
        try:
            if template.matches(spec):
                log.info("oa_template.matched", template=template.name)
                return template
        except Exception:  # noqa: BLE001 - one malformed optional pack must not break onboarding
            continue
    return None
