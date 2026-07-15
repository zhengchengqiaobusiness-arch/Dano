from __future__ import annotations

import shutil

import pytest

from dano_recording.domain.enums import (
    ChoiceEvidenceSource,
    ChoiceOption,
    EvidenceCompleteness,
)
from dano_recording.evidence.enum_extractor import EnumCandidate, EnumExtractor
from dano_recording.evidence.js_ast_worker import (
    JSAnalysisResult,
    JSStaticAnalyzer,
    StaticEnumCandidate,
)
from dano_recording.evidence.loaded_scripts import LoadedScript, LoadedScriptCollector
from dano_recording.evidence.provenance import EvidenceBinding, project_evidence_for_pi
from dano_recording.evidence.sourcemaps import (
    SourceMapFetchResult,
    SourceMapLoader,
    SourceMapStatus,
)


def test_enum_candidates_never_cross_field_boundaries() -> None:
    bindings = (
        EvidenceBinding(
            field_contract_id="field-status",
            control_id="control-status",
            request_id="request-submit",
            wire_path="body.status",
        ),
        EvidenceBinding(
            field_contract_id="field-priority",
            control_id="control-priority",
            request_id="request-submit",
            wire_path="body.priority",
        ),
    )
    status_options = (
        ChoiceOption(label="Open", value="open"),
        ChoiceOption(label="Closed", value="closed"),
    )
    extractor = EnumExtractor()
    result = extractor.resolve(
        [
            EnumCandidate(
                source_kind=ChoiceEvidenceSource.NATIVE_SELECT,
                options=status_options,
                control_id="control-status",
                completeness=EvidenceCompleteness.COMPLETE,
            ),
            # Similar static array but no proven control/wire association.
            EnumCandidate(
                source_kind=ChoiceEvidenceSource.SCRIPT_STATIC,
                options=status_options,
                symbol_path="priorityOptions",
            ),
        ],
        bindings,
    )
    assert len(result.evidence) == 1
    assert result.evidence[0].field_contract_id == "field-status"
    assert result.evidence[0].wire_path == "body.status"
    assert all(item.field_contract_id != "field-priority" for item in result.evidence)
    assert len(result.suggestions) == 1
    assert result.suggestions[0].confidence <= 0.25


def test_identity_enum_candidates_never_enter_evidence_or_suggestions() -> None:
    binding = EvidenceBinding(
        field_contract_id="field-approver",
        control_id="control-approver",
        request_id="request-submit",
        wire_path="body.approverId",
    )
    result = EnumExtractor().resolve(
        [
            EnumCandidate(
                source_kind=ChoiceEvidenceSource.RUNTIME_COMPONENT,
                options=(
                    ChoiceOption(label="Alice Zhang", value="user-7"),
                    ChoiceOption(label="alice@example.test", value="user-8"),
                ),
                control_id="control-approver",
                wire_path="body.approverId",
            )
        ],
        [binding],
    )
    assert result.evidence == ()
    assert result.suggestions == ()


def test_script_level_binding_is_not_applied_across_multiple_symbols() -> None:
    status_binding = EvidenceBinding(
        field_contract_id="field-status",
        control_id="control-status",
        request_id="request-submit",
        wire_path="body.status",
    )
    result = JSAnalysisResult(
        status="ok",
        script_url="https://example.test/app.js",
        script_hash="hash",
        candidates=(
            StaticEnumCandidate(
                symbol_path="statusOptions",
                options=(ChoiceOption(label="Open", value="open"),),
                completeness=EvidenceCompleteness.COMPLETE,
            ),
            StaticEnumCandidate(
                symbol_path="priorityOptions",
                options=(ChoiceOption(label="High", value="high"),),
                completeness=EvidenceCompleteness.COMPLETE,
            ),
        ),
    )
    extractor = EnumExtractor()

    unbound = extractor.resolve(
        extractor.from_static_analysis(result, binding=status_binding),
        [status_binding],
    )
    assert not unbound.evidence
    assert len(unbound.suggestions) == 2

    symbol_bound = extractor.resolve(
        extractor.from_static_analysis(
            result,
            symbol_bindings={"statusOptions": status_binding},
        ),
        [status_binding],
    )
    assert [item.symbol_path for item in symbol_bound.evidence] == ["statusOptions"]
    assert [item.symbol_path for item in symbol_bound.suggestions] == ["priorityOptions"]


@pytest.mark.asyncio
async def test_missing_sourcemap_degrades_without_fetch_or_exception() -> None:
    loader = SourceMapLoader()
    missing = loader.parse(None)
    assert missing.status is SourceMapStatus.MISSING

    script = LoadedScript(
        script_id="1",
        url="https://example.test/app.js",
        script_hash="abc",
        byte_size=10,
        inline=False,
        source_map_url=None,
        source="const x=1",
    )
    called = False

    async def fetcher(_: str):
        nonlocal called
        called = True
        raise AssertionError("fetcher must not be used without sourceMappingURL")

    result = await loader.load(script, fetcher=fetcher)
    assert result.status is SourceMapStatus.MISSING
    assert called is False


@pytest.mark.asyncio
async def test_external_sourcemap_requires_redirect_and_final_dns_proof() -> None:
    loader = SourceMapLoader()

    async def safe_target(_url: str) -> None:
        return None

    loader._validate_network_target = safe_target  # type: ignore[method-assign]
    script = LoadedScript(
        script_id="map-script",
        url="https://example.test/app.js",
        script_hash="abc",
        byte_size=10,
        inline=False,
        source_map_url="/app.js.map",
        source="",
    )
    body = '{"version":3,"sources":[],"names":[],"mappings":""}'

    raw = await loader.load(script, fetcher=lambda _url: body)
    assert raw.status is SourceMapStatus.BLOCKED
    assert "redirect boundary" in str(raw.error)

    redirected = await loader.load(
        script,
        fetcher=lambda _url: SourceMapFetchResult(
            body=body,
            final_url="https://evil.test/app.js.map",
            status=302,
            location="https://evil.test/app.js.map",
        ),
    )
    assert redirected.status is SourceMapStatus.BLOCKED

    loaded = await loader.load(
        script,
        fetcher=lambda _url: SourceMapFetchResult(
            body=body,
            final_url="https://example.test/app.js.map",
        ),
    )
    assert loaded.status is SourceMapStatus.LOADED


def test_sourcemap_and_loaded_script_projection_exclude_raw_code() -> None:
    loader = SourceMapLoader()
    evidence = loader.parse(
        '{"version":3,"sources":["src.ts"],"sourcesContent":["const secret = 1"],"names":[],"mappings":""}',
        map_url="https://example.test/app.js.map",
    )
    projection = project_evidence_for_pi(evidence)
    assert evidence.status is SourceMapStatus.LOADED
    assert "source_contents" not in projection
    assert "const secret" not in repr(projection)

    script = LoadedScript(
        script_id="1",
        url="https://example.test/app.js",
        script_hash="abc",
        byte_size=16,
        inline=False,
        source_map_url=None,
        source="const rawSecret = 1",
    )
    script_projection = project_evidence_for_pi(script)
    assert "source" not in script_projection
    assert "rawSecret" not in repr(script_projection)

    hostile = loader.parse(
        '{"version":3,"sources":["const RAW_JS=1"],"names":["return secret"],"mappings":""}'
    )
    hostile_projection = hostile.pi_projection()
    assert "const RAW_JS" not in repr(hostile_projection)
    assert "return secret" not in repr(hostile_projection)


@pytest.mark.asyncio
async def test_cdp_script_ids_are_scoped_per_target() -> None:
    collector = LoadedScriptCollector()
    first = await collector.add(
        target_id="target-a",
        script_id="1",
        url="https://example.test/a.js",
        source="const a = 1",
    )
    second = await collector.add(
        target_id="target-b",
        script_id="1",
        url="https://example.test/b.js",
        source="const b = 2",
    )
    assert first is not None and second is not None
    assert first.script_hash != second.script_hash
    assert len(collector.scripts) == 2
    await collector.close()


@pytest.mark.asyncio
async def test_js_worker_extracts_literals_without_returning_source() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is optional for Python unit-test environments")
    analyzer = JSStaticAnalyzer()
    source = "const options = [{label: 'One', value: 1}, {label: 'Two', value: 2}];"
    result = await analyzer.analyze(source, script_url="https://example.test/app.js")
    assert result.status == "ok"
    assert result.candidates
    assert [option.value for option in result.candidates[0].options] == [1, 2]
    assert "const options" not in repr(result.pi_projection())
