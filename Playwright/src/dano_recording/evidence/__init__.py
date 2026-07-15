"""DOM, runtime, script, SourceMap, and enum evidence collection."""

from dano_recording.evidence.dom_controls import DOMControl, DOMControlCollector
from dano_recording.evidence.enum_extractor import (
    EnumCandidate,
    EnumExtractionResult,
    EnumExtractor,
)
from dano_recording.evidence.js_ast_worker import JSAnalysisResult, JSStaticAnalyzer
from dano_recording.evidence.loaded_scripts import LoadedScript, LoadedScriptCollector
from dano_recording.evidence.provenance import (
    EnumSuggestion,
    EvidenceBinding,
    EvidenceRegistry,
    project_evidence_for_pi,
)
from dano_recording.evidence.runtime_components import RuntimeComponentClue, RuntimeComponentCollector
from dano_recording.evidence.sourcemaps import (
    SourceMapEvidence,
    SourceMapFetchResult,
    SourceMapLoader,
    SourceMapStatus,
)

__all__ = [
    "DOMControl",
    "DOMControlCollector",
    "EnumCandidate",
    "EnumExtractionResult",
    "EnumExtractor",
    "EnumSuggestion",
    "EvidenceBinding",
    "EvidenceRegistry",
    "JSAnalysisResult",
    "JSStaticAnalyzer",
    "LoadedScript",
    "LoadedScriptCollector",
    "RuntimeComponentClue",
    "RuntimeComponentCollector",
    "SourceMapEvidence",
    "SourceMapFetchResult",
    "SourceMapLoader",
    "SourceMapStatus",
    "project_evidence_for_pi",
]
