"""Isolated static JavaScript analysis worker client."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.domain.enums import ChoiceOption, EvidenceCompleteness

if TYPE_CHECKING:
    from dano_recording.evidence.sourcemaps import SourceMapEvidence


@dataclass(frozen=True, slots=True)
class StaticEnumCandidate:
    symbol_path: str
    options: tuple[ChoiceOption, ...]
    completeness: EvidenceCompleteness
    proofs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JSAnalysisResult:
    status: str
    script_url: str
    script_hash: str | None
    candidates: tuple[StaticEnumCandidate, ...] = ()
    error: str | None = None

    def pi_projection(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "script_url": self.script_url,
            "script_hash": self.script_hash,
            "candidates": [
                {
                    "symbol_path": candidate.symbol_path,
                    "options": [option.model_dump(mode="json") for option in candidate.options],
                    "completeness": candidate.completeness.value,
                    "proofs": list(candidate.proofs),
                }
                for candidate in self.candidates
            ],
            # Parser output can echo attacker-controlled source. Keep detailed
            # diagnostics local and expose only a stable category to Pi.
            "error": "analysis_error" if self.error else None,
        }


class JSStaticAnalyzer:
    """Send source as inert input to a worker that never evals or imports it."""

    def __init__(
        self,
        *,
        node_binary: str = "node",
        worker_path: str | Path | None = None,
        timeout_seconds: float = 10.0,
        max_source_bytes: int = 5_242_880,
        max_output_bytes: int = 2_097_152,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        self.node_binary = node_binary
        self.worker_path = (
            Path(worker_path)
            if worker_path
            else Path(__file__).resolve().parents[1]
            / "_resources"
            / "js_analysis"
            / "worker.mjs"
        )
        self.timeout_seconds = timeout_seconds
        self.max_source_bytes = max_source_bytes
        self.max_output_bytes = max_output_bytes
        self.redaction = redaction or RedactionPolicy()

    async def analyze(
        self,
        source: str,
        *,
        script_url: str = "",
        script_hash: str | None = None,
    ) -> JSAnalysisResult:
        raw = source.encode("utf-8", errors="replace")
        safe_url = self.redaction.redact_url(script_url) if script_url else ""
        if len(raw) > self.max_source_bytes:
            return JSAnalysisResult(
                status="too_large",
                script_url=safe_url,
                script_hash=script_hash,
                error=f"script exceeds {self.max_source_bytes} bytes",
            )
        request = json.dumps(
            {"id": "analyze", "source": source, "script_url": safe_url},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        try:
            process = await asyncio.create_subprocess_exec(
                self.node_binary,
                str(self.worker_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            return JSAnalysisResult(
                status="unavailable",
                script_url=safe_url,
                script_hash=script_hash,
                error=self.redaction.redact_text(str(exc)),
            )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request), timeout=self.timeout_seconds
            )
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
            return JSAnalysisResult(
                status="timeout",
                script_url=safe_url,
                script_hash=script_hash,
                error="static analysis timed out",
            )
        except asyncio.CancelledError:
            # Cancellation of a recording must not orphan a Node worker.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.gather(process.communicate(), return_exceptions=True)
            raise
        if len(stdout) > self.max_output_bytes:
            return JSAnalysisResult(
                status="invalid",
                script_url=safe_url,
                script_hash=script_hash,
                error="static analysis output exceeded capacity",
            )
        if process.returncode != 0:
            return JSAnalysisResult(
                status="error",
                script_url=safe_url,
                script_hash=script_hash,
                error=self.redaction.redact_text(
                    stderr.decode("utf-8", errors="replace")[:2000]
                ),
            )
        try:
            payload = json.loads(stdout.splitlines()[0])
        except (json.JSONDecodeError, IndexError, UnicodeDecodeError) as exc:
            return JSAnalysisResult(
                status="invalid",
                script_url=safe_url,
                script_hash=script_hash,
                error=self.redaction.redact_text(f"invalid worker output: {exc}"),
            )
        candidates: list[StaticEnumCandidate] = []
        for raw_candidate in payload.get("candidates") or []:
            if not isinstance(raw_candidate, dict):
                continue
            symbol_path = str(raw_candidate.get("symbol_path") or "")
            if self.redaction.is_sensitive_key(symbol_path.rsplit(".", 1)[-1]):
                continue
            options = tuple(
                ChoiceOption(
                    label=self.redaction.redact_text(str(option.get("label") or "")),
                    value=self.redaction.redact_value(option.get("value")),
                    disabled=bool(option.get("disabled")),
                )
                for option in (raw_candidate.get("options") or [])
                if isinstance(option, dict)
            )
            if not options:
                continue
            try:
                completeness = EvidenceCompleteness(
                    raw_candidate.get("completeness") or EvidenceCompleteness.PARTIAL
                )
            except ValueError:
                completeness = EvidenceCompleteness.PARTIAL
            candidates.append(
                StaticEnumCandidate(
                    symbol_path=symbol_path,
                    options=options,
                    completeness=completeness,
                    proofs=tuple(str(item) for item in (raw_candidate.get("proofs") or ())),
                )
            )
        return JSAnalysisResult(
            status=str(payload.get("status") or "ok"),
            script_url=safe_url,
            script_hash=script_hash,
            candidates=tuple(candidates),
            error=(
                self.redaction.redact_text(str(payload["error"]))
                if payload.get("error")
                else None
            ),
        )

    async def analyze_sourcemap(
        self,
        source_map: "SourceMapEvidence",
        *,
        script_hash: str | None = None,
        max_sources: int = 50,
    ) -> tuple[JSAnalysisResult, ...]:
        """Statically analyse bounded ``sourcesContent`` entries, if present."""

        results: list[JSAnalysisResult] = []
        for index, (source_url, content) in enumerate(zip(
            source_map.sources[:max_sources],
            source_map.source_contents[:max_sources],
            strict=False,
        )):
            if content is None:
                continue
            source_id = hashlib.sha256(source_url.encode("utf-8", errors="replace")).hexdigest()[:16]
            results.append(
                await self.analyze(
                    content,
                    script_url=f"sourcemap-source:{index}:{source_id}",
                    script_hash=script_hash,
                )
            )
        return tuple(results)
