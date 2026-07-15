from __future__ import annotations

from dano_recording.compiler.client_projection import compilation_to_workbench
from dano_recording.compiler.pipeline import compile_recording
from dano_recording.domain.facts import ActionFact, RequestFact
from dano_recording.domain.recording import RecordingSession


def test_compilation_projects_to_existing_workbench_shape() -> None:
    facts = (
        ActionFact(
            tenant="tenant-a",
            recording_id="rec-a",
            sequence=0,
            action_id="submit",
            action_type="click",
            label="提交申请",
            payload={
                "evidence_origin": "server_dispatched",
                "causal_eligible": True,
            },
        ),
        RequestFact(
            tenant="tenant-a",
            recording_id="rec-a",
            sequence=1,
            action_id="submit",
            request_id="request-a",
            method="POST",
            url="https://oa.example/api/applications?dry=false",
            request_body={"reason": "出差"},
            request_body_present=True,
            response_status=200,
            response_body={"id": "application-1"},
        ),
        RequestFact(
            tenant="tenant-a",
            recording_id="rec-a",
            sequence=2,
            action_id="submit",
            request_id="options-a",
            method="GET",
            url="https://oa.example/api/options/types",
            response_status=200,
            response_body=[{"label": "差旅", "value": 2}],
        ),
    )
    compilation = compile_recording(
        tenant="tenant-a", recording_id="rec-a", facts=facts, source_revision=3
    )
    session = RecordingSession(
        tenant="tenant-a",
        recording_id="rec-a",
        current_revision=3,
        metadata={"subsystem": "OA", "title": "差旅申请录制"},
    )

    full_spec = compilation_to_workbench(compilation, session)

    required = {
        "flow_id", "tenant", "subsystem", "title", "risk_level",
        "schema_version", "steps", "links", "capabilities",
        "capability_relations", "request_facts", "meta", "revision",
    }
    assert required <= full_spec.keys()
    assert full_spec["flow_id"] == "rec-a"
    assert full_spec["subsystem"] == "OA"
    assert full_spec["title"] == "差旅申请录制"
    assert full_spec["revision"] == 3
    assert full_spec["schema_version"].startswith("recording-v3")
    assert len(full_spec["steps"]) == 1
    assert full_spec["steps"][0]["request_id"] == "request-a"
    assert {row["key"] for row in full_spec["steps"][0]["params"]} == {"dry", "reason"}
    assert len(full_spec["capabilities"]) == 1
    assert len(full_spec["request_facts"]["requests"]) == 2
    option_row = next(
        row for row in full_spec["request_facts"]["requests"]
        if row["request_id"] == "options-a"
    )
    assert option_row["role"] == "option_source"
    assert option_row["response_schema"]["type"] == "array"
    assert full_spec["meta"]["recording_engine"] == "playwright_v3"
