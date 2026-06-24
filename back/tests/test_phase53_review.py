"""Phase 4:三模型评审硬闸门加固 的回归测试。

加固点(防橡皮图章,但**不破坏**既有的反误判调优——【运行架构】里的"设计如此"项仍豁免):
- 每个角色 system prompt 给**逐项清单** + 要求每条理由**点名具体依据**;
- security/compliance 各加**窄域 fail-closed**(只针对发现的可疑高危 / 合规沙箱红线);
- 三审模型非互异时告警(盲点相关风险)。

模型的真实判断无法在单测里确定性复现,故这里测**提示词内容**与**板子的接线/降级行为**(注入 fake client)。
"""

from __future__ import annotations

from types import SimpleNamespace

import dano.review.board as B
from dano.review.board import _ROLE_SYSTEM, ReviewBoard


# ─────────────────────────── 提示词加固 ───────────────────────────
def test_role_prompts_have_checklist_and_citation():
    for role in ("acceptance", "security", "compliance"):
        p = _ROLE_SYSTEM[role]
        assert "逐项核对" in p                       # 给了清单而非一句话
        assert "点名" in p                           # 每条理由要点名依据(防泛泛而谈)
        assert "passed" in p and "reasons" in p      # 输出契约不变


def test_security_and_compliance_fail_closed_scoped():
    sec, comp = _ROLE_SYSTEM["security"], _ROLE_SYSTEM["compliance"]
    assert "fail-closed" in sec and "fail-closed" in comp
    assert "sandbox" in comp                         # 合规红线:须确认全为 sandbox+test


def test_by_design_exemptions_retained():
    """关键:加固不能把既有反误判调优顶掉——verify=False / fact_check GET 等仍标注为设计如此。"""
    sec = _ROLE_SYSTEM["security"]
    assert "verify=False" in sec and "fact_check" in sec
    assert "compliance" in _ROLE_SYSTEM and "fact_check" in _ROLE_SYSTEM["compliance"]


# ─────────────────────────── 板子接线 / 降级 ───────────────────────────
class _FakeClient:
    def __init__(self, decide) -> None:
        self.decide = decide
        self.seen: dict[str, str] = {}

    async def complete_json(self, *, model, system, user, timeout_s):  # noqa: ANN001
        self.seen[model] = user
        return self.decide(system, user)


async def test_board_maps_models_and_forwards_adapter_source():
    B._VERDICT_CACHE.clear()

    def decide(system, user):
        if "漏洞检测" in system and "SEKRET_TOKEN_ABC" in user:   # security 看到硬编码令牌 → fail
            return {"passed": False, "reasons": ["source 第2行 硬编码令牌"]}
        return {"passed": True, "reasons": []}
    fake = _FakeClient(decide)
    board = ReviewBoard(client=fake,
                        models={"acceptance": "m-a", "security": "m-s", "compliance": "m-c"})
    body = {"source": "def run(inputs, creds):\n    t = 'SEKRET_TOKEN_ABC'\n    return {}\n",
            "risk_level": "L3"}
    verdicts = await board.review(asset_type="adapter", asset_key="submit_x", body=body)
    by = {v.role: v for v in verdicts}
    assert by["security"].model_id == "m-s" and by["security"].passed is False
    assert by["acceptance"].passed is True and by["compliance"].passed is True
    assert "SEKRET_TOKEN_ABC" in fake.seen["m-s"]               # adapter source 已转发给评审


async def test_board_call_failure_fails_closed():
    B._VERDICT_CACHE.clear()

    class _Boom:
        async def complete_json(self, **_k):
            raise RuntimeError("boom")
    board = ReviewBoard(client=_Boom(),
                        models={"acceptance": "a", "security": "s", "compliance": "c"},
                        max_retries=0)
    verdicts = await board.review(asset_type="connector", asset_key="x", body={})
    assert all(v.passed is False for v in verdicts)            # 调用失败 → 安全默认不通过
    assert all("评审调用失败" in v.reasons[0] for v in verdicts)


def test_from_settings_maps_models_per_role(monkeypatch):
    import dano.config as config
    s = SimpleNamespace(pi_api_key="k", pi_base_url="http://x/v1",
                        review_model_acceptance="A", review_model_security="Bb",
                        review_model_compliance="Cc", review_timeout_s=10,
                        review_max_retries=1, review_retry_backoff_s=0.5)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    board = ReviewBoard.from_settings()
    assert board.models == {"acceptance": "A", "security": "Bb", "compliance": "Cc"}
