"""方式B:服务端托管浏览器的「网页内录制」会话。

客户在前端网页里操作我们托管的浏览器(截屏流投到网页 + 点击/键盘回传),注入页面的录制器
把真实 DOM 事件转成**语义步骤**(label/role/placeholder/name/text 定位,绝不用坐标)推回后端。
客户全程**免安装、免命令行**。录完 → 复用 page_builder→回放→评审→发布 管道出页面 Skill。

三层:① 截屏(CDP Page.startScreencast → base64 jpeg 帧)② 输入回传(归一坐标 → page.mouse/keyboard)
③ 动作捕获(注入 _RECORDER_JS,事件→语义步骤→expose_binding 回 Python)。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

_VIEW_W, _VIEW_H = 1280, 800

# 注入到每个页面的录制器:把表单输入/选择/提交点击转成语义步骤,推回 window.__danoRecord。
_RECORDER_JS = r"""() => {
  if (window.__danoRecorderInstalled) return;
  window.__danoRecorderInstalled = true;
  // 通用语义引擎(与框架/语言/公司无关):ARIA role + accessible name 优先,文本/属性兜底。
  // 不按标签/class 白名单,故 Element-UI / Ant Design / 原生 / 任意自定义控件一视同仁。
  var SUBMIT = ['提交','保存','确定','确认','申请','发起','送出','申报','submit','save','ok','confirm','apply'];
  var TESTID = ['data-testid','data-test','data-test-id','data-cy','data-qa'];
  var INTERACTIVE = {button:1,link:1,menuitem:1,menuitemcheckbox:1,menuitemradio:1,tab:1,option:1,
                     checkbox:1,radio:1,switch:1,treeitem:1};
  function clean(s) { return ((s || '') + '').replace(/\s+/g, ' ').trim(); }
  function roleOf(el) {                                  // 显式 role,否则按标签推隐式 ARIA role
    var r = el.getAttribute && el.getAttribute('role'); if (r) return r;
    var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (ty === 'submit' || ty === 'button' || ty === 'reset') return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (['text','email','tel','url','search','password','number',''].indexOf(ty) >= 0) return 'textbox';
    }
    return '';
  }
  function labelText(el) {                               // 关联 label / aria-labelledby
    try { if (el.id) { var l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) return clean(l.innerText); } } catch (e) {}
    var w = el.closest ? el.closest('label') : null; if (w) return clean(w.innerText);
    var lb = el.getAttribute && el.getAttribute('aria-labelledby');
    if (lb) { var t = ''; lb.split(/\s+/).forEach(function (id) { var n = document.getElementById(id); if (n) t += clean(n.innerText) + ' '; }); if (clean(t)) return clean(t); }
    // 兜底:最近表单项的标签(.el-form-item__label / .ant-form-item-label)—— 现代框架 label 常不带 for,
    // 控件(el-select/日期/输入)靠这个才能拿到"请假类型"这类中文字段名,而不是退回 placeholder。
    try {
      var item = el.closest && el.closest('.el-form-item,.ant-form-item,[class*="form-item"],[class*="form_item"]');
      var lab = item && item.querySelector('.el-form-item__label,.ant-form-item-label,label');
      if (lab) { var lt = clean(lab.innerText).replace(/[:：*]\s*$/, ''); if (lt) return lt; }
    } catch (e) {}
    return '';
  }
  function accName(el) {                                 // 可访问名(简化 WAI-ARIA),仅用于可点元素
    var al = el.getAttribute && el.getAttribute('aria-label'); if (clean(al)) return clean(al);
    var lt = labelText(el); if (lt) return lt;
    var t = clean(el.innerText) || clean(el.value); if (t) return t;
    var ti = el.getAttribute && el.getAttribute('title'); if (clean(ti)) return clean(ti);
    return '';
  }
  function esc(s) { try { return CSS.escape(s); } catch (e) { return s; } }
  function stableId(el) { return el.id && !/^[0-9]/.test(el.id) && !/^(el-id|ant|rc_|radix)/i.test(el.id); }
  function locateField(el) {                             // 表单字段:label > placeholder > name > id(绝不用值当名)
    var lt = labelText(el); if (lt) return 'label=' + lt;
    var ph = el.getAttribute('placeholder'); if (clean(ph)) return 'placeholder=' + clean(ph);
    var ar = el.getAttribute('aria-label'); if (clean(ar)) return 'role=textbox[name=' + clean(ar) + ']';
    var nm = el.getAttribute('name'); if (nm) return 'css=[name="' + esc(nm) + '"]';
    if (stableId(el)) return 'css=#' + esc(el.id);
    return null;
  }
  function locateClickable(el) {                         // 可点元素:testid > role+name > text > id
    for (var i = 0; i < TESTID.length; i++) { var a = el.getAttribute && el.getAttribute(TESTID[i]); if (a) return 'css=[' + TESTID[i] + '="' + a + '"]'; }
    var role = roleOf(el); var name = accName(el);
    if (name.length > 60) name = '';                     // 名字过长(整块容器)不可靠
    if (role && name) return 'role=' + role + '[name=' + name + ']';
    if (name) return 'text=' + name;
    if (stableId(el)) return 'css=#' + esc(el.id);
    return null;
  }
  function fieldOf(loc) {
    if (!loc) return '';
    var i = loc.indexOf('='); var k = loc.slice(0, i); var r = loc.slice(i + 1);
    if (k === 'role') { var m = r.match(/\[name=(.*)\]/); return m ? m[1] : ''; }
    if (k === 'css') { var c = r.match(/\[name="([^"]+)"\]/); if (c) return c[1]; return r.replace(/^[#.]/, ''); }
    return r;
  }
  // 登录页检测(通用、保守):URL 命中 login/signin(SPA 路由守卫重定向就长这样)。登录不是业务步骤,不录。
  // 只看 URL,不看密码框 —— 免把业务里的"修改密码"页或测试登录表单整页误跳过。
  function onLoginPage() {
    try { return /\/(login|signin|sign-in|sso)(?:[/?#]|$)/i.test(location.href); } catch (e) { return false; }
  }
  function requiredOf(el) {                       // 该字段是否必填:读表单 * 标记(通用,跨 Element-UI / Ant / Vant / 原生)
    try {
      if (el.required || el.getAttribute('aria-required') === 'true') return true;
      // 逐祖先看必填类(is-required / xxx-required / required)+ 该容器标签带 *。**不能用 closest('[class*=form-item]')** —— 它会
      //   命中 BEM 子节点 `el-form-item__content`(含 "form-item" 子串却无 required 类),把必填判丢。逐级走才稳。
      var node = el;
      for (var i = 0; i < 6 && node && node !== document.body; i++) {
        var c = (typeof node.className === 'string') ? node.className : '';
        if (/(^|[ _-])(is-)?required($|[ _-])/i.test(c)) return true;            // is-required / ant-form-item-required …
        // 标签/标题区写了 * = 必填(各框架标签类名不一,含纯 div 标题)。不限 <label>,故 .ttl/.title 也覆盖
        var lab = node.querySelector ? node.querySelector('label,legend,dt,[class*="label"],[class*="Label"],[class*="title"],[class*="ttl"]') : null;
        if (lab && !lab.contains(el) && (lab.textContent || '').indexOf('*') >= 0) return true;
        node = node.parentElement;
      }
    } catch (e) {}
    return false;
  }
  function emit(op, loc, value, field, required, options) {
    if (!loc || onLoginPage()) return;            // 登录页上的任何操作一律不录(自动跳过登录,免手点「从这里开始录」)
    try { window.__danoRecord(JSON.stringify({ op: op, locator: loc, value: value || '', field: field || '', required: !!required, options: options || [] })); } catch (e) {}
  }
  // 下拉/级联弹层里**当前可见的选项文字**(地面真值枚举):工作日加班/周末加班/节假日加班 …
  // —— 直接读 DOM,胜过拿提交值去网络字典里猜命中(治"加班类型/请假类型绑到几百项全量字典")。框架无关。
  function popupOptions(pop) {
    if (!pop) return [];
    try {
      var sel = '[role="option"],.el-select-dropdown__item,.ant-select-item-option,.el-cascader-node,' +
                '.ant-cascader-menu-item,.el-autocomplete-suggestion li,li';
      var nodes = pop.querySelectorAll(sel); var out = [], seen = {};
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n.offsetParent === null) continue;                      // 不可见(折叠的级联子层)跳过
        var t = clean(n.innerText || n.textContent || '');
        if (t && t.length <= 60 && !seen[t]) { seen[t] = 1; out.push(t); }
        if (out.length >= 500) break;
      }
      return out;
    } catch (e) { return []; }
  }
  // 原生 <select> 的全部 <option> 文字(去掉占位空项)
  function nativeOptions(el) {
    try {
      var out = [];
      for (var i = 0; i < el.options.length; i++) { var t = clean(el.options[i].text); if (t) out.push(t); }
      return out;
    } catch (e) { return []; }
  }
  // 找交互目标:role 属交互集 / a / button,否则向上找 cursor:pointer 且短文本的(卡片/自定义控件)。
  function target(t) {
    var node = t;
    for (var i = 0; i < 8 && node && node !== document.body; i++) {
      var tag = (node.tagName || '').toLowerCase(); var role = roleOf(node);
      if ((role && INTERACTIVE[role]) || tag === 'a' || tag === 'button') return node;
      try { var tx = clean(node.innerText); if (getComputedStyle(node).cursor === 'pointer' && tx && tx.length <= 40) return node; } catch (e) {}
      node = node.parentElement;
    }
    return null;
  }
  document.addEventListener('input', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    // 密码框绝不录(安全);非文本类型跳过
    if (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','password','hidden'].indexOf(ty) < 0)) {
      var loc = locateField(el); emit('fill', loc, el.value, fieldOf(loc), requiredOf(el));
    }
  }, true);
  document.addEventListener('change', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (tag === 'select') { var l1 = locateField(el); emit('select', l1, el.value, fieldOf(l1), requiredOf(el), nativeOptions(el)); }
    else if (tag === 'input' && ty === 'file') { var l2 = locateField(el); emit('upload', l2, el.value || '', fieldOf(l2), requiredOf(el)); }
  }, true);
  // 选择型控件参数化(框架无关):日期/下拉/级联是"点"出来的,不该录成写死的点击,而该录成一个
  // pick 参数步(触发框 + 选中的最终值)。识别弹层 + 触发框,选完读触发框 input 的最终值。
  var POPUP = '.el-picker-panel,.el-select-dropdown,.el-cascader__dropdown,.el-time-panel,.el-time-spinner,' +
              '.el-date-table,.el-month-table,.el-year-table,.el-autocomplete-suggestion,' +
              '.ant-picker-dropdown,.ant-select-dropdown,.ant-cascader-dropdown,[role="listbox"]';
  var TRIGGER_CLS = '.el-date-editor,.el-select,.el-cascader,.el-time-select,.el-time-picker,' +
                    '.ant-picker,.ant-select,.ant-cascader-picker';
  var activeTrigger = null, prevVal = '', pickTimer = null, lastPickOptions = [];
  function triggerOf(t) {                               // 触发型字段:已知选择器类 / 含 readonly input / aria-haspopup
    var k = t.closest ? t.closest(TRIGGER_CLS + ',[aria-haspopup]') : null; if (k) return k;
    var node = t;
    for (var i = 0; i < 4 && node && node !== document.body; i++) {
      try { if (node.querySelector && node.querySelector('input[readonly]')) return node; } catch (e) {}
      node = node.parentElement;
    }
    return null;
  }
  // 取触发框当前"显示值":优先 input.value(旧 Element UI),退而读触发框可见文本
  // —— Element Plus(Vue3)等现代框架选中值在文本节点里、input.value 为空,必须读 innerText 才抓得到。
  function pickVal(trig) {
    if (!trig) return '';
    var inp = trig.querySelector ? trig.querySelector('input') : null;
    var v = inp ? clean(inp.value) : '';
    return v || clean((trig.innerText || ''));
  }
  // 选中值落定检测:**不靠固定延时**,轮询显示值直到变成「非空且与点击前不同」才记 pick
  // —— 异步/远程搜索/级联(值晚一点回填)也能稳抓,不会读太早拿空值而漏掉(框架无关)。
  function pollPick(trig) {
    if (pickTimer) { clearInterval(pickTimer); pickTimer = null; }
    if (!trig) return;
    var tries = 0;
    pickTimer = setInterval(function () {
      tries++;
      var v = pickVal(trig);
      if (v && v !== prevVal) {                         // 显示值已落定(与点击前不同)→ 记 pick
        clearInterval(pickTimer); pickTimer = null;
        var inp = trig.querySelector ? trig.querySelector('input') : null;
        var loc = locateField(inp || trig);
        if (loc) emit('pick', loc, v, fieldOf(loc), requiredOf(trig) || (inp && requiredOf(inp)), lastPickOptions);
        lastPickOptions = [];                           // 用过即清,下一个下拉重新抓
      } else if (tries >= 25) { clearInterval(pickTimer); pickTimer = null; }   // ~2.5s 仍没变 → 放弃
    }, 100);
  }
  // 提交时给整张表单拍快照(每控件:name/label/type/required/value)→ 后端按 name/值**结构化绑定** body 字段,
  // 取代脆弱的值匹配(治字段遗漏/认错/名字错/必填错)。框架无关:Element/Ant 的 form-item + 原生控件都覆盖。
  function _itemLabel(item) {
    var l = item.querySelector('.el-form-item__label,.ant-form-item-label,label');
    return l ? clean(l.innerText).replace(/[:：*]\s*$/, '') : '';
  }
  function _itemRequired(item, ctrl) {
    try {
      if (item.className && /is-required|required/.test(item.className)) return true;
      var l = item.querySelector('.el-form-item__label,.ant-form-item-label,label');
      if (l && (l.querySelector('[class*="required"]') || l.textContent.indexOf('*') >= 0)) return true;
    } catch (e) {}
    return !!(ctrl && ctrl.required);
  }
  function _ctrlType(ctrl) {
    var t = (ctrl.tagName || '').toLowerCase();
    if (t === 'textarea') return 'textarea';
    if (t === 'select') return 'select';
    if (t === 'input') return (ctrl.type || 'text').toLowerCase();
    return 'select';
  }
  // 明细子表(el-table / ant-table / 原生 table:权责清单、商品明细、附件行…)的**列结构**。复杂业务表单
  // 普遍带"新增一行"的重复子表;扁平 form-item 扫描会整张漏掉。读表头列名 + 必填,跳过序号/操作列。框架无关。
  function _tableCols(tbl) {
    var ths = tbl.querySelectorAll('thead th,[role="columnheader"],.el-table__header th,.ant-table-thead th');
    var cols = []; var seenC = {};
    for (var i = 0; i < ths.length; i++) {
      var th = ths[i]; var cell = th.querySelector('.cell') || th;
      var nm = clean(cell.innerText || cell.textContent || '').replace(/[:：*\s]+$/, '');
      if (!nm || nm === '#' || /^(序号|操作|action|#)$/i.test(nm) || seenC[nm]) continue;
      if (th.querySelector && th.querySelector('input[type=checkbox]')) continue;   // 多选勾选列
      seenC[nm] = 1;
      var req = /is-required|required/.test(th.className || '') ||
                (cell.querySelector && cell.querySelector('[class*="required"]')) ||
                ((th.textContent || '').indexOf('*') >= 0);
      cols.push({ name: nm, required: !!req });
    }
    return cols;
  }
  function _tableRows(tbl) {
    try {
      var trs = tbl.querySelectorAll('.el-table__body-wrapper tbody tr,.ant-table-tbody tr,tbody tr');
      var n = 0;
      for (var i = 0; i < trs.length; i++) {
        if (trs[i].querySelector('.el-table__empty-block,.ant-empty')) continue;   // 暂无数据占位行不算
        if (clean(trs[i].innerText || '')) n++;
      }
      return n;
    } catch (e) { return 0; }
  }
  // 就近标签(labelText 的 a11y 路径失败后用):往上 ≤4 层找"行容器"里的标签元素 —— 各框架标签类名不同
  //   (el-form-item__label / ant-form-item-label / van-field__label / form-label / MuiInputLabel / ivu-form-item-label),
  //   统一按 <label>/legend/dt/[class*=label] 命中,取短文本。**不靠 class 白名单**,故任意组件库都覆盖。
  function _isUnit(s) { return /^[\/\\\s]*([µμ]?[a-zA-Z]{1,3}|%|‰)[\/\\\s]*$/.test(s || ''); }   // /μL、ng、% 等纯单位不是字段名
  function _nearLabel(el) {
    var selfTxt = '';
    try { selfTxt = /^(input|textarea|select)$/i.test(el.tagName || '') ? clean(el.value || '') : clean(el.innerText || el.textContent || ''); } catch (e) {}
    var CC = 'input:not([type=hidden]):not([type=button]):not([type=submit]),textarea,select,[contenteditable="true"],[role="combobox"],[role="textbox"]';
    var node = el;
    for (var i = 0; i < 5 && node && node !== document.body; i++) {
      try {
        if (node !== el && node.querySelectorAll(CC).length > 1) break;   // 越过"本字段的行"(容器含多控件)→ 停,免借到别字段/整表的 label
        var cand = node.querySelector('label,legend,dt,[class*="label"],[class*="Label"],[class*="field__label"],[class*="item-label"]');
        if (cand && !cand.contains(el)) {
          var t = clean(cand.innerText || cand.textContent || '').replace(/[:：*]\s*$/, '');
          if (t && t.length <= 30 && !_isUnit(t)) return t;
        }
        if (node !== el) {                                               // 无语义 label 元素(纯自定义)→ 行内文本去掉控件值/按钮/单位 = 标签
          var ot = clean(node.innerText || node.textContent || '');
          if (selfTxt) ot = clean(ot.split(selfTxt).join(' '));
          ot = ot.replace(/(选择文件|选择|上传|浏览|添加|browse|choose|upload)\s*$/i, '');         // 去尾部按钮词(文件选择器)
          ot = ot.replace(/[\s\/]*((ng|m?g|[µμ]?l|m?l|pg|kg|bp|kb|rpm|[nµμm]m|cm|%|‰|个|条|次|份))+\s*$/i, '');   // 去尾部单位(/μL、ng)
          ot = ot.replace(/[:：*\s]+$/, '');
          if (ot && ot.length <= 20 && !_isUnit(ot)) return ot;
        }
      } catch (e) {}
      node = node.parentElement;
    }
    return '';
  }
  function formSnapshot(submitEl) {
    try {
      var form = (submitEl && submitEl.closest && submitEl.closest('form,.el-form,.ant-form,[role="form"]')) || document.body;
      var out = []; var seen = [];
      // **控件级**扫描(框架无关,治"换个框架就识别不全"):原生控件 + ARIA 输入角色 + 可编辑元素;
      //   逐控件用 a11y(label[for]/包裹 label/aria) → 就近标签 取名,不再靠 `.el-form-item` 这类**类名约定**。
      var CTRL = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]),' +
                 'textarea,select,[contenteditable=""],[contenteditable="true"],' +
                 '[role="combobox"],[role="textbox"],[role="spinbutton"],[role="searchbox"]';
      var ctrls = form.querySelectorAll(CTRL);
      for (var i = 0; i < ctrls.length && out.length < 200; i++) {
        var el = ctrls[i];
        if (el.closest && el.closest('.el-table,.ant-table,[role="grid"],table')) continue;   // 子表单元格 → 交下面表格扫描
        // 复合控件:role=combobox/可编辑常在外层 div 上、内部还有真 input → 取内部 input,按真实控件判重(避免一个字段抓两条)
        var inp = /^(input|textarea|select)$/i.test(el.tagName || '') ? el : (el.querySelector('input,textarea,select') || el);
        if (seen.indexOf(inp) >= 0) continue;
        seen.push(inp); if (inp !== el) seen.push(el);
        var isCtrl = /^(input|textarea|select)$/i.test(inp.tagName || '');
        var label = labelText(inp) || _nearLabel(inp);
        var value = isCtrl ? clean(inp.value) : '';
        if (!value) { try { value = clean(inp.innerText || inp.textContent || '').slice(0, 80); } catch (e) {} }
        out.push({ name: (inp.getAttribute && inp.getAttribute('name')) || '', label: clean(label),
                   type: isCtrl ? _ctrlType(inp) : 'select', required: requiredOf(inp), value: value });
      }
      // 明细子表:扫描表格控件,把"列结构"建模成一个 type=table 字段(含 columns/行数),不再整张丢掉
      var tbls = form.querySelectorAll('.el-table,.ant-table');
      if (!tbls.length) tbls = form.querySelectorAll('table');
      for (var j = 0; j < tbls.length && out.length < 220; j++) {
        var tbl = tbls[j];
        if (tbl.parentElement && tbl.parentElement.closest && tbl.parentElement.closest('.el-table,.ant-table')) continue;  // 嵌套表只取最外层
        var cols = _tableCols(tbl); if (!cols.length) continue;
        var fitem = tbl.closest && tbl.closest('.el-form-item,.ant-form-item,[class*="form-item"]');
        var tlabel = (fitem && _itemLabel(fitem)) || '';
        if (!tlabel) { var rc = cols.filter(function (c) { return c.required; }); tlabel = (rc[0] ? rc[0].name : cols[0].name) + '明细'; }
        var treq = cols.some(function (c) { return c.required; }) || !!(fitem && /is-required|required/.test(fitem.className || ''));
        out.push({ name: '', label: clean(tlabel), type: 'table', required: treq, value: '', columns: cols, rows: _tableRows(tbl) });
      }
      return out;
    } catch (e) { return []; }
  }
  // 给每个表单控件打 data-danofid 标记 + 抽 DOM 字段(label/必填/值/类型/name)。供后端把**无障碍树**(浏览器算好的
  //   可访问名/角色/必填)按 backendDOMNodeId 与这里对齐,合成权威字段表(a11y 主 + DOM 兜底)。框架无关。
  window.__danoFormDom = function (pfx) {
    try {
      pfx = pfx || '';
      var CTRL = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=checkbox]):not([type=radio]),' +
                 'textarea,select,[contenteditable="true"],[role="combobox"],[role="textbox"],[role="spinbutton"],[role="searchbox"]';
      var ctrls = document.querySelectorAll(CTRL); var out = []; var seen = []; var fid = 0;
      for (var i = 0; i < ctrls.length && out.length < 300; i++) {
        var el = ctrls[i];
        if (el.closest && el.closest('.el-table,.ant-table,[role="grid"],table')) continue;
        var inp = /^(input|textarea|select)$/i.test(el.tagName || '') ? el : (el.querySelector('input,textarea,select') || el);
        if (seen.indexOf(inp) >= 0) continue; seen.push(inp); if (inp !== el) seen.push(el);
        var f = pfx + String(fid++); try { inp.setAttribute('data-danofid', f); } catch (e3) {}
        var isCtrl = /^(input|textarea|select)$/i.test(inp.tagName || '');
        var label = labelText(inp) || _nearLabel(inp);
        var value = isCtrl ? clean(inp.value) : clean(inp.innerText || inp.textContent || '').slice(0, 80);
        out.push({ fid: f, name: (inp.getAttribute && inp.getAttribute('name')) || '', label: clean(label),
                   type: isCtrl ? _ctrlType(inp) : 'select', required: requiredOf(inp), value: value });
      }
      // 单选组:同 name 的 radio = **一个**字段(治"核酸来源被整组漏/拆散")。标签取组容器(aria/label),
      //   值取**选中项**的 value(供按值绑 body 的 source 参数);组容器打 fid → 与 a11y radiogroup 对齐。框架无关。
      var groups = {}; var radios = document.querySelectorAll('input[type="radio"]');
      for (var k = 0; k < radios.length; k++) {
        var rb = radios[k];
        if (rb.closest && rb.closest('.el-table,.ant-table,table')) continue;
        var gn = rb.getAttribute('name') || '';
        var cont = (rb.closest && rb.closest('[role="radiogroup"],.ant-radio-group,.el-radio-group,fieldset')) ||
                   (rb.closest && rb.closest('.ant-form-item,.el-form-item,[class*="form-item"]')) || rb.parentElement;
        var gkey = gn || (cont && cont.__dfk) || ('r' + k);
        if (cont && !cont.__dfk) { cont.__dfk = gkey; }
        if (!groups[gkey]) {
          var f2 = pfx + String(fid++); if (cont) { try { cont.setAttribute('data-danofid', f2); } catch (e5) {} }
          var glabel = (cont ? (labelText(cont) || _nearLabel(cont)) : '') || gn;
          groups[gkey] = { fid: f2, name: gn, label: clean(glabel), type: 'radio',
                           required: cont ? requiredOf(cont) : false, value: '' };
          out.push(groups[gkey]);
        }
        if (rb.checked) { groups[gkey].value = clean(rb.value) || clean((rb.parentElement && rb.parentElement.innerText) || ''); }
      }
      return out;
    } catch (e) { return []; }
  };
  document.addEventListener('click', function (e) {
    // A) 点在日期/下拉弹层内 = 正在选择 → 不记这次点击;先把**此刻弹层里可见的选项**抓下来(地面真值枚举),
    //    再轮询触发框值落定后随 pick 一起回传(选完即生效)
    if (e.target.closest && e.target.closest(POPUP)) {
      var _opts = popupOptions(e.target.closest(POPUP)); if (_opts.length) lastPickOptions = _opts;
      pollPick(activeTrigger); return;
    }
    // A2) 提交按钮**优先**判定:点提交永远当提交(拍表单快照 + 记 submit),绝不被下面的"选择触发框"误吞。
    //     真实表单里日期/下拉都是 readonly input,triggerOf 的 readonly 兜底会把提交点误判成开下拉而提前 return,
    //     导致快照永不触发、submit 也漏记。语义上点「提交」就是提交,先于 picker 逻辑裁定。
    var sbEl = target(e.target);
    if (sbEl) {
      var sbRole = roleOf(sbEl), sbName = accName(sbEl);
      if (sbRole === 'button' && SUBMIT.some(function (h) { return sbName.toLowerCase().indexOf(h) >= 0; })) {
        if (!onLoginPage()) { try { window.__danoRecord(JSON.stringify({ op: 'form_snapshot', fields: formSnapshot(sbEl) })); } catch (e2) {} }
        var sbLoc = locateClickable(sbEl); if (sbLoc) emit('submit', sbLoc, '', '');
        return;
      }
    }
    // B) 点选择型触发框 → 记住它 + 点击前的显示值,开始轮询(覆盖单击即选 / 远程搜索异步回填 / 级联)
    var trig = triggerOf(e.target);
    if (trig) {
      activeTrigger = trig;
      prevVal = pickVal(trig);
      pollPick(trig);
      return;
    }
    // C) 普通输入框点击 = 聚焦噪声(打字会另记 fill)→ 跳过
    if (e.target.closest && e.target.closest('input,select,textarea')) return;
    // D) 普通可点元素(按钮/卡片/菜单/链接)—— 提交已在 A2 处理,这里只记普通点击
    var el = target(e.target); if (!el) return;
    var loc = locateClickable(el); if (!loc) return;
    emit('click', loc, '', '');
  }, true);
}"""


class RecordSession:
    """一次网页内录制。start→(用户经截屏+输入回传操作)→recorded_steps→stop。"""

    def __init__(self, *, on_step: Callable[[dict], None] | None = None,
                 on_request: Callable[[dict], None] | None = None,
                 intercept_submit: bool = True, capture_reads: bool = True) -> None:
        self.steps: list[dict] = []
        self.requests: list[dict] = []      # 抓到的写请求(有序,method/url/post_data/headers)→ 参数化/多步工作流
        self.reads: list[dict] = []         # 抓到的读请求(GET+JSON 列表/字典)→ Q2 选领导等 select 的候选源
        self.form_snapshot: list[dict] = []  # 提交瞬间整张表单快照(name/label/type/required/value)→ 结构化绑定 body 字段
        self.form_ax: list[dict] = []        # 提交瞬间**无障碍树+DOM 合成**的权威字段表(build_form_spec)→ 名/必填/角色更准
        self._form_ax_task = None            # _capture_form_ax 的异步任务句柄(发布前 await 一下,确保抓完)
        self._on_step = on_step
        self._on_request_cb = on_request    # 实时把抓到的请求推给前端(诊断可见)
        # 拦截提交:点提交时抓到业务写请求后,假装成功、不真发给服务器 → 录制不产生真实记录
        self._intercept = intercept_submit
        self._capture_reads = capture_reads
        self._pw = None
        self._browser = None
        self._context = None
        self._cdp = None
        self._on_frame: Callable[[str], Awaitable[None]] | None = None   # 截屏回调(切活动页时复用)
        self._closing = False        # stop()/断连中:页面 close 事件不再重开截屏(避免在已关 context 上 new_cdp_session 抛错)
        self.page = None

    async def start(self, start_url: str, *, base_url: str = "", headless: bool = True,
                    storage_state: str | None = None, token: str | None = None,
                    token_key: str | None = None) -> None:
        from playwright.async_api import async_playwright

        from dano.execution.page.driver import apply_token_auth
        from dano.infra.http import tls_verify
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"viewport": {"width": _VIEW_W, "height": _VIEW_H},
                            "ignore_https_errors": not tls_verify()}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        full = start_url if start_url.startswith(("http", "file")) else f"{base_url.rstrip('/')}{start_url}"
        if token:                                          # 预置登录态:免在画面里登录,开局即业务页
            await apply_token_auth(self._context, token=token, url=full, token_key=token_key)
        # add_init_script 只执行脚本、不调用函数 → 包成 IIFE 才会真正安装录制器(与 evaluate 不同)
        await self._context.add_init_script(f"({_RECORDER_JS})()")
        # 录制器回调 / 路由 / 响应抓取**全部挂在 context 上** → 自动覆盖当前页 + 之后弹出的新标签页/新窗口。
        # (Playwright 一个 Page 只管一个标签页;旧实现只挂在 self.page 上,用户点开 target=_blank/window.open
        #  的新页时,新页既没装录制绑定、又没被截屏 → 表现为"新页打不开"。挂到 context 后新页天然继承。)
        await self._context.expose_binding("__danoRecord", self._on_record)
        if self._intercept:
            # 拦截模式:抓到业务写请求后假装成功、不真发 → 录制不产生真实记录(登录/校验码等放行)
            await self._context.route("**/*", self._route)
        else:
            self._context.on("request", self._on_request)  # 直录模式:照常发,只旁观抓取
        if self._capture_reads:
            self._context.on("response", self._resp_dispatch)  # 抓 GET+JSON 读响应(列表/字典)→ select 候选源
        # 新页面(target=_blank / window.open / 弹窗)→ **跟随它**:设为活动页 + 把截屏切过去(否则用户看不到=打不开)
        self._context.on("page", lambda p: asyncio.create_task(self._on_new_page(p)))
        self.page = await self._context.new_page()
        # SPA 常不触发 "load"(长连接/轮询挂着)→ 用 domcontentloaded,否则 goto 卡到超时(与运行期 driver 一致)
        await self.page.goto(full, wait_until="domcontentloaded")

    async def _on_new_page(self, page) -> None:  # noqa: ANN001 —— 新标签页/新窗口(context "page" 事件)
        """用户操作打开了新页(target=_blank / window.open / 弹窗)→ 跟随:设为活动页,把截屏切过去。
        否则新页在后台,用户看不到、也操作不到,表现为"新页打不开"。录制绑定/路由已在 context 级,自动覆盖新页。"""
        if self._closing:
            return
        try:
            page.on("close", lambda: asyncio.create_task(self._on_page_close(page)))
        except Exception:  # noqa: BLE001
            pass
        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:  # noqa: BLE001
            pass
        if self._closing or page.is_closed():     # 等待期间会话已在拆 / 新页已关 → 不切、不重开截屏
            return
        self.page = page
        if self._on_frame is not None:        # 截屏已开 → 切到新页;未开则等 start_screencast 自然开在最新页
            await self._restart_screencast()

    async def _on_page_close(self, page) -> None:  # noqa: ANN001
        """活动页被关掉(用户关新标签/弹窗)→ 回退到仍打开的页,截屏切回去,避免黑屏。
        **会话拆除(stop/断连)期间一律不动**——否则会在正在关闭的 context 上 new_cdp_session 抛 TargetClosedError。"""
        if self._closing or self._context is None or page is not self.page:
            return
        try:
            rest = [p for p in self._context.pages if not p.is_closed()]
        except Exception:  # noqa: BLE001 —— context 已开始关闭
            return
        if not rest:
            return
        self.page = rest[-1]
        if self._on_frame is not None:
            await self._restart_screencast()

    def _capture(self, m: str, url: str, pd: str | None, ct: str, headers: dict | None = None) -> None:
        """登记一个写请求(含请求头,回放鉴权用)+ 实时推给前端诊断。"""
        if pd:
            self.requests.append({"method": m, "url": url, "post_data": pd,
                                  "content_type": ct, "headers": headers or {}})
        if self._on_request_cb is not None:
            is_json = "json" in (ct or "").lower() or (pd or "").lstrip().startswith(("{", "["))
            try:
                self._on_request_cb({"method": m, "url": url, "has_body": bool(pd),
                                     "json": bool(pd) and is_json})
            except Exception:  # noqa: BLE001
                pass

    def _on_request(self, request) -> None:  # noqa: ANN001 —— playwright Request(直录模式旁观)
        try:
            m = (request.method or "").upper()
            if m not in ("POST", "PUT", "PATCH", "DELETE"):
                return
            hd = {}
            try:
                hd = dict(request.headers or {})
            except Exception:  # noqa: BLE001
                pass
            self._capture(m, request.url, request.post_data, hd.get("content-type", ""), hd)
        except Exception:  # noqa: BLE001
            pass

    def _success_envelope(self) -> str:
        """伪造的"提交成功"响应体 —— **镜像本系统自己的成功约定**(从已抓到的成功读响应学),不写死若依 code=200。

        这样不管 SPA 前端检查 code===0 / code===200 / success===true,拦截后都能正确显示成功 → 用户能继续到
        "我的记录"页(供 fact_check 抓回查源)。学不到约定时给一个并集兜底(同时带 code/success,尽量通吃)。
        """
        import json as _json

        from dano.execution.page.request_capture import infer_success_rule
        body: dict = {"msg": "录制已拦截:抓到请求,未真正提交", "success": True}
        rule = infer_success_rule(self.reads)
        if rule and rule.get("field") and rule.get("ok_values"):
            body[rule["field"]] = rule["ok_values"][0]      # 用本系统的成功字段+成功值
        else:
            body["code"] = 200                              # 兜底:最常见约定(同时已带 success:true)
        return _json.dumps(body, ensure_ascii=False)

    async def _route(self, route, request) -> None:  # noqa: ANN001 —— 拦截模式
        from dano.execution.page.request_capture import looks_like_auth_write, looks_like_read_request
        try:
            m = (request.method or "").upper()
            url = request.url
            pd = request.post_data if m in ("POST", "PUT", "PATCH", "DELETE") else None
            # 业务写请求 → 抓下来,假装成功不真发;登录/鉴权/上传等基建写、以及 POST 形态的读/查询
            #(getXxxList/queryXxx:下拉/列表源)照常放行真发(否则录制时下拉/列表加载不出来,选不了值)
            if pd and not looks_like_auth_write(url, pd) and not looks_like_read_request(url):
                hd = {}
                try:
                    hd = dict(request.headers or {})
                except Exception:  # noqa: BLE001
                    pass
                self._capture(m, url, pd, hd.get("content-type", ""), hd)
                await route.fulfill(status=200, content_type="application/json",
                                    body=self._success_envelope())
                return
            await route.continue_()
        except Exception:  # noqa: BLE001
            try:
                await route.continue_()
            except Exception:  # noqa: BLE001
                pass

    def captured_requests(self) -> list[dict]:
        return list(self.requests)

    def captured_reads(self) -> list[dict]:
        return list(self.reads)

    def captured_form_snapshot(self) -> list[dict]:
        return list(self.form_snapshot)

    def captured_form_ax(self) -> list[dict]:
        return list(self.form_ax)

    async def await_form_ax(self, timeout: float = 8.0) -> None:
        """发布/构造字段表前调一下:等无障碍树抓取任务完成(有上限,超时就用已有结果/退回 form_snapshot)。"""
        t = self._form_ax_task
        if t is None or t.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=timeout)
        except Exception:  # noqa: BLE001 —— 超时/出错都不阻断,退回 form_snapshot
            pass

    async def _capture_form_ax(self) -> None:
        """提交瞬间抓**无障碍树(CDP)+ DOM** → build_form_spec 合成权威字段表(名/必填/角色更准)。失败不阻断录制。
        用独立短命 CDP 会话(不动截屏那路);提交被拦截、页面未跳转,此刻 DOM 完好,正是抓取时机。"""
        if self._closing or self._context is None or self.page is None or self.page.is_closed():
            return
        cdp = None
        try:
            from dano.execution.page.formspec import capture_form_ax
            cdp = await self._context.new_cdp_session(self.page)
            spec = await capture_form_ax(self.page, cdp)
            if spec:
                self.form_ax = spec
            # 可观测:权威字段表抓到几个、名字来源分布(a11y/dom/占位符)→ 录制日志一眼看出是否真生效,免猜
            src: dict = {}
            for f in spec or []:
                src[f.get("source", "?")] = src.get(f.get("source", "?"), 0) + 1
            log.info("form_ax.captured", n=len(spec or []), frames=len(getattr(self.page, "frames", []) or []),
                     by_source=src)
        except Exception as e:  # noqa: BLE001 —— a11y 抓取失败 → 退回 form_snapshot,绝不影响发布
            log.warning("form_ax.failed", error=str(e))
        finally:
            if cdp is not None:
                try:
                    await cdp.detach()
                except Exception:  # noqa: BLE001
                    pass

    def _resp_dispatch(self, response) -> None:  # noqa: ANN001 —— 同步快筛后再异步读 body
        try:
            m = (response.request.method or "").upper()
            if m == "GET" or m in ("POST", "PUT", "PATCH"):   # GET=select 候选源;写=取响应(Q3 步链 taskId)
                asyncio.create_task(self._on_response(response))
        except Exception:  # noqa: BLE001
            pass

    async def _on_response(self, response) -> None:  # noqa: ANN001 —— GET=读候选源;写=把响应贴回对应请求
        from dano.execution.page.request_capture import _READ_NOISE, as_list_payload
        try:
            m = (response.request.method or "").upper()
            url = response.url
            ct = ""
            try:
                ct = (response.headers or {}).get("content-type", "")
            except Exception:  # noqa: BLE001
                pass
            if "json" not in (ct or "").lower():
                return
            try:
                data = await response.json()
            except Exception:  # noqa: BLE001
                return
            if m in ("POST", "PUT", "PATCH"):
                # 写请求的真实响应(taskId 等)→ 贴回第一个同 url、还没响应的已抓写请求,供 Q3 步间数据流发现
                for r in self.requests:
                    if r.get("url") == url and "response_json" not in r:
                        r["response_json"] = data
                        break
                # 不 return:有些系统用 POST 查"下拉/选人"列表(带过滤条件)→ 列表型响应也当 select 候选源
            if any(n in url.lower() for n in _READ_NOISE):    # 只跳静态/流(保留字典/列表接口)
                return
            # 只留"列表型"(json 是数组 / dict 里含数组)→ 才可能是下拉/选人候选源;限规模避免存爆。
            # 提交那条写请求返回的是结果对象、非列表 → as_list_payload 为 None,不会被误当候选源。
            items = as_list_payload(data)
            if items is None:
                return
            entry = {"method": m, "url": url, "status": response.status,
                     "json": data if len(self.reads) < 60 else None, "count": len(items)}
            for i, r in enumerate(self.reads):       # 同 URL 去重:用最新一次覆盖(同接口重复抓 N 次只留 1 条 → 省内存 + 加速参数推断)
                if r.get("url") == url:
                    self.reads[i] = entry
                    return
            self.reads.append(entry)
        except Exception:  # noqa: BLE001
            pass

    def _on_record(self, source, payload: str) -> None:  # noqa: ANN001 —— expose_binding 回调
        try:
            step = json.loads(payload)
        except Exception:  # noqa: BLE001
            return
        if step.get("op") == "form_snapshot":          # 提交瞬间的表单快照 → 存起来,不当步骤
            fs = step.get("fields") or []
            if isinstance(fs, list) and fs:
                self.form_snapshot = fs
            try:
                self._form_ax_task = asyncio.create_task(self._capture_form_ax())   # 抓**无障碍树**合成权威字段表(异步)
            except Exception:  # noqa: BLE001
                pass
            return
        # 同一 locator 连续 fill/select/pick(用户改了又改/逐字符)→ 覆盖,只留最后一次
        if (self.steps and self.steps[-1].get("locator") == step.get("locator")
                and step.get("op") in ("fill", "select", "pick")):
            self.steps[-1] = step
        else:
            self.steps.append(step)
        if self._on_step is not None:
            try:
                self._on_step(step)
            except Exception:  # noqa: BLE001
                pass

    # ── 截屏流(跟随活动页:用户点开新标签/弹窗时切过去)──
    async def start_screencast(self, on_frame: Callable[[str], Awaitable[None]]) -> None:
        self._on_frame = on_frame
        await self._open_screencast()

    async def _open_screencast(self) -> None:
        """在当前活动页 self.page 上开一路截屏。切页时配合 _restart_screencast 复用。
        会话拆除中 / 页面已关 → 直接返回;new_cdp_session 自身抛错(context 关闭竞态)也吞掉,绝不冒泡成未捕获异常。"""
        if self._closing or self._context is None or self.page is None or self.page.is_closed():
            return
        try:
            cdp = await self._context.new_cdp_session(self.page)
        except Exception:  # noqa: BLE001 —— TargetClosedError 等(context/page 关闭竞态)→ 不重开,静默
            return
        self._cdp = cdp

        async def _emit(params: dict) -> None:
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                if self._on_frame is not None and cdp is self._cdp:   # 只发**活动页**的帧(切页后旧帧丢弃)
                    await self._on_frame(params["data"])              # base64 jpeg
            except Exception:  # noqa: BLE001
                pass

        cdp.on("Page.screencastFrame", lambda p: asyncio.create_task(_emit(p)))
        try:
            await cdp.send("Page.startScreencast",
                           {"format": "jpeg", "quality": 50, "maxWidth": _VIEW_W, "maxHeight": _VIEW_H})
        except Exception:  # noqa: BLE001
            pass

    async def _restart_screencast(self) -> None:
        """活动页变了(切到新标签/回退)→ 关旧页截屏,在新活动页重开,画面跟随。会话拆除中不重开。"""
        old = self._cdp
        self._cdp = None
        if old is not None:
            try:
                await old.detach()
            except Exception:  # noqa: BLE001
                pass
        if not self._closing:
            await self._open_screencast()

    # ── 输入回传(归一坐标 0~1 → 视口像素)──
    async def dispatch_input(self, ev: dict) -> None:
        if self.page is None or self.page.is_closed():    # 切页瞬间 / 活动页已关 → 丢弃,避免抛错
            return
        k = ev.get("kind")
        if k == "click":
            await self.page.mouse.click(ev.get("nx", 0) * _VIEW_W, ev.get("ny", 0) * _VIEW_H)
        elif k == "dblclick":
            await self.page.mouse.dblclick(ev.get("nx", 0) * _VIEW_W, ev.get("ny", 0) * _VIEW_H)
        elif k == "text":
            # insert_text 直接插入文本(含中文 CJK)并触发 input 事件;type 模拟物理键对 CJK 不可靠
            await self.page.keyboard.insert_text(ev.get("text", ""))
        elif k == "key":
            await self.page.keyboard.press(ev.get("key", ""))
        elif k == "scroll":
            await self.page.mouse.wheel(0, ev.get("dy", 0))

    def reset(self) -> None:
        """清空已录步骤(用户登录完后点「从这里开始录」,丢弃登录步骤,只留业务流程)。"""
        self.steps.clear()

    async def storage_state(self) -> dict | None:
        """抓当前会话登录态快照(所有 cookie + localStorage),不管系统把 token 存哪。

        用户在画面里真人登录后调用 → 回放/运行期复用这份登录态,免再登录(验证码/RSA 都已过)。
        """
        if self._context is None:
            return None
        try:
            return await self._context.storage_state()
        except Exception:  # noqa: BLE001
            return None

    def recorded_steps(self):
        """已捕获步骤 → (RecordedStep 列表, sample_inputs)。字段 key 用 assign_field_keys 统一分配
        (与 build_page_script 同序同算法 → samples key 与脚本参数 key 一致;多字段塌缩同一 std_key 也不丢,P1#6)。"""
        from dano.agent_tools.page_builder import RecordedStep, assign_field_keys
        fb_idx = [i for i, s in enumerate(self.steps) if s.get("field")]
        keys = assign_field_keys([self.steps[i]["field"] for i in fb_idx])
        keymap = dict(zip(fb_idx, keys))
        steps: list[RecordedStep] = []
        samples: dict[str, str] = {}
        for i, s in enumerate(self.steps):
            field = s.get("field") or None
            steps.append(RecordedStep(op=s["op"], locator=s.get("locator"), field=field))
            if field and s.get("op") in ("fill", "select", "pick") and s.get("value") != "":
                samples[keymap[i]] = s.get("value", "")
        return steps, samples

    def recorded_dom_options(self) -> dict:
        """录制时下拉/级联里**真实可见的选项文字**(DOM 抓的枚举地面真值)→ {字段key: [选项文字]}。
        key 与 recorded_steps 同算法分配,保持一致 → 上层据此覆盖 select 的候选快照,治"绑到几百项全量字典"。"""
        from dano.agent_tools.page_builder import assign_field_keys
        fb_idx = [i for i, s in enumerate(self.steps) if s.get("field")]
        keys = assign_field_keys([self.steps[i]["field"] for i in fb_idx])
        keymap = dict(zip(fb_idx, keys))
        out: dict[str, list] = {}
        for i, s in enumerate(self.steps):
            if s.get("op") in ("pick", "select") and s.get("options") and i in keymap:
                out[keymap[i]] = list(s["options"])
        return out

    def recorded_required_labels(self) -> set:
        """录制中标了表单 * 必填的字段(供 flatten 标 required)。key 与 recorded_steps 同算法分配,保持一致。"""
        from dano.agent_tools.page_builder import assign_field_keys
        fb_idx = [i for i, s in enumerate(self.steps) if s.get("field")]
        keys = assign_field_keys([self.steps[i]["field"] for i in fb_idx])
        return {k for i, k in zip(fb_idx, keys)
                if self.steps[i].get("required") and self.steps[i].get("op") in ("fill", "select", "pick")}

    async def stop(self) -> None:
        self._closing = True         # 先置位:此后任何 page close 事件都不再重开截屏(避免在关闭中的 context 上 new_cdp_session)
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:  # noqa: BLE001
                    pass
        self._context = self._browser = self._pw = self.page = None
