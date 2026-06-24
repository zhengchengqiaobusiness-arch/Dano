"""从录制/探测到的页面步骤,确定性地构造页面脚本资产体(流程8)。

定位与 connector_builder 一致:pi 负责"编排/决策"(哪步是提交、哪个输入绑哪个字段、成功标志是什么),
Python 负责"把声明式资产体建对"——字段对齐标准词典、必填/可选拆分、写页面定 L3、断言可见性。

输入是 `RecordedStep` 列表(pi/录制给出的语义步骤);输出是校验过的 `PageScriptBody`。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.shared.asset_bodies import PageAction, PageScriptBody
from dano.shared.enums import RiskLevel
from dano.shared.std_fields import ALL_STD_FIELDS

# 会写入页面字段的操作(这些步若绑定字段 → 暴露为 Skill 参数,且执行后断言元素可见)
_INPUT_OPS = {"fill", "select", "upload", "pick"}   # pick:选择型控件(日期/下拉/级联)的参数化步


class RecordedStep(BaseModel):
    """录制/探测产出的一步(pi 或前端录制器给出)。"""

    op: str = Field(description="goto/fill/select/upload/pick/click/wait/verify/submit")
    locator: str | None = Field(default=None, description="语义定位:role=/label=/placeholder=/text=/css=")
    field: str | None = Field(default=None, description="该输入绑定的字段(系统标签/别名);设置则成为 Skill 参数")
    value: str | None = Field(default=None, description="常量值(非字段绑定步,如固定下拉项)")
    required: bool = Field(default=True, description="字段是否必填")
    optional_step: bool = Field(default=False, description="容错步:找不到元素可跳过,不判失败")
    doc: str | None = Field(default=None, description="字段语义描述")


def _std_key(field: str) -> str:
    """把系统字段名/标签对齐到平台标准字段 key;无命中则原样保留(页面表单字段多变,不强求命中)。"""
    fl = (field or "").strip().lower()
    for std in ALL_STD_FIELDS:
        if fl == std.key.lower() or fl == std.label.lower() or fl in {a.lower() for a in std.aliases}:
            return std.key
    return (field or "").strip()


def assign_field_keys(raw_fields: list[str | None]) -> list[str]:
    """把按出现顺序的一串录制字段标签 → **唯一**字段 key 列表(等长、保序)。修 P1#6 字段碰撞:

    `_std_key` 是多对一映射(多个字段可塌缩到同一标准 key,如两个日期都→start_time),直接拿它当字段身份会
    互相覆盖、丢字段。此处:优先标准词典对齐(第一个占用该 std_key 保持跨资产一致);**后续塌缩到同一 key 的
    字段退回各自原始标签**,原始标签再撞则加 `#n` 后缀 → 任意表单都保证每个录制字段拿到独立的 key,不丢不串。
    无碰撞时与旧行为完全一致(每个字段就是它的 std_key/原始标签)。
    """
    used: set[str] = set()
    out: list[str] = []
    for f in raw_fields:
        std = _std_key(f or "")
        cand = std if (std and std not in used) else ((f or "").strip() or std or "field")
        key, n = cand, 2
        while key in used:
            key = f"{cand}#{n}"
            n += 1
        used.add(key)
        out.append(key)
    return out


def build_page_script(
    steps: list[RecordedStep],
    *,
    action: str,
    dom_fingerprint: str,
    title: str = "",
    start_url: str = "",
    success_marker: str | None = None,
    risk_level: RiskLevel | None = None,
) -> PageScriptBody:
    """确定性构造页面脚本资产体。

    - 字段绑定:有 field 的输入步 → value_from='field:<std_key>',并断言该元素执行后可见。
    - 必填/可选:按步的 required 拆分;user_fields = 全部绑定字段(去重保序)。
    - 风险:含提交步(写) → 默认 L3(运行期提交前必确认,铁律③);纯导航/查询 → L1。可被 risk_level 覆盖。
    """
    actions: list[PageAction] = []
    required: list[str] = []
    optional: list[str] = []
    user: list[str] = []
    docs: dict[str, str] = {}

    # 字段 key 统一分配:标准词典对齐为主,**多字段塌缩同一 std_key 时保唯一**(不丢字段,P1#6)
    field_keys = iter(assign_field_keys([s.field for s in steps if s.field]))
    for s in steps:
        value_from: str | None = None
        assert_v = False
        if s.field:
            key = next(field_keys)
            value_from = f"field:{key}"
            assert_v = s.op in _INPUT_OPS
            user.append(key)
            (required if s.required else optional).append(key)
            if s.doc:
                docs.setdefault(key, s.doc)
        elif s.value is not None:
            value_from = f"const:{s.value}"
        actions.append(PageAction(
            op=s.op, locator=s.locator, value_from=value_from,
            assert_visible=assert_v, optional=s.optional_step,
        ))

    user = list(dict.fromkeys(user))
    required = list(dict.fromkeys(required))
    optional = [f for f in dict.fromkeys(optional) if f not in required]

    has_submit = any(s.op == "submit" for s in steps)
    risk = risk_level or (RiskLevel.L3 if has_submit else RiskLevel.L1)

    return PageScriptBody(
        actions=actions, dom_fingerprint=dom_fingerprint, action=action, title=title,
        start_url=start_url, success_marker=success_marker, user_fields=user,
        required_fields=required, optional_fields=optional, field_docs=docs, risk_level=risk,
    )
