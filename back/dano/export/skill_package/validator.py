"""Machine validator for self-contained skill packages."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import yaml


_REQUIRED_SKILL_SECTIONS = ("Transport", "Preconditions", "Steps", "Branch exit", "Pitfalls")
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
    for title in _REQUIRED_SKILL_SECTIONS:
        if not _section(text, title):
            issues.append(_issue("skill_section", f"SKILL.md requires section: {title}", path))
    steps = _section(text, "Steps")
    step_markers = re.findall(r"(?m)^(?:###\s+|\s*\d+[.)]\s+)", steps)
    done_when = re.findall(r"(?im)\bDone\s+when\s*:", steps)
    if steps and (not done_when or len(done_when) < max(1, len(step_markers))):
        issues.append(_issue("done_when", "every documented step must include `Done when:`", path))


def _check_reference(
    path: Path,
    text: str,
    issues: list[dict],
    *,
    missing_as_warnings: bool = False,
) -> None:
    for title in ("Business hard rules", "Fallback browser steps"):
        if not _section(text, title):
            issues.append(_issue(
                "reference_section",
                f"reference.md requires section: {title}",
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
    support_scripts = {"client.py", "wire_format.py"}
    capabilities = [
        path for path in python_scripts
        if path.name not in support_scripts and not path.name.startswith("verify_")
    ]
    if not capabilities:
        issues.append(_issue("missing_capability", "at least one capability script is required", scripts, warning=missing_as_warnings))
    for capability in capabilities:
        verify = scripts / f"verify_{capability.name}"
        if not verify.is_file():
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
        issues.append(_issue("missing_file", "missing required file: reference.md", reference_path))
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
    reference_path = root / "reference.md"
    skill = _read(skill_path, issues, missing_as_warnings=missing_as_warnings)
    reference = _read(reference_path, issues, missing_as_warnings=missing_as_warnings)
    if skill:
        _check_skill(skill_path, skill, issues)
    if reference:
        _check_reference(
            reference_path,
            reference,
            issues,
            missing_as_warnings=missing_as_warnings,
        )
    _check_scripts(root / "scripts", issues, missing_as_warnings=missing_as_warnings)
    _check_credentials(root, issues)
    return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}
