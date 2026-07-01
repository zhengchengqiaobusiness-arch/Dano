"""build_form_spec 三层择优合成的单测(对应病原表单实测出的故障类)+ capture_form_ax 真机端到端。"""
import pytest

from dano.execution.page.formspec import build_form_spec


# 复刻病原表单的故障类:单位后缀 /μL、无 label 关联 ng、文件选择器占位符、单选组、aria-required
_HTML_PATHOGEN = """<!doctype html><html><body><form>
<div class="ant-form-item"><div class="ant-form-item-label"><label for="conc">核酸浓度</label></div>
  <div class="ant-form-item-control"><span class="ant-input-group"><input id="conc" value="">
  <span class="ant-input-group-addon">/μL</span></span></div></div>
<div class="row"><div class="lbl">上机总量</div>
  <span class="grp"><input value=""><span class="addon">ng</span></span></div>
<div class="path"><div class="ttl">测序数据fastq文件</div>
  <input readonly placeholder="请选择远程服务器上的fastq文件"><button type="button">选择文件</button></div>
<div class="ant-form-item"><div class="ant-form-item-label"><label id="srcl">核酸来源</label></div>
  <div role="radiogroup" aria-labelledby="srcl">
    <label><input type="radio" name="source" value="field" checked>野外</label>
    <label><input type="radio" name="source" value="lab">实验</label></div></div>
<div class="ant-form-item"><div class="ant-form-item-label"><label for="op">操作人</label></div>
  <div class="ant-form-item-control"><input id="op" aria-required="true" value=""></div></div>
<button type="button" id="ok">确定</button></form></body></html>"""


async def test_capture_form_ax_real_browser():
    """真机端到端:无障碍树 + DOM 对齐 → 病原表单 5 类字段全抓对(治你截图里的 /μL、ng、占位符、漏掉的单选组)。"""
    pytest.importorskip("playwright")
    from dano.execution.page.formspec import capture_form_ax
    from dano.execution.page.recorder import _RECORDER_JS
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        pytest.skip("playwright 不可用")
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception:  # noqa: BLE001
            pytest.skip("chromium 未安装")
        try:
            pg = await b.new_page()
            await pg.set_content(_HTML_PATHOGEN)
            await pg.evaluate(f"() => {{ ({_RECORDER_JS})(); }}")     # 装录制器 → window.__danoFormDom 可用
            cdp = await pg.context.new_cdp_session(pg)
            spec = await capture_form_ax(pg, cdp)
        finally:
            await b.close()

    by_name = {f["name"]: f for f in spec}
    for nm in ("核酸浓度", "上机总量", "测序数据fastq文件", "核酸来源", "操作人"):
        assert nm in by_name, f"漏抓 {nm};实得 {list(by_name)}"
    assert "/μL" not in by_name and "ng" not in by_name          # 单位不当字段名
    assert "请选择远程服务器上的fastq文件" not in by_name              # 占位符不当字段名
    assert by_name["核酸来源"]["role"] == "radiogroup"             # 单选组 = 一个字段(不拆散/不漏)
    # 单选组现在带**选中值 + 控件 name** → 能绑 body 的 source 参数(治"抓到却绑不上")
    assert by_name["核酸来源"]["value"] == "field"
    assert by_name["核酸来源"]["dom_name"] == "source"
    assert by_name["操作人"]["required"] is True                  # aria-required 必填
    # 同一单选组不重复出现(a11y radiogroup 与 DOM 单选组去重)
    assert sum(1 for f in spec if f["name"] == "核酸来源") == 1


_HTML_FULL = """<!doctype html><html><body><form>
<div class="ant-form-item"><div class="ant-form-item-label"><label for="c">核酸浓度</label></div>
  <div class="ant-form-item-control"><span class="ant-input-group"><input id="c" name="concentration" value="">
  <span class="ant-input-group-addon">/μL</span></span></div></div>
<div class="row"><div class="lbl">上机总量</div>
  <span class="grp"><input name="totalAmount" value=""><span class="addon">ng</span></span></div>
<div class="path"><div class="ttl">测序数据fastq文件 <i style="color:red">*</i></div>
  <input name="fastqPath" readonly placeholder="请选择远程服务器上的fastq文件"><button type="button">选择文件</button></div>
<div class="ant-form-item"><div class="ant-form-item-label"><label id="sl">核酸来源</label></div>
  <div role="radiogroup" aria-labelledby="sl">
    <label><input type="radio" name="source" value="field" checked>野外</label>
    <label><input type="radio" name="source" value="lab">实验</label></div></div>
<div class="ant-form-item"><div class="ant-form-item-label"><label for="op">操作人</label></div>
  <div class="ant-form-item-control"><input id="op" name="operator" aria-required="true" value=""></div></div>
<div class="ant-form-item"><div class="ant-form-item-label"><label for="m">分析方法</label></div>
  <div class="ant-form-item-control"><select id="m" name="method"><option value="mg">宏基因组</option></select></div></div>
<button type="button" id="ok">确定</button></form></body></html>"""


async def test_full_chain_capture_to_binding():
    """端到端:无障碍树抓取 → form_ax_to_snapshot → bind_form_fields,把病原表单**所有故障类**字段都绑到 body
    且名字权威(单位/无关联/文件器/单选组/select/必填/视觉*)。这是全面性+准确性的一体化回归。"""
    pytest.importorskip("playwright")
    from dano.execution.page.formspec import capture_form_ax, form_ax_to_snapshot
    from dano.execution.page.recorder import _RECORDER_JS
    from dano.execution.page.request_capture import bind_form_fields
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        pytest.skip("playwright 不可用")
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception:  # noqa: BLE001
            pytest.skip("chromium 未安装")
        try:
            pg = await b.new_page()
            await pg.set_content(_HTML_FULL)
            await pg.evaluate(f"() => {{ ({_RECORDER_JS})(); }}")
            cdp = await pg.context.new_cdp_session(pg)
            spec = await capture_form_ax(pg, cdp)
        finally:
            await b.close()

    snap = form_ax_to_snapshot(spec)
    import json as _json
    post = _json.dumps({"concentration": "12", "totalAmount": "30", "fastqPath": "/home/x.fastq",
                        "source": "field", "operator": "张三", "method": "宏基因组",
                        "status": "running", "appID": "p"}, ensure_ascii=False)   # 末两个=系统/噪声,不该绑
    bound = bind_form_fields(post, snap)
    label = {k: (v.get("label") or "") for k, v in bound.items()}
    assert label.get("concentration") == "核酸浓度"        # 单位 /μL 不当名
    assert label.get("totalAmount") == "上机总量"          # 无 label 关联 → DOM 兜底(剥单位 ng)
    assert label.get("fastqPath") == "测序数据fastq文件"     # 占位符不当名 → DOM 标题
    assert label.get("source") == "核酸来源"               # 单选组绑到 body 且名权威
    assert label.get("operator") == "操作人"
    assert label.get("method") == "分析方法"               # select/combobox
    assert bound["operator"]["required"] is True          # aria-required
    assert bound["fastqPath"]["required"] is True         # 视觉 * 必填(DOM)
    assert "status" not in bound and "appID" not in bound  # 非表单字段不被瞎绑


async def test_capture_form_ax_inside_iframe():
    """企业 OA 常把表单嵌在 **iframe** 里 → 跨 frame 也要抓到(治"只取主 frame 整张漏 → form_ax 空 → 退回 LLM 猜名")。"""
    pytest.importorskip("playwright")
    from dano.execution.page.formspec import capture_form_ax
    from dano.execution.page.recorder import _RECORDER_JS
    try:
        from playwright.async_api import async_playwright
    except Exception:  # noqa: BLE001
        pytest.skip("playwright 不可用")
    inner = ("<form><div class='ant-form-item'><div class='ant-form-item-label'>"
             "<label for='a'>一级内设机构</label></div><div class='ant-form-item-control'>"
             "<input id='a' name='csmc' value='1'></div></div>"
             "<button type='button' id='ok'>提交</button></form>")
    html = '<!doctype html><html><body><iframe srcdoc="' + inner.replace('"', "&quot;") + '"></iframe></body></html>'
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception:  # noqa: BLE001
            pytest.skip("chromium 未安装")
        try:
            pg = await b.new_page()
            await pg.set_content(html)
            await pg.wait_for_timeout(150)                       # iframe 内文档加载
            for fr in pg.frames:                                 # 录制时 add_init_script 会注入所有 frame;测试里手动装
                try:
                    await fr.evaluate(f"() => {{ ({_RECORDER_JS})(); }}")
                except Exception:  # noqa: BLE001
                    pass
            cdp = await pg.context.new_cdp_session(pg)
            spec = await capture_form_ax(pg, cdp)
        finally:
            await b.close()
    names = {f["name"] for f in spec}
    assert "一级内设机构" in names, f"iframe 内字段没抓到:{names}"


def test_build_form_spec_hybrid_a11y_dom():
    """无障碍树 + DOM 兜底 三层择优,把病原表单 5 类字段都合成对:
    单位后缀不当名、占位符退回 DOM 标签、单选组(a11y-only)成一个字段、必填(aria∪视觉*)、低置信标注。"""
    records = [
        # 核酸浓度:a11y 与 DOM 都对(带 /μL 单位)→ a11y 主,高置信
        {"ax": {"name": "核酸浓度", "role": "textbox", "required": False},
         "dom": {"label": "核酸浓度", "required": False, "value": "", "type": "number", "name": "concentration"}},
        # 上机总量:无 label 关联 → a11y 名空 → 退回 DOM 标签(DOM 已剥掉单位 ng)
        {"ax": {"name": "", "role": "textbox", "required": False},
         "dom": {"label": "上机总量", "required": False, "value": "", "type": "number", "name": "totalAmount"}},
        # 文件选择器:a11y 只拿到占位符 → 退回 DOM 标签(真名),必填来自视觉 *(DOM)
        {"ax": {"name": "请选择远程服务器上的fastq文件", "role": "textbox", "required": False},
         "dom": {"label": "测序数据fastq文件", "required": True, "value": "", "type": "text", "name": "fastqPath"}},
        # 核酸来源:单选组 = a11y-only(DOM 控件扫描漏掉 radio)→ 一个 radiogroup 字段
        {"ax": {"name": "核酸来源", "role": "radiogroup", "required": False}, "dom": None},
        # 操作人:aria-required 必填
        {"ax": {"name": "操作人", "role": "textbox", "required": True},
         "dom": {"label": "操作人", "required": False, "value": "", "type": "text", "name": "operator"}},
        # 纯占位符且无 DOM 标签 → 低置信(待人工确认),名字保留但标 low
        {"ax": {"name": "请输入运行编号", "role": "textbox", "required": False}, "dom": None},
    ]
    spec = build_form_spec(records)
    names = [f["name"] for f in spec]
    assert names[:5] == ["核酸浓度", "上机总量", "测序数据fastq文件", "核酸来源", "操作人"]
    # 单选组:角色权威化(DOM 漏掉的字段被 a11y 补上)
    assert spec[3]["role"] == "radiogroup"
    # 必填:aria(操作人)+ 视觉*(fastq)都算上;无标记的不误判必填
    assert spec[4]["required"] is True and spec[2]["required"] is True
    assert spec[0]["required"] is False and spec[1]["required"] is False
    # 不把单位/占位符当名:fastq 用 DOM 真名,不是占位符
    assert spec[2]["source"] == "dom" and spec[2]["name"] == "测序数据fastq文件"
    # 低置信字段标注出来(只剩占位符)→ 前端灰显待确认,不静默瞎填
    assert spec[5]["confidence"] == "low" and spec[5]["source"] == "placeholder"
    # body 绑定用的控件 name 透传
    assert spec[0]["dom_name"] == "concentration"


def test_build_form_spec_no_silent_garbage():
    """空记录/无名字段:诚实标 low/空名,绝不编造。"""
    spec = build_form_spec([{"ax": None, "dom": None}, {"ax": {"name": "/μL", "role": "textbox"}, "dom": None}])
    assert spec[0]["name"] == "" and spec[0]["confidence"] == "low"
    assert spec[1]["name"] == "" and spec[1]["confidence"] == "low"   # 纯单位"/μL"不当名字
