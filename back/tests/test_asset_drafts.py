from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from dano.assets import drafts as drafts_module
from dano.assets.drafts import DraftStore
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope


class _Connection:
    def __init__(self) -> None:
        self.arguments: tuple[object, ...] | None = None

    async def fetchrow(self, _query: str, *arguments: object) -> dict[str, object]:
        self.arguments = arguments
        return {
            "asset_draft_id": uuid4(),
            "run_id": arguments[0],
            "tenant": arguments[1],
            "subsystem": arguments[2],
            "asset_type": arguments[3],
            "asset_key": arguments[4],
            "body": arguments[5],
            "content_hash": arguments[6],
            "created_at": datetime.now(timezone.utc),
        }


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_save_draft_makes_nested_json_safe_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    monkeypatch.setattr(drafts_module, "get_pool", lambda: _Pool(connection))

    draft = await DraftStore().save_draft(
        run_id="recording\x00test",
        scope=Scope(tenant="tenant\x00", subsystem=Subsystem("system\x00")),
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key="action\x00test",
        body={"response": {"bad\x00key": "before\x00after", "surrogate": "bad\ud800text"}},
    )

    assert connection.arguments is not None
    assert connection.arguments[:5] == (
        "recording\ufffdtest",
        "tenant\ufffd",
        "system\ufffd",
        AssetType.PAGE_SCRIPT.value,
        "action\ufffdtest",
    )
    persisted = json.loads(str(connection.arguments[5]))
    assert persisted == {
        "response": {
            "bad\ufffdkey": "before\ufffdafter",
            "surrogate": "bad\ufffdtext",
        },
    }
    assert draft.body == persisted
