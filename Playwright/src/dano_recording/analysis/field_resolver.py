"""Extract wire field facts and apply independent semantic decisions."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, unquote
from collections import defaultdict
from typing import Any, Iterable

from dano_recording.domain.fields import (
    EffectiveFieldContract,
    DecisionOrigin,
    FieldDimension,
    FieldDecision,
    FieldFact,
    FieldLocation,
    FieldProposal,
    WireSchema,
    resolve_field_contract,
    ValueProvider,
    ValueProviderKind,
)
from dano_recording.domain.operations import CompiledRequest
from dano_recording.header_contracts import HeaderContractKind, classify_header


def _wire_type(value: Any) -> tuple[str, str | None]:
    if value is None:
        return "any", None
    if isinstance(value, bool):
        return "boolean", None
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer", None
    if isinstance(value, float):
        return "number", None
    if isinstance(value, str):
        return "string", None
    if isinstance(value, list):
        item_types = {_wire_type(item)[0] for item in value if item is not None}
        return "array", next(iter(item_types)) if len(item_types) == 1 else None
    if isinstance(value, dict):
        return "object", None
    return "any", None


def _body_fields(value: Any, prefix: str = "") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict) and value:
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if (
                isinstance(child, dict)
                and child
                and not {"filename", "content_type", "size", "sha256"}.issubset(child)
            ):
                yield from _body_fields(child, path)
            else:
                yield path, str(key), child
        return
    path = prefix or "$"
    name = prefix.rsplit(".", 1)[-1] if prefix else "$"
    yield path, name, value


def _field_id(request_id: str, location: FieldLocation, path: str) -> str:
    digest = hashlib.sha256(f"{request_id}:{location.value}:{path}".encode()).hexdigest()[:18]
    return f"fld_{digest}"


_PATH_TEMPLATE = re.compile(r"^(?:\{(?P<brace>[A-Za-z_][\w-]*)\}|:(?P<colon>[A-Za-z_][\w-]*))$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_HEX_ID = re.compile(r"^[0-9a-f]{16,}$", re.I)
_DATE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{16,}$")
_VERSION_SEGMENT = re.compile(r"^v?\d+(?:\.\d+)*$", re.I)


def _content_type(headers: dict[str, str]) -> str:
    return next(
        (str(value).split(";", 1)[0].strip().lower()
         for key, value in headers.items() if str(key).lower() == "content-type"),
        "",
    )


def _looks_dynamic_segment(value: str, *, prior: str) -> bool:
    if _UUID.fullmatch(value) or _ULID.fullmatch(value) or _HEX_ID.fullmatch(value):
        return True
    if _DATE_ID.fullmatch(value):
        return True
    if value.isdigit():
        return prior.lower() not in {"api", "v", "version", "versions"} and not _VERSION_SEGMENT.fullmatch(prior)
    return bool(
        _OPAQUE_ID.fullmatch(value)
        and any(char.isalpha() for char in value)
        and any(char.isdigit() for char in value)
    )


def _path_fields(path: str) -> Iterable[tuple[str, str, Any, tuple[Any, ...]]]:
    segments = [unquote(value) for value in path.split("/") if value]
    for index, value in enumerate(segments):
        match = _PATH_TEMPLATE.fullmatch(value)
        if match:
            name = str(match.group("brace") or match.group("colon"))
            yield name, name, None, ()
            continue
        prior = segments[index - 1] if index else ""
        if _looks_dynamic_segment(value, prior=prior):
            name = f"segment_{index}"
            yield name, name, value, (value,)


def _make_fact(
    request: CompiledRequest,
    *,
    location: FieldLocation,
    path: str,
    name: str,
    value: Any,
    observed: tuple[Any, ...],
    required: bool = False,
) -> FieldFact:
    type_name, items_type = _wire_type(value)
    return FieldFact(
        field_contract_id=_field_id(request.request_id, location, path.lower() if location is FieldLocation.HEADER else path),
        tenant=request.tenant,
        recording_id=request.recording_id,
        request_id=request.request_id,
        location=location,
        wire_path=path,
        wire_name=name,
        wire_schema=WireSchema(
            type=type_name,
            items_type=items_type,
            nullable=value is None and location is not FieldLocation.PATH,
            sample=value,
        ),
        observed_values=observed,
        required_by_wire=required,
    )



def extract_field_facts(requests: tuple[CompiledRequest, ...]) -> tuple[FieldFact, ...]:
    facts: list[FieldFact] = []
    for request in requests:
        for path, name, sample, observed in _path_fields(request.path):
            facts.append(_make_fact(
                request,
                location=FieldLocation.PATH,
                path=path,
                name=name,
                value=sample,
                observed=observed,
                required=True,
            ))

        query_values: dict[str, list[str]] = defaultdict(list)
        for name, value in request.query:
            query_values[name].append(value)
        for name, values in query_values.items():
            sample: Any = values[0] if len(values) == 1 else list(values)
            facts.append(_make_fact(
                request,
                location=FieldLocation.QUERY,
                path=name,
                name=name,
                value=sample,
                observed=tuple(values),
            ))

        for name, value in request.headers.items():
            if classify_header(str(name)) is HeaderContractKind.TRANSPORT_CONSTANT:
                # Transport headers stay as step-level constants.  Turning
                # Content-Type/Accept into runtime fields creates providers the
                # executor does not own and needlessly exposes UI controls.
                continue
            text = str(value)
            facts.append(_make_fact(
                request,
                location=FieldLocation.HEADER,
                path=str(name),
                name=str(name),
                value=text,
                observed=(text,),
            ))

        if request.body_present:
            media_type = _content_type(request.headers)
            location = (
                FieldLocation.FORM
                if media_type in {"application/x-www-form-urlencoded", "multipart/form-data"}
                else FieldLocation.BODY
            )
            body = request.body
            if media_type == "application/x-www-form-urlencoded" and isinstance(body, str):
                grouped: dict[str, list[str]] = defaultdict(list)
                for name, value in parse_qsl(body, keep_blank_values=True):
                    grouped[name].append(value)
                body = {
                    name: values[0] if len(values) == 1 else values
                    for name, values in grouped.items()
                }
            for path, name, value in _body_fields(body):
                facts.append(_make_fact(
                    request,
                    location=location,
                    path=path,
                    name=name,
                    value=value,
                    observed=(value,),
                ))
    return tuple(facts)


def materialize_field_contracts(
    facts: tuple[FieldFact, ...],
    proposals: Iterable[FieldProposal] = (),
    decisions: Iterable[FieldDecision] = (),
) -> tuple[EffectiveFieldContract, ...]:
    """Materialize wire facts plus explicitly supplied semantic decisions.

    This stage deliberately does not infer provider, caller-requiredness,
    exposure, or business type.  Those axes have one production owner:
    :func:`dano_recording.field_inference.infer_field`, whose grounded result
    is persisted in ``FieldRegistry`` during contract integration.  The
    unresolved legacy-shaped values here exist only so wire facts and explicit
    proposal/decision inputs can travel through the immutable compilation.
    """

    proposals = tuple(proposals)
    decisions = tuple(decisions)
    materialized: list[EffectiveFieldContract] = []
    semantic_dimensions = {
        FieldDimension.BUSINESS_TYPE,
        FieldDimension.VALUE_PROVIDER,
        FieldDimension.CHOICE_CONTRACT,
        FieldDimension.REQUIRED,
        FieldDimension.EXPOSED,
    }
    for fact in facts:
        fact_proposals = tuple(
            item for item in proposals if item.field_contract_id == fact.field_contract_id
        )
        fact_decisions = tuple(
            item for item in decisions if item.field_contract_id == fact.field_contract_id
        )
        explicit_dimensions = {
            dimension
            for item in fact_proposals
            for dimension in item.values
        } | {item.dimension for item in fact_decisions}
        missing_semantics = tuple(
            sorted(
                semantic_dimensions - explicit_dimensions,
                key=lambda item: item.value,
            )
        )
        resolved = resolve_field_contract(fact, fact_proposals, fact_decisions)
        origins = dict(resolved.origins)
        for dimension in missing_semantics:
            origins[dimension] = DecisionOrigin.UNRESOLVED
        unresolved = tuple(
            dict.fromkeys(
                (
                    *resolved.unresolved_dimensions,
                    *missing_semantics,
                )
            )
        )
        materialized.append(
            resolved.model_copy(
                update={
                    "business_type": (
                        resolved.business_type
                        if FieldDimension.BUSINESS_TYPE in explicit_dimensions
                        else "unknown"
                    ),
                    "value_provider": (
                        resolved.value_provider
                        if FieldDimension.VALUE_PROVIDER in explicit_dimensions
                        else ValueProvider(kind=ValueProviderKind.UNRESOLVED)
                    ),
                    "choice_contract": (
                        resolved.choice_contract
                        if FieldDimension.CHOICE_CONTRACT in explicit_dimensions
                        else None
                    ),
                    "required": (
                        resolved.required
                        if FieldDimension.REQUIRED in explicit_dimensions
                        else False
                    ),
                    "exposed": (
                        resolved.exposed
                        if FieldDimension.EXPOSED in explicit_dimensions
                        else False
                    ),
                    "origins": origins,
                    "unresolved_dimensions": unresolved,
                },
                deep=True,
            )
        )
    return tuple(materialized)
