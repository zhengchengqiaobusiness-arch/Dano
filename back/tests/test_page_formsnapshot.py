"""Part A 地基的真机回归:把录制器**真实** _RECORDER_JS 注入 chromium,点提交,验表单快照。

仅当 playwright 包 + chromium 都可用时运行;否则整文件 skip(套件保持绿)。
本地临时 HTML(不联网)同时覆盖 Element-UI / Ant Design / 原生控件 + 系统预设 hidden。
守住三件用纯字典单测抓不到、只有真实 DOM 暴露的事:
  1) 同一字段不被 `[class*=form-item]` 命中 BEM 子节点而抓重(去重)。
  2) 表单含 readonly 日期/下拉时,点「提交」仍触发快照(提交先于 picker 判定)。
  3) 抓到的快照经 bind_form_fields 能按 name + 值兜底绑全 body,系统常量不外泄。
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from dano.execution.page.recorder import _RECORDER_JS
from dano.execution.page.request_capture import bind_form_fields

_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form class="el-form">
  <div class="el-form-item is-required"><label class="el-form-item__label">加班原因</label>
    <div class="el-form-item__content"><input class="el-input__inner" name="reason" value="项目上线赶工"></div></div>
  <div class="el-form-item is-required"><label class="el-form-item__label">加班类型</label>
    <div class="el-form-item__content"><div class="el-select"><input readonly class="el-input__inner" value="工作日加班"></div></div></div>
  <div class="el-form-item is-required"><label class="el-form-item__label">加班日期</label>
    <div class="el-form-item__content"><div class="el-date-editor el-date-editor--date"><input readonly class="el-input__inner" value="2026-06-30 18:00:00"></div></div></div>
  <div class="ant-form-item ant-form-item-required"><div class="ant-form-item-label"><label>加班地点</label></div>
    <div class="ant-form-item-control"><input class="ant-input" name="location" value="公司A座"></div></div>
  <div class="el-form-item"><label class="el-form-item__label">部门</label>
    <select name="deptId"><option value="">请选择</option><option value="10" selected>研发部</option></select></div>
  <div class="el-form-item"><label class="el-form-item__label">备注</label>
    <div class="el-form-item__content"><textarea name="remark">无</textarea></div></div>
  <div class="el-form-item"><label class="el-form-item__label">billType</label><input type="hidden" name="billType" value="OT01"></div>
  <button type="button" id="submit-btn">提交</button>
</form></body></html>"""


async def _chromium_ok() -> bool:
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            b = await p.chromium.launch()
            await b.close()
        return True
    except Exception:  # noqa: BLE001 —— 浏览器未安装等
        return False


async def _capture_snapshot() -> list[dict]:
    """注入真实录制器 JS,真点提交,回传 form_snapshot 字段。"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        try:
            pg = await b.new_page()
            await pg.set_content(_HTML)
            await pg.evaluate("() => { window.__cap = []; window.__danoRecord = (s) => window.__cap.push(s); }")
            await pg.evaluate(f"() => {{ ({_RECORDER_JS})(); }}")
            await pg.evaluate(
                "() => document.getElementById('submit-btn').dispatchEvent(new MouseEvent('click', {bubbles: true}))"
            )
            cap = await pg.evaluate("() => window.__cap")
        finally:
            await b.close()
    for s in cap:
        o = json.loads(s)
        if o.get("op") == "form_snapshot":
            return o["fields"]
    return []


async def test_form_snapshot_captures_every_field() -> None:
    if not await _chromium_ok():
        pytest.skip("chromium 未安装(python -m playwright install chromium)")
    snap = await _capture_snapshot()

    labels = [f["label"] for f in snap]
    # ② 含 readonly 日期/下拉时点提交仍触发快照(回归"提交被误判成开下拉"导致快照永不触发)
    assert snap, "点提交未触发 form_snapshot(提交被误判成 picker 触发?)"
    # 六个用户字段一个不漏
    assert set(labels) == {"加班原因", "加班类型", "加班日期", "加班地点", "部门", "备注"}
    # ① 同一字段不抓重(BEM __content/__label 去重)
    assert len(labels) == len(set(labels)) == 6, f"字段抓重:{labels}"
    assert "" not in labels, "出现空 label 行(BEM 子节点污染)"
    # 系统预设 hidden 不当用户字段
    assert "billType" not in labels
    # 必填由 DOM is-required / ant-form-item-required 权威判定
    req = {f["label"]: f["required"] for f in snap}
    assert req["加班原因"] and req["加班类型"] and req["加班日期"] and req["加班地点"]
    assert not req["部门"] and not req["备注"]


_HTML_TABLE = """<!doctype html><html><body><form class="el-form">
  <div class="el-form-item is-required"><label class="el-form-item__label">一级内设机构</label>
    <div class="el-form-item__content"><input class="el-input__inner" value="办公室"></div></div>
  <div class="el-form-item"><label class="el-form-item__label">二级内设机构</label>
    <div class="el-form-item__content"><input class="el-input__inner" value=""></div></div>
  <div class="el-form-item is-required"><div class="el-table">
    <div class="el-table__header-wrapper"><table><thead><tr>
      <th><div class="cell">序号</div></th>
      <th class="is-required"><div class="cell">权责清单</div></th>
      <th><div class="cell">所属系统</div></th>
      <th><div class="cell">系统库表</div></th>
      <th><div class="cell">编目状态</div></th>
      <th><div class="cell">操作</div></th>
    </tr></thead></table></div>
    <div class="el-table__body-wrapper"><table><tbody>
      <tr><td colspan="6"><div class="el-table__empty-block">暂无数据</div></td></tr>
    </tbody></table></div>
  </div></div>
  <button type="button" id="submit-btn">提交</button>
</form></body></html>"""


async def _capture(html: str) -> list[dict]:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        try:
            pg = await b.new_page()
            await pg.set_content(html)
            await pg.evaluate("() => { window.__cap = []; window.__danoRecord = (s) => window.__cap.push(s); }")
            await pg.evaluate(f"() => {{ ({_RECORDER_JS})(); }}")
            await pg.evaluate(
                "() => document.getElementById('submit-btn').dispatchEvent(new MouseEvent('click', {bubbles: true}))"
            )
            cap = await pg.evaluate("() => window.__cap")
        finally:
            await b.close()
    for s in cap:
        o = json.loads(s)
        if o.get("op") == "form_snapshot":
            return o["fields"]
    return []


_FW_FORMS = {
    "vant": """<form class="van-form">
      <div class="van-cell van-field van-field--required"><div class="van-field__label">姓名</div>
        <div class="van-field__body"><input class="van-field__control" name="name" value="张三"></div></div>
      <div class="van-cell van-field"><div class="van-field__label">类型</div>
        <div class="van-field__body"><input class="van-field__control" readonly value="A类"></div></div>
      <button type="button" id="submit-btn">提交</button></form>""",
    "bootstrap": """<form>
      <div class="mb-3"><label class="form-label" for="n">姓名</label><input id="n" class="form-control" name="name" value="张三" required></div>
      <div class="mb-3"><label class="form-label" for="t">类型</label><select id="t" name="type"><option selected>A类</option></select></div>
      <button type="button" id="submit-btn">提交</button></form>""",
    "mui": """<form>
      <div class="MuiFormControl-root"><label class="MuiInputLabel-root" for="n">姓名</label>
        <div class="MuiInputBase-root"><input id="n" class="MuiInputBase-input" name="name" value="张三" aria-required="true"></div></div>
      <div class="MuiFormControl-root"><label class="MuiInputLabel-root" for="t">类型</label>
        <div class="MuiInputBase-root"><input id="t" class="MuiInputBase-input" role="combobox" aria-haspopup="listbox" value="A类"></div></div>
      <button type="button" id="submit-btn">提交</button></form>""",
    "custom": """<form>
      <div class="row"><span class="lbl">姓名<i class="star">*</i></span><input name="name" value="张三"></div>
      <div class="row"><label>类型</label><div role="combobox" aria-label="类型" aria-haspopup="listbox">A类</div></div>
      <button type="button" id="submit-btn">提交</button></form>""",
}


async def test_form_snapshot_generalizes_across_frameworks() -> None:
    """泛化回归:换个组件库(Vant/Bootstrap/MUI/纯自定义 div)也要抓全字段+正确标签,不靠 `.el-form-item` 类名约定。
    治"只要换个系统就识别不全/字段名错"。控件级 + a11y/就近标签,框架无关。"""
    if not await _chromium_ok():
        pytest.skip("chromium 未安装")
    for fw, form in _FW_FORMS.items():
        fields = await _capture("<!doctype html><html><body>" + form + "</body></html>")
        labels = {f["label"] for f in fields if f.get("type") != "table" and f["label"]}
        assert {"姓名", "类型"} <= labels, f"[{fw}] 字段识别不全:{labels}"
        name_f = next((f for f in fields if f["label"] == "姓名"), None)
        assert name_f and name_f["value"] == "张三", f"[{fw}] 值没抓到"


async def test_snapshot_captures_detail_subtable() -> None:
    """复杂业务表单的「明细子表」(el-table + 新增一行)的列结构必须被建模,不能整张丢。"""
    if not await _chromium_ok():
        pytest.skip("chromium 未安装")
    fields = await _capture(_HTML_TABLE)
    tables = [f for f in fields if f.get("type") == "table"]
    flat = [f for f in fields if f.get("type") != "table"]
    # 扁平字段标签来自 DOM(真实名),二级内设机构非必填(回归"把选填判成必填")
    assert {f["label"] for f in flat} == {"一级内设机构", "二级内设机构"}
    assert next(f for f in flat if f["label"] == "二级内设机构")["required"] is False
    # 子表被建模成 type=table,列=权责清单/所属系统/系统库表/编目状态,序号/操作过滤,空表也有结构
    assert len(tables) == 1
    cols = [c["name"] for c in tables[0]["columns"]]
    assert cols == ["权责清单", "所属系统", "系统库表", "编目状态"]
    assert tables[0]["columns"][0]["required"] is True
    assert tables[0]["required"] is True and tables[0]["rows"] == 0


async def test_snapshot_binds_body_by_name_and_value() -> None:
    if not await _chromium_ok():
        pytest.skip("chromium 未安装")
    snap = await _capture_snapshot()
    # body key 与 DOM name 不同(overtimeType/overtimeDate)→ 只能靠值兜底绑;billType/processDefKey 是系统常量
    post = json.dumps({
        "reason": "项目上线赶工", "overtimeType": "工作日加班", "overtimeDate": "2026-06-30 18:00:00",
        "location": "公司A座", "deptId": "10", "remark": "无", "billType": "OT01", "processDefKey": "ot_v1",
    }, ensure_ascii=False)
    b = bind_form_fields(post, snap)
    assert set(b.keys()) == {"reason", "overtimeType", "overtimeDate", "location", "deptId", "remark"}
    # 值兜底:body 的内部名 overtimeType 拿到 DOM 权威字段名「加班类型」+ 必填
    assert b["overtimeType"]["label"] == "加班类型" and b["overtimeType"]["required"] is True
    # 系统常量绝不外泄成用户字段
    assert "billType" not in b and "processDefKey" not in b
