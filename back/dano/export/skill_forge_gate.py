"""Adapter for the host-owned standalone Skill writing and smoke gate."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys


SKILL_GATE_PROTOCOL = "agent_browser.skill_gate.v1"


class SkillForgeGateError(RuntimeError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


async def run_skill_quality_gate(
    skill_dir: Path, *, forge_root: str,
    verification_evidence: Path | None = None,
    settings=None,
) -> dict:
    if not forge_root:
        raise SkillForgeGateError(
            "quality_gate",
            "DANO_SKILL_FORGE_ROOT 未配置，禁止跳过 writing-great-skills 最终门",
        )
    root = Path(forge_root).expanduser().resolve()
    gate = root / "scripts" / "finalize_run.py"
    if not gate.is_file():
        raise SkillForgeGateError("quality_gate", f"Skill Forge host gate 不存在: {gate}")
    if settings is None:
        from dano.config import get_settings

        settings = get_settings()
    env = os.environ.copy()
    for name, value in (
        ("DANO_PI_API_KEY", settings.pi_api_key),
        ("DANO_PI_BASE_URL", settings.pi_base_url),
        ("DANO_PI_MODEL", settings.pi_model),
        ("DANO_PI_PROVIDER", settings.pi_provider),
    ):
        if value:
            env[name] = str(value)
    command = [
        sys.executable,
        str(gate),
        "--gate-existing-skill",
        str(skill_dir.resolve()),
    ]
    if verification_evidence is not None:
        command.extend(("--verification-evidence", str(verification_evidence.resolve())))
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=650)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise SkillForgeGateError("quality_gate", "Skill Forge writing review 超时") from exc
    try:
        result = json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SkillForgeGateError(
            "quality_gate", "Skill Forge host gate 未返回有效 JSON"
        ) from exc
    if process.returncode or result.get("ok") is not True:
        raise SkillForgeGateError(
            str(result.get("stage") or "quality_gate"),
            str(result.get("error") or stderr.decode("utf-8", errors="replace") or "Skill Forge host gate failed"),
        )
    if result.get("protocol") != SKILL_GATE_PROTOCOL:
        raise SkillForgeGateError("quality_gate", "Skill Forge host gate 协议版本不兼容")
    if result.get("writing_review", {}).get("performed") is not True:
        raise SkillForgeGateError("quality_gate", "writing-great-skills review 未实际执行")
    policy = result.get("policy_gate") or {}
    if (
        policy.get("id") != "writing-great-skills"
        or not policy.get("revision")
        or not str(policy.get("sha256") or "").startswith("sha256:")
    ):
        raise SkillForgeGateError("quality_gate", "writing-great-skills 固定 policy 证据无效")
    if result.get("quality", {}).get("passed") is not True:
        raise SkillForgeGateError("quality_gate", "writing-great-skills 质量检查未通过")
    if result.get("smoke_test", {}).get("passed") is not True:
        raise SkillForgeGateError("smoke_test", "确定性 smoke test 未通过")
    evidence = result.get("evidence_verification") or {}
    if evidence.get("protocol") != "dano.skill_delivery_evidence.v1" or evidence.get("passed") is not True:
        raise SkillForgeGateError("verify_result", "页面/HAR/业务结果/客户端一致性证明未通过")
    return result
