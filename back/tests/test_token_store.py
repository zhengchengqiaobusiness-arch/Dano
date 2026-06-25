"""运行期 token 存储(落 PG):纯函数 + 用 fake 连接池验存取/打码/合并/覆盖/刷新(离线,不需真 DB)。"""
from __future__ import annotations

import datetime as _dt
import json

import pytest

from dano.infra import token_store as ts


# ── fake asyncpg 连接池:内存模拟 runtime_token 表的 upsert / select ──
class _FakePool:
    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchrow(self, sql, *args):
        if sql.strip().lower().startswith("insert"):
            tenant, subsystem, headers_json, source = args
            row = {"tenant": tenant, "subsystem": subsystem, "headers": headers_json,
                   "source": source, "updated_at": _dt.datetime(2026, 6, 25, tzinfo=_dt.timezone.utc)}
            self.rows[(tenant, subsystem)] = row
            return row
        tenant, subsystem = args                       # SELECT
        return self.rows.get((tenant, subsystem))


@pytest.fixture(autouse=True)
def _fake_pool(monkeypatch):
    pool = _FakePool()
    monkeypatch.setattr(ts, "_pool_or_none", lambda: pool)
    return pool


# ───────── 纯函数(无 DB) ─────────
def test_mask_headers_masks_secrets_keeps_plain():
    masked = ts.mask_headers({"Authorization": "Bearer 4d6f99934475420b9e85ca43029f672f",
                              "satoken": "abcd1234efgh", "Tenant-Id": "1"})
    assert masked["Tenant-Id"] == "1"
    assert masked["Authorization"].startswith("Bearer 4d6f")
    assert masked["Authorization"].endswith("672f")
    assert "*" in masked["Authorization"] and "9993" not in masked["Authorization"]
    assert "*" in masked["satoken"]


def test_mask_short_secret_fully_starred():
    assert ts.mask_headers({"token": "abcd"})["token"] == "****"


def test_headers_from_api_request_single_and_workflow():
    assert ts.headers_from_api_request({"auth_headers": {"Authorization": "Bearer a"}}) == {"Authorization": "Bearer a"}
    wf = {"steps": [{"auth_headers": {"Authorization": "Bearer first"}},
                    {"auth_headers": {"Authorization": "Bearer last", "Tenant-Id": "1"}}]}
    assert ts.headers_from_api_request(wf) == {"Authorization": "Bearer last", "Tenant-Id": "1"}
    assert ts.headers_from_api_request({}) == {}


def test_merge_auth_headers_single_overrides_and_keeps_others():
    apir = {"method": "POST", "path": "/x", "auth_headers": {"Authorization": "Bearer OLD", "Tenant-Id": "1"}}
    out = ts.merge_auth_headers(apir, {"Authorization": "Bearer NEW"})
    assert out["auth_headers"] == {"Authorization": "Bearer NEW", "Tenant-Id": "1"}
    assert apir["auth_headers"]["Authorization"] == "Bearer OLD"        # 不改原对象


def test_merge_auth_headers_overrides_every_workflow_step():
    apir = {"steps": [{"auth_headers": {"Authorization": "Bearer OLD1"}},
                      {"auth_headers": {"Authorization": "Bearer OLD2", "Tenant-Id": "1"}}],
            "auth_headers": {"Authorization": "Bearer OLDTOP"}}
    out = ts.merge_auth_headers(apir, {"Authorization": "Bearer NEW"})
    assert out["auth_headers"]["Authorization"] == "Bearer NEW"
    assert all(s["auth_headers"]["Authorization"] == "Bearer NEW" for s in out["steps"])
    assert out["steps"][1]["auth_headers"]["Tenant-Id"] == "1"


def test_merge_auth_headers_empty_override_is_noop_copy():
    apir = {"auth_headers": {"Authorization": "Bearer a"}}
    out = ts.merge_auth_headers(apir, {})
    assert out == apir and out is not apir


# ───────── 存储(fake PG) ─────────
async def test_save_then_get_roundtrip():
    rec = await ts.save_token("aaa", "A-OA", {"Authorization": "Bearer abc123", "Tenant-Id": "1"})
    assert rec and rec["source"] == "recording" and rec["updated_at"]
    assert await ts.get_token_headers("aaa", "A-OA") == {"Authorization": "Bearer abc123", "Tenant-Id": "1"}


async def test_get_missing_returns_empty():
    assert await ts.get_token("aaa", "NONE") is None
    assert await ts.get_token_headers("aaa", "NONE") == {}


async def test_save_drops_empty_values_and_noop_when_all_empty():
    rec = await ts.save_token("aaa", "A-OA", {"Authorization": "Bearer x", "Empty": ""})
    assert rec["headers"] == {"Authorization": "Bearer x"}
    assert await ts.save_token("aaa", "A-OA", {"Empty": "", "Z": None}) is None


async def test_headers_stored_as_json_and_parsed_back():
    await ts.save_token("aaa", "A-OA", {"Authorization": "Bearer 你好"})
    rec = await ts.get_token("aaa", "A-OA")
    assert rec["headers"] == {"Authorization": "Bearer 你好"}     # JSONB 往返 + 中文


async def test_manual_refresh_merges_with_existing():
    await ts.save_token("aaa", "A-OA", {"Authorization": "Bearer OLD", "Tenant-Id": "1"}, source="recording")
    merged = {**(await ts.get_token_headers("aaa", "A-OA")), "Authorization": "Bearer NEW"}
    await ts.save_token("aaa", "A-OA", merged, source="manual")
    rec = await ts.get_token("aaa", "A-OA")
    assert rec["headers"] == {"Authorization": "Bearer NEW", "Tenant-Id": "1"}
    assert rec["source"] == "manual"


async def test_no_pool_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(ts, "_pool_or_none", lambda: None)     # DB 不可用
    assert await ts.save_token("aaa", "A-OA", {"Authorization": "Bearer x"}) is None
    assert await ts.get_token("aaa", "A-OA") is None
    assert await ts.get_token_headers("aaa", "A-OA") == {}
