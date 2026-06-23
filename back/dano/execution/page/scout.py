"""页面侦察:真实浏览器里抽取表单语义结构 → 候选字段 + 提交按钮 + 建议步骤。

港自旧 web_scout:语义定位优先(label > placeholder > name > id),识别提交按钮(_SUBMIT_HINTS)。
产出可直接喂 `page_builder.build_page_script` 的 RecordedStep 序列(确定性兜底,无需 LLM);
pi 可在此基础上改字段映射 / 标成功标志 / 调必填。绝不用坐标。
"""

from __future__ import annotations

from dano.agent_tools.page_builder import RecordedStep

# 提交按钮文本线索(ascii 用小写;中文 lower() 无副作用,统一按小写包含匹配)
_SUBMIT_HINTS = ("提交", "保存", "确定", "确认", "申请", "发起", "submit", "save", "ok", "confirm")

_SCOUT_JS = r"""() => {
  const labelFor = (el) => {
    if (el.id) { const l = document.querySelector('label[for="' + el.id + '"]');
                 if (l) return (l.innerText || '').trim(); }
    const w = el.closest('label'); if (w) return (w.innerText || '').trim();
    return el.getAttribute('aria-label') || '';
  };
  const skip = ['hidden', 'submit', 'button', 'reset'];
  const fields = Array.from(document.querySelectorAll('input,select,textarea')).filter((e) => {
    const t = (e.getAttribute('type') || '').toLowerCase(); return !skip.includes(t);
  }).map((e) => ({
    tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
    name: e.getAttribute('name') || '', id: e.id || '',
    placeholder: e.getAttribute('placeholder') || '', label: labelFor(e),
    required: !!e.required || e.getAttribute('aria-required') === 'true',
  }));
  const buttons = Array.from(document.querySelectorAll('button,input[type=submit]')).map((b) => ({
    text: ((b.innerText || b.value || '') + '').trim(),
  }));
  return { fields, buttons };
}"""


async def scout_dom(page) -> dict:  # noqa: ANN001 —— playwright Page
    """在已打开的页面上抽取表单结构(单次 JS 求值)。返回 {fields:[...], buttons:[...]}。"""
    return await page.evaluate(_SCOUT_JS)


def _field_locator(f: dict) -> str:
    if f.get("label"):
        return f"label={f['label']}"
    if f.get("placeholder"):
        return f"placeholder={f['placeholder']}"
    if f.get("name"):
        return f"css=[name={f['name']}]"
    if f.get("id"):
        return f"css=#{f['id']}"
    return "css=input"


def _op_for(f: dict) -> str:
    if f.get("tag") == "select":
        return "select"
    if f.get("type") == "file":
        return "upload"
    return "fill"


def _field_name(f: dict) -> str:
    return f.get("name") or f.get("label") or f.get("id") or "field"


def _submit_locator(buttons: list[dict]) -> str | None:
    for b in buttons:
        t = (b.get("text") or "").strip()
        if t and any(h in t.lower() for h in _SUBMIT_HINTS):
            return f"role=button[name={t}]"
    return None


def to_recorded_steps(dom: dict, *, include_submit: bool = True) -> tuple[list[RecordedStep], str | None]:
    """侦察结果 → 确定性 RecordedStep 序列 + 提交按钮 locator(无提交按钮返回 None)。"""
    steps: list[RecordedStep] = [
        RecordedStep(op=_op_for(f), locator=_field_locator(f),
                     field=_field_name(f), required=bool(f.get("required")))
        for f in dom.get("fields", [])
    ]
    submit = _submit_locator(dom.get("buttons", []))
    if include_submit and submit:
        steps.append(RecordedStep(op="submit", locator=submit))
    return steps, submit
