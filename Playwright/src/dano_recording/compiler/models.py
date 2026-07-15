"""Compiler result and validation report models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from dano_recording.domain._base import FrozenModel, utc_now
from dano_recording.domain.capabilities import Capability
from dano_recording.domain.facts import ActionTransaction
from dano_recording.domain.fields import EffectiveFieldContract, FieldFact
from dano_recording.domain.operations import CompiledRequest, RequestAnalysis
from dano_recording.domain.relations import CapabilityRelation


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class CompilationIssue(FrozenModel):
    code: str
    message: str
    severity: IssueSeverity
    request_id: str | None = None
    field_contract_id: str | None = None
    capability_id: str | None = None


class ValidationReport(FrozenModel):
    passed: bool
    issues: tuple[CompilationIssue, ...] = ()

    @property
    def errors(self) -> tuple[CompilationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.ERROR)

    @property
    def warnings(self) -> tuple[CompilationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.WARNING)


class RecordingCompilation(FrozenModel):
    protocol: str = "dano.recording-v3.compilation.v1"
    tenant: str
    recording_id: str
    source_revision: int = Field(default=0, ge=0)
    transactions: tuple[ActionTransaction, ...]
    request_analyses: tuple[RequestAnalysis, ...]
    requests: tuple[CompiledRequest, ...]
    field_facts: tuple[FieldFact, ...]
    fields: tuple[EffectiveFieldContract, ...]
    capabilities: tuple[Capability, ...]
    relations: tuple[CapabilityRelation, ...]
    validation: ValidationReport
    content_hash: str
    compiled_at: datetime = Field(default_factory=utc_now)
