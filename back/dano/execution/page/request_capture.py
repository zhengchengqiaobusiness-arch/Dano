"""方式B 升级:抓"提交请求" → 参数化成可调用的内部接口(无 DOM 回放,框架无关)。

无 API 页面其实是 SPA:点提交时网页向它自己后端发了个写请求(带表单值的 JSON)。把那个请求抓下来,
请求体里**等于用户填的值**的字段 → 变成参数;内部 ID/token 等保持常量。回放就是直接发这个请求。
不依赖控件长相,比录 DOM 点击稳得多。

本模块是纯函数(不碰浏览器),便于离线测试。
"""

from __future__ import annotations

import datetime as _dt
import json
import re as _re
from urllib.parse import urlparse

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# 「读请求」噪声:只排静态资源/流/心跳(通用,无任何业务路径名);保留字典/列表接口(select 候选源)。
_READ_NOISE = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff", ".ico",
               "/sse", "/socket", "/ws", "/heartbeat")
# 鉴权/基建写请求识别(P0#3:用「URL 路径段 + 请求体内容」判,**绝不写死任何系统的业务路径**)。
# 这类录制时放行真发、也绝不当成"提交候选"。提交请求改由"带最多用户填入值"识别(因果/值驱动,见 pick_submit_request)。
# ① URL 路径**整段**命中通用鉴权/上传/流概念(跨框架通用,非某系统专属;整段匹配避免 'lesson' 含 'sso' 之类误伤):
_INFRA_PATH_SEGS = frozenset({"login", "logout", "signin", "sign-in", "sso", "oauth", "oauth2",
                              "token", "refresh", "captcha", "upload", "sse", "socket", "ws"})
# ② 或请求体里带密码/验证码/凭证/OAuth 字段(按内容判,最稳:登录体必带这些,跨系统通用):
_AUTH_BODY_HINTS = ("password", "passwd", "captcha", "verifycode", "vcode", "credential",
                    "refreshtoken", "grant_type", "client_secret", "clientsecret")


# 浏览器通用头(回放交给 httpx / storageState 处理,不照搬);其余应用自定义头(鉴权/租户)要带上
_DROP_HEADERS = {
    "host", "connection", "content-length", "accept", "accept-encoding", "accept-language",
    "user-agent", "referer", "origin", "cookie", "content-type", "cache-control", "pragma",
    "dnt", "upgrade-insecure-requests", "priority", "te", "if-none-match", "if-modified-since",
}


def extract_auth_headers(headers: dict | None) -> dict:
    """从录到的请求头里留下「应用自定义头」(Authorization / Admin-Token / satoken / clientid / 租户号…),
    丢掉浏览器通用头。回放时原样带上 → 不管系统用哪个 header / token key 鉴权都通用,不写死。"""
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        kl = (k or "").lower()
        if not v or kl in _DROP_HEADERS or kl.startswith("sec-"):
            continue
        out[k] = v
    return out


def _parse_body(post_data: str | None):
    if not post_data:
        return None
    try:
        return json.loads(post_data)
    except Exception:  # noqa: BLE001 —— 非 JSON(form-urlencoded 等)暂不处理
        return None


def _values(node) -> list[str]:
    """递归取 body 里所有标量值(字符串化),用于和用户样例匹配。"""
    out: list[str] = []
    if isinstance(node, dict):
        for v in node.values():
            out += _values(v)
    elif isinstance(node, list):
        for v in node:
            out += _values(v)
    elif node is not None and not isinstance(node, bool):
        out.append(str(node))
    return out


def _all_keys(node) -> list[str]:
    """递归取 body 里所有 key(小写),用于按内容识别登录/鉴权请求。"""
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k).lower())
            out += _all_keys(v)
    elif isinstance(node, list):
        for v in node:
            out += _all_keys(v)
    return out


def looks_like_auth_write(url: str, body=None) -> bool:
    """这条写请求是否登录/鉴权/基建(而非业务提交)。**通用判定,不依赖任何系统的业务路径名**:
    ① URL 路径整段命中通用鉴权/上传/流概念(login/sso/oauth/token/captcha/upload…);
    ② 或请求体带密码/验证码/凭证/OAuth 字段。命中则:录制时放行真发、且不作为"提交候选"。

    body 可传已解析 dict 或原始 post_data 字符串(自动解析)。
    """
    if isinstance(body, str):
        body = _parse_body(body)
    segs = {s for s in urlparse(url or "").path.lower().split("/") if s}
    if segs & _INFRA_PATH_SEGS:
        return True
    return any(any(h in k for h in _AUTH_BODY_HINTS) for k in _all_keys(body))


def json_write_requests(requests: list[dict]) -> list[dict]:
    """抓到的请求里所有「带 JSON body 的写请求」(候选提交请求),保序。供前端列出来手选用哪个。"""
    out: list[dict] = []
    for r in requests:
        if (r.get("method") or "").upper() in _WRITE and _parse_body(r.get("post_data")) is not None:
            out.append(r)
    return out


# 读响应里"候选列表"的常见包装键(若依/通用):rows/records/list/data/content/items/result
_LIST_KEYS = ("rows", "records", "list", "data", "content", "items", "result", "results")


def as_list_payload(data):
    """从读响应里取出"候选列表"(下拉/选人源):data 本身是非空数组,或包装键里的非空数组。无则 None。

    通用:① 常见包装键(rows/data/records…)挖一到两层;② **兜底:任意值是非空对象数组**(覆盖未知包装键
    如 options/payload/choices,不挑系统)。供 Q2「选领导/代码下拉」等 select 解析。
    """
    if isinstance(data, list):
        return data if data and isinstance(data[0], (dict, str, int, float)) else None
    if isinstance(data, dict):
        for k in _LIST_KEYS:
            v = data.get(k)
            if isinstance(v, list) and v:
                return v
            if isinstance(v, dict):                     # 再下一层(如 data.records / data.rows)
                for k2 in _LIST_KEYS:
                    v2 = v.get(k2)
                    if isinstance(v2, list) and v2:
                        return v2
        for v in data.values():                         # 兜底:任意"非空对象数组"键(覆盖未知包装键)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return None


def _leaf_paths(body) -> list[tuple]:
    """body 拍平成 [(点路径, 值字符串, 原始值)]。"""
    out: list[tuple] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            out.append((path, "" if node is None else str(node), node))

    walk(body, "")
    return out


def _find_value_path(node, value, prefix: str = "") -> str | None:
    """在 node 里找一个叶子值 == value 的点路径(深度优先,返回第一个)。无则 None。"""
    if isinstance(node, dict):
        for k, v in node.items():
            r = _find_value_path(v, value, f"{prefix}.{k}" if prefix else k)
            if r is not None:
                return r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = _find_value_path(v, value, f"{prefix}[{i}]")
            if r is not None:
                return r
    elif node is not None and not isinstance(node, bool) and str(node) == str(value):
        return prefix or None
    return None


def discover_step_links(writes: list[dict]) -> list[dict]:
    """有序写请求(含 response_json)→ 步间数据流(Q3):某步 body 的值 == 更早某步「响应」里的值 → step: 链。

    返回 [{target_step, target_path, source_step, source_path}]。如第2步 flowTask.taskId 来自第1步响应 data.taskId。
    只认 ≥4 长的值,避免 0/1/短码误连。通用,不挑系统。
    """
    bodies = [_parse_body(w.get("post_data")) for w in writes]
    links: list[dict] = []
    for i, body in enumerate(bodies):
        if body is None:
            continue
        for tpath, tval, _raw in _leaf_paths(body):
            if len(tval) < 4:
                continue
            for j in range(i):
                resp = writes[j].get("response_json")
                if resp is None:
                    continue
                sp = _find_value_path(resp, tval)
                if sp is not None:
                    links.append({"target_step": i, "target_path": tpath,
                                  "source_step": j, "source_path": sp})
                    break
    return links


# 显示名(给人看的)字段提示;**登录名(username/account)排最后** —— 选人下拉里用户认的是"张三"而非"zhangsan",
# 用对显示名字段,name→ID 桥接与运行期解析才对得上。通用,不挑系统。
_DISPLAY_HINTS = ("nickname", "realname", "fullname", "truename", "cnname", "displayname",
                  "name", "label", "title", "caption", "text", "dept")
_LOGIN_HINTS = ("username", "loginname", "account", "loginid", "useraccount")


def _pick_label_key(item: dict, value_key: str) -> str:
    """从列表项里挑"显示名"字段当 label:优先真正给人看的名字(nickname/realname/name/label…),
    **登录名(username/account)排最后**(选人下拉用户看的是显示名);同档取最长文字。无文字字段 → 用 value_key。"""
    text = [k for k in item if k != value_key and isinstance(item[k], str) and item[k].strip()]
    if not text:
        return value_key

    def rank(k: str) -> int:
        kl = k.lower()
        if any(h in kl for h in _LOGIN_HINTS) and "nick" not in kl:
            return 2                                     # 登录名最后(username/account)
        if any(h in kl for h in _DISPLAY_HINTS):
            return 0                                     # 显示名优先
        return 1                                         # 其它文字字段居中

    return min(text, key=lambda k: (rank(k), -len(item[k])))


_IDLIKE = _re.compile(r"(id|code|key|value|no|num|guid|uuid|oid|sn)$", _re.I)


def _is_idlike(key: str) -> bool:
    """命中的列表字段是不是"ID 类"(select 引用的是项的 ID,不是某段文本)。"""
    return bool(key) and bool(_IDLIKE.search(key))


_SMALL_LIST = 50    # "字典型下拉"是小列表(事假/病假…);城市/数据大字典是大列表 → 区分短码真假命中


def suggest_selects(post_data: str | None, reads: list[dict], samples: dict | None = None) -> list[dict]:
    """提交体里"等于某候选列表项 ID 的值"的字段 → 绑 select(Agent 传名字/文字→运行期查内部 ID)。

    覆盖:选领导(approverId↔user/list)+ 代码型下拉(type=2↔字典 value=2,agent 传"事假")。
    **录制样例(samples)= 消歧器**:候选项的显示名正是用户录制时选中的值(label∈samples 值)→「确认命中」,
    是强证据 → **即便在上千项的全局大字典里、即便短码也照绑**,并据此精确选中正确那项(避免大字典里 value=2 撞多组)。
    无录制佐证时维持原精度闸门:短码(len<2)只在小列表认、同源未确认命中 >3 个按通用字典整源丢弃。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    leaves = _leaf_paths(body)
    sample_vals = {str(v) for v in (samples or {}).values() if v not in (None, "")}
    out: list[dict] = []
    seen: set[str] = set()
    for r in reads:
        items = as_list_payload(r.get("json"))
        if not items or not isinstance(items[0], dict):
            continue
        small = len(items) <= _SMALL_LIST
        hits: list[dict] = []
        for path, sv, raw in leaves:
            if not sv or path in seen:
                continue
            if _is_const_value(raw):                    # 系统常量(流程键 oa_hotel_apply/uuid/雪花)绝不是"按名字选"的下拉
                continue
            chosen = None                               # (value_key, label_key, label, confirmed)
            for it in items:
                # 值字段:优先 ID 类(id/code/value…);选项型小项(≤4 字段)允许值字段名不带 id/code(不写死字段名)
                vk = next((k for k, v in it.items() if str(v) == sv and _is_idlike(k)), None)
                if vk is None and len(it) <= 4:
                    vk = next((k for k, v in it.items()
                               if str(v) == sv and not isinstance(v, (dict, list))), None)
                if vk is None:
                    continue
                lk = _pick_label_key(it, vk)
                label = str(it.get(lk, "")).strip()
                if lk == vk or not label:               # 无独立显示名 → 不是名字→ID 下拉,防误绑
                    continue
                if any(_name_match(label, v) for v in sample_vals):   # 确认命中:正是用户录制选的显示名(含带后缀)
                    chosen = (vk, lk, label, True)
                    break
                if chosen is None and (len(sv) >= 2 or small):   # 暂存未确认命中(短码在大列表里不暂存)
                    chosen = (vk, lk, label, False)
            if chosen is None:
                continue
            vk, lk, label, confirmed = chosen
            hits.append({"path": path, "value": sv, "source_url": r.get("url"), "value_key": vk,
                         "label_key": lk, "label": label, "count": len(items), "_confirmed": confirmed})
        # 短值数字巧合去重(仅对未确认);同源未确认命中 >3 = 通用字典误命中(整源只留确认的)。确认命中永远保留。
        vcount: dict[str, int] = {}
        for h in hits:
            if not h["_confirmed"]:
                vcount[h["value"]] = vcount.get(h["value"], 0) + 1
        hits = [h for h in hits
                if h["_confirmed"] or not (len(h["value"]) <= 2 and vcount.get(h["value"], 0) >= 2)]
        if len([h for h in hits if not h["_confirmed"]]) > 3:
            hits = [h for h in hits if h["_confirmed"]]
        for h in hits:
            if h["path"] in seen:
                continue
            seen.add(h["path"])
            out.append({k: v for k, v in h.items() if k != "_confirmed"})
    return out


# 像"内部机器标识"的参数名 —— 仅高置信形态,避免误伤正常 snake_case(apply_reason/leave_type 不该告警):
_INTERNAL_NAME_RE = _re.compile(
    r"^(activity|node|task|flow|gateway|sequenceflow|usertask|bpmn|element)[_-]?\w*$"  # BPM 流程节点 ID
    r"|[0-9a-f]{8,}"                                                                   # 长 hex / hash / uuid 片段
    r"|_[0-9][0-9a-z]{4,}$",                                                           # _后数字开头随机码(_09dlq0g)
    _re.I)


def looks_internal_param_name(name: str) -> bool:
    """参数名是否像"内部机器标识"(流程节点 ID / hash / 字母_随机码)而非人类字段名 → 产出时告警、提示改名。
    含中文等非 ASCII(已是人话)或常规英文字段名(reason/startTime)不命中。通用,不挑系统/字段。"""
    n = (name or "").strip()
    if not n or not n.isascii():
        return False
    return bool(_INTERNAL_NAME_RE.search(n))


def _name_match(label: str, value) -> bool:
    """候选显示名与"录制选中值"是否同指:精确相等,或一方是另一方的子串(≥2 字)——容忍真实选人下拉
    常见的带后缀显示名(如『张三(研发部)』『病假(年度)』)。≥2 字防单字噪声误配。通用,不挑系统。"""
    a, b = (label or "").strip(), (str(value) or "").strip()
    if not a or not b:
        return False
    return a == b or (len(a) >= 2 and a in b) or (len(b) >= 2 and b in a)


def suggest_select_names(selects: list[dict], samples: dict | None) -> dict:
    """给 select/选人字段配**人类参数名**(修"选人/下拉字段参数名退回内部 key 如 Activity_xxx"的根因)。

    桥接:select 的候选**标签**(label,如"张三")== 录制样例里某字段的值 → 用那个字段的录制标签(如"领导")
    当参数名。选人字段没法靠"值匹配"命名(body 存的是内部 ID,用户选的是名字),只能经候选列表这座桥连回 DOM 标签。
    **通用、不挑字段/系统**:任何 select 都走"候选标签↔录制选项值"这一座桥;桥不上 → 不给(上层退回原名,诚实不瞎编)。
    返回 {body点路径 → 建议参数名}。
    """
    pairs = [(k, v) for k, v in (samples or {}).items() if v not in (None, "")]
    out: dict[str, str] = {}
    for s in selects or []:
        label = str(s.get("label", "")).strip()
        path = s.get("path")
        if not (path and label):
            continue
        field = next((k for k, v in pairs if _name_match(label, v)), None)   # 含带后缀显示名的子串匹配
        if field:
            out[path] = field
    return out


def _storage_scalars(storage_state: dict | None) -> dict:
    """登录态里所有离散标量值(localStorage 值递归解析 JSON + cookie 值)→ {值: 来源路径}。

    用于把提交字段认成"当前用户 / 会话值"(applicantId 等)→ 运行期从会话重取,不冻结(修 Q1 坑)。
    """
    out: dict[str, str] = {}
    if not storage_state:
        return out

    def add(v, src):
        if isinstance(v, (str, int)) and str(v) not in ("", "0", "1", "true", "false", "null"):
            out.setdefault(str(v), src)

    def walk(node, src):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{src}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{src}[{i}]")
        else:
            add(node, src)

    for o in storage_state.get("origins") or []:
        for it in o.get("localStorage") or []:
            name, val = it.get("name"), it.get("value", "")
            try:
                walk(json.loads(val), f"localStorage:{name}")
            except Exception:  # noqa: BLE001 —— 非 JSON 字符串值,整存
                add(val, f"localStorage:{name}")
    for c in storage_state.get("cookies") or []:
        add(c.get("value"), f"cookie:{c.get('name')}")
    return out


def suggest_identity(post_data: str | None, storage_state: dict | None) -> list[dict]:
    """提交体里"等于登录态里某值"的字段(如 applicantId=当前用户)→ 建议标 identity(运行期重取,不冻结)。"""
    body = _parse_body(post_data)
    if body is None:
        return []
    scal = _storage_scalars(storage_state)
    out: list[dict] = []
    for path, sv, _raw in _leaf_paths(body):
        if sv and sv in scal:
            out.append({"path": path, "value": sv, "source": scal[sv]})
    return out


def suggest_fact_check(samples: dict, reads: list[dict]) -> dict | None:
    """提交后回查源(grounded):用户提交后看了"我的记录"列表 → 该列表含刚提交的值。

    在抓到的列表读响应里找"含某提交值"的列表项 → 返回 {endpoint, match_field(项里等于该值的字段),
    param(对应用户参数/标签)}。优先用最独特(最长)的提交值,降低巧合。通用,不挑系统。
    """
    cand = sorted(((str(v), k) for k, v in (samples or {}).items() if v not in ("", None) and len(str(v)) >= 2),
                  key=lambda x: -len(x[0]))
    for r in reads or []:
        items = as_list_payload(r.get("json"))
        if not items:
            continue
        for sv, param in cand:
            for it in items:
                if isinstance(it, dict):
                    mf = next((k for k, v in it.items() if str(v) == sv), None)
                    if mf:
                        return {"endpoint": r.get("url"), "match_field": mf, "param": param}
    return None


def list_read_requests(reads: list[dict]) -> list[dict]:
    """从抓到的读响应里挑出「列表型」候选(select 候选源),给出条数 + 列表项字段名。

    供 P3 让用户把某个提交字段(如 approverId)绑定到「来自哪个列表 + 哪个字段是名字/哪个是值」。
    """
    out: list[dict] = []
    for r in reads:
        items = as_list_payload(r.get("json"))
        if not items:
            continue
        first = items[0] if isinstance(items[0], dict) else {}
        out.append({"url": r.get("url"), "count": len(items),
                    "item_keys": list(first.keys())[:20]})
    return out


def pick_submit_request(requests: list[dict], samples: dict) -> dict | None:
    """从抓到的请求里挑"提交请求"。**因果/值驱动,不挑系统**:提交 = 带最多用户填入值的那条业务写请求
    (噪声如心跳/字典/自动存草稿都不含用户填的值)。登录/鉴权写请求按内容排除。都不含用户值则取最后一条业务写请求。"""
    sample_vals = {str(v) for v in samples.values() if v not in ("", None)}
    best, best_score, last_write = None, -1, None
    for r in requests:
        if (r.get("method") or "").upper() not in _WRITE:
            continue
        body = _parse_body(r.get("post_data"))
        if body is None:
            continue
        if looks_like_auth_write(r.get("url") or "", body):   # 登录/鉴权/基建写请求 → 不是业务提交
            continue
        last_write = r
        vals = set(_values(body))
        score = len(sample_vals & vals)               # body 里命中几个用户填的值
        if score > best_score:
            best, best_score = r, score
    return best if (best is not None and best_score > 0) else last_write


def parameterize_request(req: dict, samples: dict, base_url: str = "") -> dict | None:
    """把请求体里"等于用户样例值"的字段替换成 {{字段}} 占位;内部 ID/常量保持原样。

    返回 {method, path, body_template(占位后的JSON), params:[字段], sample_inputs, content_type}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    val2field = {str(v): k for k, v in samples.items() if v not in ("", None)}
    params: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x) for x in node]
        sv = str(node)
        if sv in val2field:                            # 这个值是用户填的 → 变参数
            f = val2field[sv]
            params[f] = sv
            return "{{" + f + "}}"
        return node                                    # 内部 ID/常量 → 原样保留

    templ = walk(body)
    url = req.get("url") or ""
    path = url
    if base_url and url.startswith(base_url):
        path = url[len(base_url):] or "/"
    elif url.startswith("http"):                       # 去掉协议+域名,留 path(+query)
        from urllib.parse import urlparse
        u = urlparse(url)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
    return {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
            "content_type": req.get("content_type", "application/json"),
            "body_template": templ, "params": list(params.keys()),
            "sample_inputs": params, "auth_headers": extract_auth_headers(req.get("headers"))}


# key 像内部标识(默认不当参数):以 id/key/code/token/... 结尾
_ID_KEY = _re.compile(r"(id|key|code|token|uuid|guid|seq|no|flag|status)$", _re.I)
# key 像日期/时间(即便值是 13 位毫秒时间戳,也该当参数,不能被"长数字"规则误判成常量)
_TIME_KEY = _re.compile(r"(time|date|day|start|end|begin|expire|deadline|datetime)", _re.I)


def _is_const_value(v) -> bool:
    """像内部常量的值(默认不建议作参数):bool/null、长 hex、雪花 id(≥16 位数字)、uuid、
    snake_case 标识(如 oa_duty_leave —— 表单类型/流程键,几乎一定是固定值)。"""
    if isinstance(v, bool) or v is None:
        return True
    s = str(v)
    return bool(_re.fullmatch(r"[0-9a-fA-F]{16,}", s)          # 长 hex
               or _re.fullmatch(r"-?\d{16,}", s)               # 雪花 id(≥16 位;13 位毫秒时间戳不算)
               or _re.fullmatch(r"[0-9a-fA-F-]{32,}", s)       # uuid 形态
               or _re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", s))  # snake_case 标识(oa_duty_leave)


def _infer_type(node, key: str = "") -> str:
    """从值推断字段类型(通用,给 agent 知道该传什么):boolean/number/datetime/date/array/object/string。"""
    if isinstance(node, bool):
        return "boolean"
    if isinstance(node, int):
        if len(str(abs(node))) == 13 and _TIME_KEY.search(key):    # 13 位毫秒时间戳 + 时间类 key
            return "datetime"
        return "number"
    if isinstance(node, float):
        return "number"
    if isinstance(node, list):
        return "array"
    if isinstance(node, dict):
        return "object"
    s = str(node)
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", s):
        return "date"
    return "string"


def _date_keys(s) -> set:
    """从一个值里抽出 YYYY-MM-DD,用于日期字段跨格式匹配(通用,不挑系统):
    支持 ISO / 带斜杠日期串(2026-06-24、2026/6/24)、**10 位秒级时间戳**、12-13 位毫秒时间戳。
    时间戳按东八区(中国 OA)+ UTC 两种日期都给,容忍时区差。"""
    out: set = set()
    s = str(s)
    for m in _re.finditer(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s):       # - 或 / 分隔的日期串
        out.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    if not out and s.isdigit():
        ms = int(s) if len(s) == 13 else (int(s) * 1000 if len(s) == 10 else None)   # 13位毫秒 / 10位秒
        if ms is not None:
            try:
                for off in (8, 0):                                       # 优先东八区,再 UTC
                    out.add(_dt.datetime.fromtimestamp(ms / 1000 + off * 3600,
                                                       _dt.timezone.utc).strftime("%Y-%m-%d"))
            except Exception:  # noqa: BLE001
                pass
    return out


def flatten_body(post_data: str | None, samples: dict | None = None,
                 required_labels: set | None = None) -> list[dict]:
    """把请求体拍平成叶子字段列表 + 参数建议,供前端勾选。任意嵌套(dict/list)→ 点路径。

    suggest_name=字段中文名(录制时的 DOM 标签),**只在能确定时给**(文本按值对;日期跨格式对毫秒戳↔显示),
    对不上就退回原始 key(诚实,不瞎猜)。同值字段(都填 123123123)按录制顺序各取一个标签,不抢同一个。
    type=字段类型(值推断);required=表单 * 必填(label 命中 required_labels)。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    samples = samples or {}
    required_labels = required_labels or set()
    # 样例按录制顺序:(值字符串, 标签, 该值的日期集);allow 同值多标签按序消费
    sample_list = [(str(v), k, _date_keys(v)) for k, v in samples.items() if v not in ("", None)]
    used_i: set = set()
    # 值的全局重数:同一个值被多个表单字段共用(测试时都填 123123 / 多个字段都=1)→ 该值落到哪个字段不确定,
    # 中文名仍按录制顺序尽力给,但**不据此判必填**(避免把 房间等级=1 误当成必填的 入住人数)
    _val_mult: dict[str, int] = {}
    for _v, _lab, _dk in sample_list:
        _val_mult[_v] = _val_mult.get(_v, 0) + 1

    def match_label(sv: str):
        """→ (中文标签 or None, 是否可信)。可信=该值在表单里唯一对应一个字段,可据此判必填。"""
        for i, (v, lab, _dk) in enumerate(sample_list):        # 文本精确对(同值取下一个还没用的标签)
            if i not in used_i and v == sv:
                used_i.add(i)
                return lab, _val_mult.get(sv, 0) == 1
        sv_dates = _date_keys(sv)
        if sv_dates:                                           # 日期跨格式对(毫秒戳 ↔ 显示日期)
            for i, (v, lab, dk) in enumerate(sample_list):
                if i not in used_i and (dk & sv_dates):
                    used_i.add(i)
                    dmult = sum(1 for _v, _l, _d in sample_list if _d & sv_dates)
                    return lab, dmult == 1
        return None, False

    out: list[dict] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        else:
            sv = "" if node is None else str(node)
            key = path.split(".")[-1].split("[")[0]
            label, confident = match_label(sv)
            time_like = bool(_TIME_KEY.search(key))
            const = (not time_like) and (bool(_ID_KEY.search(key)) or _is_const_value(node))
            out.append({"path": path, "key": key, "value": sv,
                        "suggest_param": bool(label is not None or (not const and sv != "")),
                        "suggest_name": label or key,            # 对不上 → 退原始 key(不瞎猜)
                        "type": _infer_type(node, key),           # 字段类型(值推断),给 agent/契约
                        # 表单 * 必填:仅当该值唯一对应一个字段(confident)才据标签判必填,模糊命中不误标
                        "required": bool(label is not None and confident and label in required_labels)})

    walk(body, "")
    return out


def build_api_request(req: dict, param_map: dict, base_url: str = "",
                      selects: list[dict] | None = None, identity: list[dict] | None = None) -> dict | None:
    """param_map: {字段点路径 → 参数名}。把这些路径的叶子替换成 {{参数名}},其余原样。

    selects:[{path, source_url, value_key, label_key}](Q2 选领导,path 须在 param_map 里 → 运行期名字→ID);
    identity:[{path, source}](Q1 当前用户/会话值,运行期重取覆盖,不作参数)。
    返回 {method, path, url, content_type, body_template, params, sample_inputs, auth_headers, selects, identity}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    params: list[str] = []
    samples: dict[str, str] = {}
    types: dict[str, str] = {}

    def walk(node, path):
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if path in param_map:
            name = param_map[path]
            params.append(name)
            samples[name] = "" if node is None else str(node)
            types[name] = _infer_type(node, path.split(".")[-1].split("[")[0])   # 字段类型(值推断)
            return "{{" + name + "}}"
        return node

    templ = walk(body, "")
    url = req.get("url") or ""
    path = url
    if base_url and url.startswith(base_url):
        path = url[len(base_url):] or "/"
    elif url.startswith("http"):
        from urllib.parse import urlparse
        u = urlparse(url)
        path = (u.path or "/") + (("?" + u.query) if u.query else "")
    # select 元数据:path 须是参数(在 param_map),记成 param→源/键,运行期按名字查 ID
    sel_meta = [{"param": param_map[s["path"]], "source_url": s.get("source_url"),
                 "value_key": s.get("value_key"), "label_key": s.get("label_key")}
                for s in (selects or []) if s.get("path") in param_map]
    for s in sel_meta:                                          # 选领导/代码下拉 → 类型=枚举(传名字/文字)
        types[s["param"]] = "enum"
    id_meta = [{"path": i["path"], "source": i.get("source", "")} for i in (identity or [])]
    return {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
            "content_type": req.get("content_type", "application/json"),
            "body_template": templ, "params": list(dict.fromkeys(params)), "sample_inputs": samples,
            "auth_headers": extract_auth_headers(req.get("headers")),
            "field_types": types, "selects": sel_meta, "identity": id_meta}


def substitute(template, fields: dict, defaults: dict | None = None):
    """把 body_template 里的 {{字段}} 占位填回。优先用运行期 fields;没传该字段则退回 defaults(录制时的原值)
    → "全选"也安全:agent 没改的字段保持录制值(固定字段不变),不会留下空占位。"""
    defaults = defaults or {}
    if isinstance(template, dict):
        return {k: substitute(v, fields, defaults) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute(x, fields, defaults) for x in template]
    if isinstance(template, str) and template.startswith("{{") and template.endswith("}}"):
        key = template[2:-2]
        if key in fields:
            return fields[key]
        return defaults.get(key, template)
    return template


# ─────────── P4:select 名字→ID / identity 运行期重取 ───────────
def _split_path(path: str) -> list:
    """'form.items[0].id' → ['form','items',0,'id'](点路径 + 数组下标)。"""
    out: list = []
    for seg in path.split("."):
        bits = seg.split("[")
        if bits[0]:
            out.append(bits[0])
        for idx in bits[1:]:
            out.append(int(idx.rstrip("]")))
    return out


def _get_by_path(node, path: str):
    for k in _split_path(path):
        try:
            node = node[k]
        except Exception:  # noqa: BLE001
            return None
    return node


def _set_by_path(node, path: str, value) -> None:
    ks = _split_path(path)
    for k in ks[:-1]:
        try:
            node = node[k]
        except Exception:  # noqa: BLE001
            return
    try:
        node[ks[-1]] = value
    except Exception:  # noqa: BLE001
        pass


def resolve_identity_value(source: str, storage_state: dict | None):
    """从登录态按 source 取"当前用户/会话值"。source 形如 localStorage:userInfo.userId / cookie:JSESSIONID。"""
    if not storage_state or not source:
        return None
    kind, _, rest = source.partition(":")
    if kind == "cookie":
        for c in storage_state.get("cookies") or []:
            if c.get("name") == rest:
                return c.get("value")
        return None
    if kind == "localStorage":
        name, _, path = rest.partition(".")
        for o in storage_state.get("origins") or []:
            for it in o.get("localStorage") or []:
                if it.get("name") == name:
                    val = it.get("value", "")
                    if not path:
                        return val
                    try:
                        return _get_by_path(json.loads(val), path)
                    except Exception:  # noqa: BLE001
                        return val
    return None


def _apply_identity(body, api_request: dict, storage_state: dict | None) -> None:
    """把 identity 字段在运行期用会话里的当前用户值覆盖(不冻结成录制者)。"""
    for idn in api_request.get("identity") or []:
        val = resolve_identity_value(idn.get("source", ""), storage_state)
        if val is not None:
            _set_by_path(body, idn.get("path", ""), val)


async def _get_json(url: str, base_url: str, storage_state, token_key: str | None, verify: bool,
                    auth_headers: dict | None):
    """带登录态 GET 一个 URL,返回解析后的 JSON(失败返回 None)。鉴权头通用构造,不挑系统。"""
    full = url if url.startswith("http") else (base_url or "").rstrip("/") + url
    host = urlparse(full).hostname or ""
    headers: dict = dict(auth_headers or {})
    ck = _auth_headers(storage_state, host, token_key)
    if ck.get("Cookie"):
        headers["Cookie"] = ck["Cookie"]
    if "Authorization" not in headers and not (auth_headers or {}) and ck.get("Authorization"):
        headers["Authorization"] = ck["Authorization"]
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30, verify=verify) as c:
            r = await c.get(full, headers=headers)
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def _fetch_list(url: str, base_url: str, storage_state, token_key: str | None, verify: bool,
                      auth_headers: dict | None) -> list:
    """带登录态 GET 一个候选列表(选领导源),用 as_list_payload 取出数组。失败返回 []。"""
    data = await _get_json(url, base_url, storage_state, token_key, verify, auth_headers)
    return as_list_payload(data) or []


# 分页响应里"总记录数"字段(通用,不挑系统):total/totalCount/totalElements/recordsTotal…
_PAGE_TOTAL_KEYS = ("total", "totalcount", "totalelements", "totalrows", "totalnum",
                    "recordstotal", "totalsize")


def _extract_total(data) -> int | None:
    """从分页响应里抽"总记录数"(顶层或一层包装如 data.total)。无分页信息 → None。"""
    def scan(d):
        if not isinstance(d, dict):
            return None
        for k, v in d.items():
            if (str(k).lower().replace("_", "") in _PAGE_TOTAL_KEYS
                    and isinstance(v, (int, float)) and not isinstance(v, bool)):
                return int(v)
        for v in d.values():                       # 一层包装(data.total)
            if isinstance(v, dict):
                t = scan(v)
                if t is not None:
                    return t
        return None
    return scan(data)


async def _resolve_selects(api_request: dict, fields: dict, *, base_url: str, storage_state,
                           token_key: str | None, verify: bool) -> dict:
    """Q2 选领导:参数传的是名字 → 查候选列表把它换成内部 ID。查不到则原样(可能用户直接给了 ID)。"""
    for s in api_request.get("selects") or []:
        param = s.get("param")
        if param not in fields:
            continue
        name = fields[param]
        items = await _fetch_list(s.get("source_url", ""), base_url, storage_state, token_key, verify,
                                  api_request.get("auth_headers"))
        lk, vk = s.get("label_key"), s.get("value_key")
        match = next((it for it in items if isinstance(it, dict) and str(it.get(lk)) == str(name)), None)
        if match is not None and vk in match:
            fields[param] = match[vk]
    return fields


# token 在 cookie/localStorage 里的"键名"概念词(通用,不挑系统:Admin-Token/satoken/Authorization/access_token/jwt…)
_TOKEN_KEY_HINTS = ("token", "satoken", "jwt", "authorization", "auth", "access", "session", "ticket")


def _looks_like_token_key(name: str) -> bool:
    return any(h in (name or "").lower() for h in _TOKEN_KEY_HINTS)


def _token_like_value(v) -> bool:
    """像登录令牌的值:较长的不透明字符串(JWT/雪花/uuid/satoken),排除短码/带空格。"""
    s = str(v or "")
    return len(s) >= 16 and " " not in s


def _auth_headers(storage_state: dict | None, host: str, token_key: str | None = None) -> dict:
    """从登录态快照构造鉴权头(**通用,不挑系统**):同域 cookie 全带上 + 自动识别 token → Authorization Bearer。

    token 来源:① 显式 token_key 命中的 cookie/localStorage 条目(调用方已知头名时);
    ② 否则**自动识别**——键名含 token/satoken/jwt/access… 且值像令牌(长不透明串)的条目(不再写死 Admin-Token)。
    仅作"没抓到自定义鉴权头时"的兜底;主路径用录制时抓到的真实鉴权头原样带上(头名/方案都准)。
    """
    headers: dict[str, str] = {}
    if not storage_state:
        return headers
    pairs: list[str] = []
    tok_explicit, tok_auto = "", ""

    def consider(name: str, val: str) -> None:
        nonlocal tok_explicit, tok_auto
        if token_key and name == token_key:
            tok_explicit = val
        elif not tok_auto and _looks_like_token_key(name) and _token_like_value(val):
            tok_auto = val

    for c in storage_state.get("cookies") or []:
        cd = (c.get("domain") or "").lstrip(".")
        if host and cd and cd not in host and host not in cd:
            continue
        name, val = c.get("name", ""), c.get("value", "")
        pairs.append(f"{name}={val}")
        consider(name, val)
    if not tok_explicit:
        for o in storage_state.get("origins") or []:
            for it in o.get("localStorage") or []:
                consider(it.get("name", ""), it.get("value", ""))
    tok = tok_explicit or tok_auto
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    if tok:
        headers["Authorization"] = "Bearer " + tok
    return headers


# 响应体里常见的"业务成功码"字段(不信 HTTP 200,看它)。**字段名通用,但成功值不写死单一系统约定**:
# 不同系统成功值各异(若依 code=200;阿里系 code=0/"00000";有的 success=true / status="OK")。
# 故运行期**优先用资产级 success_rule**(录制期从该系统自己的真实响应学到),无则才退下面这套兜底集。
_OK_CODE_KEYS = ("code", "status", "errcode", "errCode", "resultCode", "rspCode", "retCode", "flag")
# 兜底成功值集(仅在没学到资产级规则时用;尽量覆盖常见约定,但**不能假设**——这正是 success_rule 存在的原因)
_OK_FALLBACK_VALUES = frozenset({"200", "0", "00000", "true", "success", "ok", "1"})
_MSG_KEYS = ("msg", "message", "error", "errmsg", "errMsg")


def _result_msg(data: dict) -> str:
    for k in _MSG_KEYS:
        v = data.get(k)
        if v:
            return str(v)
    return ""


def infer_success_rule(reads: list[dict]) -> dict | None:
    """从录制期抓到的**成功**读响应里,学这套系统自己的"业务成功"约定(泛化核心:不挑系统、不假设 200)。

    录制时抓到的 GET 列表响应都是真成功的 → 它们响应里出现的"成功码字段 + 该值"就是本系统的成功标志。
    多数票:同一(字段,值)在多个读响应里出现得最多者胜 → {field, ok_values}。无则 None(运行期退兜底启发式)。
    例:若依的读响应普遍是 {"code":200,...} → 学出 {"field":"code","ok_values":["200"]};
        阿里系 {"code":"0",...} → {"field":"code","ok_values":["0"]};不会把 200 强加给后者。
    """
    from collections import Counter
    votes: Counter = Counter()
    for r in reads or []:
        data = r.get("json")
        if not isinstance(data, dict):
            continue
        for k in _OK_CODE_KEYS:                              # 一个响应只取第一个命中的码字段(与 _response_ok 同序)
            v = data.get(k)
            if v is not None and not isinstance(v, (dict, list)):
                votes[(k, str(v))] += 1
                break
        else:
            if isinstance(data.get("success"), bool) and data["success"]:
                votes[("success", "true")] += 1
    if not votes:
        return None
    (field, val), _ = votes.most_common(1)[0]
    return {"field": field, "ok_values": [val]}


def _response_ok(data, rule: dict | None = None) -> tuple[bool, str]:
    """业务成功判定。**优先用资产级 success_rule**(录制期从该系统真实响应学到的约定:{field, ok_values}),
    无则按通用兜底启发式(成功码字段∈兜底成功值集 / success 布尔);都没有 → 靠 HTTP 2xx。

    解决"HTTP 200 但 body 里 code=500 / success=false = 空操作",且**不把任何单一系统的成功值写死**。
    返回 (是否成功, 失败原因)。
    """
    if not isinstance(data, dict):
        return True, ""                                       # 非对象(数组/文本)→ 没业务码,靠 HTTP
    msg = _result_msg(data)
    if rule and rule.get("field"):                            # 资产级学到的成功约定优先
        f = rule["field"]
        if f in data and not isinstance(data.get(f), (dict, list)):
            oks = {str(x) for x in (rule.get("ok_values") or [])}
            ok = str(data.get(f)) in oks
            return ok, ("" if ok else f"业务返回失败({f}={data.get(f)}):{msg}")
        # 规则字段这次没出现/类型异常 → 不硬判,退兜底启发式(系统响应结构可能变了)
    for k in _OK_CODE_KEYS:
        v = data.get(k)
        if v is not None and not isinstance(v, (dict, list)):
            ok = str(v).lower() in _OK_FALLBACK_VALUES
            return ok, ("" if ok else f"业务返回失败({k}={v}):{msg}")
    if "success" in data:
        ok = bool(data["success"])
        return ok, ("" if ok else f"业务返回 success=false:{msg}")
    return True, ""                                           # 无成功码字段 → 靠 HTTP 2xx


# ── 运行期值归一:让 agent 传"人话"值,按字段声明类型(field_types)+ 录制样例格式转成目标系统要的形态 ──
# 通用、不挑字段:number→数字、boolean→布尔、datetime/date→录制时那个字段的格式(毫秒戳/秒戳/日期串)。
# 这样 body_template 里 {{字段}} 填回去就是目标系统认的类型/格式,而不是一律字符串(否则数值条件失效、日期格式错被拒)。
_EPOCH = _dt.datetime(1970, 1, 1)
_DT_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
               "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S")


def _parse_dt(s):
    """把一个值解析成东八区 wall-time datetime(naive,贴合中国 OA);失败 None。支持毫秒/秒戳、ISO、常见日期串。"""
    s = str(s).strip()
    if not s:
        return None
    if s.lstrip("-").isdigit():                              # 时间戳(秒/毫秒)→ 东八区 wall time
        try:
            return _EPOCH + _dt.timedelta(seconds=(int(s) / 1000 if len(s) >= 12 else int(s)), hours=8)
        except Exception:  # noqa: BLE001
            return None
    for fmt in _DT_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _coerce_datetime(value, sample, ftype):
    """把日期/时间值归一成**录制样例 sample 揭示的目标形态**(毫秒戳/秒戳=数字、或日期(时间)串)。
    样例缺失或解析不了 → 原样返回(best-effort,绝不破坏请求);时间戳目标统一返回数字(与原 body 一致)。"""
    ss = str(sample or "").strip()
    dt = _parse_dt(value)
    if dt is None:
        return value
    epoch_s = (dt - _EPOCH - _dt.timedelta(hours=8)).total_seconds()
    if ss.isdigit() and len(ss) >= 12:                       # 目标:毫秒戳(数字)
        return int(epoch_s * 1000)
    if ss.isdigit() and len(ss) == 10:                       # 目标:秒戳(数字)
        return int(epoch_s)
    if ftype == "date":
        return dt.strftime("%Y-%m-%d")
    sep = "T" if "T" in ss else " "
    return dt.strftime(f"%Y-%m-%d{sep}%H:%M:%S")


def _coerce_by_type(value, ftype, sample):
    """按字段声明类型把 agent 值归一(通用,不挑字段)。类型未知/空/转不动 → 原样。"""
    if value is None:
        return value
    if ftype in ("number", "integer"):
        if isinstance(value, str) and value.strip():
            t = value.strip()
            try:
                return int(t) if t.lstrip("-").isdigit() else float(t)
            except ValueError:
                return value
        return value
    if ftype == "boolean":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "y", "是", "on")
    if ftype in ("datetime", "date"):
        return _coerce_datetime(value, sample, ftype)
    return value


def _coerce_fields(fields: dict, api_request: dict) -> dict:
    """对运行期参数按 api_request.field_types 逐个归一(日期格式取自录制样例)。无 field_types → 原样不动。"""
    ftypes = api_request.get("field_types") or {}
    if not ftypes:
        return fields
    samples = api_request.get("sample_inputs") or {}
    return {k: (_coerce_by_type(v, ftypes.get(k), samples.get(k)) if k in ftypes else v)
            for k, v in fields.items()}


async def execute_api_request(api_request: dict, fields: dict, *, base_url: str = "",
                              storage_state: dict | None = None, send: bool = True,
                              verify: bool = True, token_key: str | None = None,
                              overrides: dict | None = None) -> dict:
    """参数填回 body_template,带登录态发请求(send=True)或只校验参数齐全(send=False,dry,写安全)。

    P4:发真请求前 ① select 把参数里的名字换成内部 ID(选领导);② substitute 后用会话里的当前用户值
    覆盖 identity 字段(申请人=谁调用就是谁,不冻结成录制者);③ overrides 把上一步响应值注入本步 body
    (Q3 步链,如 taskId)。dry 不连网,只校验参数齐全。
    """
    fields = dict(fields)
    if send:                                                 # 选领导:名字→ID(需连网查候选列表)
        fields = await _resolve_selects(api_request, fields, base_url=base_url,
                                        storage_state=storage_state, token_key=token_key, verify=verify)
    # 按字段声明类型归一值(number/bool/日期格式),让 body 填回的是目标系统认的类型/格式 —— 通用,不挑字段
    fields = _coerce_fields(fields, api_request)
    body = substitute(api_request.get("body_template"), fields, api_request.get("sample_inputs") or {})
    _apply_identity(body, api_request, storage_state)        # 当前用户/会话值运行期重取覆盖
    for p, v in (overrides or {}).items():                   # Q3:上一步响应值注入(taskId 等)
        _set_by_path(body, p, v)
    method = (api_request.get("method") or "POST").upper()
    path = api_request.get("path") or ""
    # 优先用录制时的完整 url(同一 OA host 不变);否则 base_url + path
    url = api_request.get("url") or (path if path.startswith("http") else (base_url or "").rstrip("/") + path)
    if not send:
        leftover = "{{" in json.dumps(body, ensure_ascii=False)   # 还有没填上的 {{字段}}?
        return {"ok": not leftover, "dry": True, "method": method, "url": url, "body": body,
                "detail": "有参数没填上" if leftover else "请求可构造(dry,未真发)"}
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    headers = {"Content-Type": api_request.get("content_type") or "application/json"}
    # ① 录制时抓到的应用自定义鉴权头(Authorization / Admin-Token / satoken / 租户号…)原样带上 —— 通用,不挑系统
    headers.update(api_request.get("auth_headers") or {})
    # ② Cookie 用 storageState 的(更全/可能更新);没抓到自定义头时,才回退到按 token_key 猜 Authorization
    ck = _auth_headers(storage_state, host, token_key)
    if ck.get("Cookie"):
        headers["Cookie"] = ck["Cookie"]
    if "Authorization" not in headers and not (api_request.get("auth_headers") or {}) and ck.get("Authorization"):
        headers["Authorization"] = ck["Authorization"]
    import httpx
    async with httpx.AsyncClient(timeout=30, verify=verify) as c:
        r = await c.request(method, url, json=body, headers=headers)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"raw": r.text[:1000]}
    http_ok = 200 <= r.status_code < 300
    # 不信 HTTP 200:看响应体业务码。**优先用资产级 success_rule**(录制期学到的本系统成功约定),不挑系统
    biz_ok, biz_reason = _response_ok(data, api_request.get("success_rule"))
    ok = http_ok and biz_ok
    detail = (biz_reason if (http_ok and not biz_ok) else ("" if http_ok else f"HTTP {r.status_code}"))
    return {"ok": ok, "status": r.status_code, "response": data, "business_ok": biz_ok,
            "detail": detail, "method": method, "url": url}


async def execute_api_workflow(workflow: dict, fields: dict, *, base_url: str = "",
                               storage_state: dict | None = None, send: bool = True,
                               verify: bool = True, token_key: str | None = None) -> dict:
    """Q3 多写步链:按 steps 顺序执行(每步=录到的一个请求);step.links 把更早步「响应」里的值注入本步 body
    (如 taskId)。每步带各自 select/identity。任一步失败整体失败;最终步即业务结果。
    """
    steps = workflow.get("steps") or []
    responses: list = []
    last: dict = {}
    for i, step in enumerate(steps):
        overrides: dict = {}
        for lk in step.get("links") or []:
            src = responses[lk["source_step"]] if 0 <= lk.get("source_step", -1) < len(responses) else None
            if src is not None:
                val = _get_by_path(src, lk.get("source_path", ""))
                if val is not None:
                    overrides[lk.get("target_path", "")] = val
        out = await execute_api_request(step, fields, base_url=base_url, storage_state=storage_state,
                                        send=send, verify=verify, token_key=token_key, overrides=overrides)
        last = out
        responses.append(out.get("response") if send else out.get("body"))
        if not out.get("ok"):
            return {"ok": False, "failed_step": i, "detail": f"第{i + 1}步失败", "step_result": out}
    return {"ok": bool(last.get("ok", True)), "steps": len(steps),
            "status": last.get("status"), "response": last.get("response"), "final": last}


async def _grounded_recheck(fc: dict, fields: dict, *, base_url: str, storage_state, token_key: str | None,
                            verify: bool, auth_headers: dict | None,
                            retries: int = 4, backoff: float = 0.6) -> tuple[bool, str]:
    """提交后回查:GET 记录列表,确认提交的值真出现在记录里(grounded,不信接口自报成功)。

    异步写多有延迟 → 轮询 retries 次再判失败。param 没传(可能被改名)→ 跳过回查不误判。
    """
    import asyncio
    param, mf, ep = fc.get("param"), fc.get("match_field"), fc.get("endpoint", "")
    retries = int(fc.get("retries", retries))
    backoff = float(fc.get("backoff_s", backoff))
    target = fields.get(param)
    if target is None or not ep:
        return True, ""
    truncated, total = False, None                    # truncated:列表确有更多页未取(total>已取)→ 不武断判失败
    for i in range(max(1, retries)):
        data = await _get_json(ep, base_url, storage_state, token_key, verify, auth_headers)
        items = as_list_payload(data) or []
        if any(isinstance(it, dict) and str(it.get(mf)) == str(target) for it in items):
            return True, ""                           # 找到刚提交的记录 → 强阳性,确认真生效
        total = _extract_total(data)
        truncated = total is not None and total > len(items)   # 仅"明确分页且还有更多页"才算证据不足
        if i < retries - 1:
            await asyncio.sleep(backoff)
    if truncated:
        # 列表分页、记录可能在别的页 → 证据不足,不把"接口已自报成功"翻成失败(咨询性,避免误杀)
        return True, f"回查不确定:列表分页(共{total}条,仅取部分),未在已取页找到 {param}={target}(不据此判失败)"
    # 无分页(整表已取)却没有 → 真"空操作",一票否决(接地核查的价值所在)
    return False, f"回查未生效:记录列表里没找到 {param}={target}(疑似空操作)"


async def execute_api(api_request: dict, fields: dict, **kw) -> dict:
    """统一入口:api_request 有 steps → 多步工作流(Q3),否则单请求;成功后若配了 fact_check → grounded 回查。"""
    runner = execute_api_workflow if api_request.get("steps") else execute_api_request
    out = await runner(api_request, fields, **kw)
    fc = api_request.get("fact_check")
    if kw.get("send", True) and out.get("ok") and fc:
        auth = api_request.get("auth_headers") or ((api_request.get("steps") or [{}])[-1].get("auth_headers"))
        fok, freason = await _grounded_recheck(
            fc, fields, base_url=kw.get("base_url", ""), storage_state=kw.get("storage_state"),
            token_key=kw.get("token_key"), verify=kw.get("verify", True), auth_headers=auth)
        out["fact_check_passed"] = fok
        if not fok:
            out["ok"] = False
            out["detail"] = freason
        elif freason:                                # 通过但不确定(列表分页未找到)→ 记咨询性说明,不翻失败
            out["fact_check_note"] = freason
    return out


def build_api_workflow(writes: list[dict], *, param_map: dict, base_url: str = "",
                       selects: list[dict] | None = None, identity: list[dict] | None = None) -> dict:
    """把有序写请求组装成多步工作流(Q3):每步=一个抓到的请求;**最后一步**带用户参数/select/identity,
    其余步是常量(其动态值靠步链注入);自动发现步间数据流(taskId 等)挂到对应目标步。

    返回 {steps:[...]}(放进 PageScriptBody.api_request;运行期 execute_api 自动走工作流)。
    """
    n = len(writes)
    steps: list[dict] = []
    for i, w in enumerate(writes):
        last = i == n - 1
        apir = build_api_request(w, param_map if last else {}, base_url,
                                 selects=selects if last else None,
                                 identity=identity if last else None)
        steps.append(apir or {})
    for lk in discover_step_links(writes):                   # 步间数据流挂到目标步
        steps[lk["target_step"]].setdefault("links", []).append(
            {"target_path": lk["target_path"], "source_step": lk["source_step"],
             "source_path": lk["source_path"]})
    return {"steps": steps}
