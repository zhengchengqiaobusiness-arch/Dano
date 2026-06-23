"""导出文件夹名(_slug):中文动作名也要唯一,不能塌成同一目录互相覆盖。"""
from __future__ import annotations

from dano.export.agent_skills import _slug


def test_slug_english_action_readable():
    """纯英文 skill_id → 可读 kebab,不加哈希。"""
    assert _slug("A-OA.submit_leave") == "dano-a-oa-submit-leave"


def test_slug_chinese_actions_unique():
    """两个中文动作名(日报填写 / 请假)必须得到不同目录(否则导出互相覆盖,只剩一个)。"""
    a, b = _slug("A-OA.日报填写"), _slug("A-OA.请假")
    assert a != b
    assert a.startswith("dano-a-oa-") and b.startswith("dano-a-oa-")
    # 同一 skill_id 稳定(可重复导出)
    assert _slug("A-OA.日报填写") == a
