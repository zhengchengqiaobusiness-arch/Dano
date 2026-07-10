"""方式B:服务端托管浏览器的「网页内录制」会话。

客户在前端网页里操作我们托管的浏览器(截屏流投到网页 + 点击/键盘回传),注入页面的录制器
把真实 DOM 事件转成**语义步骤**(label/role/placeholder/name/text 定位,绝不用坐标)推回后端。
客户全程**免安装、免命令行**。录完 → 抓请求 → FlowSpec 工作台 → 发布页面 Skill。

三层:① 截屏(CDP Page.startScreencast → base64 jpeg 帧)② 输入回传(归一坐标 → page.mouse/keyboard)
③ 动作捕获(注入 _RECORDER_JS,事件→语义步骤→expose_binding 回 Python)。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import structlog

from dano.shared.std_fields import ALL_STD_FIELDS

log = structlog.get_logger(__name__)


def _std_key(field: str) -> str:
    fl = (field or "").strip().lower()
    for std in ALL_STD_FIELDS:
        if fl == std.key.lower() or fl == std.label.lower() or fl in {a.lower() for a in std.aliases}:
            return std.key
    return (field or "").strip()


def assign_field_keys(raw_fields: list[str | None]) -> list[str]:
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

_VIEW_W, _VIEW_H = 1280, 800
_CAST_W, _CAST_H = 1024, 640
_CAST_QUALITY = 50
_CAST_ACTIVE_FPS = 20
_CAST_IDLE_FPS = 6
_CAST_ACTIVE_WINDOW_S = 6.0
_SAFE_KEY_RE = re.compile(
    r"^(?:(?:Control|Shift|Alt|Meta)\+){0,3}"
    r"(?:Enter|Tab|Backspace|Delete|Escape|Home|End|PageUp|PageDown|"
    r"ArrowUp|ArrowDown|ArrowLeft|ArrowRight|A|Z|Y)$"
)
_PLAIN_KEYS = {
    "Enter", "Tab", "Backspace", "Delete", "Escape", "Home", "End",
    "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
}
_CTRL_META_KEYS = {"A", "Z", "Y", "Enter", "Backspace"}
_SHIFT_KEYS = {"Tab", "Enter"}


def _safe_recorder_key(key: str) -> bool:
    if not key or not _SAFE_KEY_RE.match(key):
        return False
    parts = key.split("+")
    base = parts[-1]
    mods = set(parts[:-1])
    if not mods:
        return base in _PLAIN_KEYS
    if "Alt" in mods:
        return False
    if mods == {"Shift"}:
        return base in _SHIFT_KEYS
    if mods <= {"Control", "Meta"}:
        return base in _CTRL_META_KEYS
    return False

# 诊断消息截断上限(防爆,统一:console/pageerror/requestfailed 一致)
_DIAG_MSG_MAX = 2000


def _parse_url_query(url: str) -> dict:
    """URL 的 query string → {key: [values]}。空 / 无 query → {}。给 all_requests["query"] 用。"""
    try:
        return parse_qs(urlparse(url or "").query) or {}
    except Exception:  # noqa: BLE001
        return {}

# 注入到每个页面的录制器:把表单输入/选择/提交点击转成语义步骤,推回 window.__danoRecord。
_RECORDER_JS = r"""() => {
  if (window.__danoRecorderInstalled) return;
  window.__danoRecorderInstalled = true;
  // 通用语义引擎(与框架/语言/公司无关):ARIA role + accessible name 优先,文本/属性兜底。
  // 不按标签/class 白名单,故 Element-UI / Ant Design / 原生 / 任意自定义控件一视同仁。
  var SUBMIT = ['提交','保存','确定','确认','申请','发起','送出','申报','通过','归档','完单','审核','立案','接单','发布','完成','结案','结算','新建','新增','编辑','更新','导入','导出','打印','submit','save','ok','confirm','apply','approve','finish','archive','review'];
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
  function requiredOf(el) {                       // 该字段是否必填:读表单 * 标记(通用,跨 Element-UI / Ant / 原生)
    try {
      if (el.required || el.getAttribute('aria-required') === 'true') return true;
      var item = el.closest('.el-form-item,.ant-form-item,[class*="form-item"],[class*="form_item"]');
      if (item && /required/i.test(item.className)) return true;   // el is-required / ant-form-item-required
      var lab = item && item.querySelector('label');
      if (lab && lab.textContent.indexOf('*') >= 0) return true;   // 少数把 * 直接写进 label 文本
    } catch (e) {}
    return false;
  }
  function emit(op, loc, value, field, required, options) {
    if (!loc || onLoginPage()) return;            // 登录页上的任何操作一律不录(自动跳过登录,免手点「从这里开始录」)
    try { window.__danoRecord(JSON.stringify({ op: op, locator: loc, value: value || '', field: field || '', required: !!required, options: options || [] })); } catch (e) {}
  }
  var pendingFill = {};
  var fillTimers = {};
  function scheduleFill(el) {
    try {
      var loc = locateField(el);
      if (!loc) return;
      pendingFill[loc] = { el: el, value: el.value, field: fieldOf(loc), required: requiredOf(el) };
      if (fillTimers[loc]) clearTimeout(fillTimers[loc]);
      fillTimers[loc] = setTimeout(function () { flushFill(loc); }, 300);
    } catch (e) {}
  }
  function flushFill(loc) {
    try {
      var p = pendingFill[loc];
      if (!p) return;
      delete pendingFill[loc];
      if (fillTimers[loc]) { clearTimeout(fillTimers[loc]); delete fillTimers[loc]; }
      emit('fill', loc, p.value, p.field, p.required);
    } catch (e) {}
  }
  function flushElementFill(el) {
    try {
      var loc = locateField(el);
      if (loc) {
        var pending = pendingFill[loc];
        if (pending) pending.value = el.value;
        else pendingFill[loc] = { el: el, value: el.value, field: fieldOf(loc), required: requiredOf(el) };
        flushFill(loc);
      }
    } catch (e) {}
  }
  window.__danoFlushRecorder = function () {
    try {
      Object.keys(pendingFill).forEach(function (loc) { flushFill(loc); });
    } catch (e) {}
  };
  // 下拉/级联弹层里**当前可见的选项文字**(地面真值枚举):工作日加班/周末加班/节假日加班 …
  // —— 直接读 DOM,胜过拿提交值去网络字典里猜命中(治"加班类型/请假类型绑到几百项全量字典")。
  // **通用 ARIA + 框架兜底**,不绑任何特定公司/项目:
  //   ① 隐/显 role=option / menuitem / menuitemradio / menuitemcheckbox / treeitem
  //   ② W3C aria-activedescendant / aria-selected 标识选中
  //   ③ 主流框架特定 class 兜底(向后兼容);找不到时再用 li/span 兜底
  // 框架无关,跨 Vant/Bootstrap/MUI/自研 div 也能命中。
  function popupOptions(pop) {
    if (!pop) return [];
    try {
      var aria_sel = '[role="option"],[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="treeitem"]';
      var frame_sel = '.el-select-dropdown__item,.ant-select-item-option,.ant-cascader-menu-item,' +
                      '.el-cascader-node,.el-autocomplete-suggestion li,' +
                      '.van-picker-column__item,.van-action-sheet__item,.van-dropdown-item,' +
                      '.mat-mdc-option,.mat-mdc-menu-item,.cdk-overlay-pane [role="option"],' +
                      '.bp5-menu-item,.bp5-popover-content li,' +
                      '.v-list-item--active,.v-list-item,' +
                      '.q-item,.q-menu .q-item';
      var fallback_sel = 'li:not(:empty),span[role="option"],button[role="option"]';
      // 三段优先级合并去重
      var seen_labels = {};
      var out = [];
      function harvest(nodes) {
        for (var i = 0; i < nodes.length; i++) {
          var n = nodes[i];
          // 隐藏/不可见 + 不是 fixed 定位的跳过(但 fixed-position 不漏,比如 Ant v5 弹层定位是 fixed)
          try {
            var cs = getComputedStyle(n);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            if (cs.position !== 'fixed' && n.offsetParent === null) continue;
          } catch (_) {}
          var t = clean(n.innerText || n.textContent || '');
          // 跳过空 / 纯标签符号(过滤 placeholder 灰文字 / *必填)
          if (!t || t.length > 60 || /^\s*[\*\-•]\s*$/.test(t)) continue;
          if (!seen_labels[t]) { seen_labels[t] = 1; out.push(t); }
          if (out.length >= 500) break;
        }
      }
      harvest(pop.querySelectorAll(aria_sel));
      if (out.length === 0) harvest(pop.querySelectorAll(frame_sel));
      if (out.length === 0) harvest(pop.querySelectorAll(fallback_sel));
      // 干掉弹层里的"搜索框 placeholder"、"清空"等按钮文本(只 trim,不暴力过滤):
      return out.map(function (t) { return t.replace(/^[　\s]+|[　\s]+$/g, ''); })
                .filter(function (t) { return t && t !== '清空' && t !== '清除' && t !== '搜索'; });
    } catch (e) { return []; }
  }
  // 原生 <select> 的全部 <option> 文字(去掉占位空项),返回 [{label, value}] 让标签与提交值都可追溯
  function nativeOptions(el) {
    try {
      var out = [];
      for (var i = 0; i < el.options.length; i++) {
        var t = clean(el.options[i].text);
        if (t) out.push({ label: t, value: el.options[i].value });
      }
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
    // 密码/支付敏感字段绝不录(M-A2):密码框、信用卡号、CVC、有效期、卡名、银行账号等
    if (tag === 'input' && ty === 'password') return;
    var ac = (el.getAttribute && (el.getAttribute('autocomplete') || '') + '').toLowerCase();
    if (ac && /(cc-|card|cvv|cvc|exp|iban|account-number)/.test(ac)) return;     // 信用卡/银行账号属性
    // 已知 sensitive class / id 模式
    var sig = ((el.id || '') + ' ' + (el.name || '') + ' ' + (el.className || '')).toLowerCase();
    if (/(credit.?card|card.?no|cvv|cvc|secret.?code|pay.?password|bank.?account)/.test(sig)) return;
    // 密码框绝不录(安全);非文本类型跳过
    if (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0)) {
      scheduleFill(el);
    }
  }, true);
  document.addEventListener('change', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0)) flushElementFill(el);
    if (tag === 'select') { var l1 = locateField(el); emit('select', l1, el.value, fieldOf(l1), requiredOf(el), nativeOptions(el)); }
    else if (tag === 'input' && ty === 'file') { var l2 = locateField(el); emit('upload', l2, el.value || '', fieldOf(l2), requiredOf(el)); }
  }, true);
  document.addEventListener('blur', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0)) flushElementFill(el);
  }, true);
  // 选择型控件参数化(框架无关):日期/下拉/级联是"点"出来的,不该录成写死的点击,而该录成一个
  // pick 参数步(触发框 + 选中的最终值)。识别弹层 + 触发框,选完读触发框 input 的最终值。
  // **ARIA 优先 + 框架兜底**:任何带 [role="listbox|menu|tree|grid"] 的容器(隐式或显式)都视为弹层。
  var POPUP = '[role="listbox"],[role="menu"],[role="tree"],' +
              '[aria-modal="true"],.el-picker-panel,.el-select-dropdown,.el-cascader__dropdown,' +
              '.el-time-panel,.el-time-spinner,.el-date-table,.el-month-table,.el-year-table,' +
              '.el-autocomplete-suggestion,.el-tooltip__popper,' +
              '.ant-picker-dropdown,.ant-select-dropdown,.ant-cascader-dropdown,.ant-dropdown-menu,' +
              '.van-popup,.van-action-sheet,.van-picker,.van-dropdown-item__wrapper,' +
              '.mat-mdc-select-panel,.mat-mdc-menu-panel,.cdk-overlay-pane,' +
              '.bp5-popover,.bp5-menu,.bp5-select-popover,.bp5-popover-content,' +
              '.v-overlay-container,.v-list-internal,.v-overlay__content,' +
              '.q-menu,.q-dialog__inner,.q-select__menu,.q-tooltip';
  // 触发框:W3C aria-haspopup / readonly 优先 + Element/Ant/Vant 框架特定 class 兜底
  // 触发框 = 用户点击会弹出选项面板的那个元素
  var TRIGGER_CLS = '[aria-haspopup],[aria-expanded][aria-controls],[aria-autocomplete],' +
                    'input[readonly][type="text"],input[readonly][type="search"],select,' +
                    '.el-date-editor,.el-select,.el-cascader,.el-time-select,.el-time-picker,' +
                    '.ant-picker,.ant-select,.ant-cascader-picker,' +
                    '.van-field,.van-picker__wrapper,.van-dropdown-menu__item,.van-action-sheet-header,' +
                    '.mat-mdc-select-trigger,.mat-select-trigger,' +
                    '.bp5-html-select,.bp5-popover-target,.bp5-select,' +
                    '.v-field,.v-input,' +
                    '.q-field,.q-select,.q-input';
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
    if (!v && inp) {                                       // H4+H10 修复:input.value 为空时,优先读 aria-valuetext / title(Element Plus/Ant Design 把选中值放这里)
        v = clean(inp.getAttribute('aria-valuetext') || inp.getAttribute('title') || '');
    }
    if (!v) {                                              // 兜底:读 innerText,但过滤 placeholder 灰色文本(否则与 prevVal 永远相等,pollPick 超时放弃)
        var txt = clean(trig.innerText || '');
        // M-A1 修复:用真正常见的 placeholder 选择器 + opacity < 0.6 + Element Plus `el-select__placeholder` 类
        // 框架透明色文本占位符在 Ant Design v5 是独立 span(opacity < 0.5);Element Plus 用 `.el-select__placeholder`(默认隐藏层)
        var sels = '[class*="placeholder" i],[class*="el-select__placeholder"],[placeholder]';
        var placeholders = trig.querySelectorAll ? trig.querySelectorAll(sels) : [];
        for (var i = 0; i < placeholders.length; i++) {
            try {
                var ph_el = placeholders[i];
                var op = getComputedStyle(ph_el).opacity;
                var isPlaceholder = (op && parseFloat(op) < 0.6)
                    || (ph_el.tagName === 'INPUT' && ph_el.value === '' && ph_el.getAttribute('placeholder'))
                    || /el-select__placeholder/.test(ph_el.className || '');
                if (isPlaceholder) {
                    var ph = clean(ph_el.textContent || ph_el.getAttribute('placeholder') || '');
                    if (ph && txt.indexOf(ph) >= 0) txt = txt.split(ph).join('');
                }
            } catch (_) { /* 跨域元素跳过 */ }
        }
        v = clean(txt);
    }
    return v;
  }
  // 选中值落定检测:**不靠固定延时**,轮询显示值直到变成「非空且与点击前不同」才记 pick
  // —— 异步/远程搜索/级联(值晚一点回填)也能稳抓,不会读太早拿空值而漏掉(框架无关)。
  function pollPick(trig) {
    if (pickTimer) { clearInterval(pickTimer); pickTimer = null; }
    if (!trig) return;
    lastPickOptions = [];                                 // H9 修复:启动即清,避免快速切换两个下拉时旧 timer 用新值错位
    var tries = 0;
    pickTimer = setInterval(function () {
      tries++;
      var v = pickVal(trig);
      if (v && v !== prevVal) {                         // 显示值已落定(与点击前不同)→ 记 pick
        clearInterval(pickTimer); pickTimer = null;
        var inp = trig.querySelector ? trig.querySelector('input') : null;
        var loc = locateField(inp || trig);
        if (loc) emit('pick', loc, v, fieldOf(loc), requiredOf(trig) || (inp && requiredOf(inp)), lastPickOptions);
      } else if (tries >= 25) { clearInterval(pickTimer); pickTimer = null; }   // ~2.5s 仍没变 → 放弃
    }, 100);
  }
  document.addEventListener('click', function (e) {
    // A) 点在日期/下拉弹层内 = 正在选择 → 不记这次点击;先把**此刻弹层里可见的选项**抓下来(地面真值枚举),
    //    再轮询触发框值落定后随 pick 一起回传(选完即生效)
    if (e.target.closest && e.target.closest(POPUP)) {
      var _opts = popupOptions(e.target.closest(POPUP)); if (_opts.length) lastPickOptions = _opts;
      pollPick(activeTrigger); return;
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
    // D) 普通可点元素(按钮/卡片/菜单/链接)
    var el = target(e.target); if (!el) return;
    var loc = locateClickable(el); if (!loc) return;
    var role = roleOf(el); var name = accName(el);
    var isSubmit = role === 'button' && SUBMIT.some(function (h) { return name.toLowerCase().indexOf(h) >= 0; });
    emit(isSubmit ? 'submit' : 'click', loc, '', '');
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
        # P0-1:全量捕获(基础事实)。先抓全再筛 → 治"GET 业务接口被早筛丢"等根因。
        # 不管 method / 业务角色,只要页面发出,就落一行,供后续 P0-2 角色分类 + P0-3 依赖闭包使用。
        # 字段:method/url/headers/query/post_data/response_json/status/content_type/timestamp/index。
        self.all_requests: list[dict] = []
        # P0-1:诊断事件(console/pageerror/requestfailed)→ 排查"接口成功但页面报错"等隐蔽故障。
        self.diagnostics: list[dict] = []
        self._req_counter: int = 0          # 顺序号,作为 all_requests[i]["index"] 与 diagnostics 关联锚点
        self._request_fact_index: dict[int, int] = {}
        self._page_counter: int = 0
        self._frame_counter: int = 0
        self._page_ids: dict[int, str] = {}
        self._frame_ids: dict[int, str] = {}
        self._on_step = on_step
        self._on_request_cb = on_request    # 实时把抓到的请求推给前端(诊断可见)
        # 拦截提交:点提交时抓到业务写请求后,假装成功、不真发给服务器 → 录制不产生真实记录
        self._intercept = intercept_submit
        self._capture_reads = capture_reads
        self._pw = None
        self._browser = None
        self._context = None
        self._cdp = None
        self._on_frame: Callable[[dict], Awaitable[None]] | None = None  # 截屏回调(切活动页时复用)
        self._frame_seq = 0
        self._last_frame_sent_at = 0.0
        self._last_activity_at = time.monotonic()
        self._closing = False        # stop()/断连中:页面 close 事件不再重开截屏(避免在已关 context 上 new_cdp_session 抛错)
        self.page = None

    def _page_id(self, page) -> str:  # noqa: ANN001
        if page is None:
            return ""
        key = id(page)
        if key not in self._page_ids:
            self._page_counter += 1
            self._page_ids[key] = f"page_{self._page_counter}"
        return self._page_ids[key]

    def _frame_id(self, frame) -> str:  # noqa: ANN001
        if frame is None:
            return ""
        key = id(frame)
        if key not in self._frame_ids:
            self._frame_counter += 1
            self._frame_ids[key] = f"frame_{self._frame_counter}"
        return self._frame_ids[key]

    def _request_scope(self, request) -> dict[str, str]:  # noqa: ANN001
        """Best-effort page/frame anchors for RequestGraph facts."""
        frame = None
        page = None
        try:
            frame = request.frame
        except Exception:  # noqa: BLE001
            frame = None
        if frame is not None:
            try:
                page = frame.page
            except Exception:  # noqa: BLE001
                page = None
        return {"page_id": self._page_id(page), "frame_id": self._frame_id(frame)}

    def _mark_active(self) -> None:
        """页面近期有用户输入、导航、网络或录制事件时保持较高帧率,避免画面看起来卡住。"""
        self._last_activity_at = time.monotonic()

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
        # P0-1:诊断事件(console/pageerror/requestfailed)。Playwright 的 BrowserContext **没有** pageerror/
        # console/requestfailed 事件(只有 Page 有),所以挂在 self.page;新页在 _on_new_page 里也挂。
        self._attach_diag_handlers(self.page)
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
        self._page_id(self.page)
        # C1 修复:主 page 创建后**立刻**挂诊断(否则首屏 console/pageerror/requestfailed 全失)
        self._attach_diag_handlers(self.page)
        # SPA 常不触发 "load"(长连接/轮询挂着)→ 用 domcontentloaded,否则 goto 卡到超时(与运行期 driver 一致)
        await self.page.goto(full, wait_until="domcontentloaded")

    async def _on_new_page(self, page) -> None:  # noqa: ANN001 —— 新标签页/新窗口(context "page" 事件)
        """用户操作打开了新页(target=_blank / window.open / 弹窗)→ 跟随:设为活动页,把截屏切过去。
        否则新页在后台,用户看不到、也操作不到,表现为"新页打不开"。录制绑定/路由已在 context 级,自动覆盖新页。

        P0-1:诊断事件是 page 级,新页要重新挂(否则新页 pageerror 进不来)。"""
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
        self._mark_active()
        self.page = page
        self._attach_diag_handlers(page)         # P0-1:新页挂诊断(浏览器无 context 级 pageerror)
        if self._on_frame is not None:        # 截屏已开 → 切到新页;未开则等 start_screencast 自然开在最新页
            await self._restart_screencast()

    def _attach_diag_handlers(self, page) -> None:  # noqa: ANN001
        """在指定 page 上挂 console/pageerror/requestfailed 三个诊断事件。重复挂安全(同名 handler 会去重)。"""
        try:
            page.on("console", self._on_console)
            page.on("pageerror", self._on_pageerror)
            page.on("requestfailed", self._on_requestfailed)
            page.on("framenavigated", lambda _frame: self._mark_active())
            page.on("domcontentloaded", lambda: self._mark_active())
            page.on("load", lambda: self._mark_active())
        except Exception:  # noqa: BLE001
            pass

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
        self._mark_active()
        self.page = rest[-1]
        if self._on_frame is not None:
            await self._restart_screencast()

    def _record_all(self, m: str, url: str, *, pd: str | None = None, query: dict | None = None,
                    headers: dict | None = None, status: int | None = None,
                    response_json=None, content_type: str = "", page_id: str = "",
                    frame_id: str = "") -> int:
        """全量捕获一条网络请求(GET/POST/PUT/PATCH/DELETE 都记)。返回 index。

        - 不做任何 method / 角色过滤:先抓全,后续 P0-2 角色分类 + P0-3 依赖闭包基于这份原数据工作。
        - requestfailed / 异常响应也记,response_json=None 时仍占行,便于"接口没返回"等隐蔽故障回溯。
        - query 字段:调用方显式传就用;否则从 url 自动解析(治"GET 携带什么参数"看不到)。
        """
        idx = self._req_counter
        self._req_counter += 1
        request_id = f"req_{idx}"
        entry: dict = {
            "index": idx,
            "request_id": request_id,
            "sequence": idx,
            "page_id": page_id,
            "frame_id": frame_id,
            "method": (m or "").upper(),
            "url": url or "",
            "headers": dict(headers or {}),
            "query": dict(query) if query is not None else _parse_url_query(url),
            "post_data": pd,
            "response_json": response_json,
            "status": status,
            "content_type": content_type,
            "timestamp": int(time.time() * 1000),
            # P0-2:角色分类字段。响应未到时先按现有信息初分(_classify_entry),响应落地后再 _classify_entry 一次。
            # classify_field 缺失 = 还没分类过(兜底用)。
        }
        self.all_requests.append(entry)
        self._classify_entry(entry)
        return idx

    def _attach_response(self, *, url: str, method: str, response_json, status, content_type: str,
                         request_index: int | None = None) -> bool:
        """优先按请求实例精确贴回响应，缺少实例锚点时才退回 url+method。

        集中处理反查 + 改字段,避免 _route / _on_response / 后续 P0-3 依赖闭包都各自遍历 all_requests。
        P0-2:贴完响应后顺手再分类一次(此时 read_option / business_get 才能判准)。"""
        candidates = self.all_requests
        if request_index is not None:
            candidates = [r for r in self.all_requests if r.get("index") == request_index]
        for r in reversed(candidates):
            if r.get("url") == url and r.get("method") == method and r.get("response_json") is None:
                r["response_json"] = response_json
                r["status"] = status
                r["content_type"] = content_type or r.get("content_type", "")
                self._classify_entry(r)                  # P0-2:响应落地后再分一次(更准)
                return True
        return False

    def _classify_entry(self, entry: dict) -> None:
        """P0-2:对单条 all_requests entry 调用 classify_network_request,把结果写到 entry 上。

        失败/异常时静默留空(下游 captured_all_requests 会兜底重试)。"""
        try:
            from dano.execution.page.request_capture import classify_network_request
            cls = classify_network_request({
                "method": entry.get("method"),
                "url": entry.get("url"),
                "post_data": entry.get("post_data"),
                "response_json": entry.get("response_json"),
            })
            entry["role"] = cls["role"]
            entry["keep"] = cls["keep"]
            entry["reason"] = cls["reason"]
            entry["confidence"] = cls["confidence"]
        except Exception:  # noqa: BLE001 —— 分类失败不影响事实链路,留空给兜底
            pass

    def _record_diag(self, kind: str, payload: dict) -> None:
        """记录一条诊断事件:console/pageerror/requestfailed。

        统一结构 {type, level?, message, url?, timestamp, request_index?},供 P0-6 review_items 与人工排错使用。
        request_index 关联到 all_requests 里同源请求(失败请求 → request_index=关联到该请求的 index)。"""
        rec = {"type": kind, "timestamp": int(time.time() * 1000)}
        rec.update({k: v for k, v in (payload or {}).items() if v is not None})
        self.diagnostics.append(rec)

    def _capture(self, m: str, url: str, pd: str | None, ct: str, headers: dict | None = None,
                 request_index: int | None = None) -> None:
        """登记一个写请求(含请求头,回放鉴权用)+ 实时推给前端诊断。

        P0-1 收敛:本函数只负责写 self.requests 与触发 on_request_cb;all_requests 由调用方
        (_route / _on_request)在调本函数**之前**自己 _record_all,避免同一请求被记两次。"""
        if pd:
            self.requests.append({"method": m, "url": url, "post_data": pd,
                                  "content_type": ct, "headers": headers or {},
                                  "request_index": request_index})
        if self._on_request_cb is not None:
            is_json = "json" in (ct or "").lower() or (pd or "").lstrip().startswith(("{", "["))
            try:
                self._on_request_cb({"method": m, "url": url, "has_body": bool(pd),
                                     "json": bool(pd) and is_json})
            except Exception:  # noqa: BLE001
                pass

    def _on_request(self, request) -> None:  # noqa: ANN001 —— playwright Request(直录模式旁观)
        try:
            self._mark_active()
            m = (request.method or "").upper()
            url = request.url
            pd = request.post_data if m in ("POST", "PUT", "PATCH", "DELETE") else None
            hd = {}
            try:
                hd = dict(request.headers or {})
            except Exception:  # noqa: BLE001
                pass
            # P0-1:GET 也落 all_requests(全量捕获,治"业务 GET 前置接口被早筛丢")。
            # _record_all 是 all_requests 的唯一写入点;_capture 只负责写 requests(写请求才走)。
            request_index = self._record_all(
                m, url, pd=pd, headers=hd, content_type=hd.get("content-type", ""),
                **self._request_scope(request),
            )
            self._request_fact_index[id(request)] = request_index
            if m in ("POST", "PUT", "PATCH", "DELETE"):
                self._capture(m, url, pd, hd.get("content-type", ""), hd, request_index)
        except Exception:  # noqa: BLE001
            pass

    def _success_envelope(self) -> str:
        """伪造的"提交成功"响应体 —— **镜像本系统自己的成功约定**(从已抓到的成功读响应学),不写死若依 code=200。

        这样不管 SPA 前端检查 code===0 / code===200 / success===true,拦截后都能正确显示成功 → 用户能继续到
        "我的记录"页(供 fact_check 抓回查源)。学不到约定时给一个并集兜底(同时带 code/success,尽量通吃)。
        """
        import json as _json

        from dano.execution.page.request_capture import infer_success_rule
        body: dict = {
            "msg": "录制已拦截:抓到请求,未真正提交",
            "message": "录制已拦截:抓到请求,未真正提交",
            "success": True,
            "ok": True,
            "status": 200,
            "data": {"recorded": True, "success": True},
        }
        rule = infer_success_rule(self.reads)
        if rule and rule.get("field") and rule.get("ok_values"):
            body[rule["field"]] = rule["ok_values"][0]      # 用本系统的成功字段+成功值
        else:
            body["code"] = 200                              # 兜底:最常见约定(同时已带 success:true)
        return _json.dumps(body, ensure_ascii=False)

    async def _route(self, route, request) -> None:  # noqa: ANN001 —— 拦截模式
        from dano.execution.page.request_capture import looks_like_auth_write, looks_like_read_request
        try:
            self._mark_active()
            m = (request.method or "").upper()
            url = request.url
            pd = request.post_data if m in ("POST", "PUT", "PATCH", "DELETE") else None
            hd = {}
            try:
                hd = dict(request.headers or {})
            except Exception:  # noqa: BLE001
                pass
            ct = hd.get("content-type", "")
            # P0-1:GET 也落 all_requests(全量捕获)。后续 P0-3 依赖闭包要靠它发现"业务 GET 前置接口"。
            request_index = self._record_all(
                m, url, pd=pd, headers=hd, content_type=ct, **self._request_scope(request),
            )
            self._request_fact_index[id(request)] = request_index
            # H7 修复:multipart/form-data 上传(文件/附件)必须真发,否则文件丢失但 UI 显示成功
            # multipart body 不在 request.post_data,而在 post_data_buffer,pd 必为 None,自然走 continue_,但要
            # 在此显式再判一次 content_type 兜底(部分框架 multipart 也会塞进 post_data)
            if (ct or "").lower().startswith("multipart/"):
                await route.continue_()
                return
            # 业务写请求 → 抓下来,假装成功不真发;登录/鉴权/上传等基建写、以及 POST 形态的读/查询
            #(getXxxList/queryXxx:下拉/列表源)照常放行真发(否则录制时下拉/列表加载不出来,选不了值)
            if pd and not looks_like_auth_write(url, pd) and not looks_like_read_request(url, pd):
                self._capture(m, url, pd, ct, hd, request_index)
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

    def captured_all_requests(self) -> list[dict]:
        """P0-1:全量网络请求(GET/POST/PUT/PATCH/DELETE 都记)。先抓全再筛 → P0-3 依赖闭包基于此。

        P0-2:每条 entry 已带 role/keep/reason/confidence(由 _classify_entry 写入,响应落地后再分一次);
        万一某条漏分类(异常路径),返回前兜底再分一次。

        返回不可变副本,避免外部误改污染内部状态。每条字段:index/method/url/headers/query/post_data
        /response_json/status/content_type/timestamp/role/keep/reason/confidence。"""
        # 兜底:漏分类的 entry 在返回前再分一次
        for r in self.all_requests:
            if "role" not in r:
                self._classify_entry(r)
        return [dict(r) for r in self.all_requests]

    def captured_diagnostics(self) -> list[dict]:
        """P0-1:诊断事件(console/pageerror/requestfailed)。返回不可变副本。

        每条字段:type/level?(console 用)/message/url?/timestamp/request_index?(requestfailed 用)。"""
        return [dict(d) for d in self.diagnostics]

    def recorded_raw_steps(self) -> list[dict]:
        """返回原始录制步骤副本。用于 finalize 前 flush 后补齐前端尚未收到的最后输入。"""
        return [dict(s) for s in self.steps]

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
            self._mark_active()
            m = (response.request.method or "").upper()
            url = response.url
            ct = ""
            try:
                ct = (response.headers or {}).get("content-type", "")
            except Exception:  # noqa: BLE001
                pass
            # P0-1:全量捕获响应(JSON body)→ 贴回 all_requests 同源记录(P0-3 依赖闭包靠它发现 step 串联)。
            # 写请求同时贴回 self.requests(Q3 步链 taskId);读候选源走 as_list_payload 单独进 self.reads。
            # 容错:content-type 不是 JSON 也再试一次 response.json()(治"没设 ct 但 body 是 JSON 文本")。
            if "json" in (ct or "").lower():
                try:
                    data = await response.json()
                except Exception:  # noqa: BLE001
                    data = None
                if data is not None:
                    request_index = self._request_fact_index.get(id(response.request))
                    self._attach_response(
                        url=url, method=m, response_json=data,
                        status=response.status, content_type=ct,
                        request_index=request_index,
                    )
                    if m in ("POST", "PUT", "PATCH"):
                        for r in self.requests:
                            exact = request_index is not None and r.get("request_index") == request_index
                            fallback = request_index is None and r.get("url") == url
                            if (exact or fallback) and "response_json" not in r:
                                r["response_json"] = data
                                break
                    # 不 return:有些系统用 POST 查"下拉/选人"列表(带过滤条件)→ 列表型响应也当 select 候选源
            if any(n in url.lower() for n in _READ_NOISE):    # 只跳静态/流(保留字典/列表接口)
                return
            # 只留"列表型"(json 是数组 / dict 里含数组)→ 才可能是下拉/选人候选源;限规模避免存爆。
            # 提交那条写请求返回的是结果对象、非列表 → as_list_payload 为 None,不会被误当候选源。
            data_for_list = data if 'data' in locals() else None
            if data_for_list is None:
                try:
                    data_for_list = await response.json()
                except Exception:  # noqa: BLE001
                    return
            items = as_list_payload(data_for_list)
            if items is None:
                return
            self.reads.append({"method": m, "url": url, "status": response.status,
                               "json": data_for_list if len(self.reads) < 60 else None,
                               "count": len(items)})
        except Exception:  # noqa: BLE001
            pass

    def _on_record(self, source, payload: str) -> None:  # noqa: ANN001 —— expose_binding 回调
        try:
            step = json.loads(payload)
        except Exception:  # noqa: BLE001
            return
        self._mark_active()
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

    # ── P0-1 诊断事件:console / pageerror / requestfailed ──
    def _on_console(self, msg) -> None:  # noqa: ANN001 —— context 级 console 事件
        try:
            self._record_diag("console", {
                "level": (msg.type or "log"),  # log/info/warn/error/debug
                "message": (msg.text or "")[:_DIAG_MSG_MAX],
            })
        except Exception:  # noqa: BLE001
            pass

    def _on_pageerror(self, err) -> None:  # noqa: ANN001 —— 页面 JS 异常
        try:
            self._record_diag("pageerror", {
                "level": "error",
                "message": (str(err) or "")[:_DIAG_MSG_MAX],
            })
        except Exception:  # noqa: BLE001
            pass

    def _on_requestfailed(self, request) -> None:  # noqa: ANN001 —— 请求失败(网络/CORS/超时/aborted)
        try:
            url = request.url
            # 关联到 all_requests 同源记录(若有):requestfailed 触发后 _record_all 仍会先于该事件登记(顺序不绝对,
            # 这里用"最近一条同 url/method 未失败的记录"做软关联)。
            linked = None
            for r in reversed(self.all_requests):
                if r.get("url") == url and r.get("method") == (request.method or "").upper():
                    linked = r
                    break
            failure_text = ""
            try:
                failure_text = (getattr(request.failure, "error_text", "") or "")[:_DIAG_MSG_MAX]
            except Exception:  # noqa: BLE001
                pass
            self._record_diag("requestfailed", {
                "level": "error",
                "message": failure_text or "request failed",
                "url": url,
                **({"request_index": linked["index"]} if linked else {}),
            })
        except Exception:  # noqa: BLE001
            pass

    # ── 截屏流(跟随活动页:用户点开新标签/弹窗时切过去)──
    async def start_screencast(self, on_frame: Callable[[dict], Awaitable[None]]) -> None:
        self._on_frame = on_frame
        # H6 修复:重连 / 多次调用前先 detach 旧 CDP session,避免新 session 收帧 + 旧 session 句柄泄漏
        if self._cdp is not None:
            try:
                await self._cdp.detach()
            except Exception:  # noqa: BLE001
                pass
            self._cdp = None
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
                    now = time.monotonic()
                    active = (now - self._last_activity_at) <= _CAST_ACTIVE_WINDOW_S
                    min_gap = 1.0 / (_CAST_ACTIVE_FPS if active else _CAST_IDLE_FPS)
                    if now - self._last_frame_sent_at < min_gap:
                        return
                    self._last_frame_sent_at = now
                    self._frame_seq += 1
                    await self._on_frame({"seq": self._frame_seq, "data": params["data"]})  # base64 jpeg
            except Exception:  # noqa: BLE001
                pass

        cdp.on("Page.screencastFrame", lambda p: asyncio.create_task(_emit(p)))
        try:
            await cdp.send("Page.startScreencast",
                           {"format": "jpeg", "quality": _CAST_QUALITY,
                            "maxWidth": _CAST_W, "maxHeight": _CAST_H})
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
        self._mark_active()
        k = ev.get("kind")
        if k == "click":
            await self.page.mouse.click(ev.get("nx", 0) * _VIEW_W, ev.get("ny", 0) * _VIEW_H)
        elif k == "dblclick":
            await self.page.mouse.dblclick(ev.get("nx", 0) * _VIEW_W, ev.get("ny", 0) * _VIEW_H)
        elif k == "text":
            # insert_text 直接插入文本(含中文 CJK)并触发 input 事件;type 模拟物理键对 CJK 不可靠
            await self.page.keyboard.insert_text(ev.get("text", ""))
        elif k == "key":
            key = str(ev.get("key") or "")
            if _safe_recorder_key(key):
                await self.page.keyboard.press(key)
        elif k == "scroll":
            await self.page.mouse.wheel(0, ev.get("dy", 0))

    async def flush_recording(self) -> None:
        """把页面端防抖中的 fill 立即推回 Python,用于 finalize/reset/stop 前收口。"""
        if self.page is None or self.page.is_closed():
            return
        try:
            await self.page.evaluate("window.__danoFlushRecorder && window.__danoFlushRecorder()")
            await self.page.wait_for_timeout(80)
        except Exception:  # noqa: BLE001
            pass

    def reset(self) -> None:
        """清空已录步骤(用户登录完后点「从这里开始录」,丢弃登录步骤,只留业务流程)。
        同时清 all_requests/diagnostics 与请求计数——后续诊断基于录制期抓的事实,登录噪声不计。"""
        self.steps.clear()
        self.requests.clear()
        self.reads.clear()
        self.all_requests.clear()
        self.diagnostics.clear()
        self._req_counter = 0
        self._request_fact_index.clear()
        self._page_counter = 0
        self._frame_counter = 0
        self._page_ids.clear()
        self._frame_ids.clear()
        self._page_id(self.page)

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
        """已捕获步骤 → (普通 dict 步骤列表, sample_inputs)。字段 key 用 assign_field_keys 统一分配。"""
        fb_idx = [i for i, s in enumerate(self.steps) if s.get("field")]
        keys = assign_field_keys([self.steps[i]["field"] for i in fb_idx])
        keymap = dict(zip(fb_idx, keys))
        steps: list[dict] = []
        samples: dict[str, str] = {}
        for i, s in enumerate(self.steps):
            field = s.get("field") or None
            steps.append({"op": s["op"], "locator": s.get("locator"), "field": field})
            if field and s.get("op") in ("fill", "select", "pick") and s.get("value") != "":
                samples[keymap[i]] = s.get("value", "")
        return steps, samples

    def recorded_page_enum_options(self) -> dict:
        """录制时下拉/级联里真实可见的选项 + 当前选中值。

        返回 {字段key: {options, field_key, selected}}。保留 selected 是为了把 DOM 显示项
        与提交体短码(type=2)连起来,避免发布 skill 时把下拉退化成 number。
        """
        fb_idx = [i for i, s in enumerate(self.steps) if s.get("field")]
        keys = assign_field_keys([self.steps[i]["field"] for i in fb_idx])
        keymap = dict(zip(fb_idx, keys))
        out: dict[str, dict] = {}
        last_field_idx: int | None = None
        for i, s in enumerate(self.steps):
            if i in keymap:
                last_field_idx = i
            if s.get("op") in ("pick", "select") and s.get("options"):
                # 自定义下拉常见事件序列是「点开输入框/选择器」→「点弹层选项」。
                # 后一个 pick 事件有 options/selected,但 DOM 目标已经是弹层项,拿不到字段 label。
                # 因此优先用本步字段,否则回溯最近一个可填写字段,把弹层选项归回正确业务字段。
                owner_idx = i if i in keymap else last_field_idx
                if owner_idx not in keymap:
                    continue
                field_key = keymap[owner_idx]
                out[field_key] = {
                    "options": list(s["options"]),
                    "field_key": field_key,
                    "selected": s.get("value", ""),
                }
        return out

    def recorded_required_labels(self) -> set:
        """录制中标了表单 * 必填的字段(供 flatten 标 required)。key 与 recorded_steps 同算法分配,保持一致。"""
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
