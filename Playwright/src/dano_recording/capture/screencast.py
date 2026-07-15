"""Bounded screenshot artifacts without embedding binary data in facts or Pi."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dano_recording.capture.ledger import FactLedger
from dano_recording.domain._base import new_id
from dano_recording.domain.facts import FactKind, RecordingFact


ScreenshotStore = Callable[[str, bytes], str | Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ScreenshotArtifact:
    screenshot_id: str
    page_id: str
    sha256: str
    byte_size: int
    mime_type: str
    storage_reference: str | None
    data: bytes | None = field(default=None, repr=False, compare=False)

    def pi_projection(self) -> dict[str, Any]:
        return {
            "screenshot_id": self.screenshot_id,
            "page_id": self.page_id,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "mime_type": self.mime_type,
        }


class ScreenshotCollector:
    def __init__(
        self,
        ledger: FactLedger,
        *,
        store: ScreenshotStore | None = None,
        max_bytes: int = 10_485_760,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.ledger = ledger
        self.store = store
        self.max_bytes = max_bytes

    async def capture(
        self,
        page: Any,
        *,
        page_id: str,
        full_page: bool = False,
        image_type: str = "png",
    ) -> ScreenshotArtifact:
        if image_type not in {"png", "jpeg"}:
            raise ValueError("image_type must be png or jpeg")
        result = page.screenshot(type=image_type, full_page=full_page)
        raw = await result if inspect.isawaitable(result) else result
        data = bytes(raw)
        if len(data) > self.max_bytes:
            self.ledger.emit(
                RecordingFact,
                kind=FactKind.DIAGNOSTIC,
                page_id=page_id,
                payload={
                    "type": "screenshot_capacity",
                    "byte_size": len(data),
                    "max_bytes": self.max_bytes,
                },
                redacted=True,
            )
            raise ValueError(f"screenshot exceeds {self.max_bytes} bytes")
        digest = hashlib.sha256(data).hexdigest()
        reference: str | None = None
        if self.store is not None:
            stored = self.store(f"{self.ledger.tenant}/{digest}", data)
            resolved = await stored if inspect.isawaitable(stored) else stored
            if not resolved:
                raise ValueError("screenshot store returned an empty reference")
            reference = str(resolved)
            retained_data: bytes | None = None
        else:
            retained_data = data
        artifact = ScreenshotArtifact(
            screenshot_id=new_id(),
            page_id=page_id,
            sha256=digest,
            byte_size=len(data),
            mime_type="image/png" if image_type == "png" else "image/jpeg",
            storage_reference=reference,
            data=retained_data,
        )
        self.ledger.emit(
            RecordingFact,
            kind=FactKind.PAGE,
            page_id=page_id,
            payload={
                "event": "screenshot",
                **artifact.pi_projection(),
                "storage_reference": reference,
            },
            redacted=True,
        )
        return artifact
