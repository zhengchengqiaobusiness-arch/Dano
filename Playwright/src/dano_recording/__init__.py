"""Dano recording v3.

The package deliberately owns only recording-v3 concepts.  It must not import
the legacy page recorder/compiler or the Python LLM clients.
"""

from dano_recording.compiler.pipeline import compile_recording
from dano_recording.capture_store import CaptureStore
from dano_recording.field_registry import FieldRegistry
from dano_recording.flow_migration import FlowMigrator
from dano_recording.value_evidence import ValueEvidence, ValueEvidenceFactory

__all__ = [
    "CaptureStore",
    "FieldRegistry",
    "FlowMigrator",
    "ValueEvidence",
    "ValueEvidenceFactory",
    "compile_recording",
]
__version__ = "0.1.0"
