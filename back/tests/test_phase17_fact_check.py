"""M3 验收:事实核查一等公民(声明 fact_check 就必须过,堵死"操作成功但空操作")。

- 引擎:轮询直到为真;始终为假则失败;端点/参数模板按 context 渲染;
- 适配器闸门:adapter 跑通沙箱 + 成败规则,但 fact_check 回查为空 → 沙箱判失败(疑似空操作);
  回查确认生效 → 通过。确定性:注入 fake 调用器,无需真实系统。
"""

from __future__ import annotations

import os

import pytest

from dano.execution.fact_check import run_fact_check
from dano.shared.asset_bodies import FactCheckSpec

BACK_DSN = os.environ.get("DANO_PG_DSN", "postgresql://postgres:111111@localhost:5432/dano_back")


# ── 引擎单测(纯)──
class _SeqCaller:
    """按预设序列返回 total(模拟异步:前几次还查不到,后面才出现)。"""
    def __init__(self, totals): self.totals = totals; self.calls = []
    async def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        t = self.totals[min(len(self.calls) - 1, len(self.totals) - 1)]
        return 200, {"code": 200, "total": t}


async def test_fact_check_polls_until_true():
    spec = FactCheckSpec(endpoint="/q", assert_expr="response.total > 0", retries=3, backoff_s=0.0)
    caller = _SeqCaller([0, 0, 1])
    ok, ev = await run_fact_check(spec, context={}, call=caller)
    assert ok is True and ev["attempts"] == 3


async def test_fact_check_fails_when_never_true():
    spec = FactCheckSpec(endpoint="/q", assert_expr="response.total > 0", retries=3, backoff_s=0.0)
    ok, ev = await run_fact_check(spec, context={}, call=_SeqCaller([0, 0, 0]))
    assert ok is False and ev["attempts"] == 3


async def test_fact_check_renders_placeholders():
    cap = {}
    async def caller(method, path, body=None):  # noqa: ANN001
        cap["path"] = path
        return 200, {"total": 1}
    spec = FactCheckSpec(endpoint="/wf/{procInsId}", method="GET",
                         params_template={"deployId": "{deployId}"},
                         assert_expr="response.total > 0", retries=1)
    ok, _ = await run_fact_check(spec, context={"procInsId": "42", "deployId": "7"}, call=caller)
    assert ok and cap["path"].startswith("/wf/42") and "deployId=7" in cap["path"]


# ── 适配器闸门(PG)──
@pytest.fixture()
async def _pg():
    os.environ["DANO_PG_DSN"] = BACK_DSN
    from dano.config import get_settings
    get_settings.cache_clear()
    from dano.infra.db import close_pool, get_pool, init_pool, run_migrations
    try:
        await init_pool()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用: {e}")
    await run_migrations()
    async with get_pool().acquire() as c:
        await c.execute("DELETE FROM assets WHERE tenant='gen3'")
    yield
    await close_pool()


def _fake_caller_factory(total: int):
    def factory(mat):  # noqa: ANN001
        async def call(method, path, body=None):  # noqa: ANN001
            return 200, {"code": 200, "total": total}
        return call
    return factory


async def _draft_with_factcheck(run_id: str):
    from dano.agent_tools import materials, tools as T
    materials.register(materials.MaterialContext(
        run_id=run_id, tenant="gen3", system_instance_id="A-OA", subsystem="A-OA",
        openapi={}, deploy={"base_url": "http://x", "auth": {"kind": "token"}},
        credentials={"token": "t"}))
    body = {
        "action": "submit_thing", "strategy": "workflow_bpmn",
        "source": "def run(inputs, creds):\n    return {'code': 200, 'procInsId': '99'}\n",
        "entry": "run", "success_rule": "response.code == 200",
        "fact_check": {"endpoint": "/list", "assert_expr": "response.total > 0", "retries": 2,
                       "backoff_s": 0.0},
    }
    d = await T.draft_adapter(run_id, {"system_instance_id": "A-OA", **body})
    return d["asset_draft_id"]


async def test_adapter_factcheck_fails_on_noop(_pg):
    from dano.agent_tools import tools as T
    did = await _draft_with_factcheck("gen3-noop")
    T.set_adapter_caller(_fake_caller_factory(total=0))     # 回查为空 = 空操作
    try:
        res = await T.sandbox_test_adapter("gen3-noop", {"asset_draft_id": did, "test_input": {}})
    finally:
        T.set_adapter_caller(None)
    assert res["passed"] is False
    assert any("事实核查" in r for r in res["reasons"]), res["reasons"]


async def test_adapter_factcheck_passes_when_effected(_pg):
    from dano.agent_tools import tools as T
    did = await _draft_with_factcheck("gen3-ok")
    T.set_adapter_caller(_fake_caller_factory(total=1))     # 回查到 = 真生效
    try:
        res = await T.sandbox_test_adapter("gen3-ok", {"asset_draft_id": did, "test_input": {}})
    finally:
        T.set_adapter_caller(None)
    assert res["passed"] is True, res["reasons"]
