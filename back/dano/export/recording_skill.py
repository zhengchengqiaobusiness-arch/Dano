"""Deterministic final Skill package for recorded FlowSpecs.

This module deliberately has no browser or Dano-runtime dependency.  The
recording host owns capture and review; this module only compiles the frozen
FlowSpec, renders a small standalone replay client, smokes it offline, and
hands the completed tree to the existing atomic exporter.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dano.export.live_skill import _slug as _action_slug
from dano.export.live_skill import compile_flow_spec_contract


def _stable_rows(rows: Sequence[Mapping[str, Any]] | None, key: str) -> list[dict[str, Any]]:
    """Merge reference rows by their stable id while preserving first-seen order."""
    merged: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for raw in rows or ():
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        identity = str(row.get(key) or row.get("id") or row.get("evidence_id") or "")
        if identity:
            merged[identity] = row
        elif row not in anonymous:
            anonymous.append(row)
    return [*merged.values(), *anonymous]


def compile_recording_contract(
    flow_spec: Any,
    *,
    skill_id: str,
    decisions: Sequence[Mapping[str, Any]] = (),
    limitations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compile one frozen recording into the final, fail-closed contract."""
    contract = compile_flow_spec_contract(
        flow_spec,
        skill_id=skill_id,
        decisions=_stable_rows(decisions, "decision_id"),
        limitations=_stable_rows(limitations, "id"),
        require_complete=True,
    )
    raw_spec = (
        flow_spec.model_dump(mode="json", exclude_none=True)
        if hasattr(flow_spec, "model_dump") else dict(flow_spec)
    )
    steps = {
        str(step.get("step_id") or ""): step
        for step in raw_spec.get("steps") or []
        if isinstance(step, Mapping) and str(step.get("step_id") or "")
    }
    for index, action in enumerate(contract.get("actions") or []):
        step = steps.get(str(action.get("id") or ""))
        if step is None:
            step = next(
                (
                    candidate for candidate in steps.values()
                    if _action_slug(candidate.get("step_id"), f"action-{index + 1}") == action.get("id")
                ),
                None,
            )
        if step is None:
            continue
        request = action.setdefault("request", {})
        for source_key, contract_key in (("body_template", "bodyTemplate"), ("query_template", "queryTemplate")):
            value = step.get(source_key)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = None
            if isinstance(value, (dict, list)):
                request[contract_key] = copy.deepcopy(value)
    raw = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    contract["contractDigest"] = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return contract


def quality_gate(contract: Mapping[str, Any]) -> list[str]:
    """Return deterministic publication blockers; an empty list means pass."""
    errors: list[str] = []
    if contract.get("schemaVersion") != "1.0":
        errors.append("schemaVersion 必须为 1.0")
    target = contract.get("target") if isinstance(contract.get("target"), Mapping) else {}
    if not re.fullmatch(r"https?://[^/]+", str(target.get("origin") or "")):
        errors.append("target.origin 必须是 HTTP(S) origin")
    actions = contract.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append("至少需要一个可执行 action")
        actions = []
    action_ids = [str(item.get("id") or "") for item in actions if isinstance(item, Mapping)]
    if len(action_ids) != len(set(action_ids)):
        errors.append("action id 必须唯一")
    known = set(action_ids)
    for action in actions:
        if not isinstance(action, Mapping):
            errors.append("action 必须是对象")
            continue
        action_id = str(action.get("id") or "<unknown>")
        request = action.get("request") if isinstance(action.get("request"), Mapping) else {}
        if str(request.get("method") or "").upper() not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
            errors.append(f"{action_id}: method 不受支持")
        if not str(request.get("path") or "").startswith("/"):
            errors.append(f"{action_id}: request.path 必须以 / 开始")
        if not action.get("evidenceRefs"):
            errors.append(f"{action_id}: 缺少 evidenceRefs")
        if str(request.get("method") or "GET").upper() not in {"GET", "HEAD", "OPTIONS"} and not action.get("recheck"):
            errors.append(f"{action_id}: 写动作缺少 recheck")
        for binding in request.get("bindings") or []:
            if not isinstance(binding, Mapping) or binding.get("source") not in {
                "caller_input", "prior_response", "constant", "server_auth", "computed",
            }:
                errors.append(f"{action_id}: 存在未支持的 binding source")
    capabilities = contract.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("至少需要一个 capability")
        capabilities = []
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            errors.append("capability 必须是对象")
            continue
        cap_id = str(capability.get("id") or "<unknown>")
        if capability.get("status") != "frozen":
            errors.append(f"{cap_id}: capability 尚未 frozen")
        order = capability.get("actionOrder")
        if not isinstance(order, list) or not order:
            errors.append(f"{cap_id}: actionOrder 不能为空")
        elif any(str(action_id) not in known for action_id in order):
            errors.append(f"{cap_id}: actionOrder 引用了未知 action")
    if contract.get("unsupportedCapabilities"):
        errors.append("存在 unsupportedCapabilities，禁止发布")
    return errors


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _description(contract: Mapping[str, Any]) -> str:
    return (
        f"确定性回放已录制业务能力“{contract.get('skill', {}).get('description') or contract.get('skill', {}).get('id', 'recording')}”。"
        "用户明确要求执行已发布能力时使用；仅按 contract.json 的固定请求、字段绑定和成功判据运行。"
    )


def render_skill_md(contract: Mapping[str, Any], name: str) -> str:
    capabilities = contract.get("capabilities") or []
    rows = ["| 能力 | 状态 | 动作顺序 |", "|---|---|---|"]
    for item in capabilities:
        rows.append(
            f"| {item.get('description') or item.get('id')} | `{item.get('status')}` | "
            f"`{' → '.join(item.get('actionOrder') or [])}` |"
        )
    return f'''---
name: {json.dumps(name, ensure_ascii=False)}
description: {json.dumps(_description(contract), ensure_ascii=False)}
---

# {name}

这是录制结束后生成的最终 Skill。运行时只读取本目录的 `contract.json`，使用
`scripts/invoke.py` 通过确定性的 Python HTTP 客户端回放固定请求；不使用浏览器自由点击，
也不把页面元素、CSS 选择器或模型临场决策当作执行步骤。

## 使用

1. 从 `references/capabilities.md` 选择与用户目标完全一致的 capability，并一次性收集其
   `inputSchema` 所需字段；不得猜测未列出的字段或选项。
2. 写动作执行前取得明确确认；确认后调用：
   `python scripts/invoke.py --capability <id> --input '{{...}}' --confirm`。
3. 只认末行 JSON 的 `status`。`succeeded` 才能报告成功；`failed`、`need_confirm` 或结果
   不明时停止，不自动重试写动作。

## 固定能力

{chr(10).join(rows)}

## 证据与边界

- 固定请求、字段绑定、成功判据和证据引用以 `contract.json` 为准。
- 设计决策见 `references/decisions.md`，录制证据见 `references/evidence.md`。
- 不支持项和诚实降级见 `references/limitations.md`；不要用浏览器点击绕过这些限制。
'''


def render_capabilities_md(contract: Mapping[str, Any]) -> str:
    lines = ["# Capabilities", "", "以下内容直接来自 contract.json，不补充未录制字段。", ""]
    for cap in contract.get("capabilities") or []:
        lines += [
            f"## {cap.get('description') or cap.get('id')}",
            f"- id: `{cap.get('id')}`",
            f"- status: `{cap.get('status')}`",
            f"- actionOrder: `{', '.join(cap.get('actionOrder') or [])}`",
            "- inputSchema:",
            "```json",
            _json(cap.get("inputSchema") or {}).rstrip(),
            "```",
            "- completionEvidence: " + ", ".join(f"`{ref}`" for ref in cap.get("completionEvidence") or []),
            "",
        ]
    return "\n".join(lines)


def render_reference_md(title: str, rows: Sequence[Mapping[str, Any]], empty: str) -> str:
    lines = [f"# {title}", ""]
    if not rows:
        lines.append(empty)
        return "\n".join(lines) + "\n"
    for index, row in enumerate(rows, 1):
        lines += [f"## {row.get('id') or row.get('decision_id') or row.get('evidence_id') or index}", "", "```json", _json(row).rstrip(), "```", ""]
    return "\n".join(lines)


def render_openai_yaml(name: str, description: str, slug: str) -> str:
    short = description.replace("\n", " ").strip()[:64]
    return (
        "interface:\n"
        f"  display_name: {json.dumps(name, ensure_ascii=False)}\n"
        f"  short_description: {json.dumps(short, ensure_ascii=False)}\n"
        f"  default_prompt: {json.dumps(f'使用 ${slug} 按录制契约确定性回放该业务能力。', ensure_ascii=False)}\n"
    )


_INVOKE_PY = r'''#!/usr/bin/env python3
"""Standalone deterministic replay client generated from contract.json."""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "contract.json").read_text(encoding="utf-8"))


def _emit(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _get(value, pointer):
    current = value
    for part in str(pointer or "").lstrip("/").split("/"):
        if not part:
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(pointer)
    return current


def _set(target, pointer, value):
    parts = [p.replace("~1", "/").replace("~0", "~") for p in str(pointer or "").lstrip("/").split("/") if p]
    current = target
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current.setdefault(part, {})
    if not parts:
        raise ValueError("empty target pointer")
    if isinstance(current, list):
        index = int(parts[-1])
        while len(current) <= index:
            current.append(None)
        current[index] = value
    else:
        current[parts[-1]] = value


def _input_value(inputs, pointer):
    try:
        return _get(inputs, pointer)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"缺少输入字段: {pointer}") from exc


def _validate(value, schema, path="input"):
    if not isinstance(schema, dict):
        return
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} 必须是 object")
        required = schema.get("required") or []
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{path} 缺少必填字段: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(schema.get("properties") or {})
            if extra:
                raise ValueError(f"{path} 存在未声明字段: {', '.join(sorted(extra))}")
        for key, child in (schema.get("properties") or {}).items():
            if key in value:
                _validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} 必须是 array")
        for index, item in enumerate(value):
            _validate(item, schema.get("items") or {}, f"{path}[{index}]")
    elif kind == "string" and not isinstance(value, str):
        raise ValueError(f"{path} 必须是 string")
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{path} 必须是 integer")
    elif kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError(f"{path} 必须是 number")
    elif kind == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{path} 必须是 boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在录制候选值中")


def _origin():
    origin = os.environ.get("DANO_ORIGIN") or CONTRACT.get("target", {}).get("origin")
    if not origin or urlsplit(origin).scheme not in {"http", "https"}:
        raise ValueError("未配置有效的 DANO_ORIGIN")
    return origin.rstrip("/") + "/"


def _headers():
    raw = os.environ.get("DANO_AUTH_HEADERS", "{}")
    value = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ValueError("DANO_AUTH_HEADERS 必须是 string 到 string 的 JSON object")
    return value


def _request(action, inputs, prior):
    request = action["request"]
    method = str(request["method"]).upper()
    path = str(request["path"])
    query = []
    if isinstance(request.get("queryTemplate"), dict):
        query.extend(request["queryTemplate"].items())
    headers = _headers()
    body = copy.deepcopy(request.get("bodyTemplate") or {})
    if not isinstance(body, dict):
        body = {}
    for binding in request.get("bindings") or []:
        source = binding.get("source")
        if source == "caller_input":
            value = _input_value(inputs, binding.get("from"))
        elif source == "prior_response":
            value = _get(prior[binding["fromAction"]], binding["from"])
        elif source == "constant":
            value = binding.get("value")
        elif source == "server_auth":
            continue
        else:
            raise ValueError(f"不支持的确定性 binding: {source}")
        location = binding.get("targetLocation")
        pointer = binding.get("targetPointer")
        if location == "query":
            query.append((str(pointer).lstrip("/"), value))
        elif location == "header":
            headers[str(pointer).lstrip("/")] = str(value)
        elif location == "path":
            path = path.replace("{" + str(pointer).lstrip("/") + "}", str(value))
        elif location == "body":
            _set(body, pointer, value)
        else:
            raise ValueError(f"不支持的 request target: {location}")
    if query:
        parsed = urlsplit(path)
        path = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment))
    url = urljoin(_origin(), path.lstrip("/"))
    data = None
    if request.get("bodyKind") == "json":
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif request.get("bodyKind") == "form":
        data = urlencode(body, doseq=True).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    response = urlopen(Request(url, data=data, headers=headers, method=method), timeout=30)
    raw = response.read()
    if not raw:
        result = None
    else:
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{action['id']} 返回值不是 JSON") from exc
    status = int(getattr(response, "status", 200))
    if status not in (action.get("success") or {}).get("httpStatuses", [200]):
        raise ValueError(f"{action['id']} HTTP {status} 不满足成功判据")
    predicate = (action.get("success") or {}).get("businessPredicate")
    if predicate and _get(result, predicate["pointer"]) not in predicate.get("in", []):
        raise ValueError(f"{action['id']} 未满足业务成功判据")
    return result


def invoke(capability_id, inputs, *, confirm=False, dry_run=False):
    capabilities = {str(item.get("id")): item for item in CONTRACT.get("capabilities") or []}
    capability = capabilities.get(str(capability_id))
    if capability is None:
        raise ValueError(f"未知 capability: {capability_id}")
    _validate(inputs, capability.get("inputSchema") or {"type": "object"})
    actions = {str(item.get("id")): item for item in CONTRACT.get("actions") or []}
    order = capability.get("actionOrder") or []
    if dry_run:
        return {"status": "succeeded", "capability": capability_id, "dry_run": True, "actions": order}
    if not confirm and any(actions[item].get("effect") == "mutation" for item in order):
        return {"status": "need_confirm", "capability": capability_id}
    prior = {}
    for action_id in order:
        prior[action_id] = _request(actions[action_id], inputs, prior)
    return {"status": "succeeded", "capability": capability_id, "output": prior[order[-1]], "actions": order}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--capability", required=False)
    parser.add_argument("--input", default="{}")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.input == "-" else args.input
        inputs = json.loads(raw)
        if not isinstance(inputs, dict):
            raise ValueError("input 必须是 JSON object")
        capability = args.capability or ((CONTRACT.get("capabilities") or [{}])[0].get("id"))
        result = invoke(capability, inputs, confirm=args.confirm, dry_run=args.dry_run)
        _emit(result)
        return 0 if result.get("status") in {"succeeded", "need_confirm"} else 1
    except (HTTPError, URLError, OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        _emit({"status": "failed", "reason": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


_CDP_CLIENT_PY = r'''#!/usr/bin/env python3
"""Minimal deterministic CDP discovery helper; replay does not require a browser."""
from __future__ import annotations

import argparse
import json
from urllib.request import urlopen


def discover(endpoint: str = "http://127.0.0.1:9222", timeout: float = 5.0) -> dict:
    """Read CDP's HTTP discovery metadata without navigating or clicking."""
    base = endpoint.rstrip("/")
    with urlopen(base + "/json/version", timeout=timeout) as response:
        version = json.loads(response.read().decode("utf-8"))
    with urlopen(base + "/json/list", timeout=timeout) as response:
        targets = json.loads(response.read().decode("utf-8"))
    return {"version": version, "targets": targets}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:9222")
    args = parser.parse_args(argv)
    print(json.dumps(discover(args.endpoint), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _smoke_input(schema: Mapping[str, Any]) -> Any:
    kind = schema.get("type")
    if kind == "object":
        return {key: _smoke_input(value) for key, value in (schema.get("properties") or {}).items() if key in set(schema.get("required") or [])}
    if kind == "array":
        return []
    if kind == "integer":
        return 1
    if kind == "number":
        return 1
    if kind == "boolean":
        return True
    if kind == "string":
        return (schema.get("enum") or ["smoke"])[0]
    return None


def write_recording_tree(folder: Path, contract: Mapping[str, Any], *, name: str, slug: str, decisions=(), evidence=(), limitations=()) -> None:
    """Write a complete staged final Skill tree; publication is the caller's job."""
    for child in ("agents", "scripts", "references"):
        (folder / child).mkdir(parents=True, exist_ok=True)
    description = str(contract.get("skill", {}).get("description") or name)
    (folder / "SKILL.md").write_text(render_skill_md(contract, name), encoding="utf-8")
    (folder / "contract.json").write_text(_json(contract), encoding="utf-8")
    (folder / "agents" / "openai.yaml").write_text(render_openai_yaml(name, description, slug), encoding="utf-8")
    (folder / "scripts" / "invoke.py").write_text(_INVOKE_PY, encoding="utf-8", newline="\n")
    (folder / "scripts" / "cdp_client.py").write_text(_CDP_CLIENT_PY, encoding="utf-8", newline="\n")
    (folder / "references" / "capabilities.md").write_text(render_capabilities_md(contract), encoding="utf-8")
    (folder / "references" / "decisions.md").write_text(render_reference_md("Decisions", decisions, "录制过程中没有额外决策。\n"), encoding="utf-8")
    (folder / "references" / "evidence.md").write_text(render_reference_md("Evidence", evidence, "证据引用见 contract.json 各 action 的 evidenceRefs。\n"), encoding="utf-8")
    (folder / "references" / "limitations.md").write_text(render_reference_md("Limitations", limitations, "没有额外限制；未被 canonical Contract 支持的能力不会发布。\n"), encoding="utf-8")


def smoke(folder: Path) -> None:
    """Run the generated client offline, then fail if the package is not runnable."""
    contract = json.loads((folder / "contract.json").read_text(encoding="utf-8"))
    compile((folder / "scripts" / "invoke.py").read_text(encoding="utf-8"), str(folder / "scripts" / "invoke.py"), "exec")
    compile((folder / "scripts" / "cdp_client.py").read_text(encoding="utf-8"), str(folder / "scripts" / "cdp_client.py"), "exec")
    capabilities = contract.get("capabilities") or []
    if not capabilities:
        raise ValueError("smoke: 缺少 capability")
    # The generated client deliberately treats --dry-run as offline smoke mode.
    source = (folder / "scripts" / "invoke.py").read_text(encoding="utf-8")
    namespace = {"__name__": "recording_skill_smoke", "__file__": str(folder / "scripts" / "invoke.py")}
    exec(compile(source, str(folder / "scripts" / "invoke.py"), "exec"), namespace)
    result = namespace["invoke"](
        capabilities[0]["id"],
        _smoke_input(capabilities[0].get("inputSchema") or {"type": "object"}),
        dry_run=True,
    )
    if result.get("status") != "succeeded":
        raise ValueError(f"smoke failed: {result}")


__all__ = [
    "compile_recording_contract",
    "quality_gate",
    "smoke",
    "write_recording_tree",
]
