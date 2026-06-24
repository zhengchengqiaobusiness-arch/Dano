"""流程8 页面型 Skill:数据模型派生 + 运行期解释器(M1+M2)。

纯离线:注入 FakePageDriver(零浏览器依赖)+ 内存 store,直接驱动
PageActionRuntime 与 Orchestrator._run_page。不碰 PG / 网络 / playwright。
"""
from __future__ import annotations

from uuid import uuid4

from dano.catalog.manifest import to_manifest
from dano.execution.page import FakePageDriver, PageActionRuntime
from dano.orchestrator.orchestrator import Orchestrator
from dano.orchestrator.skills import SkillRegistry
from dano.orchestrator.types import SkillSpec
from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import AssetType, Outcome, RiskLevel, Subsystem, TaskState


class _Env:
    def __init__(self, body: dict, asset_id) -> None:  # noqa: ANN001
        self.body, self.asset_id = body, asset_id


class _PageStore:
    """只装一个已发布页面脚本;其余资产类型返回空,确保编排只走页面分支。"""

    def __init__(self, body: dict, asset_id) -> None:  # noqa: ANN001
        self._body, self._aid = body, asset_id

    async def list_published(self, asset_type, scope):  # noqa: ANN001
        return [_Env(self._body, self._aid)] if asset_type == AssetType.PAGE_SCRIPT else []

    async def get(self, asset_id):  # noqa: ANN001
        return _Env(self._body, self._aid) if asset_id == self._aid else None

    async def get_published(self, asset_type, scope, *, asset_key=None):  # noqa: ANN001
        return None


def _page_body(**kw) -> dict:  # noqa: ANN003
    base = dict(
        actions=[
            PageAction(op="fill", locator="label=金额", value_from="field:amount", assert_visible=True),
            PageAction(op="select", locator="label=类别", value_from="field:category"),
        ],
        dom_fingerprint="fp-v1",
        action="submit_reimburse", title="提交报销草稿",
        success_marker="text=保存成功",
        user_fields=["amount", "category"], required_fields=["amount", "category"],
        risk_level=RiskLevel.L2,
    )
    base.update(kw)
    return PageScriptBody(**base).model_dump()


def _skill(asset_id, *, risk=RiskLevel.L2) -> SkillSpec:  # noqa: ANN001
    return SkillSpec(skill_id="A-报销.submit_reimburse", subsystem=Subsystem.REIMBURSE,
                     action="submit_reimburse", risk_level=risk, has_api=False,
                     page_asset_id=asset_id, required_fields=["amount", "category"])


def _orch(body: dict, asset_id, fake: FakePageDriver, *, skill: SkillSpec) -> Orchestrator:  # noqa: ANN001
    return Orchestrator(
        registry=SkillRegistry([skill]), store=_PageStore(body, asset_id), harness=object(),
        action_executor=object(), page_runtime=PageActionRuntime(lambda: fake),
    )


# ───────────────────────── M1:数据模型派生 ─────────────────────────
async def test_registry_derives_page_skill_from_body() -> None:
    aid = uuid4()
    reg = await SkillRegistry.from_store(_PageStore(_page_body(), aid), tenant="t",
                                         subsystems=[Subsystem.REIMBURSE])
    skill = reg.by_action(Subsystem.REIMBURSE, "submit_reimburse")
    assert skill is not None
    assert skill.has_api is False
    assert skill.page_asset_id == aid
    assert skill.risk_level == RiskLevel.L2
    assert skill.required_fields == ["amount", "category"]
    assert to_manifest(skill).integration == "page"


async def test_registry_backward_compat_old_page_body() -> None:
    """旧脚本(无 action 字段)仍按子系统兜底派生,行为与加 Playwright 前一致。"""
    aid = uuid4()
    old = {"actions": [{"op": "click", "locator": "text=提交"}], "dom_fingerprint": "fp"}
    reg = await SkillRegistry.from_store(_PageStore(old, aid), tenant="t",
                                         subsystems=[Subsystem.REIMBURSE])
    skill = reg.by_action(Subsystem.REIMBURSE, "create_reimburse_draft")
    assert skill is not None and skill.has_api is False and skill.risk_level == RiskLevel.L2


async def test_legacy_page_body_arbitrary_system_neutral_action() -> None:
    """P3:任意系统的旧页面脚本不再臆造成报销动作,派生中性 `submit_<系统key>`。"""
    aid = uuid4()
    old = {"actions": [{"op": "click", "locator": "text=提交"}], "dom_fingerprint": "fp"}
    reg = await SkillRegistry.from_store(_PageStore(old, aid), tenant="t",
                                         subsystems=[Subsystem("B-门户")])
    assert reg.by_action(Subsystem("B-门户"), "create_reimburse_draft") is None
    assert reg.by_action(Subsystem("B-门户"), "submit_门户") is not None


# ───────────────────────── M2:运行期解释器(直接驱动)─────────────────────────
async def test_runtime_passed() -> None:
    fake = FakePageDriver(fingerprint="fp-v1")
    rt = PageActionRuntime(lambda: fake)
    res = await rt.run(uuid4(), PageScriptBody(**_skill_body()), {"amount": "100", "category": "差旅"},
                       confirm=lambda f: True)
    assert res.outcome == Outcome.PASSED
    assert res.structured_output.get("success_marker") is True
    assert res.evidence.response_body is not None and res.evidence.screenshots
    # 字段绑定真生效:label=金额 被填入 amount 值
    assert ("fill", "label=金额", "100") in fake.ops


async def test_runtime_drift() -> None:
    fake = FakePageDriver(fingerprint="fp-CHANGED")   # 页面结构变了
    rt = PageActionRuntime(lambda: fake)
    res = await rt.run(uuid4(), PageScriptBody(**_skill_body()), {"amount": "1"}, confirm=lambda f: True)
    assert res.outcome == Outcome.FAILED
    assert res.structured_output.get("drift") is True


async def test_runtime_cancelled_at_submit() -> None:
    body = _skill_body(actions=[
        PageAction(op="fill", locator="label=金额", value_from="field:amount"),
        PageAction(op="submit", locator="role=button[name=提交]"),
    ])
    fake = FakePageDriver(fingerprint="fp-v1")
    rt = PageActionRuntime(lambda: fake)
    res = await rt.run(uuid4(), PageScriptBody(**body), {"amount": "1"}, confirm=lambda f: False)
    assert res.structured_output.get("cancelled") is True
    # 取消发生在提交前:不应执行 submit 的 click
    assert not any(o[0] == "click" for o in fake.ops)


async def test_runtime_failed_element_missing() -> None:
    fake = FakePageDriver(fingerprint="fp-v1", fail_locators=["label=金额"])  # 必填步元素找不到
    rt = PageActionRuntime(lambda: fake)
    res = await rt.run(uuid4(), PageScriptBody(**_skill_body()), {"amount": "1"}, confirm=lambda f: True)
    assert res.outcome == Outcome.FAILED
    assert res.structured_output.get("failed_step") == 0


# ───────────────────────── M2:经 Orchestrator 端到端 ─────────────────────────
async def test_invoke_page_skill_completed() -> None:
    aid = uuid4()
    fake = FakePageDriver(fingerprint="fp-v1")
    orch = _orch(_page_body(), aid, fake, skill=_skill(aid))
    out = await orch.invoke_skill(Subsystem.REIMBURSE, "submit_reimburse",
                                  {"amount": "100", "category": "差旅"})
    assert out.state == TaskState.COMPLETED


async def test_invoke_page_skill_drift_state() -> None:
    aid = uuid4()
    fake = FakePageDriver(fingerprint="fp-CHANGED")
    orch = _orch(_page_body(), aid, fake, skill=_skill(aid))
    out = await orch.invoke_skill(Subsystem.REIMBURSE, "submit_reimburse",
                                  {"amount": "1", "category": "差旅"})
    assert out.state == TaskState.DRIFT


async def test_invoke_page_write_gated_without_confirm() -> None:
    """L3 写页面:不带 confirm 必须被闸门拦成 CANCELLED(页面写默认 L3,与铁律③一致)。"""
    aid = uuid4()
    fake = FakePageDriver(fingerprint="fp-v1")
    orch = _orch(_page_body(risk_level=RiskLevel.L3), aid, fake, skill=_skill(aid, risk=RiskLevel.L3))
    out = await orch.invoke_skill(Subsystem.REIMBURSE, "submit_reimburse",
                                  {"amount": "1", "category": "差旅"}, confirm=False)
    assert out.state == TaskState.CANCELLED


def _skill_body(**kw) -> dict:  # noqa: ANN003 —— 直接给 PageScriptBody 的入参(非 dump)
    base = dict(
        actions=[
            PageAction(op="fill", locator="label=金额", value_from="field:amount", assert_visible=True),
            PageAction(op="select", locator="label=类别", value_from="field:category"),
        ],
        dom_fingerprint="fp-v1", action="submit_reimburse",
        success_marker="text=保存成功", required_fields=["amount"], risk_level=RiskLevel.L2,
    )
    base.update(kw)
    return base
