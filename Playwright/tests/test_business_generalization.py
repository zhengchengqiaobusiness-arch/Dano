from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from dano_recording.capture_store import CaptureStore
from dano_recording.compiler.models import RecordingCompilation
from dano_recording.compiler.pipeline import (
    RecordingContractProjection,
    compile_recording,
    integrate_compilation_contracts,
)
from dano_recording.domain.facts import ActionFact, FactKind, RecordingFact, RequestFact
from dano_recording.domain.fields import FieldLocation
from dano_recording.domain.operations import RequestDisposition
from dano_recording.field_registry import FieldRegistry
from dano_recording.value_evidence import ValueEvidenceFactory


TENANT = "tenant-generalization"
RECORDING = "recording-generalization"
SECRET = b"business-generalization-fixture-secret"


@dataclass(frozen=True, slots=True)
class ScenarioVariant:
    name: str
    domain: str
    api_root: str
    entity_id: int
    request_prefix: str
    action_prefix: str
    framework: str
    labels: dict[str, str]
    fields: dict[str, str]
    route_names: dict[str, str]
    reversed_actions: bool = False
    redraw_generation: int = 0
    enum_revision: int = 0


@dataclass(frozen=True, slots=True)
class Scenario:
    variant: ScenarioVariant
    facts: tuple[RecordingFact, ...]
    request_ids: dict[str, str]
    action_ids: dict[str, str]


@dataclass(frozen=True, slots=True)
class CompiledScenario:
    scenario: Scenario
    preliminary: RecordingCompilation
    compilation: RecordingCompilation
    contracts: RecordingContractProjection
    store: CaptureStore
    registry: FieldRegistry


BASE_FIELDS = {
    "project": "projectName",
    "approver": "approverName",
    "date_from": "dateFrom",
    "date_to": "dateTo",
    "status": "status",
    "project_id": "projectId",
    "team_id": "teamId",
    "work_type": "workTypeId",
    "approver_id": "approverId",
    "hours": "workHours",
    "content": "workContent",
    "entries": "entries",
}


BASE_ROUTES = {
    "identity": "current-user",
    "collection": "applications",
    "detail": "detail",
    "progress": "progress",
    "option_root": "options",
    "status": "statuses",
    "project": "projects",
    "team": "teams",
    "work_type": "work-types",
    "approver": "approvers",
    "validate": "validate",
    "submit": "submit",
    "withdraw": "withdraw",
}


BASE_LABELS = {
    "bootstrap": "初始化页面",
    "search": "查询申报记录",
    "detail": "查看申报详情",
    "progress": "查看审批进度",
    "options": "加载表单选项",
    "support": "检查记录版本",
    "submit": "提交工时申报",
    "withdraw": "撤回工时申报",
    "resource": "加载页面资源",
    "unsupported": "建立事件流",
}


def _variant(
    *,
    name: str,
    domain: str,
    api_root: str = "/api/v1",
    entity_id: int = 42,
    request_prefix: str,
    action_prefix: str,
    framework: str = "react",
    labels: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
    route_names: dict[str, str] | None = None,
    reversed_actions: bool = False,
    redraw_generation: int = 0,
    enum_revision: int = 0,
) -> ScenarioVariant:
    return ScenarioVariant(
        name=name,
        domain=domain,
        api_root=api_root,
        entity_id=entity_id,
        request_prefix=request_prefix,
        action_prefix=action_prefix,
        framework=framework,
        labels={**BASE_LABELS, **(labels or {})},
        fields={**BASE_FIELDS, **(fields or {})},
        route_names={**BASE_ROUTES, **(route_names or {})},
        reversed_actions=reversed_actions,
        redraw_generation=redraw_generation,
        enum_revision=enum_revision,
    )


def _build_scenario(variant: ScenarioVariant) -> Scenario:
    request_ids = {
        semantic: f"{variant.request_prefix}-{semantic}"
        for semantic in (
            "identity",
            "preflight",
            "query",
            "detail",
            "progress",
            "status-options",
            "project-options",
            "team-options",
            "work-type-options",
            "approver-options-1",
            "approver-options-2",
            "support",
            "validate",
            "submit",
            "withdraw",
            "resource",
            "unsupported",
        )
    }
    action_ids = {
        semantic: f"{variant.action_prefix}-{semantic}"
        for semantic in BASE_LABELS
    }
    list_page = f"{variant.name}-list-page"
    form_page = f"{variant.name}-form-page"
    f = variant.fields
    routes = variant.route_names
    root = f"https://{variant.domain}{variant.api_root.rstrip('/')}"
    collection = f"{root}/{routes['collection']}"
    option_root = f"{root}/{routes['option_root']}"

    facts: list[RecordingFact] = []
    sequence = 0

    def emit_page(page_id: str, path: str) -> None:
        nonlocal sequence
        facts.append(
            RecordingFact(
                tenant=TENANT,
                recording_id=RECORDING,
                sequence=sequence,
                kind=FactKind.PAGE,
                page_id=page_id,
                payload={
                    "url": f"https://{variant.domain}{path}",
                    "framework": variant.framework,
                    "route_revision": variant.redraw_generation,
                },
            )
        )
        sequence += 1

    emit_page(list_page, "/workbench")
    emit_page(form_page, f"/workbench/{variant.entity_id}/edit")

    # The same wire name appears in query and submit contracts.  The selector
    # is stable while the browser-owned control id changes after a redraw.
    for field_name, selector, page_id in (
        (f["project"], "[data-field='project']", list_page),
        (f["project_id"], "[data-field='project-id']", form_page),
        (f["hours"], "[data-field='hours']", form_page),
    ):
        facts.append(
            RecordingFact(
                tenant=TENANT,
                recording_id=RECORDING,
                sequence=sequence,
                kind=FactKind.DOM_CONTROL,
                page_id=page_id,
                payload={
                    "control_id": (
                        f"control-{variant.redraw_generation}-{field_name}"
                    ),
                    "selector": selector,
                    "tag": "input",
                    "input_type": "text",
                    "name": field_name,
                    "label": f"{variant.labels['submit']}:{field_name}",
                    "framework": variant.framework,
                    "initial_value_observed": False,
                },
            )
        )
        sequence += 1

    def request(
        semantic: str,
        *,
        action: str,
        page_id: str,
        method: str,
        url: str,
        resource_type: str = "fetch",
        headers: dict[str, str] | None = None,
        body: Any | None = None,
        body_present: bool = False,
        response_status: int | None = 200,
        response_body: Any | None = None,
    ) -> RequestFact:
        nonlocal sequence
        row = RequestFact(
            tenant=TENANT,
            recording_id=RECORDING,
            sequence=sequence,
            page_id=page_id,
            action_id=action_ids[action],
            request_id=request_ids[semantic],
            method=method,
            url=url,
            resource_type=resource_type,
            request_headers=headers or {},
            request_body=body,
            request_body_present=body_present,
            response_status=response_status,
            response_body=response_body,
            payload={
                "framework": variant.framework,
                "initiator": f"{variant.framework}:network-client",
            },
        )
        sequence += 1
        return row

    def action(semantic: str, page_id: str, requests: list[RequestFact]) -> None:
        nonlocal sequence
        locator = f"[data-action='{semantic}']"
        # Actions are emitted immediately before their requests, while the
        # global action-group order is independently variable.
        action_sequence = sequence
        facts.append(
            ActionFact(
                tenant=TENANT,
                recording_id=RECORDING,
                sequence=action_sequence,
                page_id=page_id,
                action_id=action_ids[semantic],
                action_type="click",
                label=variant.labels[semantic],
                locator=locator,
                payload={
                    "evidence_origin": "server_dispatched",
                    "causal_eligible": True,
                    "details": {
                        "selector": locator,
                        "name": f"{semantic}-command",
                        "tag": "button",
                        "framework": variant.framework,
                        "component_instance": variant.redraw_generation,
                    }
                },
            )
        )
        sequence += 1
        for row in requests:
            facts.append(row.model_copy(update={"sequence": sequence}, deep=True))
            sequence += 1

    enum_suffix = f"-r{variant.enum_revision}"
    groups: dict[str, tuple[str, list[RequestFact]]] = {
        "bootstrap": (
            list_page,
            [
                request(
                    "identity",
                    action="bootstrap",
                    page_id=list_page,
                    method="GET",
                    url=f"{root}/{routes['identity']}",
                    response_body={"id": "[REDACTED:IDENTITY]", "tenant": "current"},
                ),
                request(
                    "preflight",
                    action="bootstrap",
                    page_id=list_page,
                    method="OPTIONS",
                    url=collection,
                    response_status=204,
                ),
            ],
        ),
        "search": (
            list_page,
            [
                request(
                    "query",
                    action="search",
                    page_id=list_page,
                    method="GET",
                    url=(
                        f"{collection}?{f['project']}=Apollo&{f['approver']}=Lin"
                        f"&{f['date_from']}=2026-07-01&{f['date_to']}=2026-07-31"
                        f"&{f['status']}=pending&{f['status']}=review"
                    ),
                    response_body={
                        "records": [
                            {
                                "id": variant.entity_id,
                                f["project"]: "Apollo",
                                f["status"]: "pending",
                            }
                        ],
                        "total": 1,
                    },
                )
            ],
        ),
        "detail": (
            list_page,
            [
                request(
                    "detail",
                    action="detail",
                    page_id=list_page,
                    method="GET",
                    url=(
                        f"{collection}/{variant.entity_id}/{routes['detail']}"
                        f"?recordId={variant.entity_id}"
                    ),
                    response_body={
                        "id": variant.entity_id,
                        f["project_id"]: "P-7",
                        f["content"]: "implementation",
                    },
                )
            ],
        ),
        "progress": (
            list_page,
            [
                request(
                    "progress",
                    action="progress",
                    page_id=list_page,
                    method="GET",
                    url=(
                        f"{collection}/{variant.entity_id}/{routes['progress']}"
                        "?include=history"
                    ),
                    response_body={
                        "remainingHours": 8,
                        "records": [{"stage": "review", "done": False}],
                    },
                )
            ],
        ),
        "options": (
            form_page,
            [
                request(
                    "status-options",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['status']}",
                    response_body=[
                        {"label": f"待处理{enum_suffix}", "value": "pending"},
                        {"label": f"审批中{enum_suffix}", "value": "review"},
                    ],
                ),
                request(
                    "project-options",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['project']}?q=apo",
                    response_body=[
                        {"label": f"Apollo{enum_suffix}", "value": "P-7"}
                    ],
                ),
                request(
                    "team-options",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['team']}",
                    response_body=[
                        {"label": f"Platform{enum_suffix}", "value": "T-1"}
                    ],
                ),
                request(
                    "work-type-options",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['work_type']}",
                    response_body=[
                        {"label": f"Development{enum_suffix}", "value": "DEV"}
                    ],
                ),
                request(
                    "approver-options-1",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['approver']}?q=lin&page=1",
                    response_body={
                        "items": [
                            {"label": f"Reviewer A{enum_suffix}", "value": "U-1"}
                        ],
                        "page": 1,
                        "hasMore": True,
                    },
                ),
                request(
                    "approver-options-2",
                    action="options",
                    page_id=form_page,
                    method="GET",
                    url=f"{option_root}/{routes['approver']}?q=lin&page=2",
                    response_body={
                        "items": [
                            {"label": f"Reviewer B{enum_suffix}", "value": "U-2"}
                        ],
                        "page": 2,
                        "hasMore": False,
                    },
                ),
            ],
        ),
        "support": (
            form_page,
            [
                request(
                    "support",
                    action="support",
                    page_id=form_page,
                    method="HEAD",
                    url=f"{collection}/{variant.entity_id}",
                    response_body=None,
                )
            ],
        ),
        "submit": (
            form_page,
            [
                request(
                    "validate",
                    action="submit",
                    page_id=form_page,
                    method="POST",
                    url=f"{collection}/{routes['validate']}",
                    headers={"content-type": "application/json"},
                    body={f["hours"]: 8, f["status"]: "draft"},
                    response_body={"valid": True, "remainingHours": 8},
                ),
                request(
                    "submit",
                    action="submit",
                    page_id=form_page,
                    method="POST",
                    url=f"{collection}/{routes['submit']}",
                    headers={"content-type": "application/json"},
                    body={
                        f["project"]: "Apollo",
                        f["project_id"]: "P-7",
                        f["team_id"]: "T-1",
                        f["work_type"]: "DEV",
                        f["approver_id"]: "U-1",
                        f["hours"]: 8,
                        f["content"]: "implementation",
                        f["entries"]: [
                            {"date": "2026-07-14", "hours": 4},
                            {"date": "2026-07-15", "hours": 4},
                        ],
                    },
                    response_status=201,
                    response_body={"id": variant.entity_id, "state": "submitted"},
                ),
            ],
        ),
        "withdraw": (
            form_page,
            [
                request(
                    "withdraw",
                    action="withdraw",
                    page_id=form_page,
                    method="DELETE",
                    url=(
                        f"{collection}/{variant.entity_id}/{routes['withdraw']}"
                    ),
                    response_status=204,
                    response_body=None,
                )
            ],
        ),
        "resource": (
            list_page,
            [
                request(
                    "resource",
                    action="resource",
                    page_id=list_page,
                    method="GET",
                    url=f"https://{variant.domain}/assets/shell.css",
                    resource_type="stylesheet",
                    response_body="body{}",
                )
            ],
        ),
        "unsupported": (
            list_page,
            [
                request(
                    "unsupported",
                    action="unsupported",
                    page_id=list_page,
                    method="CONNECT",
                    url=f"{root}/events/stream",
                    resource_type="websocket",
                    response_status=None,
                    response_body=None,
                )
            ],
        ),
    }

    group_order = list(groups)
    if variant.reversed_actions:
        group_order = list(reversed(group_order))
    for semantic in group_order:
        page_id, rows = groups[semantic]
        if semantic == "options" and variant.reversed_actions:
            rows = [*reversed(rows)]
        action(semantic, page_id, rows)

    return Scenario(
        variant=variant,
        facts=tuple(facts),
        request_ids=request_ids,
        action_ids=action_ids,
    )


def _compile_scenario(
    scenario: Scenario,
    *,
    lineage: UUID | None = None,
    store: CaptureStore | None = None,
    registry: FieldRegistry | None = None,
    generation: int = 0,
    source_revision: int = 0,
) -> CompiledScenario:
    lineage = lineage or uuid4()
    store = store or CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
        capture_generation=generation,
    )
    registry = registry or FieldRegistry(lineage)
    preliminary = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=scenario.facts,
        source_revision=source_revision,
    )
    first_contracts = integrate_compilation_contracts(
        preliminary,
        facts=scenario.facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=SECRET),
    )
    compilation = compile_recording(
        tenant=TENANT,
        recording_id=RECORDING,
        facts=scenario.facts,
        source_revision=source_revision,
        evidence_graph=first_contracts.evidence_graph,
    )
    contracts = integrate_compilation_contracts(
        compilation,
        facts=scenario.facts,
        capture_store=store,
        field_registry=registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=SECRET),
    )
    return CompiledScenario(
        scenario=scenario,
        preliminary=preliminary,
        compilation=compilation,
        contracts=contracts,
        store=store,
        registry=registry,
    )


def _request_semantics(result: CompiledScenario) -> dict[str, str]:
    return {value: key for key, value in result.scenario.request_ids.items()}


def _captured_request_order(scenario: Scenario) -> tuple[str, ...]:
    semantics = {value: key for key, value in scenario.request_ids.items()}
    return tuple(
        semantics[item.request_id]
        for item in sorted(
            (fact for fact in scenario.facts if isinstance(fact, RequestFact)),
            key=lambda fact: fact.sequence,
        )
    )


def _controls_by_selector(scenario: Scenario) -> dict[str, str]:
    return {
        str(item.payload["selector"]): str(item.payload["control_id"])
        for item in scenario.facts
        if item.kind is FactKind.DOM_CONTROL
    }


def _request_fact(scenario: Scenario, semantic: str) -> RequestFact:
    request_id = scenario.request_ids[semantic]
    return next(
        item
        for item in scenario.facts
        if isinstance(item, RequestFact) and item.request_id == request_id
    )


def _capability_terminal_semantics(result: CompiledScenario) -> tuple[str, ...]:
    semantics = _request_semantics(result)
    return tuple(
        sorted(
            semantics[capability.request_ids[-1]]
            for capability in result.compilation.capabilities
        )
    )


def _transaction_signature(
    result: CompiledScenario,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    request_semantics = _request_semantics(result)
    action_semantics = {
        value: key for key, value in result.scenario.action_ids.items()
    }
    return tuple(
        sorted(
            (
                action_semantics[str(transaction.action_id)],
                tuple(
                    sorted(
                        request_semantics[request_id]
                        for request_id in transaction.request_ids
                    )
                ),
            )
            for transaction in result.compilation.transactions
        )
    )


def _capability_uuid_by_terminal(result: CompiledScenario) -> dict[str, str]:
    semantics = _request_semantics(result)
    return {
        semantics[capability.request_ids[-1]]: result.contracts.capability_uuids[
            capability.capability_id
        ]
        for capability in result.compilation.capabilities
    }


def _field_uuid_by_semantic_wire(result: CompiledScenario) -> dict[tuple[str, str, str], str]:
    semantics = _request_semantics(result)
    return {
        (
            semantics[field.request_id],
            field.location.value,
            field.wire_path,
        ): result.contracts.field_uuids[field.field_contract_id]
        for field in result.compilation.field_facts
    }


def _equivalence_signature(result: CompiledScenario) -> dict[str, Any]:
    dispositions = Counter(
        item.disposition.value for item in result.compilation.request_analyses
    )
    terminal_semantics = _capability_terminal_semantics(result)
    return {
        "request_count": len(result.compilation.requests),
        "dispositions": dict(sorted(dispositions.items())),
        "capability_terminals": terminal_semantics,
        "capability_operations": tuple(
            sorted(item.operation for item in result.compilation.capabilities)
        ),
        "capability_count": len(result.compilation.capabilities),
        "two_pages": len(
            {item.page_id for item in result.contracts.capture_store.observations}
        ),
    }


def _assert_lossless_business_fixture(result: CompiledScenario) -> None:
    compilation = result.compilation
    request_ids = result.scenario.request_ids
    captured_ids = {
        item.request_id
        for item in result.scenario.facts
        if isinstance(item, RequestFact)
    }
    compiled_ids = {item.request_id for item in compilation.requests}
    analysed_ids = {item.request_id for item in compilation.request_analyses}

    assert len(captured_ids) == 17
    assert compiled_ids == captured_ids
    assert analysed_ids == captured_ids
    assert len(compilation.request_analyses) == len(captured_ids)
    assert compilation.validation.passed
    assert not {
        "missing_request_disposition",
        "request_silently_dropped",
        "non_business_request_exposed",
    } & {item.code for item in compilation.validation.issues}

    assert Counter(item.disposition for item in compilation.request_analyses) == Counter(
        {
            RequestDisposition.IDENTITY: 1,
            RequestDisposition.PREFLIGHT: 1,
            RequestDisposition.MATERIALIZED: 6,
            RequestDisposition.OPTION_SOURCE: 6,
            RequestDisposition.SUPPORTING: 1,
            RequestDisposition.IGNORED_RESOURCE: 1,
            RequestDisposition.UNSUPPORTED: 1,
        }
    )
    by_id = {item.request_id: item for item in compilation.request_analyses}
    assert by_id[request_ids["identity"]].disposition is RequestDisposition.IDENTITY
    assert by_id[request_ids["preflight"]].disposition is RequestDisposition.PREFLIGHT
    assert by_id[request_ids["support"]].disposition is RequestDisposition.SUPPORTING
    assert by_id[request_ids["resource"]].disposition is RequestDisposition.IGNORED_RESOURCE
    assert by_id[request_ids["unsupported"]].disposition is RequestDisposition.UNSUPPORTED

    snapshot = result.contracts.capture_store
    assert {item.observation_id for item in snapshot.observations} == captured_ids
    assert {item.page_id for item in snapshot.observations} == {
        f"{result.scenario.variant.name}-list-page",
        f"{result.scenario.variant.name}-form-page",
    }
    assert len(snapshot.request_definitions) == 16
    assert (
        result.contracts.request_definition_ids[request_ids["approver-options-1"]]
        == result.contracts.request_definition_ids[request_ids["approver-options-2"]]
    )
    assert (
        result.contracts.step_uuids[request_ids["approver-options-1"]]
        == result.contracts.step_uuids[request_ids["approver-options-2"]]
    )
    assert len(set(result.contracts.step_uuids.values())) == 16
    assert request_ids["approver-options-1"] != request_ids["approver-options-2"]

    # Five real user transactions yield five public capabilities.  The
    # validation call remains retained and explicitly reviewable because no
    # causal dependency to submit was observed.
    assert _capability_terminal_semantics(result) == (
        "detail",
        "progress",
        "query",
        "submit",
        "withdraw",
    )
    assert len(set(result.contracts.capability_uuids.values())) == len(
        compilation.capabilities
    )
    transaction_ids = {item.transaction_id for item in compilation.transactions}
    assert len(transaction_ids) == len(compilation.transactions)
    assert Counter(
        request_id
        for transaction in compilation.transactions
        for request_id in transaction.request_ids
    ) == Counter({request_id: 1 for request_id in captured_ids})
    assert all(item.transaction_id in transaction_ids for item in compilation.capabilities)
    assert all(item.request_ids for item in compilation.capabilities)
    membership = {
        request_id
        for capability in compilation.capabilities
        for request_id in capability.request_ids
    }
    assert request_ids["validate"] not in membership
    assert request_ids["validate"] in snapshot.unbound_business_requests
    assert any(
        item.code == "unbound_business_request"
        and item.request_id == request_ids["validate"]
        for item in compilation.validation.issues
    )
    assert any(
        item.code == "unsupported_request_retained"
        and item.request_id == request_ids["unsupported"]
        for item in compilation.validation.issues
    )

    ignored_semantics = {
        "identity",
        "preflight",
        "status-options",
        "project-options",
        "team-options",
        "work-type-options",
        "approver-options-1",
        "approver-options-2",
        "support",
        "resource",
        "unsupported",
    }
    assert not membership & {request_ids[item] for item in ignored_semantics}

    submit_capability = next(
        item
        for item in compilation.capabilities
        if request_ids["submit"] in item.request_ids
    )
    assert submit_capability.operation == "submit"
    assert submit_capability.request_ids == (request_ids["submit"],)
    assert all(
        "submit_batch" not in " ".join(
            (item.name, item.title, item.operation)
        ).casefold()
        for item in compilation.capabilities
    )
    array_field = next(
        item
        for item in compilation.field_facts
        if item.request_id == request_ids["submit"]
        and item.wire_name == result.scenario.variant.fields["entries"]
    )
    assert array_field.location is FieldLocation.BODY
    assert array_field.wire_schema.type == "array"

    # Same labels/wire names on search and submit remain distinct permanent
    # fields because request definition context, not display text, owns them.
    project_wire = result.scenario.variant.fields["project"]
    project_fields = [
        item
        for item in compilation.field_facts
        if item.wire_name == project_wire
        and item.request_id in {request_ids["query"], request_ids["submit"]}
    ]
    assert len(project_fields) == 2
    assert len(
        {
            result.contracts.field_uuids[item.field_contract_id]
            for item in project_fields
        }
    ) == 2

    for identity in (
        *result.contracts.step_uuids.values(),
        *result.contracts.capability_uuids.values(),
        *result.contracts.field_uuids.values(),
    ):
        assert str(UUID(identity)) == identity


def test_two_page_seventeen_request_fixture_is_lossless_and_generalizes() -> None:
    baseline_variant = _variant(
        name="oa",
        domain="api.oa-one.test",
        request_prefix="oa",
        action_prefix="oa-action",
    )
    replacement_variant = _variant(
        name="casehub",
        domain="edge.casehub-two.test",
        api_root="/gateway/v9",
        entity_id=730,
        request_prefix="case",
        action_prefix="case-action",
        framework="vue",
        labels={
            "bootstrap": "Load workspace",
            "search": "Find time reports",
            "detail": "Open report record",
            "progress": "Inspect workflow history",
            "options": "Resolve form choices",
            "support": "Check optimistic version",
            "submit": "Save work report",
            "withdraw": "Revoke work report",
            "resource": "Load shell resource",
            "unsupported": "Open event channel",
        },
        fields={
            "project": "initiativeTitle",
            "approver": "reviewerQuery",
            "date_from": "periodStart",
            "date_to": "periodEnd",
            "status": "workflowState",
            "project_id": "initiativeCode",
            "team_id": "squadCode",
            "work_type": "activityCode",
            "approver_id": "reviewerCode",
            "hours": "reportedUnits",
            "content": "workNote",
            "entries": "dailyLines",
        },
        route_names={
            "identity": "profile",
            "collection": "cases",
            "detail": "record",
            "progress": "report",
            "option_root": "lookups",
            "status": "states",
            "project": "initiatives",
            "team": "squads",
            "work_type": "activities",
            "approver": "reviewers",
            "validate": "check",
            "submit": "save",
            "withdraw": "revoke",
        },
        reversed_actions=True,
        redraw_generation=4,
        enum_revision=9,
    )

    baseline = _compile_scenario(_build_scenario(baseline_variant))
    replacement = _compile_scenario(_build_scenario(replacement_variant))
    _assert_lossless_business_fixture(baseline)
    _assert_lossless_business_fixture(replacement)

    assert baseline_variant.domain != replacement_variant.domain
    assert baseline_variant.api_root != replacement_variant.api_root
    assert baseline_variant.labels != replacement_variant.labels
    assert baseline_variant.fields != replacement_variant.fields
    assert baseline_variant.route_names != replacement_variant.route_names
    assert baseline_variant.framework != replacement_variant.framework
    assert baseline_variant.redraw_generation != replacement_variant.redraw_generation
    assert baseline_variant.enum_revision != replacement_variant.enum_revision
    assert _captured_request_order(baseline.scenario) != _captured_request_order(
        replacement.scenario
    )
    assert set(_controls_by_selector(baseline.scenario)) == set(
        _controls_by_selector(replacement.scenario)
    )
    assert set(_controls_by_selector(baseline.scenario).values()).isdisjoint(
        _controls_by_selector(replacement.scenario).values()
    )
    assert _request_fact(
        baseline.scenario, "approver-options-1"
    ).response_body != _request_fact(
        replacement.scenario, "approver-options-1"
    ).response_body
    assert _transaction_signature(baseline) == _transaction_signature(replacement)
    assert _equivalence_signature(baseline) == _equivalence_signature(replacement)


def test_reanalysis_and_recapture_preserve_lineage_owned_identities() -> None:
    lineage = uuid4()
    first_variant = _variant(
        name="capture-a",
        domain="api.capture-one.test",
        entity_id=42,
        request_prefix="first",
        action_prefix="first-action",
        framework="react",
    )
    recaptured_variant = _variant(
        name="capture-b",
        domain="regional.capture-two.test",
        entity_id=730,
        request_prefix="second",
        action_prefix="second-action",
        framework="svelte",
        labels={
            "bootstrap": "Bootstrap",
            "search": "Search records",
            "detail": "Read record",
            "progress": "Read progress",
            "options": "Refresh choices",
            "support": "Read version",
            "submit": "Submit report",
            "withdraw": "Withdraw report",
            "resource": "Fetch stylesheet",
            "unsupported": "Connect stream",
        },
        reversed_actions=True,
        redraw_generation=7,
        enum_revision=5,
    )

    first = _compile_scenario(
        _build_scenario(first_variant),
        lineage=lineage,
        generation=0,
        source_revision=0,
    )
    _assert_lossless_business_fixture(first)

    # Re-analysis of exactly the same immutable facts is idempotent.
    reanalysed = integrate_compilation_contracts(
        first.compilation,
        facts=first.scenario.facts,
        capture_store=first.store,
        field_registry=first.registry,
        value_evidence_factory=ValueEvidenceFactory(server_secret=SECRET),
    )
    assert reanalysed.request_definition_ids == first.contracts.request_definition_ids
    assert reanalysed.observation_ids == first.contracts.observation_ids
    assert reanalysed.field_uuids == first.contracts.field_uuids
    assert reanalysed.step_uuids == first.contracts.step_uuids
    assert reanalysed.capability_uuids == first.contracts.capability_uuids
    assert tuple(item.transaction_id for item in first.preliminary.transactions) == tuple(
        item.transaction_id for item in first.compilation.transactions
    )

    recapture_store = CaptureStore(
        tenant_scope=TENANT,
        recording_id=RECORDING,
        lineage_id=lineage,
        capture_generation=1,
    )
    recaptured = _compile_scenario(
        _build_scenario(recaptured_variant),
        lineage=lineage,
        store=recapture_store,
        registry=first.registry,
        generation=1,
        source_revision=1,
    )
    _assert_lossless_business_fixture(recaptured)

    first_request_ids = first.scenario.request_ids
    second_request_ids = recaptured.scenario.request_ids
    for semantic in first_request_ids:
        assert (
            first.contracts.request_definition_ids[first_request_ids[semantic]]
            == recaptured.contracts.request_definition_ids[second_request_ids[semantic]]
        )
        assert (
            first.contracts.step_uuids[first_request_ids[semantic]]
            == recaptured.contracts.step_uuids[second_request_ids[semantic]]
        )

    assert _capability_uuid_by_terminal(first) == _capability_uuid_by_terminal(recaptured)
    assert _transaction_signature(first) == _transaction_signature(recaptured)
    first_fields = _field_uuid_by_semantic_wire(first)
    recaptured_fields = _field_uuid_by_semantic_wire(recaptured)
    assert first_fields == recaptured_fields

    # The lineage registry owns bindings across capture generations, whereas
    # each CaptureStore owns observations only for its own generation.  A
    # repeated/paginated request therefore extends observation provenance on
    # the same binding instead of changing field or step identity.
    registry_bindings = recaptured.registry.snapshot().bindings
    for (semantic, _location, wire_path), field_uuid in first_fields.items():
        step_uuid = first.contracts.step_uuids[first_request_ids[semantic]]
        candidates = [
            item
            for item in registry_bindings
            if str(item.field_uuid) == field_uuid
            and str(item.step_uuid) == step_uuid
            and item.wire_path == wire_path
        ]
        assert len(candidates) == 1
        assert {
            first_request_ids[semantic],
            second_request_ids[semantic],
        } <= set(candidates[0].observation_ids)

    assert _captured_request_order(first.scenario) != _captured_request_order(
        recaptured.scenario
    )
    assert set(_controls_by_selector(first.scenario)) == set(
        _controls_by_selector(recaptured.scenario)
    )
    assert set(_controls_by_selector(first.scenario).values()).isdisjoint(
        _controls_by_selector(recaptured.scenario).values()
    )
    assert _request_fact(
        first.scenario, "approver-options-1"
    ).response_body != _request_fact(
        recaptured.scenario, "approver-options-1"
    ).response_body
    assert first.contracts.capture_store.capture_generation == 0
    assert recaptured.contracts.capture_store.capture_generation == 1
    assert {
        item.observation_id for item in first.contracts.capture_store.observations
    } == set(first_request_ids.values())
    assert {
        item.observation_id for item in recaptured.contracts.capture_store.observations
    } == set(second_request_ids.values())
    assert not (
        set(first_request_ids.values())
        & set(second_request_ids.values())
    )
