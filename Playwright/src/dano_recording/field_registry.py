"""Permanent field identity and per-axis ownership for recording V3."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from threading import RLock
from typing import Any, Iterable
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from dano_recording.domain._base import FrozenDict, FrozenModel, freeze_json
from dano_recording.domain.fields import AxisDecision, AxisOrigin, FieldDimension
from dano_recording.value_evidence import ValueEvidence


class FieldRegistryError(RuntimeError):
    pass


class UnknownField(FieldRegistryError):
    pass


class AliasConflict(FieldRegistryError):
    pass


class AxisDecisionConflict(FieldRegistryError):
    pass


class FieldAliasKind(StrEnum):
    LEGACY_ID = "legacy_id"
    CONTROL = "control"
    WIRE_PATH = "wire_path"
    BUSINESS_NAME = "business_name"
    EXTERNAL = "external"


class BindingDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class BindingRole(StrEnum):
    CALLER_INPUT = "caller_input"
    RUNTIME_SOURCE = "runtime_source"
    CONSTANT = "constant"
    OUTPUT = "output"


class FieldAlias(FrozenModel):
    kind: FieldAliasKind
    value: str
    # Context prevents same labels/paths on different forms or request
    # definitions from collapsing into one field.
    context: str
    introduced_at_revision: int = Field(default=0, ge=0)

    @field_validator("value", "context")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field alias value/context must not be blank")
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind.value, self.context, self.value)


class ControlEvidence(FrozenModel):
    evidence_id: str
    field_uuid: UUID
    page_id: str
    frame_id: str
    form_id: str
    control_locator: dict[str, Any]
    label: str | None = None
    role: str | None = None
    native_control_type: str | None = None
    aria_role: str | None = None
    component_role: str | None = None
    readonly: bool = False
    disabled: bool = False
    required: bool | None = None
    initial_value: Any | None = None
    initial_value_observed: bool = False
    initial_value_evidence: tuple[ValueEvidence, ...] = ()
    options_sensitive: bool = False
    option_count: int = 0
    option_runtime_resolver: str | None = None

    @model_validator(mode="after")
    def _freeze_locator(self) -> "ControlEvidence":
        object.__setattr__(self, "control_locator", freeze_json(self.control_locator))
        object.__setattr__(self, "initial_value", freeze_json(self.initial_value))
        return self


class FieldWireBinding(FrozenModel):
    binding_id: UUID = Field(default_factory=uuid4)
    field_uuid: UUID
    request_definition_id: UUID
    observation_ids: tuple[str, ...] = ()
    step_uuid: UUID
    direction: BindingDirection
    wire_path: str
    wire_tokens: tuple[str | int, ...]
    binding_role: BindingRole

    @field_validator("wire_path")
    @classmethod
    def _wire_path_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("wire_path must not be blank")
        return value


class CanonicalField(FrozenModel):
    field_uuid: UUID = Field(default_factory=uuid4)
    lineage_id: UUID
    aliases: tuple[FieldAlias, ...] = ()
    control_evidence_ids: tuple[str, ...] = ()
    wire_binding_ids: tuple[UUID, ...] = ()
    decisions: dict[FieldDimension, AxisDecision] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_axes(self) -> "CanonicalField":
        for axis, decision in self.decisions.items():
            if FieldDimension(axis) is not decision.axis:
                raise ValueError("decision axis must match its map key")
        # ``freeze_json`` stringifies mapping keys, whereas this typed mapping
        # intentionally retains FieldDimension keys for warning-free Pydantic
        # serialization.
        object.__setattr__(
            self,
            "decisions",
            FrozenDict(
                {
                    FieldDimension(axis): decision.model_copy(deep=True)
                    for axis, decision in self.decisions.items()
                }
            ),
        )
        return self


class FieldAxisHistory(FrozenModel):
    field_uuid: UUID
    decision: AxisDecision


class FieldRegistrySnapshot(FrozenModel):
    lineage_id: UUID
    fields: tuple[CanonicalField, ...]
    controls: tuple[ControlEvidence, ...] = ()
    bindings: tuple[FieldWireBinding, ...] = ()
    decision_history: tuple[FieldAxisHistory, ...] = ()


class FieldRegistry:
    """Lineage-scoped identity registry.

    Paths, names and array positions are aliases, never primary identities.
    All mutations rebuild frozen models, which keeps returned objects safe to
    serialize into immutable revisions.
    """

    def __init__(self, lineage_id: UUID | str) -> None:
        self.lineage_id = UUID(str(lineage_id))
        self._fields: dict[UUID, CanonicalField] = {}
        self._aliases: dict[tuple[str, str, str], UUID] = {}
        self._controls: dict[str, ControlEvidence] = {}
        self._bindings: dict[UUID, FieldWireBinding] = {}
        self._history: list[FieldAxisHistory] = []
        self._lock = RLock()

    def _get(self, field_uuid: UUID | str) -> CanonicalField:
        key = UUID(str(field_uuid))
        try:
            return self._fields[key]
        except KeyError as exc:
            raise UnknownField(str(key)) from exc

    def register_field(
        self,
        *,
        aliases: Iterable[FieldAlias] = (),
        field_uuid: UUID | str | None = None,
    ) -> CanonicalField:
        aliases = tuple(dict.fromkeys(aliases))
        with self._lock:
            owners = {self._aliases[item.key] for item in aliases if item.key in self._aliases}
            requested = UUID(str(field_uuid)) if field_uuid is not None else None
            if requested is not None and requested in self._fields:
                owners.add(requested)
            if len(owners) > 1:
                raise AliasConflict("aliases already belong to different canonical fields")
            if owners:
                identity = next(iter(owners))
                if requested is not None and requested != identity:
                    raise AliasConflict("requested field_uuid conflicts with an existing alias")
                current = self._fields[identity]
            else:
                identity = requested or uuid4()
                if identity in self._fields:
                    current = self._fields[identity]
                else:
                    current = CanonicalField(field_uuid=identity, lineage_id=self.lineage_id)
            merged_aliases = list(current.aliases)
            for alias in aliases:
                owner = self._aliases.get(alias.key)
                if owner is not None and owner != identity:
                    raise AliasConflict(f"alias {alias.key!r} belongs to {owner}")
                if alias not in merged_aliases:
                    merged_aliases.append(alias)
            stored = current.model_copy(update={"aliases": tuple(merged_aliases)}, deep=True)
            self._fields[identity] = stored
            for alias in stored.aliases:
                self._aliases[alias.key] = identity
            return stored.model_copy(deep=True)

    def resolve_alias(self, alias: FieldAlias) -> CanonicalField | None:
        with self._lock:
            field_uuid = self._aliases.get(alias.key)
            if field_uuid is None:
                return None
            return self._fields[field_uuid].model_copy(deep=True)

    def get_field(self, field_uuid: UUID | str) -> CanonicalField:
        with self._lock:
            return self._get(field_uuid).model_copy(deep=True)

    def list_fields(self) -> tuple[CanonicalField, ...]:
        with self._lock:
            return tuple(
                item.model_copy(deep=True)
                for item in sorted(self._fields.values(), key=lambda value: str(value.field_uuid))
            )

    def add_control_evidence(self, evidence: ControlEvidence) -> CanonicalField:
        with self._lock:
            current = self._get(evidence.field_uuid)
            existing = self._controls.get(evidence.evidence_id)
            if existing is not None and existing != evidence:
                raise FieldRegistryError(
                    f"control evidence {evidence.evidence_id} is immutable"
                )
            self._controls[evidence.evidence_id] = evidence.model_copy(deep=True)
            ids = tuple(dict.fromkeys((*current.control_evidence_ids, evidence.evidence_id)))
            stored = current.model_copy(update={"control_evidence_ids": ids}, deep=True)
            self._fields[current.field_uuid] = stored
            return stored.model_copy(deep=True)

    def add_wire_binding(self, binding: FieldWireBinding) -> CanonicalField:
        with self._lock:
            current = self._get(binding.field_uuid)
            existing = self._bindings.get(binding.binding_id)
            if existing is not None and existing != binding:
                existing_structure = existing.model_copy(update={"observation_ids": ()})
                incoming_structure = binding.model_copy(update={"observation_ids": ()})
                if existing_structure != incoming_structure:
                    raise FieldRegistryError(
                        f"wire binding {binding.binding_id} is immutable"
                    )
                binding = existing.model_copy(
                    update={
                        "observation_ids": tuple(
                            sorted(
                                set(existing.observation_ids)
                                | set(binding.observation_ids)
                            )
                        )
                    },
                    deep=True,
                )
            self._bindings[binding.binding_id] = binding.model_copy(deep=True)
            ids = tuple(dict.fromkeys((*current.wire_binding_ids, binding.binding_id)))
            stored = current.model_copy(update={"wire_binding_ids": ids}, deep=True)
            self._fields[current.field_uuid] = stored
            return stored.model_copy(deep=True)

    def remove_wire_binding(
        self,
        field_uuid: UUID | str,
        binding_id: UUID | str,
    ) -> CanonicalField:
        """Remove one binding without changing the permanent field identity."""

        field_key = UUID(str(field_uuid))
        binding_key = UUID(str(binding_id))
        with self._lock:
            current = self._get(field_key)
            binding = self._bindings.get(binding_key)
            if binding is None or binding.field_uuid != field_key:
                raise FieldRegistryError(
                    f"wire binding {binding_key} does not belong to field {field_key}"
                )
            del self._bindings[binding_key]
            stored = current.model_copy(
                update={
                    "wire_binding_ids": tuple(
                        item for item in current.wire_binding_ids if item != binding_key
                    )
                },
                deep=True,
            )
            self._fields[field_key] = stored
            return stored.model_copy(deep=True)

    @staticmethod
    def _can_apply(current: AxisDecision | None, candidate: AxisDecision) -> None:
        if current is None:
            return
        if candidate.decided_at_revision < current.decided_at_revision:
            raise AxisDecisionConflict(
                f"stale axis decision at revision {candidate.decided_at_revision}; "
                f"current revision is {current.decided_at_revision}"
            )
        if current.manual_override and candidate.origin is not AxisOrigin.MANUAL:
            raise AxisDecisionConflict(
                f"axis {candidate.axis.value} has a manual override"
            )
        if current.value == candidate.value:
            return
        if candidate.origin is AxisOrigin.PI and current.origin is AxisOrigin.OBSERVED:
            raise AxisDecisionConflict(
                f"Pi decision conflicts with grounded {current.origin.value} decision"
            )
        if candidate.origin is AxisOrigin.DETERMINISTIC and current.origin is AxisOrigin.OBSERVED:
            raise AxisDecisionConflict("deterministic decision conflicts with observed evidence")

    def _apply_decision_unlocked(self, field_uuid: UUID, decision: AxisDecision) -> None:
        current_field = self._get(field_uuid)
        current = current_field.decisions.get(decision.axis)
        self._can_apply(current, decision)
        origin_priority = {
            AxisOrigin.PI: 1,
            AxisOrigin.DETERMINISTIC: 2,
            AxisOrigin.OBSERVED: 3,
            AxisOrigin.MANUAL: 4,
        }
        if (
            current is not None
            and current.value == decision.value
            and origin_priority[decision.origin] < origin_priority[current.origin]
        ):
            # Agreement from a weaker source is useful corroboration but must
            # not downgrade ownership of the effective axis.
            return
        if current is not None and decision.supersedes not in {None, current.decision_id}:
            raise AxisDecisionConflict("supersedes does not reference the current decision")
        if current is not None and decision.supersedes is None:
            decision = decision.model_copy(update={"supersedes": current.decision_id}, deep=True)
        decisions = dict(current_field.decisions)
        decisions[decision.axis] = decision
        self._fields[field_uuid] = current_field.model_copy(
            update={"decisions": decisions}, deep=True
        )
        self._history.append(
            FieldAxisHistory(field_uuid=field_uuid, decision=decision.model_copy(deep=True))
        )

    def apply_axis_decision(
        self,
        field_uuid: UUID | str,
        decision: AxisDecision,
    ) -> CanonicalField:
        key = UUID(str(field_uuid))
        with self._lock:
            self._apply_decision_unlocked(key, decision)
            return self._fields[key].model_copy(deep=True)

    def apply_axis_decisions(
        self,
        changes: Iterable[tuple[UUID | str, AxisDecision]],
    ) -> tuple[CanonicalField, ...]:
        """Apply a batch atomically; any conflict restores the entire registry."""

        normalized = tuple((UUID(str(field_uuid)), decision) for field_uuid, decision in changes)
        with self._lock:
            old_fields = deepcopy(self._fields)
            old_history = deepcopy(self._history)
            try:
                for field_uuid, decision in normalized:
                    self._apply_decision_unlocked(field_uuid, decision)
            except Exception:
                self._fields = old_fields
                self._history = old_history
                raise
            touched = tuple(dict.fromkeys(field_uuid for field_uuid, _ in normalized))
            return tuple(self._fields[item].model_copy(deep=True) for item in touched)

    def clear_manual_override(
        self,
        field_uuid: UUID | str,
        axis: FieldDimension | str,
        *,
        revision: int,
    ) -> CanonicalField:
        """Clear only one manual axis and restore its latest automatic value."""

        key = UUID(str(field_uuid))
        resolved_axis = FieldDimension(axis)
        with self._lock:
            current_field = self._get(key)
            current = current_field.decisions.get(resolved_axis)
            if current is None or not current.manual_override:
                return current_field.model_copy(deep=True)
            if revision < current.decided_at_revision:
                raise AxisDecisionConflict(
                    f"stale clear at revision {revision}; current revision is "
                    f"{current.decided_at_revision}"
                )
            automatic = next(
                (
                    item.decision
                    for item in reversed(self._history)
                    if item.field_uuid == key
                    and item.decision.axis is resolved_axis
                    and item.decision.origin is not AxisOrigin.MANUAL
                ),
                None,
            )
            decisions = dict(current_field.decisions)
            if automatic is None:
                decisions.pop(resolved_axis, None)
            else:
                restored = automatic.model_copy(
                    update={
                        "decision_id": str(uuid4()),
                        "decided_at_revision": revision,
                        "manual_override": False,
                        "supersedes": current.decision_id,
                    },
                    deep=True,
                )
                decisions[resolved_axis] = restored
                self._history.append(
                    FieldAxisHistory(field_uuid=key, decision=restored.model_copy(deep=True))
                )
            self._fields[key] = current_field.model_copy(
                update={"decisions": decisions}, deep=True
            )
            return self._fields[key].model_copy(deep=True)

    def snapshot(self) -> FieldRegistrySnapshot:
        with self._lock:
            return FieldRegistrySnapshot(
                lineage_id=self.lineage_id,
                fields=self.list_fields(),
                controls=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(self._controls.values(), key=lambda value: value.evidence_id)
                ),
                bindings=tuple(
                    item.model_copy(deep=True)
                    for item in sorted(
                        self._bindings.values(),
                        key=lambda value: str(value.binding_id),
                    )
                ),
                decision_history=tuple(item.model_copy(deep=True) for item in self._history),
            )

    @classmethod
    def from_snapshot(cls, snapshot: FieldRegistrySnapshot | dict[str, Any]) -> "FieldRegistry":
        parsed = (
            snapshot
            if isinstance(snapshot, FieldRegistrySnapshot)
            else FieldRegistrySnapshot.model_validate(snapshot)
        )
        registry = cls(parsed.lineage_id)
        with registry._lock:
            for field in parsed.fields:
                if field.lineage_id != parsed.lineage_id:
                    raise FieldRegistryError(
                        f"field {field.field_uuid} belongs to another lineage"
                    )
                registry._fields[field.field_uuid] = field.model_copy(deep=True)
                for alias in field.aliases:
                    owner = registry._aliases.get(alias.key)
                    if owner is not None and owner != field.field_uuid:
                        raise AliasConflict(f"snapshot alias {alias.key!r} has multiple owners")
                    registry._aliases[alias.key] = field.field_uuid
            for item in parsed.controls:
                if item.field_uuid not in registry._fields:
                    raise UnknownField(
                        f"control evidence {item.evidence_id} references {item.field_uuid}"
                    )
                existing = registry._controls.get(item.evidence_id)
                if existing is not None and existing != item:
                    raise FieldRegistryError(
                        f"control evidence {item.evidence_id} is duplicated"
                    )
                registry._controls[item.evidence_id] = item.model_copy(deep=True)
            for item in parsed.bindings:
                if item.field_uuid not in registry._fields:
                    raise UnknownField(
                        f"wire binding {item.binding_id} references {item.field_uuid}"
                    )
                existing = registry._bindings.get(item.binding_id)
                if existing is not None and existing != item:
                    raise FieldRegistryError(f"wire binding {item.binding_id} is duplicated")
                registry._bindings[item.binding_id] = item.model_copy(deep=True)
            for item in parsed.decision_history:
                if item.field_uuid not in registry._fields:
                    raise UnknownField(
                        f"decision {item.decision.decision_id} references {item.field_uuid}"
                    )
            for field in registry._fields.values():
                missing_controls = set(field.control_evidence_ids) - set(registry._controls)
                missing_bindings = set(field.wire_binding_ids) - set(registry._bindings)
                if missing_controls or missing_bindings:
                    raise FieldRegistryError(
                        f"field {field.field_uuid} references missing evidence/bindings"
                    )
            registry._history = [
                item.model_copy(deep=True) for item in parsed.decision_history
            ]
        return registry


def _snapshot_field_rows(snapshot: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return every compatibility row that projects a canonical field."""

    rows: list[dict[str, Any]] = []
    for step in snapshot.get("steps") or ():
        if isinstance(step, dict):
            rows.extend(item for item in step.get("params") or () if isinstance(item, dict))
    for capability in snapshot.get("capabilities") or ():
        if not isinstance(capability, dict):
            continue
        for key in (
            "fields",
            "inputs",
            "request_fields",
            "internal_fields",
            "computed_fields",
            "outputs",
        ):
            rows.extend(
                item for item in capability.get(key) or () if isinstance(item, dict)
            )
    rows.extend(
        item for item in snapshot.get("effective_fields") or () if isinstance(item, dict)
    )
    return tuple(rows)


def _project_axis_value(
    row: dict[str, Any],
    axis: FieldDimension,
    value: Any,
) -> None:
    if axis is FieldDimension.DISPLAY_NAME:
        row.update({"display_name": value, "label": value, "key": value})
    elif axis is FieldDimension.BUSINESS_TYPE:
        row.update({"business_type": value, "type": value})
    elif axis is FieldDimension.CLASSIFICATION:
        row["classification"] = value
    elif axis is FieldDimension.SOURCE_BINDING:
        row["source_binding"] = (
            value.model_dump(mode="json", exclude_none=True)
            if hasattr(value, "model_dump") else deepcopy(value)
        )
    elif axis is FieldDimension.DEFAULT_VALUE:
        row["default_value"] = deepcopy(value)
    elif axis is FieldDimension.CALLER_REQUIRED:
        state = str(getattr(value, "value", value))
        row["caller_required"] = state
        row["required"] = state == "true"
    elif axis is FieldDimension.WIRE_REQUIRED:
        row["wire_required"] = str(getattr(value, "value", value))
    elif axis is FieldDimension.REQUIRED_CONDITIONS:
        row["required_conditions"] = deepcopy(value)
    elif axis is FieldDimension.EXPOSURE:
        row["exposed_to_caller"] = bool(value)
        row["exposed_to_user"] = bool(value)
    elif axis is FieldDimension.ENUM_BINDING:
        row["enum_binding"] = deepcopy(value)


def sync_snapshot_axis_decision(
    snapshot: dict[str, Any],
    *,
    field_uuid: UUID | str,
    decision: AxisDecision,
) -> CanonicalField | None:
    """Atomically update the authoritative registry and all compatibility rows.

    Snapshots produced before contract migration have no registry; callers may
    retain their legacy pin behaviour until FlowMigrator creates one.
    """

    payload = snapshot.get("field_registry")
    if not isinstance(payload, dict):
        return None
    registry = FieldRegistry.from_snapshot(payload)
    field = registry.apply_axis_decision(field_uuid, decision)
    snapshot["field_registry"] = registry.snapshot().model_dump(mode="json")
    identity = str(field.field_uuid)
    for row in _snapshot_field_rows(snapshot):
        if identity not in {
            str(row.get("field_uuid") or ""),
            str(row.get("field_id") or ""),
        }:
            continue
        row.setdefault("axis_decisions", {})[decision.axis.value] = (
            decision.model_dump(mode="json")
        )
        _project_axis_value(row, decision.axis, decision.value)
    return field


def clear_snapshot_manual_axis(
    snapshot: dict[str, Any],
    *,
    field_uuid: UUID | str,
    axis: FieldDimension | str,
    revision: int,
) -> CanonicalField | None:
    payload = snapshot.get("field_registry")
    if not isinstance(payload, dict):
        return None
    resolved_axis = FieldDimension(axis)
    registry = FieldRegistry.from_snapshot(payload)
    field = registry.clear_manual_override(
        field_uuid,
        resolved_axis,
        revision=revision,
    )
    snapshot["field_registry"] = registry.snapshot().model_dump(mode="json")
    identity = str(field.field_uuid)
    decision = field.decisions.get(resolved_axis)
    for row in _snapshot_field_rows(snapshot):
        if identity not in {
            str(row.get("field_uuid") or ""),
            str(row.get("field_id") or ""),
        }:
            continue
        decisions = row.setdefault("axis_decisions", {})
        if decision is None:
            decisions.pop(resolved_axis.value, None)
        else:
            decisions[resolved_axis.value] = decision.model_dump(mode="json")
            _project_axis_value(row, resolved_axis, decision.value)
    return field


__all__ = [
    "AliasConflict",
    "AxisDecisionConflict",
    "BindingDirection",
    "BindingRole",
    "CanonicalField",
    "ControlEvidence",
    "FieldAlias",
    "FieldAliasKind",
    "FieldAxisHistory",
    "FieldRegistry",
    "FieldRegistryError",
    "clear_snapshot_manual_axis",
    "sync_snapshot_axis_decision",
    "FieldRegistrySnapshot",
    "FieldWireBinding",
    "UnknownField",
]
