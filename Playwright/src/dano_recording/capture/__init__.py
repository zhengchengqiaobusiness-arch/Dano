"""Browser capture primitives for recording V3.

The package deliberately has no hard dependency on Playwright at import time.
Production code can attach the observers to Playwright objects while unit tests
and deterministic compilation can use the same fact ledger with light-weight
fakes.
"""

from dano_recording.capture.action_transactions import ActionTracker
from dano_recording.capture.browser_session import (
    BrowserLease,
    BrowserSession,
    BrowserSessionManager,
    PlaywrightBrowserHandle,
    SessionCapacityError,
    SessionManager,
    launch_persistent_context,
)
from dano_recording.capture.input_dispatcher import InputDispatcher
from dano_recording.capture.ledger import CaptureCapacityExceeded, FactLedger
from dano_recording.capture.network_observer import NetworkObserver, NetworkObserverConfig
from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.capture.safety import SafeRecordPolicy, UnsafeURL, URLSafetyPolicy
from dano_recording.capture.screencast import ScreenshotArtifact, ScreenshotCollector

__all__ = [
    "BrowserLease",
    "BrowserSession",
    "BrowserSessionManager",
    "CaptureCapacityExceeded",
    "CaptureRuntime",
    "ActionTracker",
    "FactLedger",
    "InputDispatcher",
    "NetworkObserver",
    "NetworkObserverConfig",
    "PlaywrightBrowserHandle",
    "RedactionPolicy",
    "SafeRecordPolicy",
    "SessionCapacityError",
    "SessionManager",
    "ScreenshotArtifact",
    "ScreenshotCollector",
    "URLSafetyPolicy",
    "UnsafeURL",
    "launch_persistent_context",
]


def __getattr__(name: str):
    # CaptureRuntime composes evidence collectors, while evidence modules reuse
    # capture primitives.  Resolve the composition root lazily so importing a
    # leaf module in a fresh process cannot create a package cycle.
    if name == "CaptureRuntime":
        from dano_recording.capture.runtime import CaptureRuntime

        return CaptureRuntime
    raise AttributeError(name)
