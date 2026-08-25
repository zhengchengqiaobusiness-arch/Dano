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
import math
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import structlog

from dano.execution.page.sessions import SESSION_STORAGE_STATE_KEY
from dano.execution.page.recording_semantic_index import (
    SemanticFieldIndex,
    control_item_from_step,
    form_root_identity,
    route_identity,
    samples_from_fields,
    stamp_field_identity,
)
from dano.shared.std_fields import standard_fields_for

log = structlog.get_logger(__name__)


def _std_key(field: str, *, tenant: str = "") -> str:
    fl = (field or "").strip().lower()
    for std in standard_fields_for(tenant):
        if fl == std.key.lower() or fl == std.label.lower() or fl in {a.lower() for a in std.aliases}:
            return std.key
    return (field or "").strip()




def assign_step_field_keys(steps: list[dict], *, tenant: str = "") -> dict[int, str]:
    """为录制步骤分配稳定字段键。

    浏览器对同一个控件可能连续上报 click/fill/change/select 等多个事件。字段键必须按
    ``标准字段语义 + locator`` 标识控件，而不是按事件次数编号；同名但 locator 不同的
    两个真实控件仍分配 ``字段``、``字段#2``。返回值以原步骤下标为键，供样例、必填和
    页面枚举共同复用，避免各自计算后发生错位。
    """
    identity_to_key: dict[tuple[str, str, str, str], str] = {}
    semantic_bases: dict[str, str] = {}
    used: set[str] = set()
    out: dict[int, str] = {}
    for index, step in enumerate(steps):
        raw_field = str(step.get("field") or "").strip()
        if not raw_field:
            continue
        semantic = _std_key(raw_field, tenant=tenant) or raw_field
        semantic_id = semantic.casefold()
        locator = str(step.get("locator") or "").strip()
        page_id = str(step.get("page_id") or "").strip()
        frame_id = str(step.get("frame_id") or "").strip()
        locator_identity = locator or f"@event:{index}"
        identity = (
            semantic_id,
            page_id,
            frame_id,
            locator_identity,
            route_identity(step),
            form_root_identity(step),
        )
        key = identity_to_key.get(identity)
        if key is None:
            candidate = semantic_bases.setdefault(semantic_id, semantic)
            key, suffix = candidate, 2
            while key in used:
                key = f"{candidate}#{suffix}"
                suffix += 1
            identity_to_key[identity] = key
            used.add(key)
        out[index] = key
    return out


def has_recorded_value(step: dict) -> bool:
    value = step.get("value")
    return value is not None and value != ""


_RESPONSE_TEXT_MAX = 8192


def _classify_http_response(
    *,
    status,
    content_type: str = "",
    json_data=None,
    text: str | None = None,
    body_bytes: bytes | None = None,
) -> dict:
    """Normalize a captured HTTP response without dropping non-JSON evidence."""
    content_type = content_type or ""
    ct = content_type.lower()
    fields = {
        "status": status,
        "content_type": content_type,
        "response_json": json_data,
    }
    if json_data is not None:
        fields.update(response_kind="json", response_empty=False)
        return fields
    if status in {204, 205}:
        fields.update(response_kind="empty", response_empty=True, response_size=0)
        return fields
    if body_bytes and not any(token in ct for token in ("text/", "json", "xml", "html", "javascript")):
        fields.update(
            response_kind="binary",
            response_empty=False,
            response_size=len(body_bytes),
        )
        return fields
    raw_text = text if text is not None else ""
    if body_bytes and not raw_text:
        raw_text = body_bytes.decode("utf-8", errors="replace")
    bounded = raw_text if len(raw_text) <= _RESPONSE_TEXT_MAX else raw_text[:_RESPONSE_TEXT_MAX] + "...[已截断]"
    fields.update(
        response_kind="text" if raw_text else "empty",
        response_text=bounded if raw_text else None,
        response_empty=not raw_text,
        response_size=len(raw_text.encode("utf-8", errors="ignore")),
    )
    return fields


_VIEW_W, _VIEW_H = 1600, 800
_CAST_W, _CAST_H = _VIEW_W, _VIEW_H
_CAST_QUALITY = 70
_CAST_ACTIVE_FPS = 10
_CAST_IDLE_FPS = 2
_CAST_ACTIVE_WINDOW_S = 3.0
_SAFE_KEY_RE = re.compile(
    r"^(?:(?:Control|Shift|Alt|Meta)\+){0,3}"
    r"(?:Enter|Tab|Backspace|Delete|Escape|Home|End|PageUp|PageDown|"
    r"ArrowUp|ArrowDown|ArrowLeft|ArrowRight|A|C|X|Z|Y)$"
)
_PLAIN_KEYS = {
    "Enter", "Tab", "Backspace", "Delete", "Escape", "Home", "End",
    "PageUp", "PageDown", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
}
_CTRL_META_KEYS = {"A", "C", "X", "Z", "Y", "Enter", "Backspace"}
_SHIFT_KEYS = {"Tab", "Enter"}


def _finite_number(value, default: float) -> float:  # noqa: ANN001
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive_dimension(value, default: int) -> int:  # noqa: ANN001
    number = int(_finite_number(value, float(default)))
    return number if number > 0 else default


def _input_point(ev: dict, *, prefix: str = "") -> tuple[float, float]:
    """读取归一坐标并约束到当前固定视口，异常/越界客户端不能击穿输入循环。"""
    nx = _finite_number(ev.get(f"{prefix}nx"), 0.0)
    ny = _finite_number(ev.get(f"{prefix}ny"), 0.0)
    return min(1.0, max(0.0, nx)) * _VIEW_W, min(1.0, max(0.0, ny)) * _VIEW_H


def _mouse_button(value) -> str:  # noqa: ANN001
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"left", "middle", "right"}:
            return normalized
        if normalized.isdigit():
            value = int(normalized)
    return {0: "left", 1: "middle", 2: "right"}.get(value, "left")


def _mouse_steps(value) -> int:  # noqa: ANN001
    try:
        steps = int(value)
    except (TypeError, ValueError):
        return 1
    return min(100, max(1, steps))


def _mouse_click_count(value) -> int:  # noqa: ANN001
    """Keep native single/double-click semantics without accepting arbitrary counts."""
    try:
        click_count = int(value)
    except (TypeError, ValueError):
        return 1
    return 2 if click_count == 2 else 1


def _page_is_open(page) -> bool:  # noqa: ANN001
    if page is None:
        return False
    try:
        return not page.is_closed()
    except Exception:  # noqa: BLE001 —— page/context 拆除竞态
        return False


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
  var recorderHealth = {alive: false};
  try {
    document.dispatchEvent(new CustomEvent('__danoRecorderHealth', {detail: recorderHealth}));
  } catch (_) {}
  if (recorderHealth.alive) return;
  document.addEventListener('__danoRecorderHealth', function (event) {
    if (event && event.detail) event.detail.alive = true;
  });
  window.__danoRecorderInstalled = true;
  window.__danoRecorderDocument = document;
  // 通用语义引擎(与框架/语言/公司无关):ARIA role + accessible name 优先,文本/属性兜底。
  // 不按标签/class 白名单,故 Element-UI / Ant Design / 原生 / 任意自定义控件一视同仁。
  var SUBMIT = ['提交','保存','确定','确认','申请','发起','送出','申报','通过','归档','完单','审核','立案','接单','发布','完成','结案','结算','新建','新增','编辑','更新','导入','导出','打印','submit','save','ok','confirm','apply','approve','finish','archive','review'];
  var FORM_COMMAND = SUBMIT.concat(['查询','搜索','筛选','检索','query','search','filter']);
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
    // 控件(el-select/日期/输入)靠这个才能拿到"申请类型"这类中文字段名,而不是退回 placeholder。
    try {
      var item = el.closest && el.closest('.el-form-item,.ant-form-item,[class*="form-item"],[class*="form_item"]');
      var lab = item && item.querySelector('.el-form-item__label,.ant-form-item-label,label');
      if (lab) { var lt = clean(lab.innerText).replace(/[:：*]\s*$/, ''); if (lt) return lt; }
    } catch (e) {}
    var tableContext = tableCellContext(el);
    if (tableContext && tableContext.label) return tableContext.label;
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
  function referencedText(raw) {
    var text = '';
    String(raw || '').split(/\s+/).forEach(function (id) {
      var node = id && document.getElementById(id);
      if (node) text += clean(node.innerText || node.textContent || '') + ' ';
    });
    return clean(text);
  }
  function structuralRootIdentity(root) {
    if (!root) return '';
    var attrs = ['aria-label','name','data-testid','data-test','data-cy','data-form-id','data-dialog-id'];
    for (var i = 0; i < attrs.length; i++) {
      var value = root.getAttribute && clean(root.getAttribute(attrs[i]));
      if (value) return value;
    }
    var labelled = root.getAttribute && referencedText(root.getAttribute('aria-labelledby'));
    if (labelled) return labelled;
    if (stableId(root)) return String(root.id);
    // Instance-local structural path keeps two simultaneous anonymous forms
    // separate without treating framework classes or generated ids as stable.
    var parts = []; var node = root;
    while (node && node !== document.body && parts.length < 8) {
      var tag = String(node.tagName || '').toLowerCase();
      if (!tag) break;
      var index = 1; var sibling = node;
      while ((sibling = sibling.previousElementSibling)) {
        if (String(sibling.tagName || '').toLowerCase() === tag) index += 1;
      }
      parts.unshift(tag + ':nth-of-type(' + index + ')');
      node = node.parentElement;
    }
    return parts.join('>');
  }
  function surfaceContext(el) {
    var dialog = null; var drawer = null; var form = null;
    try {
      dialog = el && el.closest && el.closest('dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"],.el-dialog,.ant-modal,.van-dialog,.modal');
      drawer = el && el.closest && el.closest('.el-drawer,.ant-drawer,.drawer,[class*="drawer"]');
      form = el && el.closest && el.closest('form,[role="form"],fieldset,[data-form-id]');
    } catch (_) {}
    var root = form || drawer || dialog;
    return {
      in_dialog: !!(dialog || drawer),
      surface: drawer ? 'drawer' : (dialog ? 'dialog' : 'page'),
      form_root: structuralRootIdentity(root)
    };
  }
  function tableCellContext(el) {
    try {
      var cell = el && el.closest && el.closest('td,th,[role="cell"],[role="gridcell"]');
      if (!cell) return null;
      var row = cell.closest('tr,[role="row"]');
      var table = cell.closest('table,[role="table"],[role="grid"]');
      if (!row || !table) return null;
      // Virtualized/component tables commonly render header and body in two
      // sibling <table> elements.  Resolve columns and stable identity from
      // their shared semantic root while retaining the real body row.
      var tableRoot = table.closest && table.closest(
        '.el-table,.ant-table,.v-data-table,.q-table,[role="table"],[role="grid"]'
      );
      var headerScope = tableRoot || table;
      var cells = row.querySelectorAll(':scope > td,:scope > th,:scope > [role="cell"],:scope > [role="gridcell"]');
      var columnIndex = Array.prototype.indexOf.call(cells, cell);
      var ariaColumn = parseInt(cell.getAttribute('aria-colindex') || '', 10);
      if (!isNaN(ariaColumn) && ariaColumn > 0) columnIndex = ariaColumn - 1;
      if (columnIndex < 0 && typeof cell.cellIndex === 'number') columnIndex = cell.cellIndex;

      var body = row.closest('tbody,[role="rowgroup"]');
      var rows = body ? body.querySelectorAll(':scope > tr,:scope > [role="row"]') : table.querySelectorAll('tbody tr,[role="row"]');
      var rowIndex = Array.prototype.indexOf.call(rows, row);
      var ariaRow = parseInt(row.getAttribute('aria-rowindex') || '', 10);
      if (!isNaN(ariaRow) && ariaRow > 0) rowIndex = ariaRow - 1;

      var header = null;
      var headerIds = cell.getAttribute('headers');
      if (headerIds) {
        var firstHeader = String(headerIds).split(/\s+/)[0];
        header = firstHeader ? document.getElementById(firstHeader) : null;
      }
      if (!header) {
        var headers = headerScope.querySelectorAll(
          'thead th,[role="columnheader"],.el-table__header th,.ant-table-thead th'
        );
        for (var h = 0; h < headers.length; h++) {
          var headerColumn = parseInt(headers[h].getAttribute('aria-colindex') || '', 10);
          if ((!isNaN(headerColumn) && headerColumn - 1 === columnIndex) || (isNaN(headerColumn) && h === columnIndex)) {
            header = headers[h]; break;
          }
        }
      }
      var rowIdentity = '';
      ['data-row-key','data-key','data-id','aria-rowindex'].some(function (attr) {
        var value = clean(row.getAttribute && row.getAttribute(attr));
        if (value) { rowIdentity = value; return true; }
        return false;
      });
      var tableId = structuralRootIdentity(tableRoot || table);
      if (!tableId) {
        var allTables = document.querySelectorAll(
          '.el-table,.ant-table,.v-data-table,.q-table,table,[role="table"],[role="grid"]'
        );
        tableId = 'table_' + Array.prototype.indexOf.call(allTables, tableRoot || table);
      }
      var columnLabel = clean(header && (header.innerText || header.textContent || header.getAttribute('aria-label')));
      return {
        label: columnLabel,
        column_label: columnLabel,
        field_aliases: header ? controlAliases(header) : [],
        table_id: tableId,
        row_index: rowIndex >= 0 ? rowIndex : null,
        row_identity: rowIdentity,
        column_index: columnIndex >= 0 ? columnIndex : null,
        control_surface: 'table_inline'
      };
    } catch (_) { return null; }
  }
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
  function isSensitive(el) {
    try {
      if (!el) return false;
      var ty = ((el.type || '') + '').toLowerCase();
      if (ty === 'password') return true;
      var ac = ((el.getAttribute && el.getAttribute('autocomplete')) || '').toLowerCase();
      if (/(?:^|\s)(?:current-password|new-password|one-time-code|cc-|card|cvv|cvc|iban|account-number)/.test(ac)) return true;
      var sig = ((el.id || '') + ' ' + (el.name || '') + ' ' + (el.className || '') + ' ' +
                 ((el.getAttribute && el.getAttribute('aria-label')) || '')).toLowerCase();
      return /(pass(?:word)?|pwd|credit.?card|card.?no|cvv|cvc|secret.?code|pay.?password|bank.?account|access.?token)/.test(sig);
    } catch (e) { return true; }
  }
  // 登录页检测(通用、保守):URL 命中 login/signin(SPA 路由守卫重定向就长这样)。登录不是业务步骤,不录。
  // 只看 URL,不看密码框 —— 免把业务里的"修改密码"页或测试登录表单整页误跳过。
  function onLoginPage() {
    try { return /\/(login|signin|sign-in|sso)(?:[/?#]|$)/i.test(location.href); } catch (e) { return false; }
  }
  function requiredOf(el) {                       // 该字段是否必填:读表单 * 标记(通用,跨 Element-UI / Ant / 原生)
    try {
      if (el.required || el.getAttribute('aria-required') === 'true') return true;
      // Do not stop at an inner ``form-item__content`` node.  Date ranges,
      // cascaders and composed controls often put ``is-required`` on an outer
      // form item while the actual input is nested several levels below it.
      var node = el; var depth = 0;
      while (node && depth++ < 12) {
        if (node.getAttribute && node.getAttribute('aria-required') === 'true') return true;
        var cls = String(node.className || '');
        var isFormItem = /(?:^|\s)(?:el-form-item|ant-form-item)(?:\s|$)/.test(cls) || /form[-_]item/i.test(cls);
        if (isFormItem && /(?:^|[-_\s])(?:is-)?required(?:[-_\s]|$)/i.test(cls)) return true;
        if (isFormItem) {
          var lab = node.querySelector && node.querySelector('label');
          if (lab && String(lab.textContent || '').indexOf('*') >= 0) return true;
        }
        node = node.parentElement;
      }
    } catch (e) {}
    return false;
  }
  function requiredStateOf(el) {
    try {
      if (requiredOf(el)) return 'required';
      if (el.getAttribute && el.getAttribute('aria-required') === 'false') return 'optional';
    } catch (e) {}
    return 'unknown';
  }
  function recordedScalar(value) {
    return (value === null || value === undefined) ? '' : value;
  }
  function isContentEditable(el) {
    try {
      return !!(el && (el.isContentEditable || (el.getAttribute && String(el.getAttribute('contenteditable') || '').toLowerCase() === 'true')));
    } catch (e) { return false; }
  }
  function isSwitchEl(el) {
    try {
      if (!el) return false;
      var role = String((el.getAttribute && el.getAttribute('role')) || '').toLowerCase();
      if (role === 'switch') return true;
      var type = String(el.type || '').toLowerCase();
      var cls = String(el.className || '').toLowerCase();
      if (type === 'checkbox' && /switch|toggle/.test(cls)) return true;
      return !!(el.closest && el.closest('[role="switch"],.el-switch,.ant-switch,.van-switch,.q-toggle,.v-switch,.mat-mdc-slide-toggle,.ant-switch-inner'));
    } catch (e) { return false; }
  }
  function readBooleanState(el) {
    try {
      var type = String(el.type || '').toLowerCase();
      if (typeof el.checked === 'boolean' && (type === 'checkbox' || type === 'radio' || isSwitchEl(el))) return !!el.checked;
      var aria = el.getAttribute && el.getAttribute('aria-checked');
      if (aria === 'true') return true;
      if (aria === 'false') return false;
      var cls = String(el.className || '').toLowerCase();
      if (/\bis-checked\b|\bis-active\b/.test(cls)) return true;
      var host = el.closest && el.closest('[role="switch"],.el-switch,.ant-switch,.van-switch');
      if (host && host !== el) return readBooleanState(host);
    } catch (e) {}
    return false;
  }
  function radioGroupOptions(el) {
    try {
      var name = el.name || '';
      var root = el.form || document;
      var nodes = name ? root.querySelectorAll('input[type="radio"][name="' + name.replace(/"/g, '\\"') + '"]') : [el];
      var out = [];
      for (var i = 0; i < nodes.length; i++) {
        var node = nodes[i];
        out.push({
          label: clean(labelText(node) || node.getAttribute('aria-label') || node.value || ''),
          value: recordedScalar(node.value),
          checked: !!node.checked
        });
      }
      return out;
    } catch (e) { return []; }
  }
  function fileFieldFacts(el) {
    var files = el.files || [];
    var items = [];
    for (var i = 0; i < files.length; i++) {
      items.push({
        filename: files[i].name || '',
        mime_type: files[i].type || '',
        size: files[i].size || 0
      });
    }
    return {
      filename: items[0] ? items[0].filename : '',
      mime_type: items[0] ? items[0].mime_type : '',
      size: items[0] ? items[0].size : 0,
      multiple: !!el.multiple,
      file_count: files.length,
      files: items
    };
  }
  function controlAliases(el) {
    // Keep the request-field identity carried by the control structure.  A
    // repeated sample value ("1" is common in OA forms) is never an identity.
    var attrs = ['name','data-field','data-name','data-key','data-prop','data-path','prop','formcontrolname',
                 'ng-reflect-name','ng-reflect-form-control-name'];
    var out = []; var seen = {}; var nodes = [];
    function add(raw) {
      if (raw === null || raw === undefined) return;
      var value = clean(String(raw));
      if (!value || value.length > 120 || seen[value]) return;
      seen[value] = true; out.push(value);
      var leaf = value.split('.').pop();
      if (leaf && leaf !== value && !seen[leaf]) { seen[leaf] = true; out.push(leaf); }
    }
    var cursor = el;
    for (var depth = 0; cursor && depth < 7; depth++, cursor = cursor.parentElement) nodes.push(cursor);
    try {
      var owner = el && el.closest && el.closest(
        '.el-form-item,.ant-form-item,[class*="form-item"],[class*="form_item"],[data-prop],[data-field],[formcontrolname]'
      );
      if (owner && nodes.indexOf(owner) < 0) nodes.push(owner);
    } catch (_) {}
    nodes.forEach(function (node) {
      if (!node || !node.getAttribute) return;
      attrs.forEach(function (attr) {
        add(node.getAttribute(attr));
      });
      if (stableId(node)) {
        add(node.id);
      }
      try {
        var vue2 = node.__vue__;
        [
          vue2 && vue2.$props && vue2.$props.prop,
          vue2 && vue2.$props && vue2.$props.name,
          vue2 && vue2.$attrs && (vue2.$attrs.name || vue2.$attrs['data-prop']),
          vue2 && vue2.$vnode && vue2.$vnode.data && vue2.$vnode.data.model && vue2.$vnode.data.model.expression,
          vue2 && vue2.$vnode && vue2.$vnode.componentOptions && vue2.$vnode.componentOptions.propsData
            && (vue2.$vnode.componentOptions.propsData.prop || vue2.$vnode.componentOptions.propsData.name)
        ].forEach(add);
        var vue3 = node.__vueParentComponent;
        for (var vi = 0; vue3 && vi < 12; vi++, vue3 = vue3.parent) {
          add(vue3.props && (vue3.props.prop || vue3.props.name));
          add(vue3.attrs && (vue3.attrs.name || vue3.attrs['data-prop']));
          add(vue3.vnode && vue3.vnode.props && (vue3.vnode.props.prop || vue3.vnode.props.name || vue3.vnode.props['data-prop']));
        }
        Object.keys(node).forEach(function (key) {
          if (key.indexOf('__reactProps$') !== 0 && key.indexOf('__reactFiber$') !== 0) return;
          var holder = node[key] || {};
          var props = key.indexOf('__reactProps$') === 0 ? holder : (holder.memoizedProps || holder.pendingProps || {});
          add(props && (props.name || props.field || props.dataIndex || props['data-prop']));
        });
      } catch (_) {}
    });
    return out;
  }
  function controlKind(el) {
    try {
      if (!el) return 'unknown';
      var tag = String(el.tagName || '').toLowerCase();
      var type = String(el.type || '').toLowerCase();
      var host = el.closest ? el.closest(
        '.el-date-editor,.el-time-select,.el-time-picker,.ant-picker,.van-picker,' +
        '.el-select,.el-cascader,.ant-select,.ant-cascader-picker,' +
        '.mat-mdc-select,.mat-select,.q-select,.v-select'
      ) : null;
      var cls = String((host && host.className) || el.className || '').toLowerCase();
      var role = String((el.getAttribute && el.getAttribute('role')) || '').toLowerCase();
      var ariaAutocomplete = String((el.getAttribute && el.getAttribute('aria-autocomplete')) || '').toLowerCase();
      var editableCombobox = tag === 'input' && !el.readOnly
        && role === 'combobox' && ['list','both','inline'].indexOf(ariaAutocomplete) >= 0;
      var autocompleteHost = el.closest && el.closest('.el-autocomplete,[class*="autocomplete"]');
      if (type === 'datetime-local' || /datetime/.test(cls)) return 'datetime';
      if (type === 'time' || /time-picker|time-select|timepanel/.test(cls)) return 'time';
      if (['date','month','week'].indexOf(type) >= 0 || /date|month|year|calendar/.test(cls)) return 'date';
      // Autocomplete/ARIA comboboxes may accept arbitrary text.  Only native
      // selects and non-editable picker controls are executable enums.
      if (editableCombobox || (tag === 'input' && !el.readOnly && autocompleteHost)) return 'text';
      if (tag === 'select' || /select|cascader/.test(cls)) return 'select';
      if (role === 'combobox' && (el.readOnly || ariaAutocomplete === '' || ariaAutocomplete === 'none')) return 'select';
      if (type === 'file') return 'file';
      if (type === 'number' || (el.getAttribute && el.getAttribute('role')) === 'spinbutton') return 'number';
      if (role === 'switch' || isSwitchEl(el)) return 'switch';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (isContentEditable(el)) return 'contenteditable';
      if (tag === 'textarea') return 'textarea';
      if (tag === 'input') return 'text';
    } catch (_) {}
    return 'unknown';
  }
  function controlState(el, kind) {
    var host = null;
    try {
      host = el && el.closest && el.closest(
        '.el-select,.el-cascader,.ant-select,.ant-cascader-picker,' +
        '.mat-mdc-select,.mat-select,.q-select,.v-select,' +
        '[role="combobox"],[role="listbox"],[role="radiogroup"],[role="switch"]'
      );
    } catch (_) {}
    function attrTrue(node, name) {
      try { return !!(node && node.getAttribute && String(node.getAttribute(name) || '').toLowerCase() === 'true'); }
      catch (_) { return false; }
    }
    var cls = String((host && host.className) || '').toLowerCase();
    var disabled = !!(
      (el && el.disabled)
      || attrTrue(el, 'aria-disabled')
      || attrTrue(host, 'aria-disabled')
      || /(?:^|[\s_-])(?:is-)?disabled(?:[\s_-]|$)/.test(cls)
    );
    var choiceKind = ['select','radio','checkbox','switch'].indexOf(String(kind || '')) >= 0;
    var readOnly = choiceKind
      ? !!(
          attrTrue(el, 'aria-readonly')
          || attrTrue(host, 'aria-readonly')
          || attrTrue(el, 'data-readonly')
          || attrTrue(host, 'data-readonly')
          || /(?:^|[\s_-])(?:is-)?read-?only(?:[\s_-]|$)/.test(cls)
        )
      : !!(el && (el.readOnly || (el.getAttribute && el.getAttribute('readonly') !== null)));
    return {disabled: disabled, read_only: readOnly};
  }
  function inDialog(el) {
    // List filters and dialog/drawer forms commonly share the same label.
    // The input stays in its form; dropdown poppers are not the surface.
    try {
      return !!(el && el.closest && el.closest(
        '[role="dialog"],[role="alertdialog"],' +
        '.el-dialog,.el-overlay-dialog,.el-drawer,' +
        '.ant-modal,.ant-drawer,' +
        '.van-dialog,' +
        '.mat-mdc-dialog-container,.mat-dialog-container,' +
        '.q-dialog,.q-dialog__inner,' +
        '.v-dialog,.modal,.drawer'
      ));
    } catch (_) { return false; }
  }
  function fieldEvidence(el) {
    if (!el) return {};
    var surface = surfaceContext(el);
    var table = tableCellContext(el);
    var aliases = controlAliases(el);
    if (table) aliases = aliases.concat(table.field_aliases || []).filter(function (value, index, all) {
      return value && all.indexOf(value) === index;
    });
    var reqState = requiredStateOf(el);
    var kind = controlKind(el);
    var state = controlState(el, kind);
    var evidence = {
      field_aliases: aliases,
      control_kind: kind,
      input_type: String(el.type || '').toLowerCase(),
      disabled: state.disabled,
      // UI libraries put `readonly` on the inner text input of a normal
      // dropdown to prevent free typing. The choice control remains editable;
      // only explicit host-level readonly/disabled state makes it locked.
      read_only: state.read_only,
      minimum: el.getAttribute && el.getAttribute('min') !== null ? Number(el.getAttribute('min')) : null,
      maximum: el.getAttribute && el.getAttribute('max') !== null ? Number(el.getAttribute('max')) : null,
      in_dialog: !!surface.in_dialog,
      surface: surface.surface,
      form_root: surface.form_root,
      required_state: reqState
    };
    if (table) {
      ['table_id','row_index','row_identity','column_index','column_label','control_surface'].forEach(function (key) {
        evidence[key] = table[key];
      });
    }
    return evidence;
  }
  window.__danoRequiredFields = function () {
    var out = {};
    try {
      var controls = document.querySelectorAll('input,select,textarea,[role="textbox"],[role="combobox"],[role="spinbutton"]');
      for (var i = 0; i < controls.length; i++) {
        var el = controls[i];
        if (!requiredOf(el)) continue;
        var loc = locateField(el); var name = fieldOf(loc);
        if (clean(name)) out[clean(name)] = true;
        var lt = labelText(el); if (clean(lt)) out[clean(lt)] = true;
      }
      var items = document.querySelectorAll('.el-form-item,.ant-form-item,[class*="form-item"],[class*="form_item"]');
      for (var j = 0; j < items.length; j++) {
        var item = items[j]; var cls = String(item.className || '');
        var lab = item.querySelector && item.querySelector('label');
        var marked = /(?:^|[-_\s])(?:is-)?required(?:[-_\s]|$)/i.test(cls)
          || (lab && String(lab.textContent || '').indexOf('*') >= 0);
        if (marked && lab && clean(lab.textContent)) out[clean(String(lab.textContent).replace(/\*/g, ''))] = true;
      }
    } catch (e) {}
    return Object.keys(out);
  };
  window.__danoPageContext = function () {
    var seen = {}; var texts = [];
    function add(value) {
      value = clean(String(value || '').replace(/\s+/g, ' '));
      if (!value || value.length > 120 || seen[value]) return;
      seen[value] = true; texts.push(value);
    }
    try {
      add(document.title);
      var nodes = document.querySelectorAll(
        'h1,h2,h3,legend,.el-breadcrumb,.ant-breadcrumb,.page-title,.form-title,' +
        '[class*="page-title"],[class*="page_title"],[class*="form-title"],[class*="form_title"],' +
        '[role="dialog"] [class*="title"]'
      );
      for (var i = 0; i < nodes.length && texts.length < 20; i++) {
        var node = nodes[i];
        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden')) continue;
        add(node.textContent);
      }
    } catch (e) {}
    return {
      url: String(location.href || '').split('#')[0],
      path: String(location.pathname || ''),
      document_title: clean(document.title || ''),
      visible_titles: texts,
      // Date controls expose local calendar values while requests often carry
      // epochs.  Persist the browser's real offset instead of guessing UTC+8.
      timezone_offset_minutes: -new Date().getTimezoneOffset()
    };
  };
  window.__danoFormFieldEvidence = function () {
    var out = [];
    var seenRadio = {};
    try {
      var controls = document.querySelectorAll('input,select,textarea,[role="textbox"],[role="combobox"],[role="spinbutton"],[role="switch"],[contenteditable="true"]');
      for (var i = 0; i < controls.length; i++) {
        var el = controls[i];
        var ty = String(el.type || '').toLowerCase();
        var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        var choiceHost = (ty === 'radio' || ty === 'checkbox') && el.closest
          ? el.closest('label,[role="radiogroup"],[role="checkbox"],[role="switch"],.el-radio,.el-checkbox,.ant-radio-wrapper,.ant-checkbox-wrapper')
          : null;
        var hiddenChoiceWithVisibleHost = !!(
          choiceHost && choiceHost.getClientRects && choiceHost.getClientRects().length
        );
        // Component libraries normally hide the native file input behind a
        // visible upload button.  It is still the semantic file control and
        // must remain in submit-time evidence even before a file is selected.
        // Radio/checkbox libraries do the same while rendering a visible
        // label/group.  Preserve only choices with a visible semantic host so
        // unrelated hidden form values do not become caller fields.
        if (
          style && (style.display === 'none' || style.visibility === 'hidden')
          && ty !== 'file' && !hiddenChoiceWithVisibleHost
        ) continue;
        var loc = locateField(el);
        var label = clean(labelText(el));
        var field = clean(fieldOf(loc));
        var evidence = fieldEvidence(el);
        // Inline table editors often have no <label>; framework form-item
        // lookup may then pick a neighbouring column. The owning header is
        // the exact structural display-name source for this control.
        if (evidence.control_surface === 'table_inline' && evidence.column_label) {
          label = clean(evidence.column_label);
        }
        if (ty === 'radio') {
          var radioGroup = el.closest && el.closest(
            '[role="radiogroup"],.el-radio-group,.ant-radio-group,.v-radio-group,.q-option-group'
          );
          var radioGroupLabel = radioGroup && clean(
            radioGroup.getAttribute('aria-label') || labelText(radioGroup)
          );
          if (radioGroupLabel) label = radioGroupLabel;
          var group = el.name || field || loc;
          if (seenRadio[group]) continue;
          seenRadio[group] = true;
          var radios = radioGroupOptions(el);
          var selected = null;
          for (var r = 0; r < radios.length; r++) { if (radios[r].checked) selected = radios[r]; }
          field = el.name || field;
          evidence.control_kind = 'radio';
          evidence.options = radios.map(function (item) { return {label: item.label, value: item.value}; });
          evidence.group_name = el.name || '';
          evidence.selected_label = selected ? selected.label : '';
          var radioValue = selected ? selected.value : '';
          if (!label && !field) continue;
          var reqRadio = requiredStateOf(el);
          var radioItem = {
            field: field, label: label, value: radioValue, required: reqRadio === 'required',
            required_state: reqRadio,
            required_observed: reqRadio === 'required' ? true : (reqRadio === 'optional' ? false : null),
            field_aliases: evidence.field_aliases || [],
            control_kind: 'radio',
            input_type: 'radio',
            options: evidence.options,
            group_name: evidence.group_name,
            selected_label: evidence.selected_label,
            disabled: !!evidence.disabled,
            read_only: !!evidence.read_only,
            in_dialog: !!evidence.in_dialog,
            surface: evidence.surface || 'page',
            form_index: i
          };
          ['form_root','table_id','row_index','row_identity','column_index','column_label','control_surface'].forEach(function (key) {
            if (evidence[key] !== undefined && evidence[key] !== '') radioItem[key] = evidence[key];
          });
          out.push(radioItem);
          continue;
        }
        var value;
        if (isSensitive(el)) {
          value = '';
        } else if (ty === 'file' || evidence.control_kind === 'file') {
          var facts = fileFieldFacts(el);
          evidence.filename = facts.filename;
          evidence.mime_type = facts.mime_type;
          evidence.size = facts.size;
          evidence.multiple = facts.multiple;
          evidence.file_count = facts.file_count;
          evidence.files = facts.files;
          evidence.control_kind = 'file';
          value = facts.filename || '';
        } else if (ty === 'checkbox' || evidence.control_kind === 'checkbox' || evidence.control_kind === 'switch' || isSwitchEl(el)) {
          value = readBooleanState(el);
          evidence.checked = value;
        } else if (isContentEditable(el) || evidence.control_kind === 'contenteditable') {
          value = recordedScalar(el.innerText || el.textContent);
          evidence.control_kind = 'contenteditable';
        } else {
          value = recordedScalar(el.value);
          if (value === '' && el.getAttribute) value = recordedScalar(el.getAttribute('value'));
          if ((value === '' || value === null) && evidence.control_kind === 'select') {
            var selectHost = el.closest ? el.closest(TRIGGER_CLS) : null;
            value = clean(pickVal(selectHost || el));
          }
        }
        if (!label && !field) continue;
        var reqState = requiredStateOf(el);
        var item = {
          field: field, label: label, value: value, required: reqState === 'required',
          required_state: reqState,
          required_observed: reqState === 'required' ? true : (reqState === 'optional' ? false : null),
          field_aliases: evidence.field_aliases || [],
          control_kind: evidence.control_kind || 'unknown',
          input_type: evidence.input_type || '',
          disabled: !!evidence.disabled,
          read_only: !!evidence.read_only,
          minimum: evidence.minimum,
          maximum: evidence.maximum,
          in_dialog: !!evidence.in_dialog,
          surface: evidence.surface || 'page',
          form_index: i
        };
        ['checked','filename','mime_type','size','multiple','file_count','files',
         'form_root','table_id','row_index','row_identity','column_index','column_label','control_surface'].forEach(function (key) {
          if (evidence[key] !== undefined) item[key] = evidence[key];
        });
        out.push(item);
      }
    } catch (e) {}
    return out.slice(0, 200);
  };
  window.__danoTableColumnEvidence = function () {
    var out = []; var seenRoots = [];
    function visible(node) {
      if (!node) return false;
      var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
      return !(style && (style.display === 'none' || style.visibility === 'hidden'));
    }
    function addRoot(root) {
      if (!root || seenRoots.indexOf(root) >= 0 || !visible(root)) return;
      seenRoots.push(root);
    }
    try {
      var frameworkRoots = document.querySelectorAll(
        '.el-table,.ant-table,.v-data-table,.q-table,[role="table"],[role="grid"]'
      );
      for (var i = 0; i < frameworkRoots.length; i++) addRoot(frameworkRoots[i]);
      var nativeTables = document.querySelectorAll('table');
      for (var j = 0; j < nativeTables.length; j++) {
        if (!nativeTables[j].closest(
          '.el-table,.ant-table,.v-data-table,.q-table,[role="table"],[role="grid"]'
        )) addRoot(nativeTables[j]);
      }
      seenRoots.forEach(function (root, tableIndex) {
        var scrollNodes = root.querySelectorAll(
          '.el-scrollbar__wrap,.el-table__body-wrapper,.ant-table-content,.v-table__wrapper,.q-table__middle'
        );
        var tableComplete = true;
        for (var s = 0; s < scrollNodes.length; s++) {
          if (scrollNodes[s].scrollWidth > scrollNodes[s].clientWidth + 2) {
            tableComplete = false;
            break;
          }
        }
        var headers = root.querySelectorAll(
          'thead th,[role="columnheader"],.el-table__header th,.ant-table-thead th'
        );
        var rows = root.querySelectorAll(
          'tbody tr,[role="row"],.el-table__body tr,.ant-table-tbody tr'
        );
        var usableHeaders = [];
        for (var h = 0; h < headers.length; h++) {
          var label = clean(headers[h].textContent || headers[h].getAttribute('aria-label') || '');
          if (visible(headers[h]) && label) usableHeaders.push(headers[h]);
        }
        if (!usableHeaders.length) return;
        usableHeaders.forEach(function (header, columnIndex) {
          var label = clean(header.textContent || header.getAttribute('aria-label') || '');
          var samples = [];
          for (var r = 0; r < rows.length && samples.length < 5; r++) {
            var cells = rows[r].querySelectorAll('td,[role="cell"],[role="gridcell"]');
            if (columnIndex >= cells.length || !visible(cells[columnIndex])) continue;
            var value = clean(cells[columnIndex].textContent || '');
            if (value && samples.indexOf(value) < 0) samples.push(value);
          }
          var valueKind = samples.some(function (value) {
            return /^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/.test(value);
          }) ? 'datetime' : '';
          var sampleEpochMs = valueKind ? samples.map(function (value) {
            var parsed = Date.parse(value.replace(' ', 'T').replace(/\//g, '-'));
            return isNaN(parsed) ? null : parsed;
          }).filter(function (value) { return value !== null; }) : [];
          out.push({
            kind: 'table_column',
            field: '',
            label: label,
            field_aliases: controlAliases(header),
            control_kind: 'table_column',
            display_order: columnIndex,
            table_id: 'table_' + tableIndex,
            table_complete: tableComplete,
            sample_values: samples,
            sample_epoch_ms: sampleEpochMs,
            value_kind: valueKind
          });
        });
      });
    } catch (e) {}
    return out.slice(0, 200);
  };
  function emitFormSnapshot(actionId) {
    if (onLoginPage()) return;
    try {
      window.__danoRecord(JSON.stringify({
        op: 'form_snapshot',
        action_id: actionId || '',
        required_fields: window.__danoRequiredFields ? window.__danoRequiredFields() : [],
        fields: window.__danoFormFieldEvidence ? window.__danoFormFieldEvidence() : [],
        output_fields: window.__danoTableColumnEvidence ? window.__danoTableColumnEvidence() : [],
        page_context: window.__danoPageContext ? window.__danoPageContext() : {}
      }));
    } catch (e) {}
  }
  var evidenceSnapshotTimer = null;
  function scheduleEvidenceSnapshot(delay) {
    if (evidenceSnapshotTimer) return;
    evidenceSnapshotTimer = setTimeout(function () {
      evidenceSnapshotTimer = null;
      emitFormSnapshot();
    }, typeof delay === 'number' ? delay : 450);
  }
  var actionSeq = 0;
  var mutationSeq = 0;
  var mutationBuffer = [];
  function nodeEvidence(node) {
    try {
      var el = node && node.nodeType === 1 ? node : (node && node.parentElement);
      if (!el) return {};
      var loc = locateField(el) || locateClickable(el) || '';
      return {
        tag: String(el.tagName || '').toLowerCase(),
        role: roleOf(el),
        locator: loc,
        field: fieldOf(loc),
        required: requiredOf(el),
        hidden: !!(el.hidden || (el.getAttribute && el.getAttribute('aria-hidden') === 'true'))
      };
    } catch (e) { return {}; }
  }
  function rememberMutation(mutation) {
    try {
      mutationSeq += 1;
      var item = {
        sequence: mutationSeq,
        type: mutation.type,
        target: nodeEvidence(mutation.target)
      };
      if (mutation.type === 'attributes') item.attribute = mutation.attributeName || '';
      if (mutation.type === 'childList') {
        item.added = mutation.addedNodes ? mutation.addedNodes.length : 0;
        item.removed = mutation.removedNodes ? mutation.removedNodes.length : 0;
      }
      mutationBuffer.push(item);
      if (mutationBuffer.length > 300) mutationBuffer.splice(0, mutationBuffer.length - 300);
    } catch (e) {}
  }
  try {
    new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) rememberMutation(mutations[i]);
      // Query results and SPA tables render asynchronously after the click.
      // Capture the settled DOM instead of relying only on the pre-click table.
      if (mutations.length) scheduleEvidenceSnapshot();
    }).observe(document.documentElement || document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['hidden','disabled','required','aria-hidden','aria-disabled','aria-required','aria-expanded','aria-selected','aria-checked']
    });
    scheduleEvidenceSnapshot(0);
  } catch (e) {}
  function emitDomEffect(actionId, startMutationSeq) {
    try {
      var changes = mutationBuffer.filter(function (item) { return item.sequence > startMutationSeq; }).slice(-80);
      window.__danoRecord(JSON.stringify({
        op: 'dom_effect',
        action_id: actionId,
        observed_at: Date.now(),
        changes: changes,
        required_fields: window.__danoRequiredFields ? window.__danoRequiredFields() : [],
        page_context: window.__danoPageContext ? window.__danoPageContext() : {}
      }));
    } catch (e) {}
  }
  function emit(op, loc, value, field, required, options, evidence, existingActionId) {
    if (!loc || onLoginPage()) return;            // 登录页上的任何操作一律不录
    try {
      if (!existingActionId) actionSeq += 1;
      var actionId = existingActionId || ('action_' + actionSeq);
      var startMutationSeq = mutationSeq;
      var reqState = (evidence && evidence.required_state) || (required ? 'required' : 'unknown');
      var payload = {
        op: op,
        action_id: actionId,
        observed_at: Date.now(),
        locator: loc,
        value: (value === null || value === undefined) ? '' : value,
        field: field || '',
        required: reqState === 'required',
        required_state: reqState,
        required_observed: reqState === 'required' ? true : (reqState === 'optional' ? false : null),
        options: options || (evidence && evidence.options) || [],
        field_aliases: (evidence && evidence.field_aliases) || [],
        control_kind: (evidence && evidence.control_kind) || 'unknown',
        input_type: (evidence && evidence.input_type) || '',
        disabled: !!(evidence && evidence.disabled),
        read_only: !!(evidence && evidence.read_only),
        minimum: evidence && evidence.minimum,
        maximum: evidence && evidence.maximum,
        in_dialog: !!(evidence && evidence.in_dialog),
        surface: (evidence && evidence.surface) || (evidence && evidence.in_dialog ? 'dialog' : 'page'),
        enum_source: (evidence && evidence.enum_source) || '',
        mapping_complete: !!(evidence && evidence.mapping_complete),
        page_context: window.__danoPageContext ? window.__danoPageContext() : {}
      };
      if (evidence) {
        ['checked','group_name','selected_label','selected_value','filename','mime_type','size','multiple','file_count','files',
         'form_root','table_id','row_index','row_identity','column_index','column_label','control_surface'].forEach(function (key) {
          if (evidence[key] !== undefined) payload[key] = evidence[key];
        });
      }
      window.__danoRecord(JSON.stringify(payload));
      setTimeout(function () { emitDomEffect(actionId, startMutationSeq); }, 350);
    } catch (e) {}
  }
  var pendingFill = {};
  var fillTimers = {};
  function currentFillValue(el) {
    if (isContentEditable(el)) return recordedScalar(el.innerText || el.textContent);
    return recordedScalar(el.value);
  }
  function fillOccurrenceKey(el, loc) {
    var table = tableCellContext(el);
    if (!table) return loc;
    var row = table.row_identity !== '' ? table.row_identity : table.row_index;
    return loc + '|table=' + table.table_id + '|row=' + row + '|column=' + table.column_index;
  }
  function scheduleFill(el) {
    try {
      if (isSensitive(el)) return;
      var loc = locateField(el);
      if (!loc) return;
      var key = fillOccurrenceKey(el, loc);
      var existing = pendingFill[key];
      var actionId = existing && existing.actionId ? existing.actionId : null;
      if (!actionId) { actionSeq += 1; actionId = 'action_' + actionSeq; }
      pendingFill[key] = { el: el, loc: loc, value: currentFillValue(el), field: fieldOf(loc), required: requiredOf(el), evidence: fieldEvidence(el), actionId: actionId };
      if (fillTimers[key]) clearTimeout(fillTimers[key]);
      fillTimers[key] = setTimeout(function () { flushFill(key); }, 300);
    } catch (e) {}
  }
  function flushFill(key) {
    try {
      var p = pendingFill[key];
      if (!p) return;
      delete pendingFill[key];
      if (fillTimers[key]) { clearTimeout(fillTimers[key]); delete fillTimers[key]; }
      var evidence = p.evidence || fieldEvidence(p.el);
      if (isContentEditable(p.el)) evidence.control_kind = 'contenteditable';
      emit('fill', p.loc || key, p.value, p.field, p.required, [], evidence, p.actionId);
    } catch (e) {}
  }
  function flushElementFill(el) {
    try {
      if (isSensitive(el)) return;
      var loc = locateField(el);
      if (loc) {
        var key = fillOccurrenceKey(el, loc);
        var pending = pendingFill[key];
        if (pending) pending.value = el.value;
        else pendingFill[key] = { el: el, loc: loc, value: el.value, field: fieldOf(loc), required: requiredOf(el), evidence: fieldEvidence(el) };
        flushFill(key);
      }
    } catch (e) {}
  }
  window.__danoFlushRecorder = function () {
    try {
      Object.keys(pendingFill).forEach(function (key) { flushFill(key); });
    } catch (e) {}
  };
  // 下拉/级联弹层里**当前可见的选项文字**(地面真值枚举):工作日加班/周末加班/节假日加班 …
  // —— 直接读 DOM,胜过拿提交值去网络字典里猜命中(治"加班类型/申请类型绑到几百项全量字典")。
  // **通用 ARIA + 框架兜底**,不绑任何特定公司/项目:
  //   ① 隐/显 role=option / menuitem / menuitemradio / menuitemcheckbox / treeitem
  //   ② W3C aria-activedescendant / aria-selected 标识选中
  //   ③ 主流框架特定 class 兜底(向后兼容);找不到时再用 li/span 兜底
  // 框架无关,跨 Vant/Bootstrap/MUI/自研 div 也能命中。
  function popupOptions(pop) {
    if (!pop) return [];
    try {
      var aria_sel = '[role="option"],[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="treeitem"],' +
                     '[class*="tree-node"][data-key],[class*="tree-node"][data-value]';
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
      function optionValue(n, label) {
        try {
          function scalar(raw) {
            if (raw === null || raw === undefined) return null;
            if (typeof raw !== 'string' && typeof raw !== 'number' && typeof raw !== 'boolean') return null;
            if (typeof raw === 'string') {
              var value = clean(raw);
              return value === '' ? null : value;
            }
            return raw;
          }
          function componentValue(node) {
            // Custom selects often keep the real option value in framework
            // component props rather than a DOM attribute. Read only scalar,
            // explicitly named `value` props; never serialize arbitrary
            // component state or fall back to the visible label.
            try {
              // The semantic option component is commonly mounted on a
              // wrapper while role=option lives on a child. Walk only the
              // short DOM/component ancestor chain and read explicitly named
              // scalar value props; arbitrary framework state is never used.
              var owner = node;
              for (var depth = 0; owner && depth < 8; depth++, owner = owner.parentElement) {
                var vue2 = owner.__vue__;
                var vue2Candidates = vue2 ? [
                  vue2.value,
                  vue2.$props && vue2.$props.value,
                  vue2.$attrs && vue2.$attrs.value,
                  vue2.$vnode && vue2.$vnode.componentOptions && vue2.$vnode.componentOptions.propsData
                    && vue2.$vnode.componentOptions.propsData.value
                ] : [];
                for (var vi = 0; vi < vue2Candidates.length; vi++) {
                  var vue2Value = scalar(vue2Candidates[vi]);
                  if (vue2Value !== null) return vue2Value;
                }
                var vue3 = owner.__vueParentComponent;
                for (var componentDepth = 0; vue3 && componentDepth < 8; componentDepth++, vue3 = vue3.parent) {
                  var vue3Candidates = [
                    vue3.props && vue3.props.value,
                    vue3.vnode && vue3.vnode.props && vue3.vnode.props.value
                  ];
                  for (var vj = 0; vj < vue3Candidates.length; vj++) {
                    var vue3Value = scalar(vue3Candidates[vj]);
                    if (vue3Value !== null) return vue3Value;
                  }
                }
                var keys = Object.keys(owner);
                for (var ki = 0; ki < keys.length; ki++) {
                  var key = keys[ki];
                  if (key.indexOf('__reactProps$') !== 0 && key.indexOf('__reactFiber$') !== 0) continue;
                  var holder = owner[key] || {};
                  var props = key.indexOf('__reactProps$') === 0 ? holder : (holder.memoizedProps || holder.pendingProps || {});
                  var reactValue = scalar(props && (props.value !== undefined ? props.value : props['data-value']));
                  if (reactValue !== null) return reactValue;
                }
              }
            } catch (_) {}
            return null;
          }
          var attrs = ['data-value','data-key','data-id','value','aria-valuenow','aria-value','ng-reflect-value'];
          for (var ai = 0; ai < attrs.length; ai++) {
            var raw = n.getAttribute && n.getAttribute(attrs[ai]);
            var attrValue = scalar(raw);
            if (attrValue !== null) return attrValue;
          }
          var frameworkValue = componentValue(n);
          if (frameworkValue !== null) return frameworkValue;
          var nested = n.querySelector && n.querySelector('[data-value],[data-key],[data-id],[value],[ng-reflect-value]');
          if (nested) {
            for (var ni = 0; ni < attrs.length; ni++) {
              var nestedRaw = nested.getAttribute && nested.getAttribute(attrs[ni]);
              var nestedValue = scalar(nestedRaw);
              if (nestedValue !== null) return nestedValue;
            }
          }
        } catch (_) {}
        // 自定义下拉没有暴露 data-value/value 时，真实提交值未知。不能把
        // label 伪装成 value，否则 body 提交短码(如 type=2)时会生成一份
        // 看似完整、实际不可执行的 label/value 映射。
        return null;
      }
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
          if (!seen_labels[t]) {
            seen_labels[t] = 1;
            var option = { label: t };
            var option_value = optionValue(n, t);
            if (option_value !== null && option_value !== undefined && option_value !== '') option.value = option_value;
            out.push(option);
          }
          if (out.length >= 500) break;
        }
      }
      harvest(pop.querySelectorAll(aria_sel));
      if (out.length === 0) harvest(pop.querySelectorAll(frame_sel));
      if (out.length === 0) harvest(pop.querySelectorAll(fallback_sel));
      // 干掉弹层里的"搜索框 placeholder"、"清空"等按钮文本(只 trim,不暴力过滤):
      return out.map(function (item) {
                  item.label = clean(item.label).replace(/^[　\s]+|[　\s]+$/g, '');
                  return item;
                })
                .filter(function (item) {
                  return item.label && item.label !== '清空' && item.label !== '清除' && item.label !== '搜索';
                });
    } catch (e) { return []; }
  }
  function persistentChoice(t) {
    try {
      var item = t && t.closest && t.closest(
        '[role="treeitem"],[role="option"],[role="menuitemradio"],[role="menuitemcheckbox"],' +
        '.el-tree-node__content,.ant-tree-node-content-wrapper,' +
        '[class*="tree-node"][data-key],[class*="tree-node"][data-value]'
      );
      if (!item) return null;
      var owner = item.parentElement && item.parentElement.closest && item.parentElement.closest(
        '[role="tree"],[role="listbox"],[role="radiogroup"],[role="menu"],.el-tree,.ant-tree,[class*="tree"]'
      );
      if (!owner) return null;
      var ownerLabel = clean(owner.getAttribute('aria-label'))
        || referencedText(owner.getAttribute('aria-labelledby'))
        || clean(labelText(owner));
      if (!ownerLabel) {
        var scope = owner;
        for (var depth = 0; scope && depth < 6 && !ownerLabel; depth++, scope = scope.parentElement) {
          var sibling = scope.previousElementSibling;
          for (var i = 0; sibling && i < 3; i++, sibling = sibling.previousElementSibling) {
            var siblingText = clean(sibling.innerText || sibling.textContent || '');
            if (siblingText && siblingText.length <= 40) { ownerLabel = siblingText; break; }
          }
        }
      }
      var aliases = controlAliases(owner);
      var field = ownerLabel || aliases[0] || '';
      if (!field) return null;
      var selectedLabel = clean(accName(item));
      if (!selectedLabel) return null;
      var options = popupOptions(owner);
      var selected = null;
      for (var j = 0; j < options.length; j++) {
        if (clean(options[j].label) === selectedLabel) { selected = options[j]; break; }
      }
      var evidence = fieldEvidence(owner);
      evidence.field_aliases = aliases;
      evidence.control_kind = 'select';
      evidence.enum_source = 'dom';
      evidence.selected_label = selectedLabel;
      evidence.selected_value = selected && selected.value !== undefined ? selected.value : null;
      evidence.mapping_complete = !!options.length && options.every(function (option) {
        return option && option.value !== undefined && option.value !== null && String(option.value) !== '';
      });
      return {
        locator: locateClickable(item),
        field: field,
        selected_label: selectedLabel,
        options: options,
        evidence: evidence
      };
    } catch (_) { return null; }
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
  function emitRadioGroup(el) {
    var loc = locateField(el);
    if (!loc) return;
    var evidence = fieldEvidence(el);
    evidence.control_kind = 'radio';
    var options = radioGroupOptions(el);
    var selected = null;
    for (var i = 0; i < options.length; i++) { if (options[i].checked) selected = options[i]; }
    evidence.group_name = el.name || '';
    evidence.selected_label = selected ? selected.label : '';
    evidence.options = options.map(function (item) { return {label: item.label, value: item.value}; });
    var field = el.name || fieldOf(loc);
    var groupLoc = el.name ? ('css=input[type="radio"][name="' + String(el.name).replace(/"/g, '\\"') + '"]') : loc;
    emit('select', groupLoc, selected ? selected.value : recordedScalar(el.value), field, requiredOf(el), evidence.options, evidence);
  }
  function emitToggle(el, kind) {
    var loc = locateField(el) || locateClickable(el);
    if (!loc) return;
    var evidence = fieldEvidence(el);
    evidence.control_kind = kind || (isSwitchEl(el) ? 'switch' : 'checkbox');
    var checked = readBooleanState(el);
    evidence.checked = checked;
    emit('toggle', loc, checked, fieldOf(loc), requiredOf(el), [], evidence);
  }
  function emitFile(el) {
    var loc = locateField(el);
    if (!loc) return;
    var evidence = fieldEvidence(el);
    evidence.control_kind = 'file';
    var facts = fileFieldFacts(el);
    evidence.filename = facts.filename;
    evidence.mime_type = facts.mime_type;
    evidence.size = facts.size;
    evidence.multiple = facts.multiple;
    evidence.file_count = facts.file_count;
    evidence.files = facts.files;
    emit('upload', loc, facts.filename || '', fieldOf(loc), requiredOf(el), [], evidence);
  }
  document.addEventListener('input', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (isSensitive(el)) return;
    if (isContentEditable(el)) { scheduleFill(el); return; }
    if (controlKind(el) !== 'select' && (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0))) {
      scheduleFill(el);
    }
  }, true);
  document.addEventListener('change', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (isSensitive(el)) return;
    if (isContentEditable(el)) { flushElementFill(el); return; }
    if (controlKind(el) !== 'select' && (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0))) flushElementFill(el);
    if (tag === 'select') {
      var l1 = locateField(el); var nativeEvidence = fieldEvidence(el);
      nativeEvidence.enum_source = 'dom'; nativeEvidence.mapping_complete = true;
      emit('select', l1, el.value, fieldOf(l1), requiredOf(el), nativeOptions(el), nativeEvidence);
    } else if (tag === 'input' && ty === 'file') {
      emitFile(el);
    } else if (tag === 'input' && ty === 'checkbox') {
      emitToggle(el, isSwitchEl(el) ? 'switch' : 'checkbox');
    } else if (tag === 'input' && ty === 'radio') {
      emitRadioGroup(el);
    }
  }, true);
  document.addEventListener('blur', function (e) {
    var el = e.target; var tag = (el.tagName || '').toLowerCase(); var ty = ((el.type || '') + '').toLowerCase();
    if (isSensitive(el)) return;
    if (isContentEditable(el) || (controlKind(el) !== 'select' && (tag === 'textarea' || (tag === 'input' && ['checkbox','radio','submit','button','file','hidden'].indexOf(ty) < 0)))) flushElementFill(el);
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
  function enumFieldAliases(trig, inp) {
    return controlAliases(inp || trig);
  }
  function isEnumTrigger(trig) {
    var inp = trig && trig.querySelector ? trig.querySelector('input,select,[role="combobox"]') : trig;
    return controlKind(inp || trig) === 'select';
  }
  function emitEnumSnapshot(trig, actionId) {
    if (!trig || onLoginPage() || !isEnumTrigger(trig)) return;
    try {
      var inp = trig.matches && trig.matches('input,select,[role="combobox"]') ? trig :
                (trig.querySelector ? trig.querySelector('input,select,[role="combobox"]') : null);
      var loc = locateField(inp || trig);
      if (!loc || isSensitive(inp || trig)) return;
      var records = []; var seen = {};
      var controller = (inp && inp.getAttribute && (inp.getAttribute('aria-controls') || inp.getAttribute('aria-owns'))) ||
                       (trig.getAttribute && (trig.getAttribute('aria-controls') || trig.getAttribute('aria-owns'))) || '';
      if (!controller && trig.querySelector) {
        var owned = trig.querySelector('[aria-controls],[aria-owns]');
        controller = owned && (owned.getAttribute('aria-controls') || owned.getAttribute('aria-owns')) || '';
      }
      var popups = [];
      controller.split(/\s+/).filter(Boolean).forEach(function (id) {
        var controlled = document.getElementById(id);
        if (!controlled) return;
        var popup = controlled.matches && controlled.matches(POPUP) ? controlled :
                    (controlled.closest ? controlled.closest(POPUP) : null);
        if (!popup) popup = controlled;
        if (popups.indexOf(popup) < 0) popups.push(popup);
      });
      // Only the popup owned by this combobox is authoritative. The fallback
      // is for frameworks without ARIA ownership and still excludes hidden
      // teleported dropdowns (Element Plus keeps closed menus in the DOM).
      if (!popups.length) {
        var expanded = (inp && inp.getAttribute && inp.getAttribute('aria-expanded')) ||
                       (trig.getAttribute && trig.getAttribute('aria-expanded')) || '';
        if (expanded === 'false') return;
        popups = Array.prototype.slice.call(document.querySelectorAll(POPUP));
      }
      for (var i = 0; i < popups.length; i++) {
        var popup = popups[i];
        if (popup.getAttribute && popup.getAttribute('aria-hidden') === 'true') continue;
        if (popup.closest && popup.closest('[aria-hidden="true"]')) continue;
        try {
          var style = getComputedStyle(popup);
          if (style.display === 'none' || style.visibility === 'hidden') continue;
        } catch (_) {}
        var options = popupOptions(popup);
        for (var j = 0; j < options.length; j++) {
          var option = options[j]; var label = clean(option && option.label);
          if (!label || seen[label]) continue;
          seen[label] = true; records.push(option);
        }
      }
      if (!records.length) return;
      window.__danoRecord(JSON.stringify({
        op: 'enum_snapshot',
        action_id: actionId || '',
        locator: loc,
        field: fieldOf(loc),
        field_aliases: enumFieldAliases(trig, inp),
        control_kind: 'select',
        options: records,
        enum_source: 'dom',
        // A custom/virtual/remote popup only proves the currently rendered
        // rows.  A dictionary API or a complete static array must fill this.
        mapping_complete: false,
        observed_at: Date.now(),
        page_context: window.__danoPageContext ? window.__danoPageContext() : {}
      }));
    } catch (e) {}
  }
  function scheduleEnumSnapshots(trig, actionId) {
    [60, 220, 600].forEach(function (delay) {
      setTimeout(function () { emitEnumSnapshot(trig, actionId); }, delay);
    });
  }
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
  var activeTrigger = null, activeControlActionId = '', prevVal = '', pickTimer = null, lastPickOptions = [];
  function clearActiveControl() {
    if (pickTimer) { clearInterval(pickTimer); pickTimer = null; }
    activeTrigger = null;
    activeControlActionId = '';
    prevVal = '';
    lastPickOptions = [];
  }
  function triggerOf(t) {                               // 触发型字段:已知选择器类 / 含 readonly input / aria-haspopup
    var k = t.closest ? t.closest(TRIGGER_CLS + ',[aria-haspopup]') : null; if (k) return k;
    var node = t;
    for (var i = 0; i < 4 && node && node !== document.body; i++) {
      try { if (node.querySelector && node.querySelector('input[readonly]')) return node; } catch (e) {}
      node = node.parentElement;
    }
    return null;
  }
  function isFieldPickerTrigger(trig) {
    var kind = controlKind(trig);
    return ['select','date','datetime','time'].indexOf(kind) >= 0;
  }
  function emitControlOpen(trig, actionId) {
    try {
      var inp = trig && trig.querySelector ? trig.querySelector('input,select,[role="combobox"]') : trig;
      var loc = locateField(inp || trig);
      if (!loc || onLoginPage()) return;
      var evidence = fieldEvidence(inp || trig);
      window.__danoRecord(JSON.stringify({
        op: 'control_open',
        action_id: actionId,
        observed_at: Date.now(),
        locator: loc,
        field: fieldOf(loc),
        field_aliases: (evidence && evidence.field_aliases) || [],
        control_kind: (evidence && evidence.control_kind) || 'unknown',
        page_context: window.__danoPageContext ? window.__danoPageContext() : {}
      }));
    } catch (e) {}
  }
  // 取触发框当前"显示值":优先 input.value(旧 Element UI),退而读触发框可见文本
  // —— Element Plus(Vue3)等现代框架选中值在文本节点里、input.value 为空,必须读 innerText 才抓得到。
  function pickVal(trig) {
    if (!trig) return '';
    var host = trig;
    try {
      if (trig.matches && trig.matches('input,select,[role="combobox"]')) {
        var parent = trig.parentElement;
        for (var depth = 0; parent && depth < 6; depth++, parent = parent.parentElement) {
          if (parent.matches && parent.matches(TRIGGER_CLS) && controlKind(parent) === 'select') {
            host = parent; break;
          }
        }
      }
    } catch (_) {}
    var inp = trig.matches && trig.matches('input') ? trig :
              (host.querySelector ? host.querySelector('input') : null);
    var v = inp ? clean(inp.value) : '';
    if (!v && inp) {                                       // H4+H10 修复:input.value 为空时,优先读 aria-valuetext / title(Element Plus/Ant Design 把选中值放这里)
        v = clean(inp.getAttribute('aria-valuetext') || inp.getAttribute('title') || '');
    }
    if (!v) {                                              // 兜底:读 innerText,但过滤 placeholder 灰色文本(否则与 prevVal 永远相等,pollPick 超时放弃)
        var txt = clean(host.innerText || '');
        // M-A1 修复:用真正常见的 placeholder 选择器 + opacity < 0.6 + Element Plus `el-select__placeholder` 类
        // 框架透明色文本占位符在 Ant Design v5 是独立 span(opacity < 0.5);Element Plus 用 `.el-select__placeholder`(默认隐藏层)
        var sels = '[class*="placeholder" i],[class*="el-select__placeholder"],[placeholder]';
        var placeholders = host.querySelectorAll ? host.querySelectorAll(sels) : [];
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
  function pollPick(trig, resetOptions, actionId) {
    if (pickTimer) { clearInterval(pickTimer); pickTimer = null; }
    if (!trig) return;
    // 只有打开一个新触发框时才清理旧候选。点击弹层选项后再次启动轮询时，
    // 必须保留刚从弹层抓到的 options，否则录制结果只剩内部提交码。
    if (resetOptions) lastPickOptions = [];
    var tries = 0;
    pickTimer = setInterval(function () {
      tries++;
      var v = pickVal(trig);
      if (v && v !== prevVal) {                         // 显示值已落定(与点击前不同)→ 记 pick
        clearInterval(pickTimer); pickTimer = null;
        var inp = trig.querySelector ? trig.querySelector('input') : null;
        var loc = locateField(inp || trig);
        if (loc) emit(
          'pick', loc, v, fieldOf(loc), requiredOf(trig) || (inp && requiredOf(inp)),
          isEnumTrigger(trig) ? lastPickOptions : [], fieldEvidence(inp || trig), actionId
        );
        clearActiveControl();
      } else if (tries >= 25) { clearActiveControl(); }   // ~2.5s 仍没变 → 放弃并解除旧弹层归属
    }, 100);
  }
  document.addEventListener('click', function (e) {
    // A) 点在日期/下拉弹层内 = 正在选择 → 不记这次点击;先把**此刻弹层里可见的选项**抓下来(地面真值枚举),
    //    再轮询触发框值落定后随 pick 一起回传(选完即生效)
    var clickedPopup = e.target.closest && e.target.closest(POPUP);
    if (clickedPopup && activeTrigger && isFieldPickerTrigger(activeTrigger)) {
      var _opts = isEnumTrigger(activeTrigger) ? popupOptions(clickedPopup) : [];
      if (_opts.length) {
        lastPickOptions = _opts;
        // Persist the owned popup before the page hides it or a following
        // click clears the active control.  The snapshot is evidence only;
        // it never becomes an executable workflow step.
        emitEnumSnapshot(activeTrigger, activeControlActionId);
      }
      pollPick(activeTrigger, false, activeControlActionId); return;
    }
    if (clickedPopup) clearActiveControl();
    var persistent = persistentChoice(e.target);
    if (persistent) {
      emit(
        'pick', persistent.locator, persistent.selected_label, persistent.field,
        false, persistent.options, persistent.evidence
      );
      return;
    }
    // B) 点选择型触发框 → 记住它 + 点击前的显示值,开始轮询(覆盖单击即选 / 远程搜索异步回填 / 级联)
    var trig = triggerOf(e.target);
    if (trig && isFieldPickerTrigger(trig)) {
      actionSeq += 1;
      activeControlActionId = 'action_' + actionSeq;
      activeTrigger = trig;
      prevVal = pickVal(trig);
      emitControlOpen(trig, activeControlActionId);
      pollPick(trig, true, activeControlActionId);
      if (isEnumTrigger(trig)) scheduleEnumSnapshots(trig, activeControlActionId);
      return;
    }
    // C) 普通输入框点击 = 聚焦噪声(打字会另记 fill)→ 跳过
    if (e.target.closest && e.target.closest('input,select,textarea,[contenteditable="true"]')) { clearActiveControl(); return; }
    var switchHost = e.target.closest && e.target.closest('[role="switch"],.el-switch,.ant-switch,.van-switch,.q-toggle,.v-switch,.mat-mdc-slide-toggle');
    if (switchHost) {
      var switchTarget = switchHost;
      setTimeout(function () { emitToggle(switchTarget, 'switch'); }, 50);
      return;
    }
    // D) 普通可点元素(按钮/卡片/菜单/链接)
    clearActiveControl();
    var el = target(e.target); if (!el) return;
    var loc = locateClickable(el); if (!loc) return;
    var role = roleOf(el); var name = accName(el);
    var isSubmit = role === 'button' && SUBMIT.some(function (h) { return name.toLowerCase().indexOf(h) >= 0; });
    var isFormCommand = role === 'button' && FORM_COMMAND.some(function (h) { return name.toLowerCase().indexOf(h) >= 0; });
    var formActionId = '';
    if (isFormCommand) {
      actionSeq += 1;
      formActionId = 'action_' + actionSeq;
      emitFormSnapshot(formActionId);
      scheduleEvidenceSnapshot();
    }
    emit(isSubmit ? 'submit' : 'click', loc, '', '', false, [], null, formActionId || undefined);
  }, true);
}"""


class RecordSession:
    """一次网页内录制。start→(用户经截屏+输入回传操作)→recorded_steps→stop。"""

    def __init__(self, *, on_request: Callable[[dict], None] | None = None,
                 on_capture_count: Callable[[int], None] | None = None,
                 intercept_submit: bool = True, capture_reads: bool = True,
                 tenant: str = "") -> None:
        self.steps: list[dict] = []
        # 点击提交时保存的页面语义/必填快照。它独立于操作步骤，避免弹窗关闭后
        # finalize 扫描不到表单，也避免把证据误导出成回放动作。
        self.form_snapshots: list[dict] = []
        self.enum_snapshots: list[dict] = []
        # Bounded same-origin JavaScript bodies are optional enum evidence.
        # They are never executable steps and never override runtime DOM/API
        # mappings; they only repair a label-only snapshot when a statically
        # declared option array can be tied to the exact field alias.
        self.script_sources: list[dict] = []
        self._script_source_bytes = 0
        # 字典接口经常随页面初始化完成。只保留具备
        # dictType + label + value 结构的引用数据，reset 时不清除，避免枚举证据丢失。
        self.dictionary_reads: list[dict] = []
        self.reads: list[dict] = []         # 抓到的读请求(GET+JSON 列表/字典)→ Q2 选领导等 select 的候选源
        # P0-1:全量捕获(基础事实)。先抓全再筛 → 治"GET 业务接口被早筛丢"等根因。
        # 不管 method / 业务角色,只要页面发出,就落一行,供后续 P0-2 角色分类 + P0-3 依赖闭包使用。
        # 字段:method/url/headers/query/post_data/response_json/status/content_type/timestamp/index。
        self.all_requests: list[dict] = []
        # P0-1:诊断事件(console/pageerror/requestfailed)→ 排查"接口成功但页面报错"等隐蔽故障。
        self.diagnostics: list[dict] = []
        # Observer 事件链与回放步骤分开保存，避免观察证据被误当成可执行动作。
        self.page_events: list[dict] = []
        self._event_counter: int = 0
        self._last_action_by_scope: dict[tuple[str, str], dict] = {}
        self._semantic = SemanticFieldIndex()
        self._req_counter: int = 0          # 顺序号,作为 all_requests[i]["index"] 与 diagnostics 关联锚点
        self._request_fact_index: dict[int, int] = {}
        self._page_counter: int = 0
        self._frame_counter: int = 0
        self._page_ids: dict[int, str] = {}
        self._frame_ids: dict[int, str] = {}
        self._on_request_cb = on_request    # 实时把抓到的请求推给前端(诊断可见)
        self._on_capture_count_cb = on_capture_count
        # 拦截提交:点提交时抓到业务写请求后,假装成功、不真发给服务器 → 录制不产生真实记录
        self._intercept = intercept_submit
        self._capture_reads = capture_reads
        self._tenant = tenant
        self._pw = None
        self._browser = None
        self._context = None
        self._cdp = None
        self._on_frame: Callable[[dict], Awaitable[None]] | None = None  # 截屏回调(切活动页时复用)
        self._frame_seq = 0
        self._last_frame_sent_at = 0.0
        self._last_activity_at = time.monotonic()
        self._closing = False        # stop()/断连中:页面 close 事件不再重开截屏(避免在已关 context 上 new_cdp_session 抛错)
        self._recording_paused = False
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

    def _trim_page_events(self) -> None:
        if len(self.page_events) <= 1000:
            return
        for event in self.page_events[:-1000]:
            self._semantic.archive_event(event)
        self.page_events = self.page_events[-1000:]

    def _trim_form_snapshots(self) -> None:
        if len(self.form_snapshots) <= 20:
            return
        for snapshot in self.form_snapshots[:-20]:
            self._semantic.archive_form_snapshot(snapshot)
        self.form_snapshots = self.form_snapshots[-20:]

    def _trim_enum_snapshots(self) -> None:
        if len(self.enum_snapshots) <= 200:
            return
        for snapshot in self.enum_snapshots[:-200]:
            self._semantic.archive_enum_snapshot(snapshot)
        self.enum_snapshots = self.enum_snapshots[-200:]

    def _request_scope(self, request) -> dict[str, str]:  # noqa: ANN001
        """Best-effort page/frame anchors for RequestFacts."""
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
                    storage_state: str | dict | None = None, token: str | None = None,
                    token_key: str | None = None) -> None:
        from playwright.async_api import async_playwright

        from dano.execution.page.driver import apply_token_auth
        from dano.infra.http import tls_verify
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"viewport": {"width": _VIEW_W, "height": _VIEW_H},
                            "ignore_https_errors": not tls_verify()}
        playwright_state, session_storage = self._split_browser_storage_state(storage_state)
        if playwright_state:
            ctx_kwargs["storage_state"] = playwright_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        if session_storage:
            # BrowserContext init scripts run in every page/frame before the
            # application's scripts. Restore only the matching origin so one
            # system's credentials can never leak into another embedded origin.
            payload = json.dumps(session_storage, ensure_ascii=False).replace("</", "<\\/")
            await self._context.add_init_script(
                "(() => {"
                f"const byOrigin={payload};"
                "const entries=byOrigin[location.origin];"
                "if(!Array.isArray(entries))return;"
                "try{for(const entry of entries){"
                "if(entry&&typeof entry.name==='string'&&typeof entry.value==='string')"
                "sessionStorage.setItem(entry.name,entry.value);"
                "}}catch(_error){}"
                "})();"
            )
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
        await self._ensure_recorder_installed(self.page)

    @staticmethod
    def _split_browser_storage_state(storage_state: str | dict | None) -> tuple[str | dict | None, dict]:
        """Return Playwright-compatible state plus our per-origin sessionStorage.

        Existing callers may supply a path, a JSON object string, or an in-memory
        dict. A plain path without the custom root key remains a path so legacy
        Playwright behavior is unchanged.
        """
        if not storage_state:
            return None, {}
        raw: object = storage_state
        if isinstance(storage_state, str):
            stripped = storage_state.strip()
            try:
                if stripped.startswith("{"):
                    raw = json.loads(stripped)
                else:
                    path = Path(storage_state)
                    if not path.exists():
                        return storage_state, {}
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(loaded, dict) or SESSION_STORAGE_STATE_KEY not in loaded:
                        return storage_state, {}
                    raw = loaded
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return storage_state, {}
        if not isinstance(raw, dict):
            return storage_state if isinstance(storage_state, str) else None, {}
        playwright_state = dict(raw)
        session_storage = playwright_state.pop(SESSION_STORAGE_STATE_KEY, {})
        if not isinstance(session_storage, dict):
            session_storage = {}
        return playwright_state, session_storage

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
        await self._ensure_recorder_installed(page)
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
            page.on("framenavigated", self._on_frame_navigated)
            page.on("domcontentloaded", lambda: self._on_document_ready(page))
            page.on("load", lambda: self._mark_active())
        except Exception:  # noqa: BLE001
            pass

    async def _ensure_recorder_installed(self, target) -> None:  # noqa: ANN001
        """Restore the recorder after a portal replaces a live document."""
        if self._closing or target is None:
            return
        try:
            is_closed = getattr(target, "is_closed", None)
            if callable(is_closed) and is_closed():
                return
            is_detached = getattr(target, "is_detached", None)
            if callable(is_detached) and is_detached():
                return
            await target.evaluate(_RECORDER_JS)
        except Exception:  # noqa: BLE001 - navigation can replace the target mid-install
            pass

    def _schedule_recorder_install(self, target) -> None:  # noqa: ANN001
        if self._closing:
            return
        try:
            asyncio.get_running_loop().create_task(self._ensure_recorder_installed(target))
        except RuntimeError:
            pass

    def _on_frame_navigated(self, frame) -> None:  # noqa: ANN001
        self._mark_active()
        self._schedule_recorder_install(frame)

    def _on_document_ready(self, page) -> None:  # noqa: ANN001
        self._mark_active()
        self._schedule_recorder_install(page)

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
                    frame_id: str = "", resource_type: str = "",
                    navigation_request: bool = False) -> int:
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
            "resource_type": resource_type,
            "navigation_request": bool(navigation_request),
            "timestamp": int(time.time() * 1000),
            # P0-2:角色分类字段。响应未到时先按现有信息初分(_classify_entry),响应落地后再 _classify_entry 一次。
            # classify_field 缺失 = 还没分类过(兜底用)。
        }
        # 时间相邻只能作为候选因果证据，所以同时记录延迟和置信等级，供后续推导/复核使用。
        now_monotonic = time.monotonic()
        action = self._last_action_by_scope.get((page_id, frame_id))
        if action is None and page_id:
            # A request may originate from a child frame while the actionable
            # click was observed in its owning page. Fall back only within that
            # page; the old global fallback attached background requests from
            # another tab/page to the most recent unrelated action.
            same_page = [
                candidate for (candidate_page, _candidate_frame), candidate in self._last_action_by_scope.items()
                if candidate_page == page_id
            ]
            if same_page:
                action = max(same_page, key=lambda item: float(item.get("monotonic") or 0))
        if action is None and not page_id and not frame_id:
            action = self._last_action_by_scope.get(("", ""))
        if action is not None:
            delta_ms = max(0, int((now_monotonic - float(action.get("monotonic") or now_monotonic)) * 1000))
            if delta_ms <= 5000:
                entry.update({
                    "trigger_action_id": action.get("action_id") or "",
                    "trigger_transaction_id": action.get("transaction_id") or "",
                    "trigger_event_id": action.get("event_id") or "",
                    "trigger_op": action.get("op") or "",
                    "trigger_locator": action.get("locator") or "",
                    "trigger_page_context": dict(action.get("page_context") or {}),
                    "action_delta_ms": delta_ms,
                    "causality_confidence": "high" if delta_ms <= 1200 else "medium" if delta_ms <= 3500 else "low",
                })
        self.all_requests.append(entry)
        self._classify_entry(entry)
        if self._on_capture_count_cb is not None:
            try:
                self._on_capture_count_cb(len(self.all_requests))
            except Exception:  # noqa: BLE001 - 捕获进度不能中断权威请求 ledger
                pass
        return idx

    def _attach_response(self, *, url: str, method: str, status, content_type: str,
                         request_index: int | None = None,
                         response_json=None, response_kind: str = "",
                         response_text: str | None = None, response_empty: bool | None = None,
                         response_size: int | None = None) -> bool:
        """优先按请求实例精确贴回响应，缺少实例锚点时才退回 url+method。

        集中处理反查 + 改字段,避免 _route / _on_response / 后续 P0-3 依赖闭包都各自遍历 all_requests。
        P0-2:贴完响应后顺手再分类一次(此时 read_option / business_get 才能判准)。"""
        candidates = self.all_requests
        if request_index is not None:
            indexed = [r for r in self.all_requests if r.get("index") == request_index]
            if indexed:
                candidates = indexed
        for r in reversed(candidates):
            if request_index is None and not (r.get("url") == url and r.get("method") == method):
                continue
            if request_index is None and r.get("response_kind"):
                continue
            r["status"] = status
            r["response_status"] = status
            r["content_type"] = content_type or r.get("content_type", "")
            if response_kind:
                r["response_kind"] = response_kind
            if response_json is not None:
                r["response_json"] = response_json
            if response_text is not None:
                r["response_text"] = response_text
            if response_empty is not None:
                r["response_empty"] = response_empty
            if response_size is not None:
                r["response_size"] = response_size
            self._classify_entry(r)
            return True
        return False

    def _classify_entry(self, entry: dict) -> None:
        """P0-2:对单条 all_requests entry 调用 classify_network_request,把结果写到 entry 上。

        失败/异常时静默留空(下游 captured_all_requests 会兜底重试)。"""
        try:
            from dano.execution.page.request_capture import classify_network_request
            cls = classify_network_request(entry, self.all_requests)
            entry["role"] = cls["role"]
            entry["keep"] = cls["keep"]
            entry["reason"] = cls["reason"]
            entry["confidence"] = cls["confidence"]
        except Exception:  # noqa: BLE001 —— 分类失败不影响事实链路,留空给兜底
            pass

    @staticmethod
    def _looks_like_dictionary_items(items: list) -> bool:
        """只缓存明确的通用字典记录，避免把业务列表跨 reset 保存。"""
        matched = 0
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            keys = {re.sub(r"[^a-z0-9]+", "", str(key).casefold()) for key in item}
            if "dicttype" in keys and "label" in keys and "value" in keys:
                matched += 1
        return matched >= 2

    def _record_diag(self, kind: str, payload: dict) -> None:
        """记录一条诊断事件:console/pageerror/requestfailed。

        统一结构 {type, level?, message, url?, timestamp, request_index?},供 P0-6 review_items 与人工排错使用。
        request_index 关联到 all_requests 里同源请求(失败请求 → request_index=关联到该请求的 index)。"""
        rec = {"type": kind, "timestamp": int(time.time() * 1000)}
        rec.update({k: v for k, v in (payload or {}).items() if v is not None})
        self.diagnostics.append(rec)

    def _notify_request(
        self,
        m: str,
        url: str,
        pd: str | None,
        ct: str,
        *,
        resource_type: str = "",
        request_id: str = "",
        request_index: int | None = None,
    ) -> None:
        """Notify live analysis after the authoritative ledger row is recorded."""
        if self._on_request_cb is not None:
            is_json = "json" in (ct or "").lower() or (pd or "").lstrip().startswith(("{", "["))
            try:
                self._on_request_cb({
                    "method": m,
                    "url": url,
                    "has_body": bool(pd),
                    "json": bool(pd) and is_json,
                    "resource_type": resource_type,
                    "request_id": request_id,
                    "request_index": request_index,
                })
            except Exception:  # noqa: BLE001
                pass

    def _on_request(self, request) -> None:  # noqa: ANN001 —— playwright Request(直录模式旁观)
        if self._recording_paused:
            return
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
            try:
                resource_type = str(request.resource_type or "")
            except Exception:  # noqa: BLE001
                resource_type = ""
            try:
                navigation_request = bool(request.is_navigation_request())
            except Exception:  # noqa: BLE001
                navigation_request = False
            # P0-1:GET 也落 all_requests(全量捕获,治"业务 GET 前置接口被早筛丢")。
            # _record_all 是唯一请求 ledger 写入点；写请求另发轻量诊断通知。
            request_index = self._record_all(
                m, url, pd=pd, headers=hd, content_type=hd.get("content-type", ""),
                resource_type=resource_type, navigation_request=navigation_request,
                **self._request_scope(request),
            )
            self._request_fact_index[id(request)] = request_index
            self._notify_request(
                m,
                url,
                pd,
                hd.get("content-type", ""),
                resource_type=resource_type,
                request_id=f"req_{request_index}",
                request_index=request_index,
            )
        except Exception:  # noqa: BLE001
            pass

    def _success_envelope(self) -> str:
        """伪造的"提交成功"响应体 —— **镜像本系统自己的成功约定**(从已抓到的成功读响应学),不写死特定平台的 code=200。

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
        if self._recording_paused:
            await route.continue_()
            return
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
            try:
                resource_type = str(request.resource_type or "")
            except Exception:  # noqa: BLE001
                resource_type = ""
            try:
                navigation_request = bool(request.is_navigation_request())
            except Exception:  # noqa: BLE001
                navigation_request = False
            # P0-1:GET 也落 all_requests(全量捕获)。后续 P0-3 依赖闭包要靠它发现"业务 GET 前置接口"。
            request_index = self._record_all(
                m, url, pd=pd, headers=hd, content_type=ct,
                resource_type=resource_type, navigation_request=navigation_request,
                **self._request_scope(request),
            )
            self._request_fact_index[id(request)] = request_index
            self._notify_request(
                m,
                url,
                pd,
                ct,
                resource_type=resource_type,
                request_id=f"req_{request_index}",
                request_index=request_index,
            )
            # H7 修复:multipart/form-data 上传(文件/附件)必须真发,否则文件丢失但 UI 显示成功
            # multipart body 不在 request.post_data,而在 post_data_buffer,pd 必为 None,自然走 continue_,但要
            # 在此显式再判一次 content_type 兜底(部分框架 multipart 也会塞进 post_data)
            if (ct or "").lower().startswith("multipart/"):
                await route.continue_()
                return
            # 业务写请求 → 抓下来,假装成功不真发;登录/鉴权/上传等基建写、以及 POST 形态的读/查询
            #(getXxxList/queryXxx:下拉/列表源)照常放行真发(否则录制时下拉/列表加载不出来,选不了值)
            if pd and not looks_like_auth_write(url, pd) and not looks_like_read_request(url, pd):
                await route.fulfill(status=200, content_type="application/json",
                                    body=self._success_envelope())
                return
            await route.continue_()
        except Exception:  # noqa: BLE001
            try:
                await route.continue_()
            except Exception:  # noqa: BLE001
                pass

    def captured_reads(self) -> list[dict]:
        return list(self.reads)

    def captured_all_requests(self) -> list[dict]:
        """P0-1:全量网络请求(GET/POST/PUT/PATCH/DELETE 都记)。先抓全再筛 → P0-3 依赖闭包基于此。

        P0-2:每条 entry 已带 role/keep/reason/confidence(由 _classify_entry 写入,响应落地后再分一次);
        万一某条漏分类(异常路径),返回前兜底再分一次。

        返回不可变副本,避免外部误改污染内部状态。每条字段:index/method/url/headers/query/post_data
        /response_json/status/content_type/timestamp/role/keep/reason/confidence。"""
        # 使用完整请求时间线重算，避免早到请求在后续依赖出现前保留过时角色。
        for r in self.all_requests:
            self._classify_entry(r)
        return [dict(r) for r in self.all_requests]

    def captured_diagnostics(self) -> list[dict]:
        """P0-1:诊断事件(console/pageerror/requestfailed)。返回不可变副本。

        每条字段:type/level?(console 用)/message/url?/timestamp/request_index?(requestfailed 用)。"""
        return [dict(d) for d in self.diagnostics]

    def recorded_page_events(self) -> list[dict]:
        """返回脱敏后的页面观察时间线，不包含输入值或响应正文。"""
        return [dict(event) for event in self.page_events]

    def _resp_dispatch(self, response) -> None:  # noqa: ANN001 —— 同步快筛后再异步读 body
        if self._recording_paused:
            return
        try:
            m = (response.request.method or "").upper()
            if m in ("GET", "POST", "PUT", "PATCH", "DELETE"):   # GET=select 候选源;写/删=把响应贴回原 request_id
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
            try:
                resource_type = str(response.request.resource_type or "").lower()
            except Exception:  # noqa: BLE001
                resource_type = ""
            if resource_type == "script" or "javascript" in ct.lower():
                # Keep strict bounds: script evidence is a fallback index, not
                # an archive of application bundles. Cross-origin/CORS does not
                # matter here because Playwright reads the already loaded
                # response body, and no script is evaluated by Python.
                if len(self.script_sources) < 40 and self._script_source_bytes < 12_000_000:
                    try:
                        body = await response.text()
                    except Exception:  # noqa: BLE001
                        body = ""
                    body_size = len(body.encode("utf-8", errors="ignore")) if body else 0
                    if body and body_size <= 6_000_000 and self._script_source_bytes + body_size <= 12_000_000:
                        self.script_sources.append({"url": response.url, "text": body})
                        self._script_source_bytes += body_size
                return
            request_index = self._request_fact_index.get(id(response.request))
            json_data = None
            text = None
            body_bytes = None
            if "json" in (ct or "").lower() or not ct:
                try:
                    json_data = await response.json()
                except Exception:  # noqa: BLE001
                    json_data = None
            if json_data is None:
                try:
                    body_bytes = await response.body()
                except Exception:  # noqa: BLE001
                    body_bytes = None
                try:
                    text = await response.text()
                except Exception:  # noqa: BLE001
                    text = None
                if text and "json" in (ct or "").lower():
                    try:
                        json_data = json.loads(text)
                        text = None
                    except Exception:  # noqa: BLE001
                        pass
                elif text and not ct:
                    stripped = text.lstrip()
                    if stripped[:1] in "{[":
                        try:
                            json_data = json.loads(text)
                            text = None
                        except Exception:  # noqa: BLE001
                            pass
            fields = _classify_http_response(
                status=response.status,
                content_type=ct,
                json_data=json_data,
                text=text,
                body_bytes=body_bytes,
            )
            self._attach_response(
                url=url, method=m, request_index=request_index, **fields,
            )
            if any(n in url.lower() for n in _READ_NOISE):    # 只跳静态/流(保留字典/列表接口)
                return
            data_for_list = json_data
            if data_for_list is None:
                return
            items = as_list_payload(data_for_list)
            if items is None:
                return
            request_index = self._request_fact_index.get(id(response.request))
            fact = next((item for item in self.all_requests if item.get("index") == request_index), {})
            read_fact = {
                "method": m,
                "url": url,
                "status": response.status,
                "json": data_for_list if len(self.reads) < 60 else None,
                "count": len(items),
                "request_index": request_index,
                "request_id": fact.get("request_id"),
                "sequence": fact.get("sequence", request_index),
                "page_id": fact.get("page_id"),
                "frame_id": fact.get("frame_id"),
                "role": fact.get("role"),
                **{
                    key: fact.get(key)
                    for key in (
                        "trigger_action_id", "trigger_transaction_id", "trigger_event_id",
                        "trigger_op", "trigger_locator", "trigger_page_context",
                        "action_delta_ms", "causality_confidence",
                    )
                    if fact.get(key) not in (None, "")
                },
            }
            self.reads.append(read_fact)
            if self._looks_like_dictionary_items(items) and len(self.dictionary_reads) < 20:
                self.dictionary_reads.append(read_fact)
        except Exception:  # noqa: BLE001
            pass

    def _on_record(self, source, payload: str) -> None:  # noqa: ANN001 —— expose_binding 回调
        if self._recording_paused:
            return
        try:
            step = json.loads(payload)
        except Exception:  # noqa: BLE001
            return
        try:
            source_page = source.get("page") if isinstance(source, dict) else None
            source_frame = source.get("frame") if isinstance(source, dict) else None
            page_id = self._page_id(source_page)
            frame_id = self._frame_id(source_frame)
            if page_id:
                step["page_id"] = page_id
            if frame_id:
                step["frame_id"] = frame_id
        except Exception:  # noqa: BLE001
            pass
        self._mark_active()
        self._event_counter += 1
        event_id = f"event_{self._event_counter}"
        observed_at = step.get("observed_at") or int(time.time() * 1000)
        scope = (str(step.get("page_id") or ""), str(step.get("frame_id") or ""))
        op = str(step.get("op") or "")
        if op == "control_open":
            action_id = str(step.get("action_id") or f"action_server_{self._event_counter}")
            transaction_id = "|".join(part for part in (
                scope[0] or "page_unknown",
                scope[1] or "frame_unknown",
                action_id,
            ))
            event = {
                "event_id": event_id,
                "kind": "action",
                "action_id": action_id,
                "transaction_id": transaction_id,
                "op": op,
                "locator": str(step.get("locator") or ""),
                "field": str(step.get("field") or ""),
                "control_kind": str(step.get("control_kind") or "unknown"),
                "observed_at": observed_at,
                "page_id": scope[0],
                "frame_id": scope[1],
                "page_context": dict(step.get("page_context") or {}),
            }
            self.page_events.append(event)
            action_ref = {
                "action_id": action_id,
                "transaction_id": transaction_id,
                "event_id": event_id,
                "op": op,
                "locator": event["locator"],
                "page_context": event["page_context"],
                "monotonic": time.monotonic(),
            }
            self._last_action_by_scope[scope] = action_ref
            self._last_action_by_scope[("", "")] = action_ref
            self._trim_page_events()
            return
        if op == "dom_effect":
            self.page_events.append({
                "event_id": event_id,
                "kind": "dom_effect",
                "action_id": str(step.get("action_id") or ""),
                "observed_at": observed_at,
                "page_id": scope[0],
                "frame_id": scope[1],
                "changes": list(step.get("changes") or [])[:80],
                "required_fields": list(step.get("required_fields") or [])[:200],
                "page_context": dict(step.get("page_context") or {}),
            })
            self._trim_page_events()
            return
        if op == "enum_snapshot":
            action_id = str(step.get("action_id") or "")
            transaction_id = (
                "|".join(part for part in (
                    scope[0] or "page_unknown",
                    scope[1] or "frame_unknown",
                    action_id,
                ))
                if action_id else ""
            )
            raw_options = list(step.get("options") or [])
            options = raw_options[:500]
            if options and step.get("field"):
                snapshot = {
                    **step,
                    "options": options,
                    "event_id": event_id,
                    "transaction_id": transaction_id,
                    "mapping_complete": bool(step.get("mapping_complete")) and len(raw_options) <= 500,
                    "snapshot_truncated": len(raw_options) > 500,
                }
                identity = (
                    str(snapshot.get("page_id") or ""),
                    str(snapshot.get("frame_id") or ""),
                    str(snapshot.get("locator") or ""),
                    str(snapshot.get("field") or ""),
                    route_identity(snapshot),
                    form_root_identity(snapshot),
                )
                replaced = False
                for index in range(len(self.enum_snapshots) - 1, -1, -1):
                    previous = self.enum_snapshots[index]
                    previous_identity = (
                        str(previous.get("page_id") or ""),
                        str(previous.get("frame_id") or ""),
                        str(previous.get("locator") or ""),
                        str(previous.get("field") or ""),
                        route_identity(previous),
                        form_root_identity(previous),
                    )
                    if previous_identity != identity:
                        continue
                    merged = {
                        str(item.get("label") if isinstance(item, dict) else item): item
                        for item in (previous.get("options") or [])
                        if str(item.get("label") if isinstance(item, dict) else item)
                    }
                    for option in options:
                        label = str(option.get("label") if isinstance(option, dict) else option)
                        if label:
                            old = merged.get(label)
                            if (
                                isinstance(old, dict) and isinstance(option, dict)
                                and old.get("value") != option.get("value")
                                and "value" in old and "value" in option
                            ):
                                snapshot["mapping_conflict"] = True
                            merged[label] = option
                    merged_options = list(merged.values())
                    snapshot["options"] = merged_options[:500]
                    snapshot["snapshot_truncated"] = bool(
                        snapshot.get("snapshot_truncated")
                        or previous.get("snapshot_truncated")
                        or len(merged_options) > 500
                    )
                    snapshot["mapping_conflict"] = bool(
                        snapshot.get("mapping_conflict") or previous.get("mapping_conflict")
                    )
                    snapshot["mapping_complete"] = bool(
                        snapshot.get("mapping_complete")
                        and previous.get("mapping_complete")
                        and not snapshot["snapshot_truncated"]
                        and not snapshot["mapping_conflict"]
                    )
                    self.enum_snapshots[index] = snapshot
                    replaced = True
                    break
                if not replaced:
                    self.enum_snapshots.append(snapshot)
                self._semantic.archive_enum_snapshot(snapshot)
                self._trim_enum_snapshots()
                self.page_events.append({
                    "event_id": event_id,
                    "kind": "enum_snapshot",
                    "action_id": action_id,
                    "transaction_id": transaction_id,
                    "field": str(step.get("field") or ""),
                    "locator": str(step.get("locator") or ""),
                    "option_count": len(options),
                    "observed_at": observed_at,
                    "page_id": scope[0],
                    "frame_id": scope[1],
                })
                self._trim_page_events()
            return
        if step.get("op") == "form_snapshot":
            step["event_id"] = event_id
            step["observed_at"] = observed_at
            action_id = str(step.get("action_id") or "")
            if action_id:
                transaction_id = "|".join(part for part in (
                    scope[0] or "page_unknown",
                    scope[1] or "frame_unknown",
                    action_id,
                ))
                step["action_id"] = action_id
                step["transaction_id"] = transaction_id
            self.form_snapshots.append(step)
            self.page_events.append({
                "event_id": event_id,
                "kind": "form_snapshot",
                "action_id": action_id,
                "transaction_id": str(step.get("transaction_id") or ""),
                "observed_at": observed_at,
                "page_id": scope[0],
                "frame_id": scope[1],
                "required_fields": list(step.get("required_fields") or [])[:200],
                "field_count": len(step.get("fields") or []),
                "page_context": dict(step.get("page_context") or {}),
            })
            # 同一表单反复点击只保留有限快照；最后一份是发布时的地面真值。
            self._semantic.archive_form_snapshot(step)
            self._trim_form_snapshots()
            self._trim_page_events()
            return
        action_id = str(step.get("action_id") or f"action_server_{self._event_counter}")
        step["action_id"] = action_id
        step["event_id"] = event_id
        transaction_id = "|".join(part for part in (
            scope[0] or "page_unknown",
            scope[1] or "frame_unknown",
            action_id,
        ))
        step["transaction_id"] = transaction_id
        if op == "upload":
            filename = str(step.get("filename") or "").strip()
            raw_value = step.get("value")
            if isinstance(raw_value, str) and (
                "fakepath" in raw_value.casefold() or "\\" in raw_value or raw_value.startswith("/")
            ):
                basename = filename or Path(raw_value.replace("\\", "/")).name
                if "fakepath" in basename.casefold():
                    basename = filename
                step["value"] = basename
                if not filename:
                    step["filename"] = basename
            elif not filename and isinstance(raw_value, str):
                step["filename"] = Path(raw_value.replace("\\", "/")).name
        self.page_events.append({
            "event_id": event_id,
            "kind": "action",
            "action_id": action_id,
            "transaction_id": transaction_id,
            "op": op,
            "locator": str(step.get("locator") or ""),
            "field": str(step.get("field") or ""),
            "required": bool(step.get("required")),
            "has_value": has_recorded_value(step),
            "observed_at": observed_at,
            "page_id": scope[0],
            "frame_id": scope[1],
            "page_context": dict(step.get("page_context") or {}),
        })
        action_ref = {
            "action_id": action_id,
            "transaction_id": transaction_id,
            "event_id": event_id,
            "op": op,
            "locator": str(step.get("locator") or ""),
            "page_context": dict(step.get("page_context") or {}),
            "monotonic": time.monotonic(),
        }
        self._last_action_by_scope[scope] = action_ref
        self._last_action_by_scope[("", "")] = action_ref
        self._semantic.archive_event(self.page_events[-1])
        self._trim_page_events()
        stamp_field_identity(step)
        same_locator = bool(
            self.steps
            and self.steps[-1].get("locator") == step.get("locator")
            and self.steps[-1].get("page_id") == step.get("page_id")
            and self.steps[-1].get("frame_id") == step.get("frame_id")
            and route_identity(self.steps[-1]) == route_identity(step)
            and step.get("op") in ("fill", "select", "pick")
        )
        same_action = bool(
            self.steps
            and (
                str(self.steps[-1].get("action_id") or "") == str(step.get("action_id") or "")
                or str(self.steps[-1].get("transaction_id") or "") == str(step.get("transaction_id") or "")
            )
        )
        if same_locator and same_action:
            self.steps[-1] = step
        else:
            self.steps.append(step)
        if step.get("op") in {"fill", "select", "pick", "toggle", "upload"}:
            self._semantic.archive_control(control_item_from_step(step))
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
        pending_frame: dict | None = None
        frame_flush_task: asyncio.Task | None = None

        def _frame_delay() -> float:
            now = time.monotonic()
            active = (now - self._last_activity_at) <= _CAST_ACTIVE_WINDOW_S
            min_gap = 1.0 / (_CAST_ACTIVE_FPS if active else _CAST_IDLE_FPS)
            return max(0.0, min_gap - (now - self._last_frame_sent_at))

        async def _forward_frame(params: dict) -> None:
            if self._on_frame is None or cdp is not self._cdp:
                return
            self._last_frame_sent_at = time.monotonic()
            self._frame_seq += 1
            metadata = params.get("metadata") or {}
            # Chromium 的 screencast metadata 是当前设备视口；固定 DPR=1 时也正是
            # JPEG 的像素尺寸。字段同时保留扁平/分组形式，便于新旧前端平滑升级。
            frame_width = _positive_dimension(metadata.get("deviceWidth"), _CAST_W)
            frame_height = _positive_dimension(metadata.get("deviceHeight"), _CAST_H)
            await self._on_frame({
                "seq": self._frame_seq,
                "data": params["data"],
                "width": frame_width,
                "height": frame_height,
                "frame_width": frame_width,
                "frame_height": frame_height,
                "viewport_width": _VIEW_W,
                "viewport_height": _VIEW_H,
                "viewport": {"width": _VIEW_W, "height": _VIEW_H},
            })  # base64 jpeg

        async def _flush_latest_frame() -> None:
            nonlocal pending_frame, frame_flush_task
            try:
                while self._on_frame is not None and cdp is self._cdp:
                    delay = _frame_delay()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    params = pending_frame
                    pending_frame = None
                    if params is None:
                        return
                    # Chromium does not encode the next screencast JPEG until
                    # this frame is acknowledged. Apply the FPS gate before
                    # ACK so complex animated/navigation pages cannot spend
                    # CPU encoding frames that this recorder will discard.
                    await cdp.send(
                        "Page.screencastFrameAck",
                        {"sessionId": params["sessionId"]},
                    )
                    await _forward_frame(params)
                    if pending_frame is None:
                        return
            except Exception:  # noqa: BLE001 —— 截图失败不能影响输入通道
                pass
            finally:
                frame_flush_task = None
                # A frame can arrive while the previous callback is being sent.
                # Never strand that final visual update when the page becomes idle.
                if pending_frame is not None and self._on_frame is not None and cdp is self._cdp:
                    frame_flush_task = asyncio.create_task(_flush_latest_frame())

        async def _emit(params: dict) -> None:
            nonlocal pending_frame, frame_flush_task
            try:
                if self._on_frame is not None and cdp is self._cdp:   # 只发**活动页**的帧(切页后旧帧丢弃)
                    # Schedule the newest frame for the end of the FPS window.
                    # Its ACK is deliberately delayed with the forward so CDP
                    # applies backpressure before encoding another full JPEG.
                    pending_frame = params
                    if frame_flush_task is None or frame_flush_task.done():
                        frame_flush_task = asyncio.create_task(_flush_latest_frame())
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

    async def _input_page(self, *, exclude=None):  # noqa: ANN001
        """返回可操作页；活动页关闭/输入竞态时尽量切到最近仍存活的弹窗或标签页。"""
        page = self.page
        if exclude is None and _page_is_open(page):
            return page
        if self._closing or self._context is None:
            return None
        try:
            pages = list(self._context.pages)
        except Exception:  # noqa: BLE001 —— context 正在关闭
            return None
        candidates = [candidate for candidate in reversed(pages) if _page_is_open(candidate)]
        replacement = next((candidate for candidate in candidates if candidate is not exclude), None)
        if replacement is None:
            replacement = next((candidate for candidate in candidates if candidate is exclude), None)
        if replacement is None:
            return None
        changed = replacement is not self.page
        self.page = replacement
        self._page_id(replacement)
        self._attach_diag_handlers(replacement)
        self._mark_active()
        if changed and self._on_frame is not None:
            try:
                await self._restart_screencast()
            except Exception:  # noqa: BLE001 —— 恢复截屏失败不能反过来击穿输入通道
                pass
        return replacement

    # ── 输入回传(归一坐标 0~1 → 视口像素)──
    async def dispatch_input(self, ev: dict) -> dict:
        """把前端输入转发给活动页，任何单次 Playwright 异常都不得终止录制会话。

        返回值供直接调用方诊断；WebSocket
        当前无需消费它。失败后不自动重放输入，避免操作已部分生效时造成双击/重复提交。
        """
        kind = str(ev.get("kind") or "")
        page = await self._input_page()
        if page is None:
            return {"ok": False, "recoverable": True, "kind": kind, "error": "no_active_page"}
        # A portal may replace about:blank with its business document without a
        # navigation event that re-runs add_init_script. Restore the recorder
        # immediately before the user's next real interaction so that the input
        # and the request it triggers retain the same evidence chain.
        await self._ensure_recorder_installed(page)
        self._mark_active()
        try:
            x, y = _input_point(ev)
            button = _mouse_button(ev.get("button"))
            steps = _mouse_steps(ev.get("steps"))
            click_count = _mouse_click_count(ev.get("click_count"))
            if kind == "click":
                await page.mouse.click(x, y, button=button)
            elif kind == "dblclick":
                await page.mouse.dblclick(x, y, button=button)
            elif kind in {"right_click", "contextmenu"}:
                await page.mouse.click(x, y, button="right")
            elif kind in {"pointer_move", "hover"}:
                await page.mouse.move(x, y, steps=steps)
            elif kind == "pointer_down":
                await page.mouse.move(x, y)
                await page.mouse.down(button=button, click_count=click_count)
            elif kind == "pointer_up":
                await page.mouse.move(x, y)
                await page.mouse.up(button=button, click_count=click_count)
            elif kind == "drag":
                start_x, start_y = _input_point(ev, prefix="from_")
                await page.mouse.move(start_x, start_y)
                pressed = False
                try:
                    await page.mouse.down(button=button)
                    pressed = True
                    await page.mouse.move(x, y, steps=steps)
                finally:
                    if pressed:
                        try:
                            await page.mouse.up(button=button)
                        except Exception:  # noqa: BLE001 —— 外层统一报告原始操作错误
                            pass
            elif kind == "text":
                # insert_text 直接插入文本(含中文 CJK)并触发 input 事件;type 模拟物理键对 CJK 不可靠
                await page.keyboard.insert_text(str(ev.get("text") or ""))
            elif kind == "key":
                key = str(ev.get("key") or "")
                if _safe_recorder_key(key):
                    await page.keyboard.press(key)
            elif kind == "scroll":
                await page.mouse.wheel(_finite_number(ev.get("dx"), 0.0), _finite_number(ev.get("dy"), 0.0))
            else:
                return {"ok": False, "recoverable": False, "kind": kind, "error": "unsupported_input"}
            return {"ok": True, "kind": kind}
        except Exception as exc:  # noqa: BLE001 —— TargetClosed/navigation/popup 竞态必须停在事件边界内
            await self._input_page(exclude=page)
            log.debug("recorder_input_ignored", kind=kind, error_type=type(exc).__name__)
            return {
                "ok": False,
                "recoverable": True,
                "kind": kind,
                "error": "input_dispatch_failed",
                "error_type": type(exc).__name__,
            }

    async def agent_navigate(self, url: str) -> dict:
        """Navigate the retained recording browser during autonomous verification."""
        target = str(url or "").strip()
        if urlparse(target).scheme not in {"http", "https"}:
            raise ValueError("agent navigation requires an http(s) URL")
        page = await self._input_page()
        if page is None:
            raise RuntimeError("no active recording page")
        self._recording_paused = False
        self._mark_active()
        response = await page.goto(target, wait_until="domcontentloaded")
        return {
            "ok": True,
            "url": page.url.split("?", 1)[0],
            "status": response.status if response is not None else None,
        }

    async def agent_snapshot(self) -> dict:
        """Return a bounded semantic snapshot without exposing entered values."""
        page = await self._input_page()
        if page is None:
            raise RuntimeError("no active recording page")
        elements = await page.locator(
            "button, a, input, textarea, select, [role], [contenteditable='true']"
        ).evaluate_all(r"""elements => elements.slice(0, 300).flatMap((element, index) => {
          const style = window.getComputedStyle(element);
          if (style.display === 'none' || style.visibility === 'hidden') return [];
          const tag = element.tagName.toLowerCase();
          const implicit = tag === 'button' ? 'button'
            : tag === 'a' ? 'link'
            : tag === 'select' ? 'combobox'
            : tag === 'textarea' ? 'textbox'
            : tag === 'input' ? ((element.type === 'checkbox') ? 'checkbox'
              : (element.type === 'radio') ? 'radio' : 'textbox') : '';
          const resolvedRole = element.getAttribute('role') || implicit;
          const labelledBy = element.getAttribute('aria-labelledby');
          const labelledText = labelledBy
            ? labelledBy.split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ')
            : '';
          const label = element.labels?.[0]?.textContent || '';
          const name = element.getAttribute('aria-label') || labelledText || label
            || element.getAttribute('placeholder') || element.getAttribute('name') || '';
          const text = resolvedRole ? (element.textContent || '') : '';
          const options = tag === 'select'
            ? Array.from(element.options || []).slice(0, 500).map(option => ({
                label: (option.textContent || '').trim(), value: option.value,
              })) : undefined;
          return [{ index, role: resolvedRole,
            name: name.trim().slice(0, 200), text: text.trim().slice(0, 200), options }];
        })""")
        self._mark_active()
        return {
            "url": page.url.split("?", 1)[0],
            "title": (await page.title())[:300],
            "elements": elements,
        }

    async def agent_act(self, kind: str, locator: dict, value=None) -> dict:  # noqa: ANN001
        """Perform one semantic Playwright action while normal capture remains active."""
        action = str(kind or "")
        if action not in {"click", "fill", "select"}:
            raise ValueError(f"unsupported agent browser action: {action}")
        if not isinstance(locator, dict):
            raise TypeError("locator must be an object")
        role = str(locator.get("role") or "").strip()
        name = str(locator.get("name") or "").strip()
        text = str(locator.get("text") or "").strip()
        if not role and not text:
            raise ValueError("locator requires role or text")
        page = await self._input_page()
        if page is None:
            raise RuntimeError("no active recording page")
        target = (
            page.get_by_role(role, name=name or None, exact=bool(locator.get("exact")))
            if role else page.get_by_text(text, exact=bool(locator.get("exact")))
        ).first
        self._recording_paused = False
        self._mark_active()
        if action == "click":
            await target.click()
        elif action == "fill":
            await target.fill(str(value or ""))
        else:
            await target.select_option(value=value)
        await page.wait_for_timeout(100)
        return {"ok": True, "kind": action, "url": page.url.split("?", 1)[0]}

    async def flush_recording(self) -> None:
        """把页面端防抖中的 fill 立即推回 Python,用于 finalize/reset/stop 前收口。"""
        if self.page is None or self.page.is_closed():
            return
        try:
            await self.page.evaluate("window.__danoFlushRecorder && window.__danoFlushRecorder()")
            await self.page.wait_for_timeout(80)
        except Exception:  # noqa: BLE001
            pass

    def pause_recording(self) -> None:
        """Stop collecting facts while keeping the browser and transport alive."""
        self._recording_paused = True

    async def storage_state(self) -> dict | None:
        """抓当前会话登录态快照(cookie/localStorage + 各 origin sessionStorage)。

        用户在画面里真人登录后调用 → 回放/运行期复用这份登录态,免再登录(验证码/RSA 都已过)。
        """
        if self._context is None:
            return None
        try:
            state = await self._context.storage_state()
            by_origin: dict[str, dict[str, str]] = {}
            # sessionStorage is scoped to a top-level browsing context and is
            # deliberately omitted by Playwright storage_state. Inspect every
            # live page/frame; same-origin entries are merged deterministically.
            for page in list(self._context.pages):
                for frame in list(page.frames):
                    try:
                        snapshot = await frame.evaluate(
                            """() => {
                              const items = [];
                              for (let i = 0; i < sessionStorage.length; i += 1) {
                                const name = sessionStorage.key(i);
                                if (name !== null) items.push({name, value: sessionStorage.getItem(name) ?? ''});
                              }
                              return {origin: location.origin, items};
                            }"""
                        )
                    except Exception:  # noqa: BLE001 - detached/opaque/cross-origin frame
                        continue
                    origin = str((snapshot or {}).get("origin") or "")
                    if not origin or origin == "null":
                        continue
                    values = by_origin.setdefault(origin, {})
                    for entry in (snapshot or {}).get("items") or []:
                        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                            continue
                        value = entry.get("value")
                        values[entry["name"]] = value if isinstance(value, str) else str(value or "")
            if by_origin:
                state[SESSION_STORAGE_STATE_KEY] = {
                    origin: [{"name": name, "value": value} for name, value in sorted(values.items())]
                    for origin, values in sorted(by_origin.items())
                    if values
                }
                if not state[SESSION_STORAGE_STATE_KEY]:
                    state.pop(SESSION_STORAGE_STATE_KEY, None)
            return state
        except Exception:  # noqa: BLE001
            return None

    def recorded_steps(self):
        """已捕获步骤 → (普通 dict 步骤列表, sample_inputs)。"""
        keymap = assign_step_field_keys(self.steps, tenant=self._tenant)
        steps: list[dict] = []
        samples: dict[str, object] = {}
        for i, s in enumerate(self.steps):
            field = s.get("field") or None
            steps.append({"op": s["op"], "locator": s.get("locator"), "field": field})
            if field and s.get("op") in ("fill", "select", "pick", "toggle", "upload") and has_recorded_value(s):
                samples[keymap[i]] = s.get("value", "")
        return steps, samples

    def recorded_page_enum_options(self) -> dict:
        """录制时下拉/级联里真实可见的选项 + 当前选中值。

        返回 {字段key: {options, field_key, selected}}。保留 selected 是为了把 DOM 显示项
        与提交体短码(type=2)连起来,避免发布 skill 时把下拉退化成 number。
        """
        # enum_snapshot is emitted as soon as a dropdown opens. It is kept
        # outside executable steps so opening a control never pollutes the
        # generated workflow, but it must share the same identity/key
        # allocation as the later select/pick event.
        def evidence_order(item: dict) -> tuple[int, int]:
            event_id = str(item.get("event_id") or "")
            match = re.search(r"(\d+)$", event_id)
            return (
                int(item.get("observed_at") or 0),
                int(match.group(1)) if match else 0,
            )

        evidence = sorted(
            [*self.steps, *self.enum_snapshots, *self._semantic.enums.values()],
            key=evidence_order,
        )
        keymap = assign_step_field_keys(evidence, tenant=self._tenant)
        out: dict[str, dict] = {}
        last_field_idx_by_scope: dict[tuple[str, str, str, str], int] = {}
        for i, s in enumerate(evidence):
            scope_key = (
                str(s.get("page_id") or ""),
                str(s.get("frame_id") or ""),
                route_identity(s),
                str(s.get("action_id") or s.get("transaction_id") or ""),
            )
            if i in keymap:
                last_field_idx_by_scope[scope_key] = i
                last_field_idx_by_scope[(
                    str(s.get("page_id") or ""),
                    str(s.get("frame_id") or ""),
                    route_identity(s),
                    "",
                )] = i
            if s.get("op") in ("pick", "select", "enum_snapshot") and s.get("options"):
                # 自定义下拉常见事件序列是「点开输入框/选择器」→「点弹层选项」。
                # 后一个 pick 事件有 options/selected,但 DOM 目标已经是弹层项,拿不到字段 label。
                # 因此优先用本步字段,否则回溯最近一个可填写字段,把弹层选项归回正确业务字段。
                owner_idx = i if i in keymap else last_field_idx_by_scope.get(scope_key)
                if owner_idx not in keymap:
                    owner_idx = last_field_idx_by_scope.get((
                        str(s.get("page_id") or ""),
                        str(s.get("frame_id") or ""),
                        route_identity(s),
                        "",
                    ))
                if owner_idx not in keymap:
                    continue
                owner = evidence[owner_idx]
                field_key = keymap[owner_idx]
                scope_key = (
                    str(s.get("page_id") or owner.get("page_id") or ""),
                    str(s.get("frame_id") or owner.get("frame_id") or ""),
                    route_identity(s) or route_identity(owner),
                )
                storage_key = field_key
                existing = out.get(storage_key)
                existing_scope = (
                    str(existing.get("page_id") or ""),
                    str(existing.get("frame_id") or ""),
                    route_identity(existing),
                ) if existing else None
                if existing and existing_scope != scope_key:
                    storage_key = f"{field_key}@{scope_key[0]}:{scope_key[1]}:{scope_key[2]}"
                previous = out.get(storage_key, {})
                merged: dict[str, object] = {}
                mapping_conflict = bool(previous.get("mapping_conflict") or s.get("mapping_conflict"))
                for option in [*(previous.get("options") or []), *list(s["options"])]:
                    label = str(option.get("label") if isinstance(option, dict) else option).strip()
                    if label:
                        old = merged.get(label)
                        if (
                            isinstance(old, dict) and isinstance(option, dict)
                            and "value" in old and "value" in option
                            and old.get("value") != option.get("value")
                        ):
                            mapping_conflict = True
                        merged[label] = option
                selected_raw = s.get("value", "")
                selected_label = str(s.get("selected_label") or "").strip()
                selected_value = s.get("selected_value")
                if selected_label and selected_value not in (None, ""):
                    pass
                elif s.get("mapping_complete") and s.get("enum_source") == "dom":
                    selected_value = selected_raw
                    native_matches = [
                        option for option in merged.values()
                        if isinstance(option, dict)
                        and "value" in option
                        and str(option.get("value")) == str(selected_raw)
                    ]
                    if len(native_matches) == 1:
                        selected_label = str(native_matches[0].get("label") or "").strip()
                else:
                    selected_label = str(selected_raw or "").strip()
                aliases = list(dict.fromkeys([
                    *list(previous.get("field_aliases") or []),
                    *list(owner.get("field_aliases") or []),
                    *list(s.get("field_aliases") or []),
                ]))
                entry = {
                    "options": list(merged.values()),
                    "field_key": field_key,
                    "selected": selected_label or str(previous.get("selected_label") or previous.get("selected") or ""),
                    "selected_label": selected_label or str(previous.get("selected_label") or previous.get("selected") or ""),
                    "mapping_complete": bool(
                        s.get("mapping_complete")
                        and not s.get("snapshot_truncated")
                        and not mapping_conflict
                    ),
                    "page_id": scope_key[0],
                    "frame_id": scope_key[1],
                    "page_context": dict(
                        s.get("page_context") or owner.get("page_context")
                        or previous.get("page_context") or {}
                    ),
                }
                if selected_value not in (None, ""):
                    entry["selected_value"] = selected_value
                elif previous.get("selected_value") not in (None, ""):
                    entry["selected_value"] = previous.get("selected_value")
                if mapping_conflict:
                    entry["mapping_conflict"] = True
                for fact_key in (
                    "action_id", "transaction_id", "observed_at", "enum_source",
                    "source_url", "dict_type", "script_url",
                ):
                    fact_value = s.get(fact_key)
                    if fact_value in (None, ""):
                        fact_value = owner.get(fact_key)
                    if fact_value in (None, ""):
                        fact_value = previous.get(fact_key)
                    if fact_value not in (None, ""):
                        entry[fact_key] = fact_value
                if (
                    s.get("control_kind") or owner.get("control_kind")
                    or aliases or previous.get("control_kind")
                ):
                    entry.update({
                        "page_id": scope_key[0],
                        "frame_id": scope_key[1],
                        "page_context": dict(
                            s.get("page_context") or owner.get("page_context")
                            or previous.get("page_context") or {}
                        ),
                        "control_kind": str(
                            s.get("control_kind") or owner.get("control_kind")
                            or previous.get("control_kind") or "select"
                        ),
                    })
                if aliases:
                    entry["field_aliases"] = aliases
                out[storage_key] = entry
        self._supplement_page_enums_from_dictionaries(out)
        self._supplement_page_enums_from_scripts(out)
        return out

    @staticmethod
    def _script_literal_value(raw: str):  # noqa: ANN205
        value = str(raw or "").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1].replace("\\'", "'").replace('\\"', '"')
        if value == "true":
            return True
        if value == "false":
            return False
        try:
            return float(value) if "." in value else int(value)
        except (TypeError, ValueError):
            return value

    @classmethod
    def _static_enum_arrays(cls, source: str) -> list[dict]:
        arrays: list[dict] = []
        object_re = re.compile(r"\{(?P<body>[^{}]{1,600})\}", re.S)
        item_re = re.compile(
            r"(?:['\"](?P<qkey>[^'\"]+)['\"]|(?P<key>[A-Za-z_$][\w$]*))\s*:\s*"
            r"(?P<value>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"|-?\d+(?:\.\d+)?|true|false)",
            re.S,
        )
        key_re = re.compile(
            r"(?:['\"](?P<qkey>[^'\"]+)['\"]|(?P<key>[A-Za-z_$][\w$]*))\s*:",
            re.S,
        )
        label_keys = ("label", "text", "name", "title")
        value_keys = ("value", "id", "code", "key")
        text = source or ""
        identifier_chars = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$"
        )

        # Do not use ``.*?`` to search a whole minified bundle.  On a multi-MB
        # application chunk a missing/remote closing bracket makes that regex
        # backtrack over the same bytes many times.  Find assignment brackets
        # linearly, then scan only the bounded candidate while respecting JS
        # string literals.  This keeps the fallback usable for large bundles
        # without executing their code.
        candidates: list[tuple[str, str]] = []
        cursor = 0
        while True:
            bracket = text.find("[", cursor)
            if bracket < 0:
                break
            cursor = bracket + 1
            before = bracket - 1
            while before >= 0 and text[before].isspace():
                before -= 1
            if before < 0 or text[before] not in ":=":
                continue
            before -= 1
            while before >= 0 and text[before].isspace():
                before -= 1
            name_end = before + 1
            while before >= 0 and text[before] in identifier_chars:
                before -= 1
            name = text[before + 1:name_end]
            if not (3 <= len(name) <= 80) or name[0] not in (
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_$"
            ):
                continue

            depth = 1
            quote = ""
            escaped = False
            body_end = -1
            limit = min(len(text), bracket + 1 + 30_000)
            for index in range(bracket + 1, limit):
                char = text[index]
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = ""
                    continue
                if char in {"'", '"', "`"}:
                    quote = char
                elif char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
                    if depth == 0:
                        body_end = index
                        break
            if body_end > bracket + 1:
                candidates.append((name, text[bracket + 1:body_end]))

        def split_top_level(body: str) -> list[str]:
            elements: list[str] = []
            start = 0
            brace = bracket = paren = 0
            quote = ""
            escaped = False
            for index, char in enumerate(body):
                if quote:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        quote = ""
                    continue
                if char in {"'", '"', "`"}:
                    quote = char
                elif char == "{":
                    brace += 1
                elif char == "}":
                    brace -= 1
                elif char == "[":
                    bracket += 1
                elif char == "]":
                    bracket -= 1
                elif char == "(":
                    paren += 1
                elif char == ")":
                    paren -= 1
                elif char == "," and brace == bracket == paren == 0:
                    if body[start:index].strip():
                        elements.append(body[start:index].strip())
                    start = index + 1
            if body[start:].strip():
                elements.append(body[start:].strip())
            return elements

        for name, body in candidates:
            options: list[dict] = []
            elements = split_top_level(body)
            mapping_complete = bool(elements) and len(elements) <= 200
            for element in elements:
                obj = object_re.fullmatch(element)
                if obj is None:
                    mapping_complete = False
                    continue
                values = {
                    str(item.group("qkey") or item.group("key") or "").lower(): cls._script_literal_value(item.group("value"))
                    for item in item_re.finditer(obj.group("body"))
                }
                present_keys = {
                    str(item.group("qkey") or item.group("key") or "").lower()
                    for item in key_re.finditer(obj.group("body"))
                }
                present_label_keys = [key for key in label_keys if key in present_keys]
                present_value_keys = [key for key in value_keys if key in present_keys]
                if any(key not in values for key in [*present_label_keys, *present_value_keys]):
                    mapping_complete = False
                label_key = present_label_keys[0] if present_label_keys else ""
                value_key = present_value_keys[0] if present_value_keys else ""
                if label_key and value_key:
                    if values.get(label_key) not in (None, "") and value_key in values:
                        options.append({"label": str(values[label_key]), "value": values[value_key]})
                    else:
                        mapping_complete = False
                else:
                    mapping_complete = False
            unique = {(item["label"], repr(item["value"])) for item in options}
            unique_labels = {item["label"] for item in options}
            mapping_complete = bool(
                mapping_complete
                and len(options) == len(elements)
                and len(unique) == len(options)
                and len(unique_labels) == len(options)
            )
            if len(unique) >= 2:
                arrays.append({
                    "name": name,
                    "options": options[:200],
                    "mapping_complete": mapping_complete,
                    "truncated": len(options) > 200,
                })
        return arrays

    @staticmethod
    def _script_dictionary_constants(source: str) -> dict[str, str]:
        """Extract symbolic dictionary constants without executing application JS."""
        # Matching may start immediately after ``.`` in ``e.SYMBOL=...``; the
        # optional ``identifier.`` prefix used here previously was unnecessary
        # and catastrophically expensive on minified multi-MB bundles.
        constant_re = re.compile(
            r"(?<![A-Za-z0-9_$])(?P<name>[A-Z][A-Z0-9_]{2,})\s*=\s*"
            r"(?P<quote>['\"])(?P<value>[a-z][a-z0-9_.:-]{2,})(?P=quote)"
        )
        return {
            match.group("name"): match.group("value")
            for match in constant_re.finditer(source or "")
        }

    @classmethod
    def _script_dictionary_associations(
        cls,
        source: str,
        constants: dict[str, str],
    ) -> dict[str, set[str]]:
        """Return exact form-field -> dictType links found in one compiled chunk.

        A field section is bounded by the next form identity marker. This avoids
        assigning every dictionary referenced by a minified page to every field.
        """
        if not constants:
            return {}
        marker_re = re.compile(
            r"(?:prop|name|field|fieldName)\s*:\s*['\"](?P<field>[A-Za-z_$][\w$.-]{1,100})['\"]"
        )
        markers = list(marker_re.finditer(source or ""))
        symbol_re = re.compile(
            r"(?<![A-Za-z0-9_$])(?P<name>[A-Z][A-Z0-9_]{2,})(?![A-Za-z0-9_$])"
        )
        result: dict[str, set[str]] = {}
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else min(len(source), marker.end() + 2000)
            segment = source[marker.end():min(end, marker.end() + 2000)]
            # Tokenize the bounded segment once.  The former nested loop ran a
            # separately compiled regex for every known dictionary constant
            # (hundreds of searches per field marker).
            names: set[str] = set()
            for match in symbol_re.finditer(segment):
                name = match.group("name")
                if name not in constants:
                    continue
                prefix = segment[max(0, match.start() - 160):match.start()]
                # Match the symbol at its actual call position.  Looking at a
                # suffix after the symbol made the end anchor impossible for
                # real forms such as ``getDict(DictType.STATUS)`` because the
                # context ended in ``))`` rather than ``DictType.``.
                direct_dict_type = re.search(r"(?:[A-Za-z_$][\w$]*\.)?DictType\s*\.\s*$", prefix, re.I)
                direct_dict_call = re.search(
                    r"(?:get|use)?dict(?:ionary|type)?\s*\(\s*(?:[A-Za-z_$][\w$]*\.)?\s*$",
                    prefix,
                    re.I,
                )
                if direct_dict_type or direct_dict_call:
                    names.add(name)
            types = {constants[name] for name in names}
            if len(types) == 1:
                result.setdefault(marker.group("field"), set()).update(types)
        return result

    @staticmethod
    def _decode_script_label(raw: str) -> str:
        """Decode a quoted JS label without evaluating the application bundle."""
        literal = str(raw or "")
        if len(literal) < 2 or literal[0] not in {"'", '"'} or literal[-1] != literal[0]:
            return ""
        body = literal[1:-1]
        body = re.sub(
            r"\\u([0-9a-fA-F]{4})",
            lambda match: chr(int(match.group(1), 16)),
            body,
        )
        body = re.sub(
            r"\\x([0-9a-fA-F]{2})",
            lambda match: chr(int(match.group(1), 16)),
            body,
        )
        return (
            body.replace(r"\'", "'")
            .replace(r'\"', '"')
            .replace(r"\\/", "/")
            .replace(r"\\\\", "\\")
            .strip()
        )

    @classmethod
    def _script_form_label_fields(cls, source: str) -> dict[str, set[str]]:
        """Return exact visible-label -> request-field links from compiled forms.

        Component libraries often keep ``prop`` only on a virtual form-item,
        so the rendered input exposes the Chinese label but no ``name`` or
        ``data-prop`` attribute.  Compiled Vue/React form declarations still
        place ``label`` and ``prop`` in the same item.  Bind only that local,
        explicit pair; order-based or fuzzy cross-field matching is forbidden.
        """
        marker_re = re.compile(
            r"(?:prop|name|field|fieldName)\s*:\s*['\"](?P<field>[A-Za-z_$][\w$.-]{1,100})['\"]"
        )
        label_re = re.compile(
            r"(?:^|[,{}])\s*label\s*:\s*"
            r"(?P<label>'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
        )
        markers = list(marker_re.finditer(source or ""))
        result: dict[str, set[str]] = {}
        for index, marker in enumerate(markers):
            previous_end = markers[index - 1].end() if index else max(0, marker.start() - 800)
            prefix = source[max(previous_end, marker.start() - 800):marker.start()]
            labels = list(label_re.finditer(prefix))
            if not labels:
                continue
            # The last label before prop belongs to this form-item.  Keep a
            # tight distance bound so unrelated component labels cannot leak.
            label_match = labels[-1]
            if len(prefix) - label_match.end() > 240:
                continue
            label = cls._decode_script_label(label_match.group("label"))
            if label:
                result.setdefault(label, set()).add(marker.group("field"))
        return result

    @staticmethod
    def _dictionary_options_from_reads(reads: list[dict], dict_type: str) -> tuple[list[dict], str, bool]:
        from dano.execution.page.request_capture import as_list_payload

        candidates: dict[str, tuple[list[dict], bool]] = {}
        for read in reads:
            source_url = str(read.get("url") or "").strip()
            if not source_url:
                continue
            items = as_list_payload(read.get("json"))
            if not items:
                continue
            by_label: dict[str, dict] = {}
            matched_here = False
            complete = True
            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized = {
                    re.sub(r"[^a-z0-9]+", "", str(key).casefold()): value
                    for key, value in item.items()
                }
                if str(normalized.get("dicttype") or "") != dict_type:
                    continue
                matched_here = True
                label = normalized.get("label")
                if label in (None, "") or "value" not in normalized or normalized.get("value") is None:
                    complete = False
                    continue
                option = {"label": str(label), "value": normalized["value"]}
                previous = by_label.get(option["label"])
                if previous is not None and previous.get("value") != option["value"]:
                    complete = False
                by_label[option["label"]] = option
            options = list(by_label.values())
            if matched_here:
                complete = bool(complete and 2 <= len(options) <= 200)
                # Repeated captures of the same endpoint use the latest
                # complete snapshot. A different endpoint is ambiguous.
                candidates[source_url] = (options[:200], complete)
        if len(candidates) != 1:
            return [], "", False
        source_url, (options, complete) = next(iter(candidates.items()))
        return options, source_url, complete

    def _supplement_page_enums_from_dictionaries(self, page_options: dict) -> None:
        from dano.execution.page.request_capture import as_list_payload

        if not page_options:
            return

        constant_values: dict[str, set[str]] = {}
        for script in self.script_sources:
            for name, value in self._script_dictionary_constants(str(script.get("text") or "")).items():
                constant_values.setdefault(name, set()).add(value)
        constants = {
            name: next(iter(values))
            for name, values in constant_values.items()
            if len(values) == 1
        }
        associations: dict[str, set[str]] = {}
        label_fields: dict[str, set[str]] = {}
        for script in self.script_sources:
            source = str(script.get("text") or "")
            for field, dict_types in self._script_dictionary_associations(
                source, constants,
            ).items():
                associations.setdefault(field, set()).update(dict_types)
            for label, fields in self._script_form_label_fields(source).items():
                label_fields.setdefault(label, set()).update(fields)

        def normalized(value: object) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

        reads = [*self.dictionary_reads, *self.reads]
        def normalized_label(value: object) -> str:
            return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "")).casefold()

        labels_by_norm: dict[str, set[str]] = {}
        for label, fields in label_fields.items():
            key = normalized_label(label)
            if key:
                labels_by_norm.setdefault(key, set()).update(fields)

        for storage_key, entry in page_options.items():
            explicit_fields = {
                field
                for label in (storage_key, entry.get("field_key"))
                for field in labels_by_norm.get(normalized_label(label), set())
            }
            # A single exact compiled label/prop pair repairs Element Plus
            # controls whose rendered input has no structural DOM alias.
            if len(explicit_fields) == 1:
                field = next(iter(explicit_fields))
                field_aliases = list(entry.get("field_aliases") or [])
                if field not in field_aliases:
                    entry["field_aliases"] = [*field_aliases, field]
            aliases = {
                normalized(str(alias).split(":", 1)[-1])
                for alias in [entry.get("field_key"), *(entry.get("field_aliases") or [])]
                if len(normalized(str(alias).split(":", 1)[-1])) >= 3
            }
            matched_types = {
                dict_type
                for field, dict_types in associations.items()
                if normalized(field) in aliases
                for dict_type in dict_types
            }
            if not matched_types:
                visible_labels = {
                    str((item.get("label") if isinstance(item, dict) else item) or "").strip()
                    for item in (entry.get("options") or [])
                } - {""}
                inferred_types: set[str] = set()
                if len(visible_labels) >= 2:
                    for read in reads:
                        grouped: dict[str, set[str]] = {}
                        for item in (as_list_payload(read.get("json")) or []):
                            if not isinstance(item, dict):
                                continue
                            values = {
                                re.sub(r"[^a-z0-9]+", "", str(key).casefold()): value
                                for key, value in item.items()
                            }
                            dict_type = str(values.get("dicttype") or "")
                            label = str(values.get("label") or "").strip()
                            if dict_type and label and values.get("value") is not None:
                                grouped.setdefault(dict_type, set()).add(label)
                        inferred_types.update(
                            dict_type for dict_type, labels in grouped.items()
                            if labels == visible_labels
                        )
                if len(inferred_types) == 1:
                    matched_types = inferred_types
            if len(matched_types) != 1:
                continue
            dict_type = next(iter(matched_types))
            mapped, source_url, mapping_complete = self._dictionary_options_from_reads(reads, dict_type)
            if len(mapped) < 2 or not source_url or not mapping_complete:
                continue
            entry["options"] = mapped
            entry["enum_source"] = "script_dictionary"
            entry["dict_type"] = dict_type
            entry["source_url"] = source_url
            entry["mapping_complete"] = True

    def _supplement_page_enums_from_scripts(self, page_options: dict) -> None:
        if not page_options or not self.script_sources:
            return

        def normalized(value: object) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

        def owner_name(value: object) -> str:
            name = normalized(value)
            # Deterministic generated-code convention, not substring/fuzzy
            # matching: only a complete, recognized collection suffix may be
            # removed before comparing with the exact form property.
            for suffix in ("selectoptions", "enumoptions", "optionlist", "options"):
                if name.endswith(suffix) and len(name) > len(suffix):
                    return name[:-len(suffix)]
            return name

        arrays: list[dict] = []
        for script in self.script_sources:
            for candidate in self._static_enum_arrays(str(script.get("text") or "")):
                candidate["url"] = str(script.get("url") or "")
                options = list(candidate.get("options") or [])
                labels = [str(option.get("label") or "").strip() for option in options]
                if (
                    candidate["url"]
                    and candidate.get("mapping_complete") is True
                    and candidate.get("truncated") is not True
                    and len(options) >= 2
                    and all(
                        isinstance(option, dict)
                        and option.get("label") not in (None, "")
                        and "value" in option
                        and option.get("value") is not None
                        for option in options
                    )
                    and len(set(labels)) == len(labels)
                ):
                    arrays.append(candidate)
        for entry in page_options.values():
            options = list(entry.get("options") or [])
            if (
                entry.get("mapping_complete") is True
                and options
                and all(isinstance(item, dict) and "value" in item for item in options)
            ):
                continue
            aliases = {
                normalized(str(alias).split(":", 1)[-1])
                for alias in [entry.get("field_key"), *(entry.get("field_aliases") or [])]
                if len(normalized(str(alias).split(":", 1)[-1])) >= 3
            }
            related = [
                candidate for candidate in arrays
                if any(
                    normalized(candidate["name"]).startswith(alias)
                    for alias in aliases
                )
            ]
            matches = [
                candidate for candidate in related
                if owner_name(candidate["name"]) in aliases
                and {
                    str(option.get("label") if isinstance(option, dict) else option).strip()
                    for option in options
                    if str(option.get("label") if isinstance(option, dict) else option).strip()
                }.issubset({str(option.get("label") or "").strip() for option in candidate["options"]})
            ]
            # Ambiguous bundles are not evidence. Exact one-field/one-array
            # association is required before static JS can fill wire values.
            # A second field-prefixed array (for example ``statusBackup``)
            # makes the naming convention ambiguous even if only one array
            # happens to end in ``Options``.
            if len(related) != 1 or len(matches) != 1:
                continue
            entry["options"] = list(matches[0]["options"])
            entry["enum_source"] = "script_static"
            entry["script_url"] = matches[0]["url"]
            entry["mapping_complete"] = True

    def _supplement_field_aliases_from_scripts(self, evidence: list[dict]) -> list[dict]:
        """Recover a missing DOM alias from one explicit compiled label/prop pair."""
        if not evidence or not self.script_sources:
            return evidence
        fields_by_label: dict[str, set[str]] = {}
        for script in self.script_sources:
            for label, fields in self._script_form_label_fields(
                str(script.get("text") or ""),
            ).items():
                fields_by_label.setdefault(label, set()).update(fields)
        out: list[dict] = []
        for raw in evidence:
            item = dict(raw)
            aliases = [
                str(alias).strip()
                for alias in (item.get("field_aliases") or [])
                if str(alias or "").strip()
            ]
            label = str(item.get("label") or item.get("field") or "").strip()
            fields = fields_by_label.get(label, set())
            if not aliases and len(fields) == 1:
                item["field_aliases"] = [next(iter(fields))]
                item["identity_sources"] = list(dict.fromkeys([
                    *list(item.get("identity_sources") or []),
                    "script_form_declaration",
                ]))
                for key in ("field_identity_id", "occurrence_id", "evidence_id"):
                    item.pop(key, None)
                stamp_field_identity(item)
            out.append(item)
        return out

    def recorded_field_evidence(self) -> list[dict]:
        """Return control-identity evidence scoped to the page that emitted it.

        Field names and types are grounded by ``name``/``data-prop``/control kind.
        Values remain samples only. Cross-transaction facts are never merged.
        """
        for snapshot in self.form_snapshots:
            self._semantic.archive_form_snapshot(snapshot)
        for step in self.steps:
            if step.get("op") in {"fill", "select", "pick", "toggle", "upload"}:
                self._semantic.archive_control(control_item_from_step(step))
        return self._supplement_field_aliases_from_scripts(
            self._semantic.field_evidence(),
        )

    def recorded_required_labels(self) -> set:
        """录制中标了表单 * 必填的字段(供 flatten 标 required)。key 与 recorded_steps 同算法分配,保持一致。"""
        keymap = assign_step_field_keys(self.steps, tenant=self._tenant)
        out = {
            key for i, key in keymap.items()
            if self.steps[i].get("required")
            and self.steps[i].get("op") in ("fill", "select", "pick")
        }
        for snapshot in self.form_snapshots or []:
            out.update(
                str(label or "").strip()
                for label in (snapshot.get("required_fields") or [])
                if str(label or "").strip()
            )
        return out

    def recorded_form_samples(self) -> dict[str, object]:
        """Return submit-time label/value evidence from one complete snapshot."""
        if self._semantic.tx_samples:
            latest = next(reversed(list(self._semantic.tx_samples.values())))
            if latest:
                return dict(latest)
        snapshots = list(self.form_snapshots or [])
        if not snapshots:
            return {}
        fields = [item for item in (snapshots[-1].get("fields") or []) if isinstance(item, dict)]
        return samples_from_fields(fields)

    def recorded_form_samples_by_transaction(self) -> dict[str, dict]:
        samples = {
            key: dict(value)
            for key, value in self._semantic.tx_samples.items()
            if value
        }
        for snapshot in self.form_snapshots or []:
            tx = str(snapshot.get("transaction_id") or snapshot.get("action_id") or "")
            if not tx:
                continue
            fields = [item for item in (snapshot.get("fields") or []) if isinstance(item, dict)]
            extracted = samples_from_fields(fields)
            if extracted:
                samples[tx] = extracted
                action_id = str(snapshot.get("action_id") or "")
                if action_id:
                    samples[action_id] = extracted
        return samples

    def recorded_form_samples_by_request(self) -> dict[str, dict]:
        samples = {
            key: dict(value)
            for key, value in self._semantic.req_samples.items()
            if value
        }
        for snapshot in self.form_snapshots or []:
            request_id = str(snapshot.get("request_id") or "")
            if not request_id:
                continue
            fields = [item for item in (snapshot.get("fields") or []) if isinstance(item, dict)]
            extracted = samples_from_fields(fields)
            if extracted:
                samples[request_id] = extracted
        return samples

    async def observed_required_labels(self) -> set[str]:
        """Scan the live page for required controls, including untouched fields."""
        out: set[str] = set()
        if self.page is None:
            return out
        # The active page owns the submit form. Scanning every still-open tab
        # would let an unrelated required label from an earlier page leak into
        # the current request's field contract. Recorded interactions already
        # preserve required evidence for earlier pages.
        for frame in list(self.page.frames):
            try:
                labels = await frame.evaluate(
                    "() => window.__danoRequiredFields ? window.__danoRequiredFields() : []"
                )
            except Exception:  # noqa: BLE001
                continue
            for label in labels or []:
                text = str(label or "").strip()
                if text:
                    out.add(text)
        return out

    async def observed_page_context(self) -> dict:
        """Return stable business page evidence captured before and after submit."""
        submitted_contexts = [
            snapshot.get("page_context")
            for snapshot in self.form_snapshots
            if isinstance(snapshot.get("page_context"), dict)
        ]
        live_contexts: list[dict] = []
        if self.page is not None:
            for frame in list(self.page.frames):
                try:
                    context = await frame.evaluate(
                        "() => window.__danoPageContext ? window.__danoPageContext() : {}"
                    )
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(context, dict):
                    live_contexts.append(context)
        contexts = [*submitted_contexts, *live_contexts]
        titles: list[str] = []
        seen: set[str] = set()
        for context in contexts:
            for value in [context.get("document_title"), *(context.get("visible_titles") or [])]:
                text = str(value or "").strip()
                if text and text not in seen:
                    seen.add(text)
                    titles.append(text)
        latest = live_contexts[-1] if live_contexts else {}
        submitted = submitted_contexts[-1] if submitted_contexts else latest
        return {
            "url": str(submitted.get("url") or latest.get("url") or ""),
            "path": str(submitted.get("path") or latest.get("path") or ""),
            "document_title": str(submitted.get("document_title") or latest.get("document_title") or ""),
            "visible_titles": titles[:30],
        }

    async def stop(self) -> None:
        self._closing = True         # 先置位:此后任何 page close 事件都不再重开截屏(避免在关闭中的 context 上 new_cdp_session)
        for obj, meth in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:  # noqa: BLE001
                    pass
        self._context = self._browser = self._pw = self.page = None
