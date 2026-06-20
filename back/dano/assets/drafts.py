"""草案 + 验证证据存储(REWRITE_PLAN §4 发布硬关卡的底座)。

纪律:pi 起草资产 → 后端真验证生成 validation_run(带 content_hash 绑定)→ 发布时只认
后端生成的 validation_run_id,重读校验。**绝不接受 agent 自报的 sandbox_passed 布尔值。**
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field

from dano.infra.db import get_pool
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope

if TYPE_CHECKING:
    import asyncpg

log = structlog.get_logger(__name__)

ValidationKind = Literal["connect", "sandbox", "readback", "health", "replay", "cases", "vuln"]
ReviewRole = Literal["acceptance", "security", "compliance"]

# 各资产类型发布所需的验证种类(硬关卡:全覆盖才可发布)
REQUIRED_KINDS: dict[AssetType, set[ValidationKind]] = {
    AssetType.CONNECTOR: {"connect", "sandbox"},
    AssetType.FIELD_MAPPING: {"readback"},
    AssetType.ENV_PROFILE: {"health"},
    AssetType.PAGE_SCRIPT: {"replay"},
    AssetType.POLICY_RULE: {"cases"},   # 制度规则须用例全通过(放行/拦截/转审批,生成期离线跑)
    AssetType.WORKFLOW: {"sandbox"},    # 复合流程须整条按序 dry-run 通过(sandbox_test_workflow)
    AssetType.ADAPTER: {"sandbox", "vuln"},  # 代码适配器:隔离 runner 跑通 + 漏洞校验静态扫描
}

# 须过三模型评审委员会才可发布的资产类型(业务 Skill);其余(如确定性发布的 env_profile)免评审
REVIEW_REQUIRED_TYPES: set[AssetType] = {AssetType.CONNECTOR, AssetType.WORKFLOW, AssetType.ADAPTER}
REQUIRED_ROLES: set[str] = {"acceptance", "security", "compliance"}


class AssetDraft(BaseModel):
    asset_draft_id: UUID
    run_id: str
    tenant: str
    subsystem: Subsystem
    asset_type: AssetType
    asset_key: str
    body: dict[str, Any]
    content_hash: str
    created_at: datetime | None = None


class ValidationRun(BaseModel):
    validation_run_id: UUID
    asset_draft_id: UUID
    content_hash: str
    kind: ValidationKind
    environment: str = "sandbox"
    credential_type: str = "test"
    passed: bool
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None


class ReviewRun(BaseModel):
    model_config = ConfigDict(protected_namespaces=())   # model_id 非 pydantic 保护字段

    review_run_id: UUID
    asset_draft_id: UUID
    content_hash: str
    role: ReviewRole
    model_id: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    expires_at: datetime | None = None


def content_hash(*, asset_type: AssetType, scope: Scope, asset_key: str, body: dict) -> str:
    """资产内容指纹:绑定 类型+作用域+key+body。验证证据须对应同一 hash。"""
    canonical = json.dumps(
        {"asset_type": asset_type.value, "tenant": scope.tenant,
         "subsystem": scope.subsystem.value, "asset_key": asset_key, "body": body},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DraftStore:
    """草案 + 验证证据访问(无状态,依赖全局连接池)。"""

    async def save_draft(self, *, run_id: str, scope: Scope, asset_type: AssetType,
                         asset_key: str, body: dict) -> AssetDraft:
        h = content_hash(asset_type=asset_type, scope=scope, asset_key=asset_key, body=body)
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO asset_drafts (run_id, tenant, subsystem, asset_type, asset_key, body, content_hash)
                   VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *""",
                run_id, scope.tenant, scope.subsystem.value, asset_type.value,
                asset_key, json.dumps(body), h,
            )
        return self._draft(row)

    async def get_draft(self, asset_draft_id: UUID) -> AssetDraft | None:
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM asset_drafts WHERE asset_draft_id=$1", asset_draft_id)
        return self._draft(row) if row else None

    async def record_validation(self, *, asset_draft_id: UUID, kind: ValidationKind, passed: bool,
                                environment: str = "sandbox", credential_type: str = "test",
                                request: dict | None = None, response: dict | None = None,
                                evidence: dict | None = None) -> ValidationRun:
        """记录一次验证证据。content_hash 从草案取(绑定),非调用方传入。"""
        draft = await self.get_draft(asset_draft_id)
        if draft is None:
            raise ValueError(f"草案不存在: {asset_draft_id}")
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO validation_runs
                   (asset_draft_id, content_hash, kind, environment, credential_type, request, response, evidence, passed)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *""",
                asset_draft_id, draft.content_hash, kind, environment, credential_type,
                _j(request), _j(response), _j(evidence), passed,
            )
        return self._vrun(row)

    async def list_validations(self, asset_draft_id: UUID) -> list[ValidationRun]:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM validation_runs WHERE asset_draft_id=$1 ORDER BY created_at", asset_draft_id)
        return [self._vrun(r) for r in rows]

    async def record_review(self, *, asset_draft_id: UUID, role: ReviewRole, model_id: str,
                            passed: bool, reasons: list[str] | None = None) -> ReviewRun:
        """记录一条评审结论。content_hash 从草案取(绑定),非调用方传入。"""
        draft = await self.get_draft(asset_draft_id)
        if draft is None:
            raise ValueError(f"草案不存在: {asset_draft_id}")
        async with get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO review_runs (asset_draft_id, content_hash, role, model_id, passed, findings)
                   VALUES ($1,$2,$3,$4,$5,$6) RETURNING *""",
                asset_draft_id, draft.content_hash, role, model_id, passed,
                _j({"reasons": reasons or []}),
            )
        return self._rrun(row)

    async def list_reviews(self, asset_draft_id: UUID) -> list[ReviewRun]:
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM review_runs WHERE asset_draft_id=$1 ORDER BY created_at", asset_draft_id)
        return [self._rrun(r) for r in rows]

    async def verify_reviewed(
        self, asset_draft_id: UUID, review_run_ids: list[UUID]
    ) -> tuple[bool, str]:
        """三模型评审硬关卡:后端重读评审证据校验,不信 agent 自报。

        免评审类型直接放行;否则校验:每条属本草案、content_hash 一致、passed、未过期 →
        role 覆盖 {acceptance, security, compliance}(三个角色都过即可;**模型可相同**,不强制 distinct)。
        """
        from dano.config import get_settings
        if not get_settings().review_enabled:        # 运维急停:临时关闭评审闸门(审计留痕)
            log.warning("verify_reviewed.bypassed", draft=str(asset_draft_id),
                        note="review_enabled=false,评审闸门已临时关闭")
            return True, "ok(评审已临时关闭/降级)"
        draft = await self.get_draft(asset_draft_id)
        if draft is None:
            return False, f"草案不存在: {asset_draft_id}"
        if draft.asset_type not in REVIEW_REQUIRED_TYPES:
            return True, "ok(此类型免三模型评审)"
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM review_runs WHERE review_run_id = ANY($1::uuid[])",
                [str(v) for v in review_run_ids])
        runs = [self._rrun(r) for r in rows]
        if len(runs) != len(set(review_run_ids)):
            return False, "部分 review_run_id 不存在(只认后端生成的评审证据)"
        now = datetime.now(timezone.utc)
        roles: set[str] = set()
        models: set[str] = set()
        for r in runs:
            if r.asset_draft_id != asset_draft_id:
                return False, f"评审 {r.review_run_id} 不属于该草案"
            if r.content_hash != draft.content_hash:
                return False, f"评审 content_hash 不匹配(防换草案):{r.review_run_id}"
            if not r.passed:
                detail = "; ".join(r.reasons) or str(r.review_run_id)
                return False, f"评审未通过（{r.role}）:{detail}"
            if r.expires_at and r.expires_at < now:
                return False, f"评审已过期:{r.review_run_id}"
            roles.add(r.role)
            models.add(r.model_id)
        missing = REQUIRED_ROLES - roles
        if missing:
            return False, f"缺少评审角色:{sorted(missing)}(已有 {sorted(roles)})"
        if not models or "" in models:               # 只要三个角色都由非空模型评过即可,模型可相同
            return False, "评审模型为空(请在运行配置填评审模型)"
        return True, "ok"

    async def verify_publishable(
        self, asset_draft_id: UUID, validation_run_ids: list[UUID]
    ) -> tuple[bool, str]:
        """发布硬关卡(§4):后端重读证据校验,不信 agent 自报。

        校验:草案存在 → 每条证据属本草案、passed、未过期、env=sandbox、cred=test、
        content_hash 与草案一致 → 该资产类型要求的验证种类全覆盖。
        """
        draft = await self.get_draft(asset_draft_id)
        if draft is None:
            return False, f"草案不存在: {asset_draft_id}"
        async with get_pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM validation_runs WHERE validation_run_id = ANY($1::uuid[])",
                [str(v) for v in validation_run_ids])
        runs = [self._vrun(r) for r in rows]
        if len(runs) != len(set(validation_run_ids)):
            return False, "部分 validation_run_id 不存在(只认后端生成的证据)"
        now = datetime.now(timezone.utc)
        covered: set[str] = set()
        for r in runs:
            if r.asset_draft_id != asset_draft_id:
                return False, f"证据 {r.validation_run_id} 不属于该草案"
            if r.content_hash != draft.content_hash:
                return False, f"证据 content_hash 不匹配(防换草案):{r.validation_run_id}"
            if not r.passed:
                return False, f"证据未通过:{r.validation_run_id}"
            if r.environment != "sandbox" or r.credential_type != "test":
                return False, f"证据非沙箱/测试凭证:{r.validation_run_id}"
            if r.expires_at and r.expires_at < now:
                return False, f"证据已过期:{r.validation_run_id}"
            covered.add(r.kind)
        required = REQUIRED_KINDS.get(draft.asset_type, set())
        missing = required - covered
        if missing:
            return False, f"缺少必需验证种类:{sorted(missing)}(已有 {sorted(covered)})"
        return True, "ok"

    # ── row → model ──
    @staticmethod
    def _draft(row: "asyncpg.Record") -> AssetDraft:
        return AssetDraft(
            asset_draft_id=row["asset_draft_id"], run_id=row["run_id"], tenant=row["tenant"],
            subsystem=Subsystem(row["subsystem"]), asset_type=AssetType(row["asset_type"]),
            asset_key=row["asset_key"], body=json.loads(row["body"]),
            content_hash=row["content_hash"], created_at=row["created_at"],
        )

    @staticmethod
    def _vrun(row: "asyncpg.Record") -> ValidationRun:
        return ValidationRun(
            validation_run_id=row["validation_run_id"], asset_draft_id=row["asset_draft_id"],
            content_hash=row["content_hash"], kind=row["kind"], environment=row["environment"],
            credential_type=row["credential_type"], passed=row["passed"],
            request=_d(row["request"]), response=_d(row["response"]), evidence=_d(row["evidence"]),
            created_at=row["created_at"], expires_at=row["expires_at"],
        )

    @staticmethod
    def _rrun(row: "asyncpg.Record") -> ReviewRun:
        findings = _d(row["findings"]) or {}
        return ReviewRun(
            review_run_id=row["review_run_id"], asset_draft_id=row["asset_draft_id"],
            content_hash=row["content_hash"], role=row["role"], model_id=row["model_id"],
            passed=row["passed"], reasons=findings.get("reasons", []),
            created_at=row["created_at"], expires_at=row["expires_at"],
        )


def _j(v: dict | None) -> str | None:
    return json.dumps(v) if v is not None else None


def _d(v: Any) -> dict | None:
    return json.loads(v) if isinstance(v, str) else v
