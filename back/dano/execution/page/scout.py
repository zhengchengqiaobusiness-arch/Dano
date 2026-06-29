"""页面侦察:真实浏览器里抽取表单语义结构 → 候选字段 + 提交按钮 + 建议步骤。

港自旧 web_scout:语义定位优先(label > placeholder > name > id),识别提交按钮(_SUBMIT_HINTS)。
产出可直接喂 `page_builder.build_page_script` 的 RecordedStep 序列(确定性兜底,无需 LLM);
pi 可在此基础上改字段映射 / 标成功标志 / 调必填。绝不用坐标。
"""

from __future__ import annotations

from dano.agent_tools.page_builder import RecordedStep

# 提交按钮文本线索(ascii 用小写;中文 lower() 无副作用,统一按小写包含匹配)
_SUBMIT_HINTS = ("提交", "保存", "确定", "确认", "申请", "发起", "submit", "save", "ok", "confirm")

# **通用语义抽取(与框架/class 无关,和录制器同一套引擎)**:按 **ARIA role**(显式或隐式推断)+ accessible name
# 识别字段,**不点名任何框架**(不写 .el-select/.ant-select);自定义下拉/日期靠**通用信号**——role=combobox/listbox、
# aria-haspopup、或 **readonly 文本框**(自定义选择控件的触发器几乎都是 readonly input)。每字段算好:
#  - kind(= page_act 的 op):fill / pick(自定义选择)/ select(原生 <select>)/ upload / click
#  - locator(拿来即用):css=#id 优先(最稳)→ role=<role>[name=<名>](语义、通用)→ css=cssPath(结构兜底)
_SCOUT_JS = r"""() => {
  const clean = (s) => ((s||'')+'').replace(/\s+/g,' ').trim().replace(/[:：*\s]+$/,'');
  function roleOf(el){                                  // 显式 role,否则按标签/类型推隐式 ARIA role(通用)
    const r = el.getAttribute && el.getAttribute('role'); if(r) return r;
    const tag=(el.tagName||'').toLowerCase(), ty=((el.getAttribute&&el.getAttribute('type'))||'').toLowerCase();
    if(tag==='select') return 'combobox';
    if(tag==='textarea') return 'textbox';
    if(tag==='button') return 'button';
    if(tag==='input'){
      if(['submit','button','reset'].indexOf(ty)>=0) return 'button';
      if(ty==='checkbox') return 'checkbox';
      if(ty==='radio') return 'radio';
      if(ty==='file') return 'file';
      if(['text','email','tel','url','search','password','number',''].indexOf(ty)>=0) return 'textbox';
    }
    if(el.isContentEditable) return 'textbox';
    return '';
  }
  function accName(el){                                 // accessible name:label[for]/包裹label/aria/最近字段容器标签/placeholder
    try{ if(el.id){ const l=document.querySelector('label[for="'+CSS.escape(el.id)+'"]'); if(l) return clean(l.innerText); } }catch(e){}
    const w=el.closest&&el.closest('label'); if(w) return clean(w.innerText);
    const al=el.getAttribute&&el.getAttribute('aria-label'); if(al) return clean(al);
    const lb=el.getAttribute&&el.getAttribute('aria-labelledby');
    if(lb){ let t=''; lb.split(/\s+/).forEach((id)=>{const n=document.getElementById(id); if(n)t+=clean(n.innerText)+' ';}); if(clean(t)) return clean(t); }
    const item=el.closest&&el.closest('[class*="form-item"],[class*="form_item"],[class*="field"],[class*="form-group"]');
    const lab=item&&item.querySelector('label,[class*="label"]'); if(lab){ const lt=clean(lab.innerText); if(lt) return lt; }
    return clean((el.getAttribute&&el.getAttribute('placeholder'))||'');
  }
  function visible(el){
    try{ const r=el.getBoundingClientRect(), s=getComputedStyle(el);
         return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; }catch(e){ return true; }
  }
  function cssPath(el){
    const parts=[]; let cur=el, depth=0;
    while(cur && cur.nodeType===1 && depth<5){
      if(cur.id){ parts.unshift('#'+CSS.escape(cur.id)); break; }
      let sel=cur.tagName.toLowerCase();
      const cls=[...cur.classList].filter(c=>!/focus|hover|active|open|visible|selected/.test(c)).slice(0,2);
      if(cls.length) sel+='.'+cls.map(c=>CSS.escape(c)).join('.');
      const sibs = cur.parentNode ? [...cur.parentNode.children].filter(c=>c.tagName===cur.tagName) : [];
      if(sibs.length>1) sel+=':nth-of-type('+(sibs.indexOf(cur)+1)+')';
      parts.unshift(sel); cur=cur.parentElement; depth++;
    }
    return parts.join(' > ');
  }
  function locOf(el, role, name){
    if(el.id) return 'css=#'+CSS.escape(el.id);
    if(name) return 'role='+role+'[name='+name+']';
    return 'css='+cssPath(el);
  }
  function kindOf(el, role){                            // 按 role + **通用信号**判该用的 op,不点名框架
    const tag=(el.tagName||'').toLowerCase();
    if(role==='file') return 'upload';
    if(role==='checkbox'||role==='radio'||role==='switch') return 'click';
    if(tag==='select') return 'select';                                   // 原生下拉
    if(role==='combobox'||role==='listbox'||(el.getAttribute&&el.getAttribute('aria-haspopup'))) return 'pick';  // 自定义下拉(通用)
    if(role==='textbox'){ return (el.hasAttribute&&el.hasAttribute('readonly')) ? 'pick' : 'fill'; }  // readonly文本框=选择控件触发器(通用)
    return '';
  }
  const out=[]; const seen=new Set();
  // 候选:原生表单控件 + 任何带交互 role/aria-haspopup/contenteditable 的元素(覆盖任意框架自定义控件)
  document.querySelectorAll('input,select,textarea,[role],[aria-haspopup],[contenteditable]').forEach((el)=>{
    if(seen.has(el) || !visible(el)) return;
    const role=roleOf(el); const kind=kindOf(el, role);
    if(!kind) return;
    if(kind==='pick'){ try{ el.querySelectorAll('input,textarea').forEach((x)=>seen.add(x)); }catch(e){} }  // 内层输入并入,免重复
    seen.add(el);
    const name=accName(el);
    out.push({ kind, label: name, locator: locOf(el, role, name),
               required: !!el.required || (el.getAttribute&&el.getAttribute('aria-required')==='true'),
               tag: (el.tagName||'').toLowerCase(),
               type: ((el.getAttribute&&el.getAttribute('type'))||'').toLowerCase(),
               name: (el.getAttribute&&el.getAttribute('name'))||'', id: el.id||'',
               placeholder: (el.getAttribute&&el.getAttribute('placeholder'))||'' });
  });
  const buttons = Array.from(document.querySelectorAll('button,input[type=submit],[role=button]'))
    .filter(visible).map((b)=>{ const t=clean(b.innerText||b.value||''); return { text: t, locator: locOf(b,'button',t) }; })
    .filter((b)=>b.text);
  return { fields: out, buttons };
}"""


async def scout_dom(page) -> dict:  # noqa: ANN001 —— playwright Page
    """在已打开的页面上抽取表单结构(单次 JS 求值)。返回 {fields:[...], buttons:[...]}。"""
    return await page.evaluate(_SCOUT_JS)


def _field_locator(f: dict) -> str:
    if f.get("locator"):                 # scout 已算好拿来即用的定位(覆盖自定义控件)
        return f["locator"]
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
    if f.get("kind"):                    # scout 已判好该用的 op(fill/pick/select/upload/click)
        return f["kind"]
    if f.get("tag") == "select":
        return "select"
    if f.get("type") == "file":
        return "upload"
    return "fill"


def _field_name(f: dict) -> str:
    return f.get("label") or f.get("name") or f.get("id") or "field"


def _submit_locator(buttons: list[dict]) -> str | None:
    for b in buttons:
        t = (b.get("text") or "").strip()
        if t and any(h in t.lower() for h in _SUBMIT_HINTS):
            return b.get("locator") or f"role=button[name={t}]"   # scout 算好的精确定位优先
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
