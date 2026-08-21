"""Machine validator for self-contained skill packages."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

from dano.onboarding.skill_generation.validate import HANDBOOK_BAN_MARKERS


_REQUIRED_SKILL_SECTIONS = (
    "适用场景", "不适用场景", "选择工作流", "组合与交接规则",
    "执行协议", "成功、失败与停止", "按需读取资源",
)
_LEGACY_SKILL_SECTIONS = (
    "适用场景", "不适用场景", "能力关系", "操作路由", "输入",
    "操作步骤", "工具", "输出", "完成标准", "失败处理", "安全边界",
)
_WORKFLOW_SECTION = "执行协议"
_PROCESS_LEAK_MARKERS = (
    "generator-guides",
    "阶段1", "阶段 1", "阶段6", "阶段 6", "阶段7", "阶段 7", "阶段8", "阶段 8",
    "FlowSpec", "fingerprint", "unverified",
    "verification_id", "录制识别顺序",
)
_VERIFICATION_ID_RE = re.compile(
    r"\bverification_id\s*[:=]?\s*[`\[]?(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})",
    re.I,
)
_API_LINE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD)\b|(?:->|→)", re.I)
_PLAINTEXT_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:Bearer|Basic|Token)\s+(?![<{($])[A-Za-z0-9._~+/-]{12,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)[\"'](?:password|secret|access[_-]?token|refresh[_-]?token|session|cookie)[\"']\s*:\s*[\"'](?![<{($*])[^\"']{8,}[\"']"),
)


def _issue(code: str, message: str, path: Path, *, warning: bool = False) -> dict:
    return {
        "severity": "warning" if warning else "error",
        "code": code,
        "path": str(path),
        "message": message,
    }


def _read(path: Path, issues: list[dict], *, missing_as_warnings: bool) -> str:
    if not path.is_file():
        issues.append(_issue("missing_file", f"missing required file: {path.name}", path, warning=missing_as_warnings))
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(_issue("encoding", "file must be UTF-8 text", path))
        return ""


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    match = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, re.S)
    if not match:
        return {}
    value = yaml.safe_load(match.group(1))
    return value if isinstance(value, dict) else {}


def _section(text: str, title: str) -> str:
    match = re.search(
        rf"(?ims)^##+\s+{re.escape(title)}\s*$\n(.*?)(?=^##+\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def _check_skill(path: Path, text: str, issues: list[dict]) -> None:
    metadata = _frontmatter(text)
    for key in ("name", "description"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            issues.append(_issue("frontmatter", f"SKILL.md frontmatter requires non-empty {key}", path))
    if "version" in metadata or "compatibility" in metadata:
        issues.append(_issue("frontmatter", "SKILL.md frontmatter must omit version and compatibility", path))
    if metadata.get("disable-model-invocation") is False:
        issues.append(_issue("frontmatter", "do not emit disable-model-invocation: false", path))
    required = _REQUIRED_SKILL_SECTIONS
    if _section(text, "操作路由") and not _section(text, "选择工作流"):
        required = _LEGACY_SKILL_SECTIONS
    for title in required:
        if not _section(text, title):
            issues.append(_issue("skill_section", f"SKILL.md requires section: {title}", path))
    workflow = _WORKFLOW_SECTION if _section(text, _WORKFLOW_SECTION) else "操作步骤"
    steps = _section(text, workflow)
    step_markers = re.findall(r"(?m)^(?:###\s+|\s*\d+[.)]\s+)", steps)
    done_when = re.findall(r"(?im)\bDone\s+when\s*:|完成后检查\s*[：:]", steps)
    if steps and (not done_when or len(done_when) < max(1, len(step_markers))):
        issues.append(_issue("done_when", "every documented step must include `Done when:`", path))
    if re.search(r"(必须|请)?先?(阅读|读取).{0,16}全部.{0,12}(generator-guides|references\s*下所有)", text, re.I):
        issues.append(_issue("progressive_disclosure", "SKILL.md must not unconditionally load all references", path))
    _check_handbook_bans(path, text, issues)
    for marker in _PROCESS_LEAK_MARKERS:
        if marker and marker in text:
            issues.append(_issue("process_leak", f"consumer handbook must not contain: {marker}", path))
            break


def _doc_intro(text: str) -> str:
    body = re.sub(r"\A---\s*\n.*?\n---(?:\s*\n|\Z)", "", text, count=1, flags=re.S)
    match = re.search(r"(?ms)^#\s+[^\n]+\n(.*?)(?=^##\s+|\Z)", body)
    return match.group(1) if match else body[:800]


def _check_handbook_bans(path: Path, text: str, issues: list[dict]) -> None:
    for marker in HANDBOOK_BAN_MARKERS:
        if marker and marker in text:
            issues.append(_issue("handbook_language", f"handbook must not contain: {marker}", path))
            return


def _check_reference(
    path: Path,
    text: str,
    issues: list[dict],
    *,
    missing_as_warnings: bool = False,
) -> None:
    if not _section(text, "Business hard rules"):
        issues.append(_issue(
            "reference_section",
            "reference.md requires section: Business hard rules",
            path,
            warning=missing_as_warnings,
        ))
    chain = _section(text, "API chain")
    if not chain:
        issues.append(_issue(
            "api_chain",
            "reference.md requires a non-empty API chain section",
            path,
            warning=missing_as_warnings,
        ))
        return
    lines = [line.strip() for line in chain.splitlines() if _API_LINE_RE.search(line)]
    if not lines:
        issues.append(_issue(
            "api_chain",
            "API chain must describe at least one request chain",
            path,
            warning=missing_as_warnings,
        ))
    for line in lines:
        if not _VERIFICATION_ID_RE.search(line) and "unverified" not in line.casefold():
            issues.append(_issue(
                "chain_evidence",
                f"API chain lacks verification_id or unverified marker: {line}",
                path,
                warning=missing_as_warnings,
            ))


def _api_chain_lines(text: str) -> list[str]:
    chain = _section(text, "API chain")
    return [line.strip() for line in chain.splitlines() if _API_LINE_RE.search(line)]


def _check_scripts(scripts: Path, issues: list[dict], *, missing_as_warnings: bool) -> None:
    if not scripts.is_dir():
        issues.append(_issue("missing_scripts", "missing required scripts directory", scripts, warning=missing_as_warnings))
        return
    client = scripts / "client.py"
    if not client.is_file():
        issues.append(_issue("missing_client", "scripts/client.py is required", client, warning=missing_as_warnings))
    python_scripts = sorted(scripts.glob("*.py"))
    support_scripts = {"client.py", "wire_format.py", "format_list.py"}
    capabilities = [
        path for path in python_scripts
        if path.name not in support_scripts and not path.name.startswith("verify_")
    ]
    if not capabilities:
        issues.append(_issue("missing_capability", "at least one capability script is required", scripts, warning=missing_as_warnings))
    required_verify = _required_verify_scripts(scripts.parent)
    for capability in capabilities:
        verify = scripts / f"verify_{capability.name}"
        if required_verify is None:
            if not verify.is_file():
                issues.append(_issue("missing_verify", f"missing verifier for {capability.name}", verify))
        elif verify.name in required_verify and not verify.is_file():
            issues.append(_issue("missing_verify", f"missing verifier for {capability.name}", verify))
    for script in python_scripts:
        source = _read(script, issues, missing_as_warnings=False)
        emits_json = bool(re.search(r"\bjson\.(?:dump|dumps)\s*\(", source))
        # Delegated emission: client.py owns json.dumps and exposes emit();
        # capability/verify scripts satisfy the contract by calling it.
        delegates_emit = bool(
            re.search(r"(?m)^from\s+client\s+import\s+.*\bemit\b", source)
            and re.search(r"(?<![\w.])emit\s*\(", source)
        )
        if script.name not in support_scripts and source and not (emits_json or delegates_emit):
            issues.append(_issue("json_stdout", "script must emit operational JSON", script))
        try:
            completed = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=str(scripts),
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(_issue("script_help", f"--help failed: {exc}", script))
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-500:]
            issues.append(_issue("script_help", f"--help exited {completed.returncode}: {detail}", script))


def _required_verify_scripts(root: Path) -> set[str] | None:
    """Return verify script names that CONTRACT marks as required, or None for legacy packages."""
    contract_path = root / "references" / "CONTRACT.json"
    if not contract_path.is_file():
        return None
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(contract, dict) or not isinstance(contract.get("capabilities"), list):
        return None
    required: set[str] = set()
    saw_flag = False
    for item in contract["capabilities"]:
        if not isinstance(item, dict):
            continue
        if "requires_verify" in item:
            saw_flag = True
        if item.get("requires_verify"):
            verify = Path(str(item.get("verify_script") or "")).name
            if verify:
                required.add(verify)
    return required if saw_flag else None


def _script_slug(value: str) -> str:
    slug = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", str(value or "").casefold().replace("-", "_"))).strip("_")
    return slug


def _check_route_files(root: Path, skill_text: str, issues: list[dict]) -> None:
    contract_path = root / "references" / "CONTRACT.json"
    routes_dir = root / "references" / "routes"
    combo_ids: list[str] = []
    all_ids: list[str] = []
    if contract_path.is_file():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            contract = {}
        for route in contract.get("routes") or []:
            if not isinstance(route, dict):
                continue
            sequence = [str(item) for item in (route.get("capability_sequence") or []) if str(item)]
            route_id = str(route.get("route_id") or "").strip()
            if route_id:
                all_ids.append(route_id)
            if len(sequence) > 1 and route_id:
                combo_ids.append(route_id)
    seen: set[str] = set()
    for route_id in all_ids:
        if route_id in seen:
            issues.append(_issue(
                "duplicate_route_id",
                f"duplicate route_id would overwrite route file: {route_id}",
                contract_path,
            ))
            continue
        seen.add(route_id)
    expected = [f"{route_id}.md" for route_id in combo_ids]
    expected_set = set(expected)
    if len(expected) != len(expected_set):
        issues.append(_issue(
            "duplicate_combo_route",
            "combination contracts must map one-to-one to route files",
            contract_path,
        ))
    existing = {path.name for path in routes_dir.glob("*.md")} if routes_dir.is_dir() else set()
    for name in sorted(expected_set - existing):
        issues.append(_issue("missing_route_file", f"missing route file for combination: {name}", routes_dir / name))
    for name in sorted(existing - expected_set):
        issues.append(_issue("extra_route_file", f"route file has no matching combination contract: {name}", routes_dir / name))
    if skill_text:
        for name in expected_set:
            pointer = f"references/routes/{name}"
            if pointer not in skill_text and f"routes/{name}" not in skill_text:
                issues.append(_issue(
                    "route_pointer",
                    f"SKILL.md must point directly at {pointer}",
                    Path("SKILL.md"),
                ))


def _check_planning(root: Path, skill_text: str, issues: list[dict]) -> None:
    """When CONTRACT has a stage-8 plan, SKILL.md and packed scripts must match it.

    Packages without planning fields keep the original single-capability contract.
    """
    contract_path = root / "references" / "CONTRACT.json"
    if not contract_path.is_file():
        return
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        issues.append(_issue("contract_json", "references/CONTRACT.json is not valid JSON", contract_path))
        return
    if not isinstance(contract, dict):
        return
    routes = contract.get("routes") if isinstance(contract.get("routes"), list) else []
    selected = [str(item) for item in (contract.get("selected_capability_ids") or []) if str(item)]
    if not contract.get("planning_mode") and not routes and not selected:
        return
    packed = {
        str(item.get("name") or "")
        for item in (contract.get("capabilities") or [])
        if isinstance(item, dict)
    } | {
        str(item.get("capability_id") or "")
        for item in (contract.get("capabilities") or [])
        if isinstance(item, dict)
    }
    packed.discard("")
    for cap_id in selected:
        if cap_id not in packed:
            issues.append(_issue(
                "planning_selected_missing",
                f"CONTRACT selected capability is not packed: {cap_id}",
                contract_path,
            ))
    route_ids: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_id = str(route.get("route_id") or "").strip()
        if route_id:
            route_ids.append(route_id)
        for cap_id in route.get("capability_sequence") or []:
            if str(cap_id) not in selected and selected:
                issues.append(_issue(
                    "planning_route_capability",
                    f"route {route_id or '?'} references capability not selected: {cap_id}",
                    contract_path,
                ))
    for item in contract.get("unused_capabilities") or []:
        if not isinstance(item, dict):
            continue
        unused_id = str(item.get("capability_id") or "")
        unused_name = str(item.get("name") or "")
        if unused_id and unused_id in selected:
            issues.append(_issue(
                "planning_unused_selected",
                f"unused capability is also selected: {unused_id}",
                contract_path,
            ))
        script_hints = [unused_name, unused_id, _script_slug(unused_name), _script_slug(unused_id)]
        for hint in script_hints:
            if not hint:
                continue
            packed_script = root / "scripts" / f"{hint}.py"
            if packed_script.is_file():
                issues.append(_issue(
                    "planning_unused_script",
                    f"unused capability script must not be packed: {packed_script.name}",
                    packed_script,
                ))
            if skill_text and f"scripts/{hint}.py" in skill_text:
                issues.append(_issue(
                    "planning_unused_script_ref",
                    f"SKILL.md must not reference unused script scripts/{hint}.py",
                    Path("SKILL.md"),
                ))
    capabilities_text = ""
    capabilities_path = root / "references" / "CAPABILITIES.md"
    if capabilities_path.is_file():
        capabilities_text = capabilities_path.read_text(encoding="utf-8")
    handbook = f"{skill_text}\n{capabilities_text}"
    for item in contract.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        name = str(item.get("name") or "").strip()
        business = title or name
        if business and handbook and business not in handbook:
            issues.append(_issue(
                "planning_operation_mismatch",
                f"handbook is missing packed operation {business}",
                Path("SKILL.md"),
            ))
    if selected:
        support = {"client.py", "wire_format.py", "format_list.py"}
        scripts_dir = root / "scripts"
        if scripts_dir.is_dir():
            allowed_scripts = {
                Path(str(item.get("script") or "")).name
                for item in (contract.get("capabilities") or [])
                if isinstance(item, dict)
            } | {
                Path(str(item.get("verify_script") or "")).name
                for item in (contract.get("capabilities") or [])
                if isinstance(item, dict)
            }
            for script in scripts_dir.glob("*.py"):
                if script.name in support or script.name in allowed_scripts:
                    continue
                if script.name.startswith("verify_"):
                    continue
                issues.append(_issue(
                    "planning_extra_script",
                    f"packed script is not in selected capabilities: {script.name}",
                    script,
                ))


def _check_input_truth(root: Path, issues: list[dict]) -> None:
    """CONTRACT, route steps, and INPUT_FORMS must share one caller-field fact."""
    contract_path = root / "references" / "CONTRACT.json"
    forms_path = root / "references" / "INPUT_FORMS.md"
    if not contract_path.is_file():
        return
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    forms = forms_path.read_text(encoding="utf-8") if forms_path.is_file() else ""
    forms_dir = root / "references" / "forms"
    if forms_dir.is_dir():
        for path in forms_dir.glob("*.md"):
            forms += "\n" + path.read_text(encoding="utf-8")
    caps: dict[str, dict] = {}
    for item in contract.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        for key in (item.get("capability_id"), item.get("name")):
            if key:
                caps[str(key)] = item
    for route in contract.get("routes") or []:
        if not isinstance(route, dict):
            continue
        route_id = str(route.get("route_id") or "")
        for step in route.get("steps") or []:
            if not isinstance(step, dict):
                continue
            cap = caps.get(str(step.get("capability_id") or ""))
            if cap is None:
                continue
            schema = cap.get("input_schema") if isinstance(cap.get("input_schema"), dict) else {}
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            required = {str(item) for item in (schema.get("required") or [])}
            title = str(cap.get("title") or cap.get("name") or "")
            for source in step.get("input_sources") or []:
                if not isinstance(source, dict) or source.get("source") != "user":
                    continue
                field = str(source.get("field") or "").strip()
                if not field:
                    continue
                if field not in properties:
                    issues.append(_issue(
                        "input_truth_contract",
                        f"route {route_id} asks for {field} but CONTRACT capability {title or step.get('capability_id')} has no such field",
                        contract_path,
                    ))
                elif field not in required:
                    issues.append(_issue(
                        "input_truth_required",
                        f"route {route_id} user field {field} must be required in CONTRACT",
                        contract_path,
                    ))
                if forms and field not in forms:
                    issues.append(_issue(
                        "input_truth_forms",
                        f"INPUT_FORMS.md is missing caller field {field} required by route {route_id}",
                        forms_path,
                    ))


def _check_credentials(pkg_dir: Path, issues: list[dict]) -> None:
    for path in pkg_dir.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in _PLAINTEXT_CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append(_issue("credential_leak", f"possible plaintext credential: {match.group(0)[:24]}", path))
                break


def flow_spec_verification_ids(spec) -> set[str]:  # noqa: ANN001
    """Return only execution evidence identifiers attached to a FlowSpec."""
    ids = {
        str(item.get("verification_id"))
        for item in (spec.meta or {}).get("verification_log") or []
        if isinstance(item, dict)
        and item.get("status") == "passed"
        and item.get("verification_id")
    }
    return ids


def flow_spec_unverified_capability_names(spec) -> set[str]:  # noqa: ANN001
    """Identify public write capabilities without trusted read-back evidence."""
    trusted = flow_spec_verification_ids(spec)
    steps = {step.step_id: step for step in spec.steps}
    unverified: set[str] = set()
    for capability in spec.capabilities:
        step_ids = [str(value) for value in capability.step_ids]
        step_ids.extend(str(ref.step_id) for ref in capability.request_refs if ref.step_id)
        step_ids.extend(
            str(node.get("step_id"))
            for node in capability.nodes
            if isinstance(node, dict) and node.get("type") == "call" and node.get("step_id")
        )
        selected = [steps[step_id] for step_id in dict.fromkeys(step_ids) if step_id in steps]
        if not selected:
            selected = list(steps.values())
        writes = [step for step in selected if (step.method or "GET").upper() not in {"GET", "HEAD"}]
        if writes and any(
            (step.fact_check or {}).get("verified") is not True
            or str((step.fact_check or {}).get("verification_id") or "") not in trusted
            for step in writes
        ):
            unverified.add(str(capability.name or capability.capability_id))
    return unverified


def validate_skill_documents(
    skill_md: str,
    reference_md: str,
    *,
    allowed_verification_ids: set[str] | None = None,
    required_chain_names: set[str] | None = None,
    required_unverified_chains: set[str] | None = None,
) -> dict:
    """Validate model-authored package documents before filesystem rendering."""
    issues: list[dict] = []
    skill_path = Path("SKILL.md")
    reference_path = Path("reference.md")
    if not isinstance(skill_md, str) or not skill_md.strip():
        issues.append(_issue("missing_file", "missing required file: SKILL.md", skill_path))
    else:
        _check_skill(skill_path, skill_md, issues)
    if not isinstance(reference_md, str) or not reference_md.strip():
        issues.append(_issue("missing_file", "missing required file: reference.md", reference_path, warning=True))
    else:
        _check_reference(reference_path, reference_md, issues)
        chain_lines = _api_chain_lines(reference_md)
        for name in sorted(required_chain_names or set()):
            if not any(name.casefold() in line.casefold() for line in chain_lines):
                issues.append(_issue(
                    "missing_api_chain",
                    f"API chain is missing capability: {name}",
                    reference_path,
                ))
        for name in sorted(required_unverified_chains or set()):
            matching = [line for line in chain_lines if name.casefold() in line.casefold()]
            if matching and not any("unverified" in line.casefold() for line in matching):
                issues.append(_issue(
                    "missing_unverified_marker",
                    f"unverified write capability must be marked unverified: {name}",
                    reference_path,
                ))
        if allowed_verification_ids is not None:
            allowed = {str(value) for value in allowed_verification_ids}
            for match in _VERIFICATION_ID_RE.finditer(reference_md):
                verification_id = match.group("id")
                if verification_id not in allowed:
                    issues.append(_issue(
                        "ungrounded_verification",
                        f"verification_id is not present in executor-owned FlowSpec evidence: {verification_id}",
                        reference_path,
                    ))
    for path, text in ((skill_path, skill_md), (reference_path, reference_md)):
        if not isinstance(text, str):
            continue
        for pattern in _PLAINTEXT_CREDENTIAL_PATTERNS:
            match = pattern.search(text)
            if match:
                issues.append(_issue("credential_leak", f"possible plaintext credential: {match.group(0)[:24]}", path))
                break
    return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}


def validate_skill_package(pkg_dir: Path, *, missing_as_warnings: bool = False) -> dict:
    """Validate one package and return ``{ok, issues}`` with stable issue codes."""
    root = Path(pkg_dir)
    issues: list[dict] = []
    if not root.is_dir():
        return {"ok": False, "issues": [_issue("missing_package", "package directory does not exist", root)]}
    skill_path = root / "SKILL.md"
    operations_path = root / "references" / "OPERATIONS.md"
    capabilities_path = root / "references" / "CAPABILITIES.md"
    options_path = root / "references" / "OPTIONS.md"
    reference_path = root / "reference.md"
    skill = _read(skill_path, issues, missing_as_warnings=missing_as_warnings)
    new_layout = capabilities_path.is_file()
    operations = _read(operations_path, issues, missing_as_warnings=True) if operations_path.is_file() else ""
    reference = ""
    if not operations and not new_layout and reference_path.is_file():
        reference = _read(reference_path, issues, missing_as_warnings=missing_as_warnings)
    if skill:
        _check_skill(skill_path, skill, issues)
    if operations:
        _check_handbook_bans(operations_path, _doc_intro(operations), issues)
    forms_path = root / "references" / "INPUT_FORMS.md"
    if forms_path.is_file():
        forms = _read(forms_path, issues, missing_as_warnings=True)
        if forms:
            _check_handbook_bans(forms_path, _doc_intro(forms), issues)
            if forms.count("\n") >= 100 and "## 目录" not in forms and "/forms/" not in forms:
                issues.append(_issue("long_reference", "INPUT_FORMS.md over 100 lines needs a TOC or split", forms_path))
    if new_layout:
        for required in (capabilities_path, options_path, forms_path):
            if not required.is_file():
                issues.append(_issue("missing_file", f"missing required file: {required.name}", required))
            else:
                leaked = _read(required, issues, missing_as_warnings=True)
                for marker in _PROCESS_LEAK_MARKERS:
                    if marker and marker in leaked:
                        issues.append(_issue("process_leak", f"{required.name} must not contain: {marker}", required))
                        break
        if operations_path.is_file():
            issues.append(_issue(
                "duplicate_layout",
                "new packages must not generate OPERATIONS.md alongside CAPABILITIES.md",
                operations_path,
            ))
        _check_route_files(root, skill, issues)
        _check_input_truth(root, issues)
    else:
        chain_source = operations_path if operations else reference_path
        chain_text = operations or reference
        if chain_text:
            _check_reference(
                chain_source,
                chain_text,
                issues,
                missing_as_warnings=missing_as_warnings,
            )
        elif not missing_as_warnings:
            issues.append(_issue(
                "missing_file",
                "missing references/OPERATIONS.md",
                operations_path,
            ))
    if (root / "references" / "generator-guides").exists():
        issues.append(_issue(
            "generator_guides_leaked",
            "references/generator-guides must not appear in a consumer Skill package",
            root / "references" / "generator-guides",
        ))
    _check_scripts(root / "scripts", issues, missing_as_warnings=missing_as_warnings)
    _check_credentials(root, issues)
    if skill:
        _check_planning(root, skill, issues)
    return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}
