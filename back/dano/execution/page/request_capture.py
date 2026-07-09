"""录制 V2:抓"提交请求" → 参数化成可调用的内部接口(框架无关)。

很多业务页面其实是 SPA:点提交时网页向它自己后端发了个写请求(带表单值的 JSON)。把那个请求抓下来,
请求体里**等于用户填的值**的字段 → 变成参数;内部 ID/token 等保持常量。回放就是直接发这个请求。
不依赖控件长相,比录 DOM 点击稳得多。

本模块是纯函数(不碰浏览器),便于离线测试。
"""
from __future__ import annotations

import datetime as _dt
import copy
import json
import logging

_log = logging.getLogger("dano.request_capture")
import re as _re
from urllib.parse import parse_qsl, urlencode, unquote, urlparse, urlunparse

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


# 标记"这层原本是字符串化的 JSON"(如若依/工作流把整张表单打成一段 JSON 文本塞进 formData):
# 运行期 substitute 据此把填好值的内层结构 re-stringify 回字符串,目标系统照常解析。
_JSONSTR = "__dano_jsonstr__"
# 段拼接模板:值嵌在长串里时(如 "请假事由:回家",用户只填"回家")只参数化那一段、保留常量前后缀。
# 形如 {__dano_seg__: ["请假事由:", {"$p": "原因"}, "后缀"]} → 运行期 substitute join 成最终字符串。
_SEG = "__dano_seg__"


def _unwrap_json_strings(node, depth: int = 0):
    """递归把"值是 JSON 文本"的字符串叶子解开成嵌套结构,用 {__dano_jsonstr__: 解开后} 包住(以便运行期再 stringify)。

    只解 dict / 非空对象数组(防把 'true'/'123'/'[]'/普通文本误当结构);限深度防套娃。通用,不挑系统/字段——
    任何把表单序列化成 JSON 字符串的请求体,内层字段都能被后续参数化逻辑当独立字段看到。
    """
    if isinstance(node, dict):
        return {k: _unwrap_json_strings(v, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_unwrap_json_strings(v, depth) for v in node]
    if isinstance(node, str) and depth < 4:
        s = node.strip()
        if s[:1] in ("{", "[") and len(s) >= 2:
            try:
                inner = json.loads(s)
            except Exception:  # noqa: BLE001
                return node
            if isinstance(inner, dict) or (isinstance(inner, list) and inner and isinstance(inner[0], dict)):
                return {_JSONSTR: _unwrap_json_strings(inner, depth + 1)}
    return node


def _parse_body(post_data: str | None):
    if not post_data:
        return None
    try:
        return _unwrap_json_strings(json.loads(post_data))   # JSON:解析 + 解开内层 stringified JSON,内层字段可参数化
    except Exception:  # noqa: BLE001 —— 非 JSON,尝试 application/x-www-form-urlencoded(a=1&b=2)
        if "=" in post_data:
            from urllib.parse import parse_qsl
            pairs = parse_qsl(post_data, keep_blank_values=True)
            if pairs:
                return dict(pairs)                           # 扁平表单字段(同样可参数化 / identity / select)
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


# POST 其实是"读/查询"的常见动词前缀(很多系统用 POST 传查询条件):get/query/list/search/page…
# 这类不是业务写(不该当提交候选/工作流步骤,录制时也不该被拦成假成功,否则下拉/列表加载不出来)。通用,不挑系统。
_READ_VERB_RE = _re.compile(
    r"^(get|query|list|search|find|load|page|count|tree|fetch|select|view|export|download|stat|statistic)",
    _re.I)
_READ_PATH_HINTS = (
    "查询", "列表", "分页", "搜索", "字典", "候选", "下拉", "详情",
    "chaxun", "liebiao", "fenye", "sousuo", "zidian", "xiala",
)
_READ_BODY_KEY_HINTS = frozenset({
    "pageno", "pageindex", "pageidx", "pagenum", "pagesize", "currentpage",
    "offset", "limit", "keyword", "keywords", "searchkey",
    "searchtext", "filter", "filters", "criteria", "condition", "conditions",
    "sort", "sorter", "orderby", "ordertype",
    "页码", "页数", "关键字", "关键词", "查询", "搜索",
})


def _normalized_key(key: str) -> str:
    return _re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "", str(key or "").lower())


def _body_has_read_shape(body) -> bool:
    """POST 查询常靠 body 传分页/过滤条件；用结构信号识别，避免只靠路径名。"""
    if isinstance(body, str):
        body = _parse_body(body)
    keys = {_normalized_key(k) for k in _all_keys(body)}
    keys.discard("")
    if keys & _READ_BODY_KEY_HINTS:
        return True
    # 常见组合: page + size / current + size。避免把单独 size 字段误判成查询。
    has_page = bool(keys & {"page", "pageindex", "pagenum", "currentpage", "current"})
    has_size = bool(keys & {"size", "pagesize", "limit"})
    return has_page and has_size


def looks_like_read_request(url: str, body=None) -> bool:
    path = unquote(urlparse(url or "").path or "")
    segs = [s for s in path.split("/") if s]
    if not segs:
        return _body_has_read_shape(body)
    last = segs[-1].split("?")[0]
    last_norm = last.lower()
    if _READ_VERB_RE.match(last_norm):
        return True
    path_norm = path.lower()
    if any(h in path_norm for h in _READ_PATH_HINTS):
        return True
    return _body_has_read_shape(body)


def json_write_requests(requests: list[dict]) -> list[dict]:
    """抓到的请求里所有「带 JSON body 的写请求」(候选提交请求),保序。供前端列出来手选用哪个。
    """
    out: list[dict] = []
    for r in requests:
        if ((r.get("method") or "").upper() in _WRITE and _parse_body(r.get("post_data")) is not None
                and not looks_like_read_request(r.get("url") or "", r.get("post_data"))):
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
    """body 拍平成 [(点路径, tokens, 值字符串, 原始值)]。

    tokens=真实分段列表(str 键 / int 下标),用于**无歧义**按路径注入——键名含 '.'/'[' 也安全。
    点路径仅供展示/前端协议(键名特殊时有歧义,故 identity/串联注入一律走 tokens)。"""
    out: list[tuple] = []

    def walk(node, path, toks):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, toks + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", toks + [i])
        else:
            out.append((path, toks, "" if node is None else str(node), node))

    walk(body, "", [])
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


def _tokens_to_str(toks) -> str:
    """tokens → 点路径字符串(仅供展示;注入仍用 tokens)。"""
    out = ""
    for t in toks:
        out += f"[{t}]" if isinstance(t, int) else (f".{t}" if out else str(t))
    return out


def _find_value_tokens(node, value, toks=None, expected_type: str | None = None):
    """在 node 里找一个叶子值 == value 的 **tokens 路径**(深度优先,第一个)。无则 None。

    与 _find_value_path 同义,但返回真实分段列表 → 读响应取值时键含点也无歧义。
    H13 修复:expected_type 限定叶子值类型(int/str/bool/float),防 int/str 撞值(如 10 位时间戳 vs 字符串 hash)。
    """
    toks = toks or []
    if isinstance(node, dict):
        for k, v in node.items():
            r = _find_value_tokens(v, value, toks + [k], expected_type)
            if r is not None:
                return r
    elif isinstance(node, list):
        for i, v in enumerate(node):
            r = _find_value_tokens(v, value, toks + [i], expected_type)
            if r is not None:
                return r
    elif node is not None and not isinstance(node, bool) and str(node) == str(value):
        if expected_type:
            actual = ("bool" if isinstance(node, bool)
                      else "int" if isinstance(node, int) and not isinstance(node, bool)
                      else "float" if isinstance(node, float)
                      else "str")
            if actual != expected_type:
                return None                                    # 类型不匹配 → 不算命中(防撞值)
        return toks or None
    return None


def discover_step_links(writes: list[dict]) -> list[dict]:
    """有序写请求(含 response_json)→ 步间数据流(Q3):某步 body 的值 == 更早某步「响应」里的值 → step: 链。

    返回 [{target_step, target_path, source_step, source_path}]。如第2步 flowTask.taskId 来自第1步响应 data.taskId。
    H13 修复:阈值保持 4(短 taskId 如 T-777/P-1/U-123 是常见长度,提到 6 会把真实工作流链路打掉);同时校验
    value 类型匹配(int 不与字符串撞值,如 10 位时间戳 vs 字符串 hash),防误连。
    """
    bodies = [_parse_body(w.get("post_data")) for w in writes]
    links: list[dict] = []
    for i, body in enumerate(bodies):
        if body is None:
            continue
        for tpath, ttoks, tval, _raw in _leaf_paths(body):
            if len(tval) < 4:                              # 保持 4:短 taskId/uuid 前缀常见
                continue
            for j in range(i):
                resp = writes[j].get("response_json")
                if resp is None:
                    continue
                resp_unwrapped = _unwrap_json_strings(resp)   # H14 修复:stringified JSON 嵌套也要解开比对(Ruoyi 风格)
                stoks = _find_value_tokens(resp_unwrapped, tval, expected_type=_raw_type(_raw))
                if stoks is not None:
                    links.append({"target_step": i, "target_path": tpath, "target_tokens": ttoks,
                                  "source_step": j, "source_path": _tokens_to_str(stoks),
                                  "source_tokens": stoks})
                    break
    return links


def _raw_type(raw) -> str:
    """_leaf_paths 拆出的原始叶子类型(int/str/bool/float/other),用于 H13 防撞值。"""
    if raw is None:
        return "null"
    if isinstance(raw, bool):
        return "bool"
    if isinstance(raw, int):
        return "int"
    if isinstance(raw, float):
        return "float"
    return "str"


# 显示名(给人看的)字段提示;**登录名(username/account)排最后** —— 选人下拉里用户认的是"张三"而非"zhangsan",
# 用对显示名字段,name→ID 桥接与运行期解析才对得上。通用,不挑系统。
_DISPLAY_HINTS = ("nickname", "realname", "fullname", "truename", "cnname", "displayname",
                  "name", "label", "title", "caption", "text")
_LOGIN_HINTS = ("username", "loginname", "account", "loginid", "useraccount")
# dept/org/company 是"上下文"id-like 字段,不当显示名;它们结尾也是 "Id"/"id",
# 容易在 _pick_label_key 里被错当 label
_CONTEXT_ID_HINTS = ("deptid", "orgid", "companyid", "unitid", "tenantid", "teamid",
                      "positionid", "roleid", "groupid", "postid", "deptname",
                      "orgname", "companyname", "unitname")


def _pick_label_key(item: dict, value_key: str) -> str:
    """从列表项里挑"显示名"字段当 label:优先真正给人看的名字(nickname/realname/name/label…),
    **登录名(username/account)排最后**(选人下拉用户看的是显示名);同档取最长文字。无文字字段 → 用 value_key。"""
    text = [k for k in item if k != value_key and isinstance(item[k], str) and item[k].strip()]
    if not text:
        return value_key

    def rank(k: str) -> int:
        kl = k.lower()
        if any(h in kl for h in _CONTEXT_ID_HINTS):
            return 3                                     # 部门/组织/租户等"上下文 id-like"字段不当 label(选人下拉里它们是噪音)
        if any(h in kl for h in _LOGIN_HINTS) and "nick" not in kl:
            return 2                                     # 登录名最后(username/account)
        if any(h in kl for h in _DISPLAY_HINTS):
            return 0                                     # 显示名优先
        return 1                                         # 其它文字字段居中

    return min(text, key=lambda k: (rank(k), -len(item[k])))


_IDLIKE = _re.compile(r"(id|code|key|value|no|num|guid|uuid|oid|sn|kind|status|state|level)$", _re.I)


def _is_idlike(key: str) -> bool:
    """命中的列表字段是不是"ID 类"(select 引用的是项的 ID,不是某段文本)。"""
    return bool(key) and bool(_IDLIKE.search(key))


_SMALL_LIST = 50    # "字典型下拉"是小列表(事假/病假…);城市/数据大字典是大列表 → 区分短码真假命中
_OPTIONS_SNAPSHOT_MAX = 500    # 快照进 skill 的候选选项上限(再多就只存来源、运行期 --list-options 现拉)
_LIST_ROW_CONST_OK_RE = _re.compile(r"(type|status|state|flag|sort|order|level|kind|class|role)$", _re.I)


def _enum_option_record(label, value=None) -> dict | None:
    lab = str(label or "").strip()
    if not lab:
        return None
    return {"label": lab, "value": lab if value is None else value}


def _enum_records_from_items(items, label_key: str | None, value_key: str | None = None,
                             *, limit: int = _OPTIONS_SNAPSHOT_MAX) -> list[dict]:
    """接口候选项 → 标准枚举 [{label,value}]。

    label 是用户/前端选择的真实业务枚举；value 是请求实际提交值。
    """
    if not label_key:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rec = _enum_option_record(it.get(label_key), it.get(value_key) if value_key and value_key in it else None)
        if not rec or rec["label"] in seen:
            continue
        seen.add(rec["label"])
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def _enum_records_from_page_options(opts: list | tuple | None) -> list[dict]:
    """DOM 下拉快照 → 标准枚举 [{label,value}]。

    原生 select 会提供 {label,value};弹窗类下拉通常只有文字,此时 value=label。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for opt in opts or []:
        if isinstance(opt, dict):
            rec = _enum_option_record(
                opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value"),
                opt.get("value", opt.get("label", opt.get("text", opt.get("name")))),
            )
        else:
            rec = _enum_option_record(opt, opt)
        if not rec or rec["label"] in seen:
            continue
        seen.add(rec["label"])
        out.append(rec)
    return out


def _page_enum_selected_label(opts, picked: str) -> str:
    """page_enum_options 里的选中显示值。兼容旧形态 key=选中值,新形态 {selected/value}。"""
    if isinstance(opts, dict):
        for k in ("selected", "selected_label", "value", "label"):
            v = opts.get(k)
            if v not in (None, ""):
                return str(v)
    return str(picked or "")


def _records_with_existing_option_map(records: list[dict], option_map: dict | None) -> list[dict]:
    """DOM 只给 label 时,保留之前从 API 字典学到的 label→提交值映射。

    典型场景:页面下拉显示「病假/事假/婚假」,提交体是 type=2;API 字典已识别
    「病假→2」。DOM 选项是地面真值,但不能把已有映射洗成 label→label。
    """
    if not isinstance(option_map, dict) or not option_map:
        return records
    out: list[dict] = []
    for rec in records:
        label = str(rec.get("label") or "").strip()
        if label and label in option_map:
            nr = dict(rec)
            nr["value"] = option_map[label]
            out.append(nr)
        else:
            out.append(rec)
    return out


_CN_FIELD_ALIASES = {
    "类型": {"type", "kind", "category", "class", "leaveType", "gslx"},
    "类别": {"type", "kind", "category", "class"},
    "状态": {"status", "state"},
    "项目": {"project", "xm", "xmId", "projectId"},
    "审批": {"approver", "assignee", "user", "userId"},
}


def _norm_field_token(v) -> str:
    return _re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(v or "")).lower()


def _label_matches_internal_field(label: str, path: str, field: dict | None = None) -> bool:
    """把 DOM 字段名(如「请假类型」)和提交体内部字段(type/leaveType/gslx)连起来。"""
    label_s = str(label or "")
    path_s = str(path or "")
    leaf = path_s.split(".")[-1].split("[")[0]
    candidates = [path_s, leaf]
    if field:
        candidates.extend([field.get("key"), field.get("path"), field.get("suggest_name")])
    cand_norm = {_norm_field_token(c) for c in candidates if c not in (None, "")}
    label_norm = _norm_field_token(label_s)
    if label_norm and any(label_norm == c or label_norm in c or c in label_norm for c in cand_norm if c):
        return True
    for cn, aliases in _CN_FIELD_ALIASES.items():
        if cn in label_s and any(_norm_field_token(a) in cand_norm for a in aliases):
            return True
    return False


def _records_with_recorded_value(records: list[dict], selected: str, current) -> list[dict]:
    """DOM 只有显示名时,用录制时提交值推导 label→value。

    常见 Element/Ant 下拉:第 1/2/3 个选项提交 1/2/3；若选中「婚假」且 body 为 3,
    就可安全推导 事假→1、病假→2、婚假→3。无法证明时保留原记录,交给发布闸门阻断。
    """
    labels = [str(r.get("label") or "").strip() for r in records]
    if not labels:
        return records
    selected_s = str(selected or "").strip()
    try:
        idx = next(i for i, label in enumerate(labels) if _name_match(label, selected_s))
    except StopIteration:
        return records
    cur = str(current if current is not None else "").strip()
    if not _re.fullmatch(r"-?\d+", cur):
        return records
    n = int(cur)
    if n == idx + 1:
        return [{**r, "value": i + 1} for i, r in enumerate(records)]
    if n == idx:
        return [{**r, "value": i} for i, r in enumerate(records)]
    return records


def _enum_labels(records: list[dict]) -> list[str]:
    return [str(r["label"]) for r in records if str(r.get("label") or "").strip()]


def _enum_option_map(records: list[dict]) -> dict:
    return {str(r["label"]): r.get("value") for r in records if str(r.get("label") or "").strip()}


def _attach_enum_binding(entry: dict, records: list[dict], *, source: str, confirmed: bool) -> dict:
    """把真实枚举源挂到 select entry。没有候选时不伪装成 enum。"""
    labels = _enum_labels(records)
    if not labels:
        return entry
    entry["options"] = labels
    entry["option_map"] = _enum_option_map(records)
    entry["count"] = len(labels)
    entry["enum_source"] = source
    entry["enum_confirmed"] = bool(confirmed)
    return entry


def _is_scalar(v) -> bool:
    return isinstance(v, (str, int, float)) and not isinstance(v, bool)


def _parent_path(path: str) -> str:
    """叶子点路径的父对象路径:'ywsxList[0].yyxtmc' → 'ywsxList[0]';'csmc' → ''。用于找"名/ID 配对"的兄弟字段。"""
    if path.endswith("]"):
        return path[:path.rfind("[")]
    i = path.rfind(".")
    return path[:i] if i >= 0 else ""


_AGG_MIN_ITEMS = 50    # 列表 ≥ 此规模才考虑"按类目聚合的全量字典"判定(更小的就是单字段选项列表)
# 像"分类/类目键"的字段名(若依 dictType / type / category / group …):聚合字典靠它分组,收窄时优先认它。
_CATEGORY_KEY_RE = _re.compile(
    r"(dicttype|type|categ|category|group|kind|class|classify|module|biztype|sort|parent)", _re.I)


def _aggregate_category(items: list[dict], value_key: str, label_key: str) -> str | None:
    """列表是不是"按类目聚合的**全量**字典"(若依 dict_data 这类:同一码在不同 dictType 下重复出现)?
    是 → 返回**分类键**(供按"所选项所属类目"把全量收窄成真正属于该字段的选项);否(单字段选项源:
    城市/用户/部门,码/ID 全局唯一)→ None。**通用,不挑系统/字段名**。

    判据:① 列表够大(>_AGG_MIN_ITEMS);② 值键取值在整列表里**大量重复**(distinct < 0.7×N)——单字段
    选项源的码基本唯一,只有"多字段拼在一起的全量字典"才会重码;③ 存在一个标量、近乎全员都有、取值数
    1<distinct<N 的列表字段当分类键(名字像 type/category/dictType 的加分,再取分组更粗的)。
    """
    n = len(items)
    if n < _AGG_MIN_ITEMS:
        return None
    vals = [str(it.get(value_key)) for it in items if isinstance(it, dict) and it.get(value_key) is not None]
    if not vals or len(set(vals)) >= 0.7 * len(vals):       # 码基本唯一 → 单字段选项源,不是聚合
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    best = None
    for k in first:
        if k in (value_key, label_key):
            continue
        col = [it.get(k) for it in items if isinstance(it, dict)]
        if sum(1 for v in col if _is_scalar(v)) < 0.8 * n:  # 不是"近乎全员都有的标量"列 → 不当分类键
            continue
        distinct = len({str(v) for v in col if v is not None})
        if 1 < distinct < n:                                # 能分组(既非全同也非全异)
            score = (1 if _CATEGORY_KEY_RE.search(str(k)) else 0, -distinct)
            if best is None or score > best[0]:
                best = (score, k)
    return best[1] if best else None


def _match_select(sv: str, items: list[dict], sample_vals: set, small: bool, *,
                  path_is_assignee: bool = False):
    """判提交值 sv 对应候选列表里哪种选项 → (mode, value_key, label_key, label, confirmed, item) 或 None。
    mode='name':字段存的是**显示名**(配对 id 字段另存,如 yyxtmc=名 / yyxtid=id);
    mode='code':字段存的是**码/ID**(单字段,如 type=2 / approverId=12,agent 传名运行期换码)。通用,不挑系统。
    返回里带命中的**列表项 item**:聚合字典需读它的分类键(dictType…)把全量收窄成该字段的真实选项。
    path_is_assignee: 路径在审批人/选人容器下时,豁免「亲手填的值当码」的拒判。"""
    name_cand = code_cand = None
    for it in items:
        id_vk = next((k for k, v in it.items() if _is_scalar(v) and _is_idlike(k)), None)
        if id_vk is not None:                            # NAME:sv 命中该项的**显示名**字段(非随便某文字)+ 项里另有 id 类字段
            disp = _pick_label_key(it, id_vk)            # 项的规范显示名字段(nickName/xtmc/label…),非 dictType 这类分类键
            if disp != id_vk and not _is_idlike(disp) and _is_scalar(it.get(disp)) and str(it.get(disp)) == sv:
                conf = any(_name_match(sv, s) for s in sample_vals)
                if name_cand is None or (conf and not name_cand[4]):
                    name_cand = ("name", id_vk, disp, sv, conf, it)
                if conf:
                    break
        cvk = next((k for k, v in it.items() if _is_scalar(v) and str(v) == sv and _is_idlike(k)), None)
        if cvk is None and len(it) <= 4:                 # 小项允许值字段名不带 id/code(不写死字段名)
            cvk = next((k for k, v in it.items() if _is_scalar(v) and str(v) == sv), None)
        if cvk is not None:                              # CODE:sv 命中 id 类字段 + 项里另有独立文字标签
            clk = _pick_label_key(it, cvk)
            label = str(it.get(clk, "")).strip()
            if clk != cvk and label:
                conf = any(_name_match(label, s) for s in sample_vals)
                if code_cand is None or (conf and not code_cand[4]):
                    code_cand = ("code", cvk, clk, label, conf, it)
    cands = [c for c in (name_cand, code_cand) if c]
    if not cands:
        return None
    cands.sort(key=lambda c: (c[4], c[0] == "name"), reverse=True)   # 确认命中优先;其次 name(更贴近人选)
    best = cands[0]
    mode, _vk, _lk, _label, conf, _it = best
    if conf:                                             # 录制选项佐证 → 强证据,直接采纳
        return best
    # 系统化:小列表 + 候选是「枚举形态」(value_key/labels 形如 dictValue/dictLabel/value/label/code 而不是 id/name/person) +
    # value 恰好等于某候选的 cvk 值 → 视为可信命中,即使 unconfirmed(治「请假类型=1」短码撞状态字典而被「短串」拒)。
    # 关键防误伤:必须 value_key 是 **enum 形**(dictValue/value/code),不是 `id`/`name` 等通用字段名。
    # 系统化:小列表 + 候选**看起来是枚举形态**(value_key 是值态、label_key 是短文字标签)**且**
    # candidate 列表整体形态像 person/org/tenant —— 才排除。
    #   用以治「请假类型=1」短码撞状态字典而被「短串」规则误拒。
    # 关键防误伤:候选**同时**满足 ① label_key 是「name/realname/deptname」类人员字段名
    #                       ② label 值是「张三/研发中心」类具体人/部门词
    #            才认为它**不是**枚举列表,而是 user/dept/org 列表;
    # 仅满足其一不排除(否则会把字典里的「name」label 误伤)。
    is_small_aligned = bool(small) and _vk and _lk \
                       and _enum_like_key(_vk) and _enum_like_key(_lk) \
                       and not (_looks_people_or_org_key(_lk) and _looks_people_or_org_label(_label)) \
                       and any(str(it.get(_vk)) == sv for it in items if isinstance(it, dict))
    if not (len(sv) >= 2 or small) and not is_small_aligned:
        return None
    if mode == "code" and sv in sample_vals and not path_is_assignee and not is_small_aligned:
        # 用户亲手填的值当码 → 自由文本,拒(治"1"撞状态字典)
        # 审批人/选人容器下豁免;小列表对齐(短码真在枚举里)也豁免
        return None
    if mode == "name" and not small:                     # 未确认的名选只在小列表认(大列表巧合多)
        return None
    return best


# 系统化:value/label 字段名形如「枚举」(dictValue/dictLabel/value/code/dict_type/dict_type_id)。
# 关键:**不**包含通用 `id`、`name`——这两太常见,在非枚举列表(用户/部门)里也出现,
# 容易错认成 enum。不包 `code` 也容易撞通用字段名但**保留**(字典 status/code 是常见枚举)。
# 字段形态用「判定字段名」做数据形态过滤,不绑具体业务。
_ENUM_LIKE_VALUE_KEYS = ("value", "valuecode", "dictvalue", "dict_value",
                        "dicttype", "dict_type",
                        "type", "types", "kind", "category", "status",
                        "state", "level", "code", "id",
                        "no", "num", "number")
# 系统化:`name` 也常作 enum label(若依/OA 系统常见 `name`/`labelName`/`text`/`title` 当 label),
# 配合 `_looks_people_or_org_key`/`_looks_people_or_org_label` 兜底排除人/部门列表。
_ENUM_LIKE_LABEL_KEYS = ("label", "labelname", "dictlabel", "dict_label",
                         "text", "title", "caption", "typename",
                         "name", "displayname", "showname", "description")


def _enum_like_key(k: str) -> bool:
    """候选里 value 或 label 字段名是否形如「枚举」(dictValue/dictLabel/value/code)—
    不绑具体业务;通用字典响应里前端的约定俗成。
    刻意**不**包含 `id`/`name`/`userId`/`deptId`——它们太常见,在无关 user/org/tenant 列表里也出现,
    易把无关 user/dept 列表误当 enum 命中(系统化不误伤的保证)。
    """
    if not k:
        return False
    kl = k.lower()
    if any(h in kl for h in _ENUM_LIKE_VALUE_KEYS):
        return True
    if any(h in kl for h in _ENUM_LIKE_LABEL_KEYS):
        return True
    return False


# 系统化:人员/部门/组织/职位 字段名 —— 当作人员/实体列表,不当 enum。
# 通用:对中英文 OA 通用,只要字段名命中这些形态词就不当 enum label。
_PEOPLE_ORG_LABEL_KEYS = ("name", "username", "realname", "fullname", "nickname",
                          "deptname", "orgname", "unitname", "companyname",
                          "tenantname", "teamname", "rolename", "position",
                          "title_label", "displayname", "showname")


def _looks_people_or_org_key(k: str) -> bool:
    """候选 label 字段名是否像「人/组织/职位」——不当 enum label 形态(避免 user/dept 列表误命中)。"""
    if not k:
        return False
    kl = k.lower().replace("_", "")
    return any(h.replace("_", "") in kl for h in _PEOPLE_ORG_LABEL_KEYS)


def _looks_people_or_org_label(label: str) -> bool:
    """label 值看着像「人/部门/组织/职位」——不当 enum label 形态(避免 user/dept/org 列表误命中)。
    启发式:
    - 含「状态/类型/性别/等级/审批/意见/启用/停用」等枚举语义词 → 不是人/组织
    - 长度 3+ 且无数字 + 不是状态/类型语义 → 多半是描述(人名/部门/组织)3-8 字
    通用,不绑具体业务系统,纯形态判定。
    """
    if not label:
        return False
    s = str(label).strip()
    if not s:
        return False
    if len(s) > 30:
        return False  # 长文本一定不是 enum label
    if s[0].isdigit() or s.replace(" ", "").replace("-", "").replace("_", "").isdigit():
        return False
    # 含枚举语义词 — 直接不当 enum
    enum_words = ("状态", "类型", "类别", "等级", "性别", "方式", "审批",
                    "意见", "结果", "级别", "方向", "模式", "办法", "原因",
                    "enabled", "disabled", "active", "inactive", "pending",
                    "approved", "rejected", "open", "closed", "yes", "no")
    s_norm = s.lower().replace(" ", "")
    if any(w in s_norm for w in enum_words):
        return False
    # 长度 ≥ 3 且无数字特征 → 多半是描述性词(人名/部门/组织)
    if len(s) >= 3 and not any(c in s for c in "0123456789"):
        return True
    return False


def _sample_values_for_leaf(path: str, sv: str, samples: dict | None, field: dict | None = None) -> set[str]:
    """只取当前字段自己的录制佐证值，避免短码字段被其它字段的下拉样例误确认。"""
    out = {str(sv)} if sv not in (None, "") else set()
    leaf = path.split(".")[-1].split("[")[0]
    keys = {path, leaf}
    if field:
        keys.update(str(field.get(k) or "") for k in ("key", "suggest_name", "path"))
    for k, v in (samples or {}).items():
        if v in (None, ""):
            continue
        sk = str(k)
        if sk in keys or any(_name_match(sk, kk) for kk in keys if kk):
            out.add(str(v))
    return out


def suggest_selects(post_data: str | None, reads: list[dict], samples: dict | None = None,
                    skip_paths: list[str] | None = None,
                    fields: list[dict] | None = None) -> list[dict]:
    """提交体里"对应某候选列表项"的字段 → 绑 select(Agent 传名字/文字→运行期查内部 ID)。

    两形态(通用,不挑系统):① **单码字段**(type=2↔字典 value=2、approverId=12↔user/list:字段存码,agent 传名)
    ② **名/ID 配对**(yyxtmc=显示名 + 兄弟 yyxtid=内部 id:两字段一次选定)→ 输出 id_path/id_tokens,运行期
    解析后**同时**写回显示名字段与配对 id 字段(换一个选项时 id 不再冻结成录制值)。
    **录制样例(samples)= 字段级消歧器**:候选显示名 == 当前字段自己的录制选中值 →「确认命中」强证据。
    无佐证时:短码(<2)只在小列表认、用户亲填值不当码、同源未确认命中 >3 个按通用字典整源丢弃。
    skip_paths:已被**列表多选**(suggest_list_selects)整体接管的对象数组路径 → 其下逐元素叶子不再单独绑
    (修"选了多个人却被拆成 participants[0].userId 冻死、只认最后一个"的根因)。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    leaves = _leaf_paths(body)                            # [(path, tokens, sv, raw)]
    skip_pref = tuple((p + "[") for p in (skip_paths or []))   # 数组多选路径下的逐元素叶子(participants[0]…)整体跳过
    if skip_pref:
        leaves = [lf for lf in leaves if not lf[0].startswith(skip_pref)]
    use_field_scope = fields is not None
    field_by_path = {str(f.get("path") or ""): f for f in (fields or []) if f.get("path")}
    all_sample_vals = {str(v) for v in (samples or {}).values() if v not in (None, "")}
    by_path: dict[str, list[dict]] = {}                   # path → 跨**所有** read 源的候选绑定(供择优)
    for r in reads:
        items = as_list_payload(r.get("json"))
        if not items or not isinstance(items[0], dict):
            continue
        small = len(items) <= _SMALL_LIST
        hits: list[dict] = []
        for path, toks, sv, raw in leaves:
            if not sv or _is_const_value(raw):           # 系统常量(流程键/uuid/雪花)不作"按名字选"的下拉参数
                continue
            sample_vals = (_sample_values_for_leaf(path, sv, samples, field_by_path.get(path))
                           if use_field_scope else all_sample_vals)
            m = _match_select(sv, items, sample_vals, small,
                              path_is_assignee=_is_assignee_path(path))
            if m is None:
                continue
            mode, vk, lk, label, confirmed, item = m
            # 来源是"按类目聚合的全量字典"(若依 dict_data:同码跨 dictType 重复)→ 整表不是这个字段的选项。
            #   · 已确认(录制选中文字命中某项)→ 按命中项的**分类键**收窄成真正属于该字段的选项(治"请假类型
            #     绑到 1431 项含歌词模式/OpenAI/档案/银行…的全量字典");并把分类过滤随 select 走,运行期同样收窄。
            #   · 未确认 → 无从判定属于哪个类目,绑全量必错 → **不绑**(字段退回普通参数,诚实不瞎给选项)。
            cat_key = _aggregate_category(items, vk, lk)
            cat_val = None
            opt_items = items
            if cat_key is not None:
                if not confirmed:
                    continue
                cat_val = item.get(cat_key)
                if cat_val is not None:
                    opt_items = [it for it in items
                                 if isinstance(it, dict) and str(it.get(cat_key)) == str(cat_val)]
            entry = {"path": path, "tokens": toks, "value": sv, "source_url": r.get("url"),
                     "value_key": vk, "label_key": lk, "label": label, "count": len(opt_items),
                     "_confirmed": confirmed}
            _attach_enum_binding(
                entry,
                _enum_records_from_items(opt_items, lk, vk),
                source="api",
                confirmed=confirmed,
            )
            if cat_key is not None and cat_val is not None:     # 分类过滤随 select 走(运行期 list-options / 名→ID 同样收窄)
                entry["category_key"], entry["category_value"] = cat_key, str(cat_val)
            if mode == "name":                           # 名/ID 配对:找兄弟"内部 id"字段(同父 + 值==该项的 id)
                it = next((x for x in items if str(x.get(lk)) == sv), None)
                idval = str(it.get(vk)) if it and it.get(vk) is not None else None
                if idval is not None:
                    par = _parent_path(path)
                    sib = next(((lp, lt) for lp, lt, lsv, _lr in leaves
                                if lp != path and _parent_path(lp) == par and lsv == idval), None)
                    if sib:
                        entry["id_path"], entry["id_tokens"] = sib[0], sib[1]
            hits.append(entry)
        # 同源内防护(沿用):短值数字巧合去重 + 未确认命中 >3 = 通用字典误命中(整源只留确认的)。
        # **审批人/选人容器下的未确认命中不计入**:body 存 user id,佐证清晰,不该被通用字典误命中规则误杀。
        # 确认命中永远保留。
        vcount: dict[str, int] = {}
        for h in hits:
            if h["_confirmed"] or _is_assignee_path(h["path"]):
                continue
            vcount[h["value"]] = vcount.get(h["value"], 0) + 1
        hits = [h for h in hits
                if h["_confirmed"]
                   or _is_assignee_path(h["path"])
                   or not (len(h["value"]) <= 2 and vcount.get(h["value"], 0) >= 2)]
        if len([h for h in hits if not h["_confirmed"] and not _is_assignee_path(h["path"])]) > 3:
            hits = [h for h in hits if h["_confirmed"] or _is_assignee_path(h["path"])]
        for h in hits:
            by_path.setdefault(h["path"], []).append(h)
    # **跨源择优(根治"请假类型绑到 1431 项通用大字典"):每条 leaf 在所有源里选最佳 ——
    #  ① 确认命中(候选显示名==录制选中值)优先 ② 其次列表更小(更专门的字典,而非通用大字典)。**
    out: list[dict] = []
    claimed: set[str] = set()                             # 已被某 select 接管的路径(含配对 id 字段),不重复绑
    order = {p: i for i, (p, _t, _s, _r) in enumerate(leaves)}
    for path in sorted(by_path, key=lambda p: order.get(p, 1 << 30)):
        if path in claimed:
            continue
        best = sorted(by_path[path], key=lambda e: (e["_confirmed"], -e["count"]), reverse=True)[0]
        claimed.add(path)
        if best.get("id_path"):
            claimed.add(best["id_path"])
        out.append({k: v for k, v in best.items() if not k.startswith("_")})
    return out


def _array_object_paths(body) -> list[tuple]:
    """body 里**对象数组**的点路径 → [(path, tokens, elements)];元素须同构 dict(键集一致,≥1 个)。
    供"列表多选"识别(participants[]=选了多个人 → 一个数组里几份同形对象)。通用,不挑系统。"""
    out: list[tuple] = []

    def walk(node, path, toks):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, toks + [k])
        elif isinstance(node, list):
            if node and all(isinstance(e, dict) for e in node):
                keys = set(node[0].keys())
                if all(set(e.keys()) == keys for e in node):
                    out.append((path, list(toks), node))
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", toks + [i])

    walk(body, "", [])
    return out


def suggest_list_selects(post_data: str | None, reads: list[dict] | None,
                         samples: dict | None = None) -> list[dict]:
    """**列表多选**:提交体里"选了多个对象"的数组(participants[]=多个参会人、抄送人…)→ 绑成**一个列表参数**
    (agent 传**名字列表**,运行期每个名字经来源接口换成整份元素 {userId,userName,avatar,type…}),
    而不是把数组拆成 participants[0].userId / [1].userId… 一堆字段(还把前几个冻成固定值)。

    判据(通用,不挑系统/字段):① body 里某对象数组,元素同构、且每个元素有 id 类子键 + 文字子键(像选出来的"实体");
    ② 抓到的某读响应(选人列表)里,**≥1 个元素**能按 id 子键命中其某项 → 认定该数组来自这个多选源;
    ③ 用命中的元素 + 其源项,推出**元素模板**:每个子键来自源项哪个字段(userId←id、userName←nickName、
       头像←avatar),整列一致的子键当常量(participantType=2)。运行期用模板把"名字列表"展开成对象数组。
    录制样例命中元素显示名 → 确认命中(强证据)。返回 [{path, multi, source_url, value_key, label_key,
    element_template, label_subkey, options, count, label, values, _confirmed}]。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    sample_vals = {str(v) for v in (samples or {}).values() if v not in (None, "")}
    out: list[dict] = []
    for path, toks, elems in _array_object_paths(body):
        e0 = elems[0]
        id_subs = [k for k in e0 if _is_scalar(e0.get(k)) and _is_idlike(k)]
        name_subs = [k for k in e0 if isinstance(e0.get(k), str) and e0.get(k).strip() and not _is_idlike(k)]
        if not id_subs or not name_subs:                 # 非"实体多选"(无 id 子键或无文字子键)→ 跳过
            continue
        best = None                                      # (命中数, url, value_key, sub, items, item_by_val, matched)
        for r in reads or []:
            items = as_list_payload(r.get("json"))
            if not items or not isinstance(items[0], dict):
                continue
            item_idks = [k for k in items[0] if _is_scalar(items[0].get(k)) and _is_idlike(k)]
            for sub in id_subs:
                for vk in item_idks:
                    by_val = {str(it.get(vk)): it for it in items if isinstance(it, dict) and it.get(vk) is not None}
                    matched = [e for e in elems if str(e.get(sub)) in by_val]
                    if matched and (best is None or len(matched) > best[0]):
                        best = (len(matched), r.get("url"), vk, sub, items, by_val, matched)
        if not best:
            continue
        _n, src_url, vk, sub, items, by_val, matched = best
        e, it = matched[0], by_val[str(matched[0].get(sub))]
        lk = _pick_label_key(it, vk)
        template: dict = {}                              # 子键 → {"from":"item","item_key":..} | {"const":..}
        for sk in e0:
            if sk == sub:
                template[sk] = {"from": "item", "item_key": vk}
            elif (ikm := next((k for k in it if _is_scalar(it.get(k)) and str(it.get(k)) == str(e.get(sk))), None)) is not None:
                template[sk] = {"from": "item", "item_key": ikm}     # 子键值==源项某字段 → 来自源项
            elif len({str(x.get(sk)) for x in elems}) == 1:
                template[sk] = {"const": e0.get(sk)}                 # 整列一致 → 常量(participantType=2)
            else:
                template[sk] = {"const": e.get(sk)}                  # 兜底(罕见,无源可对又不一致)
        # 可新增行的业务明细表常长得像 [{业务文本, 下拉名, 下拉ID}]。它不是“多选实体列表”；
        # 若折叠整行，会把业务文本冻成录制值。只要行内存在用户填写的标量且不能从候选项推出，就不折叠。
        has_user_scalar_not_from_item = any(
            "from" not in (template.get(sk) or {})
            and _is_scalar(e0.get(sk))
            and str(e0.get(sk)) in sample_vals
            for sk in e0
        )
        has_business_scalar_not_from_item = any(
            "from" not in (template.get(sk) or {})
            and _is_scalar(e0.get(sk))
            and str(e0.get(sk)).strip()
            and not _is_idlike(sk)
            and not _LIST_ROW_CONST_OK_RE.search(str(sk))
            for sk in e0
        )
        if has_user_scalar_not_from_item or has_business_scalar_not_from_item:
            continue
        label_sub = next((sk for sk, m in template.items() if m.get("item_key") == lk), None) or name_subs[0]
        labels = [str(x.get(label_sub, "")).strip() for x in elems if str(x.get(label_sub, "")).strip()]
        confirmed = any(any(_name_match(lb, s) for s in sample_vals) for lb in labels)
        entry = {"path": path, "tokens": list(toks), "multi": True, "source_url": src_url,
                 "value_key": vk, "label_key": lk, "element_template": template,
                 "label_subkey": label_sub,
                 "count": len(items), "label": labels[0] if labels else "",
                 "values": labels, "_confirmed": confirmed}
        _attach_enum_binding(
            entry,
            _enum_records_from_items(items, lk, vk),
            source="api",
            confirmed=confirmed,
        )
        out.append(entry)
    return out


def _page_enum_option_list(opts) -> list:
    """统一 page_enum_options 的取值:旧形态(list)与新形态({options, field_key})都返回 options 列表。"""
    if isinstance(opts, dict):
        return list(opts.get("options") or [])
    return list(opts or [])


def _page_enum_field_key(opts, picked: str) -> str | None:
    if isinstance(opts, dict):
        fk = opts.get("field_key")
        if fk:
            return str(fk)
    return None


def _dom_key_matches_field(dom_key: str, field: dict | None) -> bool:
    if not field:
        return False
    keys = [
        field.get("suggest_name"),
        field.get("key"),
        field.get("path"),
        str(field.get("path") or "").split(".")[-1].split("[")[0],
    ]
    return any(_name_match(dom_key, k) for k in keys if k not in (None, ""))


def apply_page_enum_options(selects: list[dict], page_enum_options: dict | None,
                      post_data: str | None = None, fields: list[dict] | None = None) -> list[dict]:
    """用**录制时下拉里真实可见的选项**覆盖 select 的候选快照 —— 这是枚举地面真值,
    胜过拿提交值去网络字典里猜命中(治"加班类型/请假类型绑到几百项含工作日/档案/银行…的全量字典")。

    page_enum_options 形态向后兼容:
      - 旧:{选中显示值: [选项文字]}
      - 新:{键(显示值/字段 key): {"options": [...], "field_key": 内部字段名}}
    按「选中显示值 ⟺ select 的 label/value/path/字段 key」把 DOM 选项挂上:options/count/option_map、
    标 enum_source=dom,并撤掉按类目收窄。通用,不挑系统。
    """
    if not page_enum_options:
        return selects
    pairs = []
    for k, v in page_enum_options.items():
        opts = _page_enum_option_list(v)
        if opts:
            pairs.append((str(k), opts, _page_enum_field_key(v, str(k)), _page_enum_selected_label(v, str(k))))
    body = _parse_body(post_data) if post_data is not None else None
    value_by_path = {p: sv for p, _t, sv, _raw in _leaf_paths(body)} if body is not None else {}
    field_by_path = {str(f.get("path") or ""): f for f in (fields or []) if f.get("path")}
    for s in selects or []:
        lbl, val = str(s.get("label") or ""), str(s.get("value") or "")
        path = str(s.get("path") or "")
        body_val = value_by_path.get(path, "")
        field = field_by_path.get(path)
        leaf_key = str(path).split(".")[-1].split("[")[0]
        matched = next(((ov, fk, selected) for kv, ov, fk, selected in pairs
                     if _name_match(kv, lbl)
                     or _name_match(kv, val)
                     or _name_match(kv, body_val)
                     or _name_match(selected, lbl)
                     or _name_match(selected, val)
                     or _name_match(selected, body_val)
                     or _name_match(kv, path)
                     or _name_match(kv, leaf_key)
                     or (fk and _name_match(fk, leaf_key))
                     or _dom_key_matches_field(kv, field)
                     or _dom_key_matches_field(selected, field)), None)
        if matched:
            opts, fk, _selected = matched
            records = _records_with_existing_option_map(
                _enum_records_from_page_options(opts),
                s.get("option_map") if isinstance(s, dict) else None,
            )
            _attach_enum_binding(
                s,
                records,
                source="dom",
                confirmed=True,
            )
            if fk:
                s["field_key"] = fk
            s.pop("category_key", None)
            s.pop("category_value", None)
    return selects


def page_enum_selects(post_data: str | None, page_enum_options: dict | None,
                     existing_paths: set | None = None,
                     fields: list[dict] | None = None) -> list[dict]:
    """录到了下拉选项、但提交体里这个字段**没绑上任何来源 select**(纯枚举:body 存的就是显示名)→
    造一个**无来源**枚举 select(options + option_map),agent 传名字时按真实提交值回填。治"页面明明只有 3 个选项,
    却因为匹配不到网络字典而被当普通文本"。通用,不挑系统。page_enum_options 兼容新旧两种形态。"""
    body = _parse_body(post_data)
    if body is None or not page_enum_options:
        return []
    existing = existing_paths or set()
    out: list[dict] = []
    field_by_path = {str(f.get("path") or ""): f for f in (fields or []) if f.get("path")}
    leaves = _leaf_paths(body)
    for picked, opts_raw in page_enum_options.items():
        opts = _page_enum_option_list(opts_raw)
        fk = _page_enum_field_key(opts_raw, str(picked))
        selected = _page_enum_selected_label(opts_raw, str(picked))
        if not opts:
            continue
        # 1)精确匹配:body leaf 值等于显示值(label)
        toks = _find_value_tokens(body, selected)
        if toks is not None:
            path = _tokens_to_str(toks)
        else:
            # 2)field_key/path 子串/leaf key 反查:治"请假类型=病假,但 body 字段是 leaveType=2 的内部码"
            scored = []
            for path, toks, sv, _raw in leaves:
                field = field_by_path.get(path)
                leaf = str(path).split(".")[-1].split("[")[0]
                score = 0
                if fk and (_name_match(fk, path) or _name_match(fk, leaf) or _label_matches_internal_field(fk, path, field)):
                    score += 6
                if _dom_key_matches_field(str(picked), field) or _dom_key_matches_field(selected, field):
                    score += 5
                if _label_matches_internal_field(str(picked), path, field) or _label_matches_internal_field(selected, path, field):
                    score += 4
                if _name_match(str(picked), path) or _name_match(selected, path):
                    score += 2
                if _name_match(str(picked), leaf) or _name_match(selected, leaf):
                    score += 2
                if str(sv).strip() and not any(_name_match(str(sv), o.get("label")) for o in _enum_records_from_page_options(opts)):
                    # 下拉显示名不在 body,body 大概率是内部值;短数字/短码更像枚举值,长文本更像普通输入。
                    score += 2 if _re.fullmatch(r"[A-Za-z0-9_-]{1,8}", str(sv).strip()) else -2
                if score > 0:
                    scored.append((score, path, toks))
            hit = max(scored, default=None, key=lambda x: x[0])
            if hit is None:
                continue
            _score, path, toks = hit
        if path in existing or any(o.get("path") == path for o in out):
            continue
        current = next((sv for p, _t, sv, _raw in leaves if p == path), "")
        entry = {"path": path, "tokens": toks, "source_url": "", "value_key": "", "label_key": "",
                 "label": selected or picked, "value": current, "count": len(opts), "field_key": fk or ""}
        records = _enum_records_from_page_options(opts)
        records = _records_with_recorded_value(records, selected or picked, current)
        _attach_enum_binding(
            entry,
            records,
            source="dom",
            confirmed=True,
        )
        out.append(entry)
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


# BPMN/Flowable「发起人自选审批人」容器键 + 流程节点 ID 形态(通用,不挑租户):
#   提交体里审批人挂在 startUserSelectAssignees / approvers / candidate… 下,叶子键是节点 ID(Activity_xxx)。
_ASSIGNEE_CONTAINER_RE = _re.compile(
    r"(start_?user_?select_?assignees|assignees?|approvers?|candidate(?:users?|groups?)?)", _re.I)


def _is_assignee_path(path: str) -> bool:
    """路径看起来是审批人选人字段(路径在 startUserSelectAssignees/approvers/assignees/Activity_xxx 等容器下)。
    通用,不挑系统。"""
    if not path:
        return False
    if _ASSIGNEE_CONTAINER_RE.search(path):
        return True
    leaf_key = path.split(".")[-1].split("[")[0]
    return bool(_BPMN_NODE_RE.match(leaf_key)) if leaf_key else False


_BPMN_NODE_RE = _re.compile(r"^(activity|usertask|task|node|flow|gateway|sequenceflow|sid)[_-]", _re.I)


# H24 修复:收紧 "^id$" —— 之前全字匹配,会让普通 list 字段(下拉/选人列表的 id 列)值若凑巧是 Activity_xxx 被当 BPMN 节点名建索引,污染 node_names
_NODE_ID_RE = _re.compile(r"(^node_?id$|^task_?id$|^act_?id$|^activity_?id$|^sid$|^element_?id$)", _re.I)
_NODE_NAME_RE = _re.compile(r"(node_?name|task_?name|activity_?name|^name$|label|title|caption)", _re.I)


def _iter_dicts(node):
    """深度遍历出所有 dict 节点(用于在任意嵌套读响应里找"节点ID→节点名"映射)。"""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _bpmn_node_names(reads: list[dict]) -> dict:
    """从抓到的读响应里建 {BPMN 节点 ID → 显示名}(流程定义/下一节点列表常返回 nodeId+nodeName)。

    只认**节点 ID 形态**(Activity_xxx/Task_xxx)的项,避免把普通列表的 id+name 误当节点名。通用,不挑系统。
    """
    idx: dict[str, str] = {}
    for r in reads or []:
        for it in _iter_dicts(r.get("json")):
            nid = next((str(it[k]) for k in it
                        if _is_scalar(it[k]) and _NODE_ID_RE.search(str(k))
                        and _BPMN_NODE_RE.search(str(it[k]))), None)
            if not nid:
                continue
            nm = next((it[k].strip() for k in it
                       if isinstance(it[k], str) and it[k].strip()
                       and _NODE_NAME_RE.search(str(k)) and not _is_idlike(str(k))), None)
            if nm:
                idx.setdefault(nid, nm)
    return idx


def suggest_assignee_names(post_data: str | None, reads: list[dict] | None,
                           samples: dict | None = None) -> dict:
    """给 BPMN/Flowable「发起人自选审批人」字段配**人类参数名**(治"审批人参数名退回 Activity_09dlq0g 内部节点 ID")。

    判据(通用,不挑租户):叶子键是流程节点 ID(Activity_xxx)或路径挂在 startUserSelectAssignees/approvers 容器下。
    名字来源(择优):① 抓到的流程定义读响应里有 节点ID→节点名 → 用节点名(如"领导审批""人力审批",**理解后命名**);
       ② 否则按节点出现序给"审批人1/审批人2"(只有一个审批节点就"审批人")。返回 {body点路径 → 建议参数名}。
    """
    body = _parse_body(post_data)
    if body is None:
        return {}
    node_names = _bpmn_node_names(reads or [])
    cands: list[tuple] = []                                   # (path, node_id or None)
    for path, _toks, _sv, _raw in _leaf_paths(body):
        leaf_key = path.split(".")[-1].split("[")[0]
        is_node = bool(_BPMN_NODE_RE.search(leaf_key))
        if not (is_node or _ASSIGNEE_CONTAINER_RE.search(path)):
            continue
        cands.append((path, leaf_key if is_node else None))
    nodes: list = []
    for _p, nid in cands:                                    # 去重保序:几个审批节点
        if nid not in nodes:
            nodes.append(nid)
    multi = len(nodes) > 1
    out: dict[str, str] = {}
    for path, nid in cands:
        if nid and nid in node_names:                        # 流程定义里有节点名 → 直接用("领导审批")
            out[path] = node_names[nid]
        else:
            out[path] = f"审批人{nodes.index(nid) + 1}" if multi else "审批人"
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


def suggest_identity(post_data: str | None, storage_state: dict | None,
                     samples: dict | None = None) -> list[dict]:
    """提交体里"等于登录态里某值"的字段(如 applicantId=当前用户)→ 建议标 identity(运行期重取,不冻结)。

    防误判(通用,不挑系统):**用户亲手填的值不算 identity** —— sv 是录制样例(用户填的)就是参数,
    即便它恰好等于某会话标量(如二级内设机构=2 撞会话 roleLevel=2、职能描述=3 撞 orgType=3),也不冻结成会话值。
    真 identity(applicantId=当前用户 id 等)是系统自动带上的、用户没填,故不在样例里,照常识别。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    scal = _storage_scalars(storage_state)
    typed = {str(v) for v in (samples or {}).values() if v not in (None, "")}
    out: list[dict] = []
    for path, toks, sv, _raw in _leaf_paths(body):
        if not sv or sv not in scal:
            continue
        if sv in typed:                                  # 用户填的值 → 参数,不是会话身份值(治 ercsmc=2/qzms=3 误判)
            continue
        out.append({"path": path, "tokens": toks, "value": sv, "source": scal[sv]})
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
    (噪声如心跳/字典/自动存草稿都不含用户填的值)。登录/鉴权写请求按内容排除。

    H17 修复:全 auth 过滤时 last_write=None(无 fallback)→ 退化到第一个写请求(避免前端选不到 submit,只能用户手工指定);
    若 requests 列表本身空,返回 None(由调用方处理空状态)。
    """
    sample_vals = {str(v) for v in samples.values() if v not in ("", None)}
    best, best_score, last_write = None, -1, None
    first_write = None                                # H17:兜底用 —— 全过滤时仍能返回一个候选
    for r in requests:
        if (r.get("method") or "").upper() not in _WRITE:
            continue
        if first_write is None:
            first_write = r
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
    # 优先级:① best 命中用户值(score>0) ② last_write(非 auth 业务写,可能无用户值) ③ first_write(全过滤兜底) ④ None
    if best is not None and best_score > 0:
        return best
    if last_write is not None:
        return last_write
    return first_write                                 # H17:全 auth / 全过滤时退化到第一个写


def suggest_workflow_steps(writes: list[dict], samples: dict) -> list[int]:
    """**自动建议**哪些写请求组成业务流程、及顺序(确定性,"提交锚点 + 数据依赖闭包")。

    锚点=提交那条(带最多用户值);从它**回溯**纳入"其响应喂给已纳入步 body"的更早写请求(taskId 等串联);
    按录制序排(源在前、提交最后)。不在依赖链上的(聊天/改旧实体/无关写)= 噪声,不纳入。
    返回 writes 里的全局下标(有序);单条或无依赖时只返回提交那条。通用,不挑系统。"""
    biz = []
    for i, w in enumerate(writes):
        if (w.get("method") or "").upper() not in _WRITE:
            continue
        body = _parse_body(w.get("post_data"))
        if body is None or looks_like_auth_write(w.get("url") or "", body):   # 排除登录/鉴权/基建写
            continue
        if looks_like_read_request(w.get("url") or "", w.get("post_data")):    # 排除 POST 形态的读/查询(下拉/列表源)
            continue
        biz.append((i, w))
    if not biz:
        return []
    submit = pick_submit_request([w for _i, w in biz], samples)
    sub_pos = next((k for k, (_i, w) in enumerate(biz) if w is submit), len(biz) - 1)
    deps: dict[int, set] = {}                          # 目标步 ← 来源步(相对 biz 的下标,源响应喂目标 body)
    for lk in discover_step_links([w for _i, w in biz]):
        deps.setdefault(lk["target_step"], set()).add(lk["source_step"])
    included, stack = set(), [sub_pos]                 # 从提交回溯依赖闭包
    while stack:
        p = stack.pop()
        if p in included:
            continue
        included.add(p)
        stack.extend(deps.get(p, ()))
    # 依赖闭包之外,也纳入**含用户填写值**的业务写(它们是有意的流程步,非噪声;无值无依赖的才丢)
    sample_vals = {str(v) for v in (samples or {}).values() if v not in ("", None)}
    for pos, (_gi, w) in enumerate(biz):
        if pos not in included:
            b = _parse_body(w.get("post_data"))
            if b and (sample_vals & set(_values(b))):
                included.add(pos)
    ordered = sorted(included, key=lambda p: (p == sub_pos, p))   # 提交最后,其余按录制序(≈依赖序)
    return [biz[p][0] for p in ordered]


def parameterize_request(req: dict, samples: dict, base_url: str = "") -> dict | None:
    """把请求体里"等于用户样例值"的字段替换成 {{字段}} 占位;内部 ID/常量保持原样。

    返回 {method, path, body_template(占位后的JSON), params:[字段], sample_inputs, content_type}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    val2fields: dict[str, list[str]] = {}
    for k, v in (samples or {}).items():
        if v in ("", None):
            continue
        val2fields.setdefault(str(v), []).append(k)
    params: dict[str, str] = {}

    def walk(node, provenance: list | None = None, path: str = ""):
        if isinstance(node, dict):
            return {k: walk(v, provenance, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(x, provenance, f"{path}[{i}]") for i, x in enumerate(node)]
        sv = str(node)
        if val2fields.get(sv):                         # 这个值是用户填的 → 变参数；同值字段按叶子顺序逐个消费
            f = val2fields[sv].pop(0)
            params[f] = sv
            if provenance is not None:
                provenance.append({"path": path, "field": f, "kind": "user_input", "source": "sample"})
            return "{{" + f + "}}"
        # H15 修复:常量/内部 ID 也记入 provenance,审计可追溯"为什么这个字段没被参数化"
        if provenance is not None and path:
            provenance.append({"path": path, "field": None, "kind": "constant", "source": "raw_value"})
        return node                                    # 内部 ID/常量 → 原样保留

    provenance: list[dict] = []                       # H15:全字段溯源数组,前端/修复期可见
    templ = walk(body, provenance)
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
            "sample_inputs": params, "auth_headers": extract_auth_headers(req.get("headers")),
            "provenance": provenance}                  # H15 修复:全字段溯源(用户值+常量都记),供接口图全追溯


# key 像内部标识(默认不当参数):以 id/key/code/token/... 结尾
_ID_KEY = _re.compile(r"(id|key|code|token|uuid|guid|seq|no|flag|status)$", _re.I)
# key 像日期/时间(即便值是 13 位毫秒时间戳,也该当参数,不能被"长数字"规则误判成常量)
# H11 修复:中文 OA 系统常用「创建时间/更新时间/申请时间」等,必须一并识别 —— 否则会按"长数字常量"漏判成系统常量,运行期写入录制时刻
_TIME_KEY = _re.compile(
    r"(time|date|day|start|end|begin|expire|deadline|datetime"
    r"|创建时间|更新时间|修改时间|提交时间|申请时间|开始时间|结束时间|生效时间|失效时间|过期时间|操作时间|发起时间|审批时间|完成时间|截止时间)",
    _re.I,
)


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


# 系统**自动写入**的时间戳 key(创建/提交/修改/同步…)—— 这类才该运行期填 now;
# **用户挑选的日期**(startTime/endTime/beginTime/applyDate/leaveDate…)绝不在此列,不能被 now 覆盖(否则改坏用户选的日期)。
_SYS_TIME_KEY = _re.compile(
    r"(create|created|submit|submitted|update|updated|modif|gmt|insert|record|audit|oper|sync|"
    r"add_?time|reg_?time|log_?time|last_?time"
    # H12 修复:中文 OA 系统常把系统写入时间命名为「创建时间/更新时间」,若同时是 13 位时间戳,应判为系统时间(运行期填 now),不参数化
    r"|创建时间|更新时间|修改时间|提交时间|操作时间|发起时间)", _re.I)


def _is_system_timestamp(key: str, value) -> bool:
    """系统在提交时**自动写入**的时间戳(submitTime/createTime/updateTime/gmtCreate 等):**系统类时间 key** + 裸 10–13 位时间戳。
    用于三处一致判定:① flatten 不参数化 ② build 标 system_values(运行期填 now)③ 检出器不报"焊死会话值"。
    **关键**:只认系统类 key(create/submit/update…);**用户挑的日期(startTime/endTime/beginTime…)不命中** ——
    它们是参数(由 match_label 跨格式对样例命名),绝不能被 now 覆盖。通用,不挑系统。"""
    return bool(_SYS_TIME_KEY.search(key or "")) and bool(_re.fullmatch(r"-?\d{10,13}", str(value if value is not None else "")))


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


# 字段置信度阈值(P1):≥0.90 自动录入;0.70–0.90 建议用户确认;<0.70 不应自动录入(需澄清)
CONF_AUTO = 0.90
CONF_CLARIFY = 0.70


def confidence_tier(c: float) -> str:
    """置信度 → 路由:auto(自动录)/ clarify(建议用户确认)/ reject(不自动录,需澄清)。"""
    if c >= CONF_AUTO:
        return "auto"
    if c >= CONF_CLARIFY:
        return "clarify"
    return "reject"


def _field_confidence(label, confident: bool, key: str, is_param: bool) -> float:
    """对字段语义(名字/含义)的置信度。固定字段不影响调用记高;参数按 DOM 标签佐证强弱与 key 形态评分。
    通用,不挑系统/字段。"""
    if not is_param:
        return 0.95                                # 固定字段:不参数化,语义不影响调用
    if label and confident:
        return 0.96                                # 值唯一对到 DOM 标签 → 高(可自动)
    if label:
        return 0.78                                # 有标签但值有歧义/跨格式对 → 中(建议确认)
    if looks_internal_param_name(key):
        return 0.45                                # 无标签且 key 像内部机器标识(Activity_xxx/hash)→ 低(需澄清)
    return 0.72                                    # 无标签但 key 人类可读 → 中(可勉强自动,建议确认)


# ─────────── P2:活体验证自适应策略(可逆沙箱才硬卡真跑,不可逆只结构验、诚实降级) ───────────
def env_controllability(deploy: dict | None) -> str:
    """环境可控性分级 —— 决定活体验证能否硬卡(可逆沙箱可真发写+撤销;不可逆真发会污染、删不掉)。

    reversible:声明的可逆测试沙箱(env=sandbox/test/staging 或 reversible=True)→ 可真跑+回查+清理;
    irreversible:生产/不可逆(env=prod/live 或 reversible=False)→ 只做结构验,降级 partially_verified;
    unknown:未声明 → **保守当不可逆**(宁可降级,不冒险真发)。通用,不挑系统。"""
    d = deploy or {}
    if d.get("reversible") is True:
        return "reversible"
    if d.get("reversible") is False:
        return "irreversible"
    env = str(d.get("environment") or d.get("env") or "").lower()
    if env in ("sandbox", "test", "staging"):
        return "reversible"
    if env in ("prod", "production", "live"):
        return "irreversible"
    return "unknown"


def capture_verification_plan(deploy: dict | None, api_request: dict) -> dict:
    """录制 skill 该做哪种验证(自适应闸门 = f(可控性 × 回查手段)):

    live:环境可逆 **且** 有 fact_check 回查手段 → 可真跑+事实核查 → 通过即 verified;
    structural:否则只做确定性 self_check → partially_verified(诚实降级,不假装活体验过)。"""
    ctrl = env_controllability(deploy)
    has_fc = bool(api_request.get("fact_check"))
    if ctrl == "reversible" and has_fc:
        return {"mode": "live", "controllability": ctrl, "fact_check": True,
                "reason": "环境可逆且有回查手段 → 可真跑 + 事实核查(verified)"}
    reason = ("环境不可逆/未声明 → 不真发写,避免污染(partially_verified)" if ctrl != "reversible"
              else "缺回查手段(未录「查看记录」步)→ 无法确认业务真生效(partially_verified)")
    return {"mode": "structural", "controllability": ctrl, "fact_check": has_fc, "reason": reason}


def test_data_tag(run_id: str) -> str:
    """活体真跑时给测试单据打的唯一标记 → 便于事后识别/撤销,避免污染真实审批队列。"""
    return f"[DANO-TEST-{run_id}]"


# 危险写概念(整段命中,跨系统通用):删除/驳回/终止/撤销 —— 这类不做自动化录入(代他人删/驳回风险)。
# 只收明确破坏性的词;不收 cancel/abort 等易在合法端点出现的歧义词,避免误伤。
_DANGER_PATH_SEGS = frozenset({"delete", "remove", "destroy", "reject", "terminate", "revoke"})


def looks_dangerous_write(api_request: dict) -> bool:
    """危险写请求识别(确定性,业务相关性门):DELETE 方法,或 URL 路径**整段**命中删除/驳回/终止/撤销概念。
    命中则该录制不应静默自动化(代他人删单/驳回审批等),应拒发让人工处理。通用,不挑系统。"""
    for r in (api_request.get("steps") or [api_request]):
        if (r.get("method") or "").upper() == "DELETE":
            return True
        url = r.get("url") or r.get("path") or ""
        path = urlparse(url).path if str(url).startswith("http") else str(url)
        segs = {s for s in _re.split(r"[^a-zA-Z0-9]+", path.lower()) if s}
        if segs & _DANGER_PATH_SEGS:
            return True
    return False


def classify_request_role(req: dict) -> dict:
    """请求语义角色(**确定性**,node 4 语义分类):method + 路径段 + 内容 → {semanticRole, sideEffect, riskLevel}。
    跨系统通用、零业务字面量;供录入去噪/审计标注。比 LLM 分类更稳(且不占录制热路径)。"""
    method = (req.get("method") or "GET").upper()
    if looks_dangerous_write(req):
        return {"semanticRole": "destructive", "sideEffect": "delete", "riskLevel": "L4"}
    url = req.get("url") or req.get("path") or ""
    if looks_like_auth_write(url, req.get("post_data")):
        return {"semanticRole": "auth", "sideEffect": "none", "riskLevel": "L1"}
    path = (urlparse(url).path if str(url).startswith("http") else str(url)).lower()
    segs = {s for s in _re.split(r"[^a-zA-Z0-9]+", path) if s}
    if method not in _WRITE:
        role = "enum_options" if (segs & {"list", "options", "dict", "select", "candidates"}) else "query"
        return {"semanticRole": role, "sideEffect": "read", "riskLevel": "L1"}
    role = ("workflow_submit" if (segs & {"submit", "start", "apply", "create", "flow", "process", "task"})
            else "business_write")
    return {"semanticRole": role, "sideEffect": "write", "riskLevel": "L3"}


# 提交锚点路径段(P0-2 强信号:用户主动提交触发,优先级高于普通 business_write)
_SUBMIT_PATH_SEGS = frozenset({"submit", "save", "send", "create", "apply", "start", "flow", "process", "task",
                               "chat", "complete", "finish", "publish"})
# 静态资源 / 长连接 / 噪声(扩展名命中即丢)
_NOISE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".woff", ".woff2", ".ico",
               ".map", ".ttf", ".mp4", ".mp3", ".wav", ".zip", ".rar")
# 业务 GET 响应形态关键词(返回值是单值/对象,不是列表 → 像 getappid 这类)
_OBJECT_VALUE_KEYS = ("code", "value", "data", "appcode", "config", "result", "id")


def _looks_graphql_request(req: dict) -> bool:
    url = str(req.get("url") or req.get("path") or "").lower()
    if "graphql" in url:
        return True
    payload = req.get("post_data")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            payload = {}
    if not isinstance(payload, dict):
        return False
    query = str(payload.get("query") or "").lstrip()
    return query.startswith(("query", "mutation", "subscription")) or query.startswith("{")


def classify_network_request(req: dict) -> dict:
    """P0-2:把每条网络请求翻译成 {role, keep, reason, confidence},供前端 + 后续 P0-3 依赖闭包使用。

    复用既有 classify_request_role / looks_dangerous_write / looks_like_read_request / as_list_payload,加层薄壳
    转成 P0-2 约定的角色枚举 + keep/reason/confidence 字段。**单请求判定**:跨请求的"业务 GET 是否被后续 step
    引用"留给 P0-3 依赖闭包,本函数只判单条本身。

    输出:
        role ∈ {auth, noise, read_option, read_context, business_get, business_write,
                 submit_anchor, post_submit_verify, destructive, unsupported_upload, unknown}
        keep ∈ {True, False}: 是否进入主流程步骤(destructive / noise / auth / read_option 都不进)
        reason: 给人看的一句话解释
        confidence: 0~1,0.9+ 高置信,0.7~0.9 中等,<0.7 让人工确认
    """
    method = (req.get("method") or "GET").upper()
    url = req.get("url") or req.get("path") or ""
    pd = req.get("post_data")
    resp = req.get("response_json")

    # 1) 静态资源 / 长连接 → noise(最低优先级,但最高效)
    ul = (url or "").lower()
    if any(ul.endswith(ext) for ext in _NOISE_EXTS):
        return {"role": "noise", "keep": False,
                "reason": "静态资源/二进制,非业务接口", "confidence": 0.99}
    if any(n in ul for n in _READ_NOISE):
        return {"role": "noise", "keep": False,
                "reason": "长连接/SSE/心跳,非业务接口", "confidence": 0.95}

    ct = (req.get("content_type") or req.get("headers", {}).get("content-type") or "").lower()
    path_segs = {s for s in _re.split(r"[^a-zA-Z0-9]+", (urlparse(url).path or "").lower()) if s}
    if ct.startswith("multipart/") or path_segs & {"upload", "file", "files", "attachment", "attachments"}:
        return {"role": "unsupported_upload", "keep": False,
                "reason": "文件/附件上传请求已放行真发；当前 FlowSpec 暂不自动复用 multipart 文件内容",
                "confidence": 0.96}
    if _looks_graphql_request(req):
        return {"role": "unsupported_graphql", "keep": False,
                "reason": "GraphQL 请求可能包含多操作与动态 selection set；当前 FlowSpec 暂不自动复用",
                "confidence": 0.92}

    # 2) 危险写:DELETE 或路径含 delete/remove/destroy/reject/terminate/revoke
    if looks_dangerous_write(req):
        return {"role": "destructive", "keep": False,
                "reason": "DELETE 或路径含删除/驳回/终止/撤销概念,代他人操作风险高",
                "confidence": 0.95}

    # 3) 登录/鉴权/基建写
    if looks_like_auth_write(url, pd):
        return {"role": "auth", "keep": False,
                "reason": "URL 或请求体命中登录/鉴权/凭证概念,基建而非业务",
                "confidence": 0.95}

    path = (urlparse(url).path if str(url).startswith("http") else str(url)).lower()
    segs = {s for s in _re.split(r"[^a-zA-Z0-9]+", path) if s}

    # 4) GET 路径:细分 read_option / read_context / business_get
    if method not in _WRITE:
        # 4a) 列表型响应 → read_option(下拉/选人/字典源,作为字段来源,不入主流程)
        items = as_list_payload(resp) if resp is not None else None
        if items:
            seg_hint = "路径含 list/options/dict/select/candidates" if (segs & {"list", "options", "dict", "select", "candidates", "tree", "menu", "candidates"}) else "响应为列表"
            return {"role": "read_option", "keep": False,
                    "reason": f"{seg_hint} → 作为下拉/选人/字典源,供字段绑定,不进主流程",
                    "confidence": 0.92}
        # 4b) 单值/对象响应 → 业务 GET(像 getappid 返回 appCode;像 config/getConfig)
        if resp is not None:
            if isinstance(resp, dict) and any(k in resp for k in _OBJECT_VALUE_KEYS):
                return {"role": "business_get", "keep": True,
                        "reason": "响应是单值/对象,可能含业务 ID/配置,留作依赖闭包候选",
                        "confidence": 0.78}
            if not isinstance(resp, (list, dict)):
                return {"role": "read_context", "keep": True,
                        "reason": "GET 返回非结构化值(可能是用户档案/上下文),保留备查",
                        "confidence": 0.65}
        # 4c) 兜底:无响应或响应非业务形态 → 未知 GET(给中低置信,等 P0-3 依赖闭包裁决)
        return {"role": "read_context", "keep": False,
                "reason": "GET 但响应未落地或非业务形态(可能上下文/初始化查询),不进主流程",
                "confidence": 0.5}

    # 5) POST/PUT/PATCH:细分 submit_anchor / business_write / read_option
    # 5a) POST 形态的读/查询(getXxxList/queryXxx)→ 不当业务写
    if method == "POST" and looks_like_read_request(url, pd):
        return {"role": "read_option", "keep": False,
                "reason": "POST 路径动词是 get/query/list/search(下拉/列表源)",
                "confidence": 0.9}
    # 5b) 路径含 submit/save/send/create/apply/start 等 → submit_anchor 候选
    if segs & _SUBMIT_PATH_SEGS:
        return {"role": "submit_anchor", "keep": True,
                "reason": f"路径含提交动作({','.join(sorted(segs & _SUBMIT_PATH_SEGS))}) → 主流程锚点",
                "confidence": 0.88}
    # 5c) 兜底业务写
    return {"role": "business_write", "keep": True,
            "reason": "写请求(POST/PUT/PATCH)且不属于登录/查询/危险,默认进入业务步骤",
            "confidence": 0.7}


def validate_goal(goal: dict, api_request: dict) -> list[str]:
    """Goal 完整性门(**确定性,不信 LLM 自说**):intent 非空、required_inputs 有来源(∈实际参数,
    防 LLM 臆造)、success_criteria 可验证(非空)、forbidden_actions 已明确、risk_level 已识别。
    返回违规清单(空=通过)。通用,不挑系统/业务。"""
    out: list[str] = []
    g = goal or {}
    if not str(g.get("intent") or "").strip():
        out.append("Goal.intent 为空(业务意图不清)")
    params = set(api_request.get("params") or [])
    if not params and api_request.get("steps"):
        params = set(((api_request["steps"][-1] or {})).get("params") or [])
    ungrounded = [r for r in (g.get("required_inputs") or []) if r not in params]
    if ungrounded:
        out.append(f"Goal.required_inputs 含无来源项(不在实际参数里,疑似 LLM 臆造):{ungrounded}")
    if not (g.get("success_criteria") or []):
        out.append("Goal.success_criteria 为空(成功标准无法验证)")
    if not (g.get("forbidden_actions") or []):
        out.append("Goal.forbidden_actions 未明确(未声明禁止的危险动作)")
    if not str(g.get("risk_level") or "").strip():
        out.append("Goal.risk_level 未识别")
    return out


def merge_llm_field_names(fields: list[dict], llm_names: dict) -> list[dict]:
    """把 LLM 提议的字段中文名**只**补到「确定性没把握」的字段上(suggest_name 仍等于原始 key);
    确定性已确信的名字(值对到 DOM 标签)**绝不覆盖**。打 `name_source="llm"` 标记。通用,不挑系统。"""
    if not llm_names:
        return fields
    for f in fields:
        key = f.get("key")
        proposed = llm_names.get(key) or llm_names.get(f.get("path"))
        if proposed and f.get("suggest_name") == key and str(proposed).strip() and str(proposed).strip() != key:
            f["suggest_name"] = str(proposed).strip()
            f["name_source"] = "llm"                  # 标明此名是 LLM 提议(供前端区分/用户确认)
    return fields


def goal_needs_confirmation(goal: dict | None) -> bool:
    """写操作(L3+)的 Goal **必须经用户确认**才发布 —— LLM 自信但错代价最高,这是唯一不可跳过的人工关。
    风险未识别也保守要求确认。"""
    rl = str((goal or {}).get("risk_level") or "").upper()
    return rl in ("", "L3", "L4", "L5")


def flatten_body(post_data: str | None, samples: dict | None = None,
                 required_labels: set | None = None,
                 collapse_paths: list[str] | None = None) -> list[dict]:
    """把请求体拍平成叶子字段列表 + 参数建议,供前端勾选。任意嵌套(dict/list)→ 点路径。

    suggest_name=字段中文名(录制时的 DOM 标签),**只在能确定时给**(文本按值对;日期跨格式对毫秒戳↔显示),
    对不上就退回原始 key(诚实,不瞎猜)。同值字段(都填 123123123)按录制顺序各取一个标签,不抢同一个。
    type=字段类型(值推断);required=表单 * 必填(label 命中 required_labels)。
    collapse_paths:**列表多选**接管的对象数组路径(participants…)→ 其下逐元素叶子折叠成**一个**列表参数字段
    (type=list-enum),前端只见一个"选多个人"的参数,不再是 participants[0].userId… 一堆。
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

    # 先收集所有叶子(保录制序),再**两遍配样例**:真业务字段(非内部 id/状态)先认领样例值,内部标识字段
    # (id/code/status…)只认领剩余 —— 避免系统字段(processStatus=4)抢走真字段(备注=4)同值的样例标签。
    leaves: list[tuple] = []          # (path, key, node, sv, time_like, internal)

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
            time_like = bool(_TIME_KEY.search(key))
            internal = (not time_like) and (bool(_ID_KEY.search(key)) or _is_const_value(node))
            leaves.append((path, key, node, sv, time_like, internal))

    walk(body, "")
    labels: dict[int, tuple] = {}
    for i, (_p, _k, _n, sv, _tl, internal) in enumerate(leaves):   # ① 真业务字段先认领样例
        if not internal:
            labels[i] = match_label(sv)
    for i, (_p, _k, _n, sv, _tl, internal) in enumerate(leaves):   # ② 内部标识字段认领剩余(不与真字段抢同值)
        if internal:
            labels[i] = match_label(sv)

    out: list[dict] = []
    for i, (path, key, node, sv, time_like, internal) in enumerate(leaves):
        label, confident = labels[i]
        # 系统生成的时间戳(submitTime/createTime/updateTime 等):**系统类时间 key** + 裸时间戳 + 对不上任何录制样例
        #  → 系统在提交时自动写入、用户没填 → 当常量不参数化(运行期 build 标 system_values 填 now,而非焊死)。
        #  判据用系统类 key(create/submit/update…),**用户挑的日期(startTime/endTime…)不命中**,
        #  且即便对不上样例也只当参数、绝不被 now 覆盖。用户真选的日期另经 match_label 跨格式对样例命名。
        sys_time = label is None and _is_system_timestamp(key, sv)
        const = sys_time or internal
        is_param = bool(label is not None or (not const and sv != ""))
        conf = _field_confidence(label, confident, key, is_param)
        # 必填判定(默认必填,写操作宁多勿漏;自动判,免手动勾选):
        #  · 非参数(常量/内部 id)→ 非必填(本就不是用户要填的项)
        #  · 表单确实区分了必填(抓到 * 标记,required_labels 非空)且本字段被**确信**映射到某 DOM 标签
        #    → 信表单:标了 * 才必填,没标 * 即可选
        #  · 其余(表单没区分 / 映射不确信)→ 默认必填(不敢判可选,宁多勿漏)
        if not is_param:
            required = False
        elif required_labels and label is not None and confident:
            required = label in required_labels
        else:
            required = True
        out.append({"path": path, "key": key, "value": sv,
                    "suggest_param": is_param,
                    "suggest_name": label or key,            # 对不上 → 退原始 key(不瞎猜)
                    "type": _infer_type(node, key),           # 字段类型(值推断),给 agent/契约
                    "confidence": conf,                       # 字段语义置信度(P1)
                    "confidence_tier": confidence_tier(conf),  # auto / clarify / reject(需澄清)
                    "required": required,
                    "system_value": bool(sys_time)})          # 系统运行期自动填(submitTime/createTime),前端可标
    # 列表多选:把每个被接管的对象数组的逐元素叶子,折叠成**一个**列表参数字段(原位插回,前端只见一个参数)
    for ap in (collapse_paths or []):
        pref = ap + "["
        idxs = [i for i, f in enumerate(out) if f["path"].startswith(pref)]
        if not idxs:
            continue
        pos = idxs[0]
        out = [f for f in out if not f["path"].startswith(pref)]
        key = ap.split(".")[-1].split("[")[0]
        out.insert(min(pos, len(out)),
                   {"path": ap, "key": key, "value": "", "suggest_param": True,
                    "suggest_name": key, "type": "list-enum", "confidence": 0.78,
                    "confidence_tier": "clarify", "required": True, "system_value": False})
    return out


def auto_required_fields(post_data: str | None, samples: dict | None, param_map: dict | None,
                         *, form_required_labels: set | None = None,
                         params: list[str] | None = None) -> list[str]:
    """**自动**判定哪些参数必填(免手动勾选,默认全部必填,写操作宁多勿漏)。

    post_data/samples/form_required_labels 同 flatten_body;param_map:{字段点路径→参数名};
    params:最终参数名(给定则按它过滤+排序,适配多步取最后一步的 params)。
    判据复用 flatten_body 的 required(已按"默认必填 + 表单 * 区分时降级可选"算好),
    再经 param_map 把点路径桥到参数名;本请求体里找不到的路径(如多步早期步)默认必填。
    返回必填参数名(有序、去重)。"""
    fl = flatten_body(post_data, samples, form_required_labels)
    path_req = {f["path"]: bool(f.get("required")) for f in fl}
    name_req: dict[str, bool] = {}
    for path, name in (param_map or {}).items():
        r = path_req.get(path, True)
        name_req[name] = name_req.get(name, False) or r       # 多路径绑同名 → 任一必填则必填
    names = list(params) if params is not None else list(dict.fromkeys(name_req))
    return [p for p in names if name_req.get(p, True)]         # 未知参数 → 默认必填


def build_api_request(req: dict, param_map: dict, base_url: str = "",
                      selects: list[dict] | None = None, identity: list[dict] | None = None,
                      typed: dict | None = None) -> dict | None:
    """param_map: {字段点路径 → 参数名}。把这些路径的叶子替换成 {{参数名}},其余原样。

    selects:[{path, source_url, value_key, label_key}](Q2 选领导,path 须在 param_map 里 → 运行期名字→ID);
    identity:[{path, source}](Q1 当前用户/会话值,运行期重取覆盖,不作参数);
    typed:{参数名 → 录制时用户填写值}。仅当某参数的填写值是其叶子的**真子串**(如叶子"请假事由:回家"、填写"回家")
    时,改成段拼接(B2):只参数化那一段、保留常量前后缀。其余情况整值替换(不变)。
    返回 {method, path, url, content_type, body_template, params, sample_inputs, auth_headers, selects, identity}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    params: list[str] = []
    samples: dict[str, str] = {}
    types: dict[str, str] = {}

    def walk(node, path):
        if path in param_map and isinstance(node, (list, dict)):   # 列表多选/整段对象:整个结构=一个参数
            name = param_map[path]                                  # 不递归进元素 → 不再拆 participants[0].userId…
            params.append(name)
            types[name] = "list-enum" if isinstance(node, list) else "object"
            samples[name] = node                                   # 默认值=录制时整份结构(agent 没传则用录制选中项)
            return "{{" + name + "}}"
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if path in param_map:
            name = param_map[path]
            sv = "" if node is None else str(node)
            params.append(name)
            types[name] = _infer_type(node, path.split(".")[-1].split("[")[0])   # 字段类型(值推断)
            rec = str(typed.get(name)) if (typed and typed.get(name) not in (None, "")) else None
            if rec and len(rec) >= 2 and rec != sv and isinstance(node, str) and rec in sv:
                samples[name] = rec                          # B2:填写值是真子串 → 段拼接,保留常量前后缀
                pre, _, post = sv.partition(rec)
                return {_SEG: [s for s in (pre, {"$p": name}, post) if s != ""]}
            samples[name] = sv
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
    # 叶子点路径→tokens(无歧义注入用;键含点也安全)。select/identity 注入都优先用 tokens。
    _leaf_tok = {p: t for p, t, _sv, _raw in _leaf_paths(body)}
    # select 元数据:path 须是参数(在 param_map),记成 param→源/键,运行期按名字查 ID。
    # 名/ID 配对(yyxtmc+yyxtid)额外带 id 字段路径 → 运行期解析后**同时**写回配对 id 字段(不冻结)。
    sel_meta = []
    for s in (selects or []):
        if s.get("path") not in param_map:
            continue
        meta = {"param": param_map[s["path"]], "source_url": s.get("source_url"),
                "value_key": s.get("value_key"), "label_key": s.get("label_key"),
                "options": list(s.get("options") or []),     # 候选选项快照(存进 skill 让 agent 从中选,问题1)
                "count": s.get("count")}
        if s.get("option_map"):
            meta["option_map"] = dict(s.get("option_map") or {})
        if s.get("enum_source"):
            meta["enum_source"] = s.get("enum_source")
        if s.get("enum_confirmed") is not None:
            meta["enum_confirmed"] = bool(s.get("enum_confirmed"))
        if s.get("category_key"):                            # 聚合字典:分类过滤随 select 走 → 运行期同样按类目收窄
            meta["category_key"], meta["category_value"] = s["category_key"], s.get("category_value")
        if s.get("multi"):                                   # 列表多选:整份元素模板随 select 走 → 运行期把名字列表展开成对象数组
            meta["multi"] = True
            meta["element_template"] = s.get("element_template") or {}
            meta["label_subkey"] = s.get("label_subkey")
        if s.get("id_path") or s.get("id_tokens"):
            meta["id_path"] = s.get("id_path")
            meta["id_tokens"] = s.get("id_tokens") or _leaf_tok.get(s.get("id_path"))
        sel_meta.append(meta)
    for s in sel_meta:                                          # 选领导/代码下拉 → 枚举;列表多选 → 列表枚举(传名字列表)
        types[s["param"]] = "list-enum" if s.get("multi") else "enum"
    id_meta = []
    for i in (identity or []):
        if i.get("path") in param_map:        # 同一字段不能既是参数又是 identity:用户既已参数化 → 参数优先,不再运行期覆盖
            continue
        toks = i.get("tokens") or _leaf_tok.get(i.get("path"))
        ev = [f"request://body.{i['path']}"]                  # 证据来源(node 8):该字段在请求体的位置 + 登录态来源
        if i.get("source"):
            ev.append(f"identity://{i['source']}")
        id_meta.append({"path": i["path"], "source": i.get("source", ""), "evidence": ev,
                        **({"tokens": toks} if toks else {})})
    # 系统时间戳(submitTime/createTime 等:时间类 key + 裸时间戳,用户没勾成参数)→ **运行期填 now**,
    # 而非焊死录制时刻(否则每次提交都带过去的旧时间;也不会被检出器当"一次性会话值焊死"而拦发布)。通用,不挑系统。
    system_values = []
    for p, toks, _sv, raw in _leaf_paths(body):
        if p in param_map:
            continue
        if _is_system_timestamp(p.split(".")[-1].split("[")[0], raw):
            system_values.append({"path": p, "tokens": toks,
                                  "kind": "now_ms" if len(str(raw)) == 13 else "now_s"})
    out = {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
           "content_type": req.get("content_type", "application/json"),
           "body_template": templ, "params": list(dict.fromkeys(params)), "sample_inputs": samples,
           "auth_headers": extract_auth_headers(req.get("headers")),
           "field_types": types, "selects": sel_meta, "identity": id_meta,
           "system_values": system_values}
    # P1:把提交请求**自身的响应**学成业务成功约定(code=200/success=true 等)+ 留证据。
    # 单接口即便没有额外 GET 查询读,资产里也有 success_rule → acceptance 能验"业务成功",不再报"无法验证"。
    resp = req.get("response_json")
    if resp is not None:
        out["response_json"] = resp                       # 证据(node 8):提交真实/拦截响应
        sr = infer_success_rule([{"json": resp}])
        if sr:
            out["success_rule"] = sr
    return out


def substitute(template, fields: dict, defaults: dict | None = None):
    """把 body_template 里的 {{字段}} 占位填回。优先用运行期 fields;没传该字段则退回 defaults(录制时的原值)
    → "全选"也安全:agent 没改的字段保持录制值(固定字段不变),不会留下空占位。"""
    defaults = defaults or {}
    if isinstance(template, dict):
        if set(template) == {_JSONSTR}:                  # 这层原本是 JSON 字符串:**先保留标记**,
            return {_JSONSTR: substitute(template[_JSONSTR], fields, defaults)}   # 等 identity/串联注入后再 re-stringify
        if set(template) == {_SEG}:                      # 段拼接:常量 + {{参数}} 子串 → join 成最终字符串
            out = []
            for it in template[_SEG]:
                if isinstance(it, dict) and "$p" in it:
                    k = it["$p"]
                    out.append(str(fields[k]) if k in fields else
                               (str(defaults[k]) if k in defaults else "{{" + k + "}}"))
                else:
                    out.append(str(it))
            return "".join(out)
        return {k: substitute(v, fields, defaults) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute(x, fields, defaults) for x in template]
    if isinstance(template, str) and template.startswith("{{") and template.endswith("}}"):
        key = template[2:-2]
        if key in fields:
            return fields[key]
        return defaults.get(key, template)
    return template


def _finalize_jsonstr(node):
    """把 substitute 后仍带 __dano_jsonstr__ 标记的内层结构 re-stringify 回字符串。

    **必须在 identity 重取 / 步链注入(_apply_identity / overrides 的 _set_by_path)之后调用** —— 那些按路径写值的
    操作要在结构还是嵌套时做(blob 内的申请人/taskId 才改得到);改完再压回字符串,否则申请人会被冻结成录制者。
    """
    if isinstance(node, dict):
        if set(node) == {_JSONSTR}:
            # 紧凑分隔符,贴近前端 JSON.stringify 的原始形态(无多余空格),减少与录制时 payload 的差异
            return json.dumps(_finalize_jsonstr(node[_JSONSTR]), ensure_ascii=False, separators=(",", ":"))
        return {k: _finalize_jsonstr(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_finalize_jsonstr(x) for x in node]
    return node


# ─────────── P4:select 名字→ID / identity 运行期重取 ───────────
def _split_path(path) -> list:
    """'form.items[0].id' → ['form','items',0,'id'];**已是 tokens 列表/元组则原样返回**(无歧义,键含点也安全)。"""
    if isinstance(path, (list, tuple)):
        return list(path)
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
            # H18 修复:路径不可达时记 debug 日志,便于排查"参数找不到 → 运行期 None 静默注入"
            _log.debug("get_by_path miss path=%r k=%r", path, k)
            return None
    return node


def _set_by_path(node, path, value) -> bool:
    """返回是否写入成功(C5 修复:不再静默吞所有异常,区分 KeyError(路径错)与 TypeError(节点非容器))。

    path 可为 str('a.b.c')、list/tuple(['a','b','c'])。失败原因会作为 audit 项返回给 _identity_audit。
    """
    if isinstance(path, (list, tuple)):
        ks = list(path)
    else:
        ks = _split_path(path)
    for k in ks[:-1]:
        try:
            node = node[k]
        except (KeyError, IndexError, TypeError):
            return False                                     # 路径不可达 / 中间节点不是容器
    try:
        node[ks[-1]] = value
        return True
    except (KeyError, IndexError, TypeError):
        return False


def resolve_identity_value(source: str, storage_state: dict | None, auth_headers: dict | None = None):
    """从登录态按 source 取"当前用户/会话值"。source 形如 localStorage:userInfo.userId / cookie:JSESSIONID。"""
    if not source:
        return None
    kind, _, rest = source.partition(":")
    if kind == "requestHeader":
        for k, v in (auth_headers or {}).items():
            if str(k).lower() == rest.lower():
                val = str(v or "")
                return val[7:].strip() if val.lower().startswith("bearer ") else val
        return None
    if not storage_state:
        return None
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


def _apply_identity(body, api_request: dict, storage_state: dict | None) -> list[str]:
    """把 identity 字段在运行期用会话里的当前用户值覆盖(不冻结成录制者)。

    返回写入失败路径列表(C5 修复:不再完全静默,让 _identity_audit 能区分"路径不可达"与"值没解析到")。
    """
    failed: list[str] = []
    for idn in api_request.get("identity") or []:
        val = resolve_identity_value(idn.get("source", ""), storage_state, api_request.get("auth_headers"))
        if val is not None:
            path = idn.get("tokens") or idn.get("path", "")   # tokens 优先(键含点也无歧义)
            if not _set_by_path(body, path, val):
                failed.append(path)                            # 路径不可达 → 入失败清单
    return failed


def _apply_system_values(body, api_request: dict) -> None:
    """系统生成的时间戳(submitTime/createTime 等)运行期填**当前时间**,而非焊死录制时刻。通用,不挑系统。"""
    import time as _time
    now_ms = int(_time.time() * 1000)
    for sv in api_request.get("system_values") or []:
        val = now_ms if sv.get("kind") == "now_ms" else now_ms // 1000
        _set_by_path(body, sv.get("tokens") or sv.get("path", ""), val)


# ─────────── P0:发布前确定性自检(self_check) + 运行期换身后置审计 ───────────
_PROBE_PREFIX = "__DANO_PROBE_"        # 唯一哨兵前缀;穿过 blob re-stringify 后在外层 dumps 里仍是连续子串
_PATH_MISSING = object()               # "走不到"哨兵,区别于"值恰好是 None"


def _path_lookup(node, path: str):
    """按 path(与 _set_by_path 同一套 _split_path 约定,含 __dano_jsonstr__ 段)取值;走不到返回 _PATH_MISSING。

    与运行期 _set_by_path 的可达性判定**完全一致**——它写得进的这里就取得到;它写不进的(键含点被 _split_path
    拆错、blob 提前压字符串等)这里就报缺失。所以自检对 identity/link 的判定 == 运行期真实行为。"""
    try:
        keys = _split_path(path)
    except Exception:  # noqa: BLE001  —— 键含 '[' 等导致解析异常,等同不可达
        return _PATH_MISSING
    cur = node
    for k in keys:
        if isinstance(cur, dict):
            if k not in cur:
                return _PATH_MISSING
            cur = cur[k]
        elif isinstance(cur, list):
            if not isinstance(k, int) or not (-len(cur) <= k < len(cur)):
                return _PATH_MISSING
            cur = cur[k]
        else:
            return _PATH_MISSING
    return cur


def _query_path_tokens(path) -> list | None:
    toks = _split_path(path)
    if toks and toks[0] == "query":
        return toks[1:]
    return None


def _render_query_template(query_template, fields: dict, defaults: dict | None = None) -> dict:
    """把 query_template 渲染成 dict；None 值不进入 URL，其余保留给 urlencode 处理。"""
    if not isinstance(query_template, dict):
        return {}
    rendered = substitute(query_template, fields, defaults or {})
    if not isinstance(rendered, dict):
        return {}
    return {str(k): v for k, v in rendered.items() if v is not None}


def _merge_query_into_url(url: str, query: dict | None) -> str:
    if not query:
        return url
    parsed = urlparse(url or "")
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged = {**existing, **query}
    encoded = urlencode(merged, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, encoded, parsed.fragment))


def _wellformed_identity_source(src: str) -> bool:
    kind, sep, rest = (src or "").partition(":")
    return bool(sep) and kind in ("cookie", "localStorage", "requestHeader") and bool(rest)


def _check_step_links(workflow: dict) -> list[str]:
    """多步串联:每条 link 的目标路径必须在「目标步」构造结果里真实可达,否则运行期 overrides 的
    _set_by_path 静默写不进(taskId 串不上,脏数据)。通用,不挑系统。"""
    out: list[str] = []
    steps = workflow.get("steps") or []
    for i, st in enumerate(steps):
        templ = st.get("body_template")
        query_templ = st.get("query_template")
        has_body = isinstance(templ, (dict, list))
        has_query = isinstance(query_templ, dict)
        if not has_body and not has_query:
            continue
        probes = {p: f"{_PROBE_PREFIX}{j}__" for j, p in enumerate(st.get("params") or [])}
        nested = substitute(templ, probes, {}) if has_body else None
        query = _render_query_template(query_templ, probes, {}) if has_query else {}
        for lk in st.get("links") or []:
            tp = lk.get("target_tokens") or lk.get("target_path", "")
            disp = lk.get("target_path") or tp
            source_step = lk.get("source_step")
            source_path = lk.get("source_tokens") or lk.get("source_path", "")
            query_tokens = _query_path_tokens(tp)
            if query_tokens is not None:
                missing_target = _path_lookup(query, query_tokens) is _PATH_MISSING
            else:
                missing_target = (not has_body) or _path_lookup(nested, tp) is _PATH_MISSING
            if not tp or missing_target:
                out.append(f"步骤{i + 1}:串联目标路径 `{disp}` 找不到落点 —— 运行期 taskId 等会串不进(脏数据)")
            if source_step is None or not source_path:
                out.append(f"步骤{i + 1}:串联 `{disp}` 无来源(source_step/source_path 为空)—— 运行期取不到值,无法串联")
                continue
            if not isinstance(source_step, int) or source_step < 0 or source_step >= len(steps):
                out.append(f"步骤{i + 1}:串联 `{disp}` 的 source_step={source_step} 越界—— 运行期取不到上游响应")
                continue
            response_json = steps[source_step].get("response_json")
            if response_json is not None and _path_lookup(response_json, source_path) is _PATH_MISSING:
                out.append(f"步骤{i + 1}:串联 `{disp}` 的来源路径 `{lk.get('source_path') or source_path}` 在上游响应样例里找不到")
    return out


def self_check(api_request: dict) -> list[str]:
    """录制产出的「请求 skill」发布前**确定性**自检(零网络、零会话)。返回违规清单(空=通过)。

    校验 skill 数据喂给**运行期同一解释器**能否构造出"对"的请求,断言后置不变量(通用,不挑系统/字段):
      a) 每个 identity 字段的注入路径在 body 结构里真实可达、取值来源合法 —— 否则运行期换身静默失败(申请人冻结)。
      b) 不留 {{}} 残缺(参数声明与 body_template 一致)。
      c) 填入参数的值能穿过整条流水线(含 blob re-stringify)出现在最终 body —— 否则"改了也不生效"。
      d) 多步串联(links)的目标路径在对应步 body 里真实可达。
    多步工作流逐步校验 + 跨步 link 校验。
    """
    steps = api_request.get("steps")
    if steps:
        out: list[str] = []
        for i, st in enumerate(steps):
            out += [f"步骤{i + 1}:{m}" for m in self_check({**st, "steps": None})]
        return out + _check_step_links(api_request)

    templ = api_request.get("body_template")
    query_templ = api_request.get("query_template")
    has_body = isinstance(templ, (dict, list))
    has_query = isinstance(query_templ, dict)
    params = list(api_request.get("params") or [])
    problems: list[str] = []
    if not has_body and not has_query:
        for p in params:
            problems.append(f"参数 `{p}` 没有 body_template/query_template 落点 —— agent 改了也不生效")
        return problems

    # (b)+(c):每个参数一个唯一哨兵 → 跑完整构造流水线(substitute→finalize)→ 哨兵必须都出现在最终 body
    probes = {p: f"{_PROBE_PREFIX}{i}__" for i, p in enumerate(params)}
    nested = substitute(templ, probes, {}) if has_body else None              # 不喂 defaults:逼出"参数无占位"的问题
    query = _render_query_template(query_templ, probes, {}) if has_query else {}
    final_parts: list[str] = []
    if has_body:
        final_parts.append(json.dumps(_finalize_jsonstr(nested), ensure_ascii=False))
    if has_query:
        final_parts.append(json.dumps(query, ensure_ascii=False))
    final_str = "\n".join(final_parts)
    if "{{" in final_str:
        problems.append("模板里仍残留 {{}} 占位 —— 参数声明与 body_template/query_template 不一致(有参数没填上)")
    for p, probe in probes.items():
        cnt = final_str.count(probe)
        if cnt == 0:
            problems.append(f"参数 `{p}` 填入的值进不了最终请求体/查询参数(被覆盖/丢失/未真正参数化)—— agent 改了也不生效")
        elif cnt > 1:
            problems.append(f"参数 `{p}` 同时填入 {cnt} 处(疑似扁平/嵌套键路径歧义,一个参数替换了多个字段)")

    # (a):identity 路径在"未 finalize 的嵌套结构"上必须可达(blob 内段含 __dano_jsonstr__)
    if has_body:
        for idn in api_request.get("identity") or []:
            path, src = idn.get("path", ""), idn.get("source", "")
            pathlike = idn.get("tokens") or path                # tokens 优先(键含点也能准确判可达)
            if not pathlike or _path_lookup(nested, pathlike) is _PATH_MISSING:
                problems.append(f"identity 字段路径 `{path}` 在请求体里找不到落点 —— 运行期换身会静默失败(申请人冻结)")
            elif not _wellformed_identity_source(src):
                problems.append(f"identity 字段 `{path}` 取值来源 `{src}` 非法(应为 cookie:KEY 或 localStorage:KEY.path)")
    elif api_request.get("identity"):
        problems.append("无 body_template 的请求暂不支持 identity 写入 —— 运行期换身没有落点")
    return problems


def _identity_audit(body, api_request: dict, storage_state: dict | None) -> list[str]:
    """运行期换身后置审计:identity 源能取到值、但 body 该路径的值 != 取到的值 → 换身失败(冻结)。

    **须在 _finalize_jsonstr 之前**调用(blob 仍嵌套,路径含 __dano_jsonstr__ 才走得到)。
    只在确证"源有值却没写进去"时报警 —— 无会话值(val=None)一律跳过,绝不误伤正常调用。"""
    bad: list[str] = []
    for idn in api_request.get("identity") or []:
        val = resolve_identity_value(idn.get("source", ""), storage_state, api_request.get("auth_headers"))
        if val is None:
            continue
        cur = _path_lookup(body, idn.get("tokens") or idn.get("path", ""))
        if cur is _PATH_MISSING or str(cur) != str(val):
            bad.append(f"identity `{idn.get('path')}` 未成功换身(仍为录制值)")
    return bad


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
        async with httpx.AsyncClient(timeout=30, verify=verify, trust_env=False) as c:
            r = await c.get(full, headers=headers)
        return r.json()
    except Exception:  # noqa: BLE001
        return None


async def _fetch_list(url: str, base_url: str, storage_state, token_key: str | None, verify: bool,
                      auth_headers: dict | None) -> list:
    """带登录态 GET 一个候选列表(选领导源),用 as_list_payload 取出数组。失败返回 []。"""
    data = await _get_json(url, base_url, storage_state, token_key, verify, auth_headers)
    return as_list_payload(data) or []


def _filter_category(items: list, sel: dict) -> list:
    """聚合字典的 select 带分类过滤(category_key/value)→ 运行期把全量收窄到该字段所属类目;无过滤则原样。
    与录制期快照 _aggregate_category 收窄**完全一致**——快照里是哪几项,现拉/名→ID 也只在那几项里。"""
    ck, cv = sel.get("category_key"), sel.get("category_value")
    if not ck:
        return items
    return [it for it in items if isinstance(it, dict) and str(it.get(ck)) == str(cv)]


def find_field_select(api_request: dict, field: str) -> dict | None:
    """在 api_request(单请求 + 多步各步)里找参数名==field 的 select 元数据(source_url/value_key/label_key)。
    供运行期"实时拉该字段当前可选项"(问题1:把接口放进 skill,选字段时直接调接口)。无则 None。"""
    sels = list((api_request or {}).get("selects") or [])
    for st in (api_request or {}).get("steps") or []:
        sels += list((st or {}).get("selects") or [])
    return next((s for s in sels if s.get("param") == field), None)


async def fetch_field_options(api_request: dict, field: str, *, base_url: str = "",
                              storage_state=None, token_key: str | None = None,
                              verify: bool = True, limit: int = 500) -> dict:
    """**实时**拉某选择型字段的当前可选项(直接调它的来源接口,带登录态)→ {field, options:[{label,value}], count}。
    通用,不挑系统。该字段不是选择型/无来源 → options=[] 并说明。失败 → options=[] 不抛(让 agent 退回传名字)。"""
    sel = find_field_select(api_request, field)
    if not sel:
        return {"field": field, "options": [], "count": 0,
                "note": "该字段不是选择型;直接按字段说明传值即可"}
    snapshot = []
    opt_map = sel.get("option_map") if isinstance(sel.get("option_map"), dict) else {}
    for x in (sel.get("options") or []):
        if isinstance(x, dict):
            lab = str(x.get("label") or x.get("text") or x.get("name") or x.get("value") or "").strip()
            if lab:
                snapshot.append({"label": lab, "value": opt_map.get(lab, x.get("value", lab))})
        else:
            lab = str(x).strip()
            if lab:
                snapshot.append({"label": lab, "value": opt_map.get(lab, x)})
    if not sel.get("source_url"):
        if snapshot:
            return {"field": field, "options": snapshot, "count": len(snapshot),
                    "note": "该字段候选来自录制页面真实下拉快照"}
        return {"field": field, "options": [], "count": 0,
                "note": "该字段没有实时选项来源;请按字段说明传值"}
    lk, vk = sel.get("label_key"), sel.get("value_key")
    items = await _fetch_list(sel["source_url"], base_url, storage_state, token_key, verify,
                              (api_request or {}).get("auth_headers"))
    items = _filter_category(items, sel)        # 聚合字典 → 只列该字段所属类目(与录制快照一致)
    opts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        lab = str(it.get(lk, "")).strip()
        if lab:
            opts.append({"label": lab, "value": it.get(vk)})
        if len(opts) >= limit:
            break
    if opts:
        return {"field": field, "options": opts, "count": len(items), "note": "选项来自实时接口"}
    if snapshot:
        return {"field": field, "options": snapshot, "count": len(snapshot),
                "note": "实时接口暂未返回选项,已回退录制页面下拉快照"}
    return {"field": field, "options": [], "count": 0,
            "note": "实时接口未返回可用选项"}


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
                           token_key: str | None, verify: bool) -> tuple[dict, dict]:
    """选择型:参数传的是名字 → 查候选列表换成内部 ID。返回 (fields, id_overrides)。

    两形态:① **单码字段**(approverId/type:字段本身就该存码)→ fields[param] 直接换成 id;
    ② **名/ID 配对**(yyxtmc 显示名 + yyxtid 内部 id)→ fields[param] 规整成候选规范显示名,
       配对 id 字段经 id_overrides({tokens 元组: id 值})在 substitute 后写回(换选项时 id 不冻结)。
    查不到则原样(可能用户直接给了 ID)。
    """
    id_overrides: dict = {}
    for s in api_request.get("selects") or []:
        if s.get("multi"):                               # 列表多选另走 _resolve_list_selects(展开成对象数组)
            continue
        param = s.get("param")
        if param not in fields:
            continue
        name = fields[param]
        opt_map = s.get("option_map") or {}
        if isinstance(opt_map, dict) and str(name) in {str(k) for k in opt_map}:
            match_key = next(k for k in opt_map if str(k) == str(name))
            mapped = opt_map[match_key]
            # DOM/native select 已给出真实提交值时,它比全量字典更可靠;若只是 label→label,继续尝试接口解析。
            if str(mapped) != str(name) or not s.get("source_url"):
                fields[param] = mapped
                continue
        if not s.get("source_url"):
            continue
        items = await _fetch_list(s.get("source_url", ""), base_url, storage_state, token_key, verify,
                                  api_request.get("auth_headers"))
        items = _filter_category(items, s)        # 聚合字典 → 只在该字段所属类目里名→ID(同名跨类目不串)
        lk, vk = s.get("label_key"), s.get("value_key")
        match = next((it for it in items if isinstance(it, dict) and str(it.get(lk)) == str(name)), None)
        if match is None:
            continue
        if s.get("id_tokens") or s.get("id_path"):       # 名/ID 配对:显示名字段保留名、配对 id 字段写 id
            if lk in match:
                fields[param] = match[lk]                 # 规整成候选里的规范显示名
            if vk in match:
                toks = s.get("id_tokens") or _split_path(s.get("id_path", ""))
                id_overrides[tuple(toks)] = match[vk]
        elif vk in match:                                # 单码字段:字段值换成 id
            fields[param] = match[vk]
    return fields, id_overrides


def _build_element(item: dict | None, template: dict, name: str, label_subkey: str | None) -> dict:
    """按元素模板把"一个选中项"拼成对象数组的一份元素:子键来自源项字段 / 常量;查不到源项时
    显示名子键保留传入名字、其余留空(best-effort,不破坏请求结构)。"""
    elem: dict = {}
    for sk, m in (template or {}).items():
        if isinstance(m, dict) and "const" in m:
            elem[sk] = m["const"]
        elif item is not None and isinstance(m, dict) and m.get("item_key") in (item or {}):
            elem[sk] = item.get(m["item_key"])
        else:
            elem[sk] = name if sk == label_subkey else ""
    return elem


async def _resolve_list_selects(api_request: dict, fields: dict, *, base_url: str, storage_state,
                                token_key: str | None, verify: bool) -> dict:
    """列表多选:参数传的是**名字列表**(["亚历山大大帝","狗蛋",…])→ 每个名字经来源接口查到整项,
    按元素模板拼成对象数组({userId,userName,avatar,participantType}…),回填 fields[param]。
    传单个名字/逗号串也容忍;查不到的名字仍保留(显示名字段=原名,其余空),不静默丢人。通用,不挑系统。"""
    for s in api_request.get("selects") or []:
        if not s.get("multi"):
            continue
        param = s.get("param")
        if param not in fields:
            continue
        val = fields[param]
        if isinstance(val, str):
            names = [x.strip() for x in val.split(",") if x.strip()]
        elif isinstance(val, list):
            names = [x if isinstance(x, str) else str(x) for x in val]
        else:
            continue
        if not names or all(not isinstance(x, str) for x in names):
            continue
        lk = s.get("label_key")
        tmpl, label_sub = s.get("element_template") or {}, s.get("label_subkey")
        if not s.get("source_url"):
            opt_map = s.get("option_map") or {}
            if isinstance(opt_map, dict) and opt_map:
                fields[param] = [opt_map.get(nm, nm) for nm in names]
                continue
            if tmpl:
                fields[param] = [_build_element(None, tmpl, nm, label_sub) for nm in names]
            continue
        items = await _fetch_list(s.get("source_url", ""), base_url, storage_state, token_key, verify,
                                  api_request.get("auth_headers"))
        items = _filter_category(items, s)
        built = []
        for nm in names:
            it = next((x for x in items if isinstance(x, dict) and _name_match(str(x.get(lk)), nm)), None)
            built.append(_build_element(it, tmpl, nm, label_sub))
        fields[param] = built
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
        # C4 修复:严格后缀匹配(避免 "evil.com" ⊂ "myevil.com" 的子串撞名 → 跨域带 cookie)
        # 允许:host==cd(主域) 或 host 以 ".cd" 结尾(子域)。其余一律不带。
        # 注意:合法的子域(如 amazon.cn.com 之于 .cn.com、a.b.example.com 之于 .example.com)依然正确放行。
        if host and cd and host != cd and not host.endswith("." + cd):
            continue
        name, val = c.get("name", ""), c.get("value", "")
        # H16 修复:cookie value 含 `=`/`,`/`;`/`"` 会破坏 Cookie 头(标准要求 semicolon 拆分),必须 quote
        from urllib.parse import quote
        pairs.append(f"{name}={quote(val, safe='')}")
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
    sel_overrides: dict = {}
    if send:                                                 # 选择型:名字→ID(需连网查候选列表);名/ID 配对返回 id 覆盖
        fields, sel_overrides = await _resolve_selects(api_request, fields, base_url=base_url,
                                                        storage_state=storage_state, token_key=token_key, verify=verify)
        # 列表多选:名字列表 → 对象数组(参会人[]:每个名字经来源接口拼成整份元素);须在 substitute 前
        fields = await _resolve_list_selects(api_request, fields, base_url=base_url,
                                             storage_state=storage_state, token_key=token_key, verify=verify)
    # 按字段声明类型归一值(number/bool/日期格式),让 body 填回的是目标系统认的类型/格式 —— 通用,不挑字段
    fields = _coerce_fields(fields, api_request)
    method = (api_request.get("method") or "POST").upper()
    path = api_request.get("path") or ""
    # 优先用录制时的完整 url(同一 OA host 不变);否则 base_url + path
    url = api_request.get("url") or (path if path.startswith("http") else (base_url or "").rstrip("/") + path)
    body_template = api_request.get("body_template")
    body = substitute(body_template, fields, api_request.get("sample_inputs") or {}) if isinstance(body_template, (dict, list)) else None
    query = _render_query_template(api_request.get("query_template"), fields, api_request.get("sample_inputs") or {})
    id_guarded: set[tuple] = set()
    id_write_failures: list[str] = []
    if body is not None:
        id_write_failures = _apply_identity(body, api_request, storage_state)   # C5:返回写入失败路径清单
        _apply_system_values(body, api_request)                  # 系统时间戳(submitTime/createTime)运行期填 now,不焊死录制时刻
        # C6 修复:收集所有 identity 字段路径(tuple 化),后续 overrides/sel_overrides 命中即跳过——避免
        # 跨步 link 覆盖运行期身份(以录制者身份写入)。路径统一为 tuple 便于和 overrides 的 tuple key 对比
        for idn in api_request.get("identity") or []:
            raw = idn.get("tokens") or idn.get("path", "")
            if not raw:
                continue
            if isinstance(raw, (list, tuple)):
                id_guarded.add(tuple(str(x) for x in raw))
            else:
                id_guarded.add(tuple(_split_path(raw)))
        for toks, v in sel_overrides.items():                    # 名/ID 配对:把解析出的内部 id 写回配对 id 字段(不冻结)
            if tuple(str(x) for x in toks) in id_guarded:        # C6:守护字段不覆盖
                continue
            _set_by_path(body, list(toks), v)
    for p, v in (overrides or {}).items():                       # Q3:上一步响应值注入(taskId/appCode 等)
        # C6 修复:overrides 命中 identity 守护路径即跳过 → 运行期身份不会被跨步 link 覆盖回录制者
        if isinstance(p, tuple) and tuple(str(x) for x in p) in id_guarded:
            continue
        query_tokens = _query_path_tokens(p)
        if query_tokens is not None:
            _set_by_path(query, query_tokens, v)
        elif body is not None:
            _set_by_path(body, p, v)
    id_issues = _identity_audit(body, api_request, storage_state) if send and body is not None else []   # 换身后置审计(blob 仍嵌套,可达)
    if send and id_write_failures:                              # C5:身份字段写入失败 → 必拒(避免以录制者身份写入)
        id_issues = list(id_issues) + [f"identity 字段路径不可达: {p}" for p in id_write_failures]
    body = _finalize_jsonstr(body) if body is not None else None  # identity/串联注入后,再把内层 JSON 压回字符串
    url = _merge_query_into_url(url, query)
    if not send:
        # **self_check 是唯一承重闸门**:它用哨兵填满每个参数,既查"残留 {{}}(参数未声明)"也查"参数填不进 body"。
        # 这里再用录制默认值看 leftover 只作信息——某参数**没有录制默认值**(运行期由 agent 提供)会留 {{}},
        # 但那不是缺陷(self_check 已证明该参数结构正确),不能因此拦发布(否则误报"参数没全填上")。
        leftover = "{{" in json.dumps(body, ensure_ascii=False) or "{{" in json.dumps(query, ensure_ascii=False)
        problems = self_check(api_request)                        # P0:发布前确定性自检(skill 数据,承重闸门)
        return {"ok": not problems, "dry": True, "method": method, "url": url, "body": body, "query": query,
                "self_check": problems, "leftover_no_default": leftover,
                "detail": ("；".join(problems) if problems else "请求可构造(dry,未真发)")}
    if id_issues:                                            # 真发前最后一道:换身失败就拒发,绝不以录制者身份写入
        return {"ok": False, "blocked": True, "method": method, "url": url,
                "identity_issues": id_issues,
                "detail": "；".join(id_issues) + " —— 已拒绝提交(避免以录制者身份写入)"}
    host = urlparse(url).hostname or ""
    ct = api_request.get("content_type") or "application/json"
    headers = {"Content-Type": ct} if body is not None else {}
    # ① 录制时抓到的应用自定义鉴权头(Authorization / Admin-Token / satoken / 租户号…)原样带上 —— 通用,不挑系统
    headers.update(api_request.get("auth_headers") or {})
    # ② Cookie 用 storageState 的(更全/可能更新);没抓到自定义头时,才回退到按 token_key 猜 Authorization
    ck = _auth_headers(storage_state, host, token_key)
    if ck.get("Cookie"):
        headers["Cookie"] = ck["Cookie"]
    if "Authorization" not in headers and not (api_request.get("auth_headers") or {}) and ck.get("Authorization"):
        headers["Authorization"] = ck["Authorization"]
    import httpx
    # 按录制时的编码发:form-urlencoded 走 data(httpx 自动 urlencode 扁平表单),否则 JSON body —— 通用,不挑系统
    is_form = "form-urlencoded" in ct.lower()
    if method in ("GET", "HEAD") or body is None:
        send_kw = {}
    else:
        send_kw = ({"data": {k: ("" if v is None else v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                                  if isinstance(v, (dict, list)) else str(v)) for k, v in (body or {}).items()}}
                   if is_form else {"json": body})
    async with httpx.AsyncClient(timeout=30, verify=verify, trust_env=False) as c:
        r = await c.request(method, url, headers=headers, **send_kw)
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
                val = _get_by_path(src, lk.get("source_tokens") or lk.get("source_path", ""))
                if val is not None:                          # tokens 优先,且用元组(可 hash)做 overrides 键
                    overrides[tuple(_split_path(lk.get("target_tokens") or lk.get("target_path", "")))] = val
        out = await execute_api_request(step, fields, base_url=base_url, storage_state=storage_state,
                                        send=send, verify=verify, token_key=token_key, overrides=overrides)
        last = out
        if send:
            responses.append(out.get("response"))
        elif step.get("response_json") is not None:
            responses.append(step.get("response_json"))
        else:
            responses.append(out.get("body") if out.get("body") is not None else {"query": out.get("query")})
        if not out.get("ok"):
            return {"ok": False, "failed_step": i, "detail": f"第{i + 1}步失败", "step_result": out}
    return {"ok": bool(last.get("ok", True)), "steps": len(steps),
            "status": last.get("status"), "response": last.get("response"), "final": last}


def _find_capability(api_request: dict, name: str | None) -> dict | None:
    if not name:
        return None
    target = str(name).strip()
    for cap in api_request.get("capabilities") or []:
        if not isinstance(cap, dict):
            continue
        if str(cap.get("name") or "").strip() == target or str(cap.get("kind") or "").strip() == target:
            return cap
    return None


def _capability_node_step_ids(cap: dict) -> list[str]:
    ids: list[str] = []

    def walk(nodes):  # noqa: ANN001
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            sid = str(node.get("step_id") or "")
            if sid and sid not in ids:
                ids.append(sid)
            for key in ("steps", "then", "otherwise"):
                child = node.get(key)
                if isinstance(child, list):
                    walk(child)

    walk(cap.get("nodes") or cap.get("workflow_nodes") or [])
    return ids


def _workflow_with_steps(api_request: dict, steps: list[dict], cap: dict) -> dict:
    out = copy.deepcopy(api_request)
    out["steps"] = steps
    out["capability"] = cap.get("name") or cap.get("kind") or ""
    out["capability_kind"] = cap.get("kind") or ""
    out["capabilities"] = [cap]
    params: list[str] = []
    samples: dict = {}
    field_types: dict = {}
    for st in steps:
        for p in st.get("params") or []:
            if p not in params:
                params.append(p)
        samples.update(st.get("sample_inputs") or {})
        field_types.update(st.get("field_types") or {})
    out["params"] = params
    out["sample_inputs"] = samples
    out["field_types"] = field_types
    if (cap.get("kind") or "") in {"query_status", "list_options", "validate_batch"}:
        out.pop("fact_check", None)
    return out


def _capability_batch_enabled(cap: dict | None) -> bool:
    if not cap:
        return False
    if (cap.get("kind") or "") != "submit_batch":
        return False
    contract = cap.get("execution_contract") if isinstance(cap.get("execution_contract"), dict) else {}
    batch = contract.get("batch") if isinstance(contract.get("batch"), dict) else {}
    return bool(batch.get("enabled", True))


def _capability_nodes(cap: dict | None) -> list[dict]:
    if not cap:
        return []
    contract = cap.get("execution_contract") if isinstance(cap.get("execution_contract"), dict) else {}
    nodes = contract.get("nodes") if isinstance(contract.get("nodes"), list) else None
    if nodes is None:
        nodes = cap.get("workflow_nodes") if isinstance(cap.get("workflow_nodes"), list) else None
    if nodes is None:
        nodes = cap.get("nodes") if isinstance(cap.get("nodes"), list) else []
    return [n for n in nodes if isinstance(n, dict)]


def _capability_has_structured_plan(cap: dict | None) -> bool:
    nodes = _capability_nodes(cap)
    if not nodes:
        return False
    return any(str(n.get("type") or "") not in {"call"} for n in _iter_capability_plan_nodes(nodes))


def _iter_capability_plan_nodes(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        out.append(node)
        for key in ("steps", "then", "otherwise", "else", "children"):
            child = node.get(key)
            if isinstance(child, list):
                out.extend(_iter_capability_plan_nodes([x for x in child if isinstance(x, dict)]))
    return out


def _capability_child_nodes(node: dict, *keys: str) -> list[dict]:
    for key in keys:
        value = node.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _cap_parse_literal(value):
    if value is None:
        return None
    if isinstance(value, (int, float, bool, list, dict)):
        return value
    text = str(value).strip()
    if not text:
        return ""
    low = text.lower()
    if low in {"true", "yes", "y"}:
        return True
    if low in {"false", "no", "n"}:
        return False
    if low in {"null", "none"}:
        return None
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        return text[1:-1]
    try:
        return int(text) if _re.fullmatch(r"-?\d+", text) else float(text)
    except Exception:  # noqa: BLE001
        return text


def _cap_get_context_value(expr, ctx: dict):
    if expr is None:
        return None
    if isinstance(expr, (int, float, bool, list, dict)):
        return expr
    text = str(expr).strip()
    if not text:
        return None
    if text.startswith(("'", '"')) or _re.fullmatch(r"-?\d+(?:\.\d+)?", text) or text.lower() in {"true", "false", "null", "none"}:
        return _cap_parse_literal(text)
    if text.startswith("input."):
        return _cap_project_path(ctx.get("fields") or {}, text.split(".", 1)[1])
    if text.startswith("item."):
        return _cap_project_path(ctx.get("item") or {}, text.split(".", 1)[1])
    if text.startswith("var."):
        return _cap_project_path(ctx.get("vars") or {}, text.split(".", 1)[1])
    if text.startswith("node."):
        return _cap_project_path(ctx.get("node_results") or {}, text.split(".", 1)[1])
    if text.startswith("response."):
        return _cap_project_path(ctx.get("last_response"), text.split(".", 1)[1])
    if text in (ctx.get("responses_by_step") or {}):
        return (ctx.get("responses_by_step") or {}).get(text)
    if text in (ctx.get("vars") or {}):
        return (ctx.get("vars") or {}).get(text)
    if text in (ctx.get("node_results") or {}):
        return (ctx.get("node_results") or {}).get(text)
    if "." in text:
        head, tail = text.split(".", 1)
        if head in (ctx.get("responses_by_step") or {}):
            return _cap_project_path((ctx.get("responses_by_step") or {}).get(head), tail)
        if head in (ctx.get("node_results") or {}):
            return _cap_project_path((ctx.get("node_results") or {}).get(head), tail)
    fields = ctx.get("fields") or {}
    if text in fields:
        return fields.get(text)
    return _cap_parse_literal(text)


def _cap_project_path(node, path: str):
    """Capability output path reader with simple list projection support.

    Supports normal paths like ``data.id`` and projection paths like
    ``results[].item.date``. The projection form is intentionally small but
    enough for batch outputs such as success_dates / failed_dates.
    """
    if path in (None, "", "$", "."):
        return node
    if isinstance(path, (list, tuple)):
        path = ".".join(str(p) for p in path)
    text = str(path or "").strip()
    if not text:
        return node
    if "[]" not in text:
        return _get_by_path(node, text)
    head, _, tail = text.partition("[]")
    head = head.rstrip(".")
    tail = tail.lstrip(".")
    base = _get_by_path(node, head) if head else node
    if not isinstance(base, list):
        return None
    out = []
    for item in base:
        value = _cap_project_path(item, tail) if tail else item
        if isinstance(value, list):
            out.extend(value)
        elif value is not None:
            out.append(value)
    return out


def _cap_output_name(mapping: dict, idx: int) -> str:
    for key in ("field", "name", "output", "target", "key"):
        value = str(mapping.get(key) or "").strip()
        if value:
            return value.split(".")[-1]
    path = str(mapping.get("response_path") or mapping.get("path") or "").strip()
    if path and path not in {"response", "$", "."}:
        return path.replace("[]", "").split(".")[-1] or f"output_{idx + 1}"
    return f"output_{idx + 1}"


def _cap_resolve_output_mapping(mapping: dict, ctx: dict):
    kind = str(mapping.get("kind") or "").strip()
    if "value" in mapping:
        return _cap_get_context_value(mapping.get("value"), ctx)
    if mapping.get("source"):
        return _cap_get_context_value(mapping.get("source"), ctx)

    step_id = str(mapping.get("step_id") or "").strip()
    response_path = str(mapping.get("response_path") or mapping.get("path") or "").strip()
    if response_path.startswith("response."):
        response_path = response_path.split(".", 1)[1]

    if kind in {"response_path", "step_response"} or step_id:
        base = (ctx.get("responses_by_step") or {}).get(step_id) if step_id else ctx.get("last_response")
        return _cap_project_path(base, response_path) if response_path and response_path not in {"response", "$", "."} else base
    if kind in {"node_result", "node"}:
        node_id = str(mapping.get("node_id") or mapping.get("node") or "").strip()
        base = (ctx.get("node_results") or {}).get(node_id) if node_id else ctx.get("last_result")
        return _cap_project_path(base, response_path) if response_path else base
    if kind in {"var", "variable"}:
        var_name = str(mapping.get("var") or mapping.get("variable") or "").strip()
        base = (ctx.get("vars") or {}).get(var_name) if var_name else ctx.get("vars")
        return _cap_project_path(base, response_path) if response_path else base
    if kind in {"batch_result", "batch"}:
        base = (ctx.get("vars") or {}).get("batch_result") or ctx.get("last_result")
        return _cap_project_path(base, response_path) if response_path else base

    base = ctx.get("last_response")
    if base is None:
        base = ctx.get("last_result")
    return _cap_project_path(base, response_path) if response_path and response_path not in {"response", "$", "."} else base


def _cap_apply_output_mapping(cap: dict | None, ctx: dict, fallback):
    mappings = list((cap or {}).get("output_mapping") or [])
    mappings = [m for m in mappings if isinstance(m, dict)]
    if not mappings:
        return fallback
    structured: dict = {}
    for idx, mapping in enumerate(mappings):
        name = _cap_output_name(mapping, idx)
        structured[name] = _cap_resolve_output_mapping(mapping, ctx)
    return structured


def _cap_truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "false", "0", "no", "n", "null", "none"}
    return bool(value)


def _cap_eval_condition(expr, ctx: dict) -> bool:
    if expr is None or expr == "":
        return True
    if isinstance(expr, bool):
        return expr
    text = str(expr).strip()
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op not in text:
            continue
        left, right = text.split(op, 1)
        lv = _cap_get_context_value(left.strip(), ctx)
        rv = _cap_get_context_value(right.strip(), ctx)
        if op == "==":
            return str(lv) == str(rv) if isinstance(lv, str) or isinstance(rv, str) else lv == rv
        if op == "!=":
            return str(lv) != str(rv) if isinstance(lv, str) or isinstance(rv, str) else lv != rv
        try:
            lf = float(lv)
            rf = float(rv)
        except Exception:  # noqa: BLE001
            return False
        if op == ">=":
            return lf >= rf
        if op == "<=":
            return lf <= rf
        if op == ">":
            return lf > rf
        if op == "<":
            return lf < rf
    return _cap_truthy(_cap_get_context_value(text, ctx))


def _capability_precondition_failures(cap: dict | None, fields: dict) -> list[str]:
    if not cap:
        return []
    ctx = {"fields": fields, "vars": {}, "node_results": {}, "responses_by_step": {}}
    failures: list[str] = []
    for idx, pre in enumerate(cap.get("preconditions") or []):
        if not isinstance(pre, dict):
            continue
        expr = pre.get("check") or pre.get("condition") or pre.get("expr")
        if expr and not _cap_eval_condition(expr, ctx):
            failures.append(str(pre.get("message") or f"前置条件 {idx + 1} 未满足: {expr}"))
    return failures


async def _execute_capability_batch(api_request: dict, fields: dict, *, cap: dict, runner, kw: dict) -> dict:
    contract = cap.get("execution_contract") if isinstance(cap.get("execution_contract"), dict) else {}
    batch = contract.get("batch") if isinstance(contract.get("batch"), dict) else {}
    items_field = str(batch.get("items_field") or "entries")
    entries = fields.get(items_field)
    if entries is None and items_field != "items":
        entries = fields.get("items")
    if not isinstance(entries, list):
        return await runner(api_request, fields, **kw)

    base_fields = {k: v for k, v in fields.items() if k not in {items_field, "items", "entries"}}
    results: list[dict] = []
    failed_items: list[dict] = []
    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            failed_items.append({"index": idx, "item": item, "detail": "批量条目必须是对象"})
            results.append({"ok": False, "index": idx, "detail": "批量条目必须是对象"})
            continue
        item_fields = {**base_fields, **item}
        out = await runner(api_request, item_fields, **kw)
        out = {**out, "index": idx}
        results.append(out)
        if not out.get("ok"):
            failed_items.append({"index": idx, "item": item, "detail": out.get("detail") or "执行失败", "result": out})
    out = {
        "ok": not failed_items,
        "capability": cap.get("name") or cap.get("kind") or "",
        "batch": True,
        "total": len(entries),
        "success_count": len(entries) - len(failed_items),
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "results": results,
        "final": results[-1] if results else {},
    }
    if cap.get("output_mapping"):
        ctx = {
            "fields": fields,
            "vars": {"batch_result": out},
            "node_results": {"batch_result": out},
            "responses_by_step": {},
            "last_response": None,
            "last_result": out,
        }
        mapped = _cap_apply_output_mapping(cap, ctx, out)
        out = {**out, "response": mapped, "output": mapped, "structured_output": mapped}
    return out


async def _execute_capability_plan(api_request: dict, fields: dict, *, cap: dict, kw: dict) -> dict:
    steps = list(api_request.get("steps") or [])
    if not steps:
        return await execute_api_request(api_request, fields, **kw)
    step_by_id = {str(st.get("step_id") or ""): st for st in steps}
    step_index = {str(st.get("step_id") or ""): idx for idx, st in enumerate(steps)}
    nodes = _capability_nodes(cap)
    if not nodes:
        return await execute_api_workflow(api_request, fields, **kw)

    ctx = {
        "fields": dict(fields or {}),
        "vars": {},
        "responses": [],
        "responses_by_step": {},
        "results_by_step": {},
        "node_results": {},
        "last_response": None,
        "last_result": None,
        "item": None,
    }

    async def run_call(node: dict, local_fields: dict, local_item=None) -> dict:
        step_id = str(node.get("step_id") or "")
        step = step_by_id.get(step_id)
        if step is None:
            return {"ok": False, "blocked": True, "detail": f"能力节点缺少有效接口步骤: {step_id}", "node": node.get("id")}
        overrides: dict = {}
        for lk in step.get("links") or []:
            old_src = lk.get("source_step")
            source_step_id = ""
            if isinstance(old_src, int) and 0 <= old_src < len(steps):
                source_step_id = str(steps[old_src].get("step_id") or "")
            src = ctx["responses_by_step"].get(source_step_id)
            if src is not None:
                val = _get_by_path(src, lk.get("source_tokens") or lk.get("source_path", ""))
                if val is not None:
                    overrides[tuple(_split_path(lk.get("target_tokens") or lk.get("target_path", "")))] = val
        out = await execute_api_request(step, local_fields, **kw, overrides=overrides)
        out = {**out, "final": out}
        idx = step_index.get(step_id, len(ctx["responses"]))
        response = out.get("response")
        if response is None:
            response = step.get("response_json")
        if response is None:
            response = out.get("body") if out.get("body") is not None else {"query": out.get("query")}
        while len(ctx["responses"]) <= idx:
            ctx["responses"].append(None)
        ctx["responses"][idx] = response
        ctx["responses_by_step"][step_id] = response
        ctx["results_by_step"][step_id] = out
        ctx["last_response"] = response
        ctx["last_result"] = out
        node_id = str(node.get("id") or step_id)
        ctx["node_results"][node_id] = out
        if local_item is not None:
            out = {**out, "item": local_item}
        return out

    async def run_nodes(plan_nodes: list[dict], local_fields: dict, local_item=None) -> dict:
        last: dict = {"ok": True}
        old_item = ctx.get("item")
        ctx["item"] = local_item
        try:
            for node in plan_nodes:
                node_type = str(node.get("type") or "call")
                node_id = str(node.get("id") or node_type)
                if node_type == "call":
                    last = await run_call(node, local_fields, local_item)
                    if not last.get("ok"):
                        return last
                elif node_type in {"condition", "filter"}:
                    expr = node.get("condition") or node.get("check") or node.get("expr")
                    branch = _capability_child_nodes(node, "then", "steps", "children") if _cap_eval_condition(expr, ctx) else _capability_child_nodes(node, "otherwise", "else")
                    if branch:
                        last = await run_nodes(branch, local_fields, local_item)
                        if not last.get("ok"):
                            return last
                elif node_type == "foreach":
                    source = node.get("items") or node.get("source") or "input.entries"
                    items = _cap_get_context_value(source, ctx)
                    if not isinstance(items, list):
                        return {"ok": False, "blocked": True, "detail": f"foreach 节点 `{node_id}` 的 items 不是数组: {source}"}
                    child_nodes = _capability_child_nodes(node, "steps", "children")
                    if not child_nodes:
                        child_nodes = [n for n in nodes if n.get("type") == "call"]
                    results: list[dict] = []
                    failed: list[dict] = []
                    for item_idx, item in enumerate(items):
                        if not isinstance(item, dict):
                            failed.append({"index": item_idx, "item": item, "detail": "批量条目必须是对象"})
                            results.append({"ok": False, "index": item_idx, "detail": "批量条目必须是对象"})
                            continue
                        item_fields = {**local_fields, **item}
                        item_out = await run_nodes(child_nodes, item_fields, item)
                        item_out = {**item_out, "index": item_idx}
                        results.append(item_out)
                        if not item_out.get("ok"):
                            failed.append({"index": item_idx, "item": item, "detail": item_out.get("detail") or "执行失败", "result": item_out})
                    last = {
                        "ok": not failed,
                        "batch": True,
                        "total": len(items),
                        "success_count": len(items) - len(failed),
                        "failed_count": len(failed),
                        "failed_items": failed,
                        "results": results,
                        "final": results[-1] if results else {},
                    }
                    ctx["vars"]["batch_result"] = last
                    ctx["node_results"][node_id] = last
                    ctx["last_result"] = last
                elif node_type == "map":
                    target = str(node.get("target") or "")
                    value = _cap_get_context_value(node.get("source"), ctx)
                    if target.startswith("var."):
                        _set_by_path(ctx["vars"], target.split(".", 1)[1], value)
                    elif target.startswith("input."):
                        _set_by_path(local_fields, target.split(".", 1)[1], value)
                    last = {"ok": True, "mapped": target, "value": value}
                    ctx["node_results"][node_id] = last
                elif node_type == "return":
                    if "value" in node:
                        value = _cap_get_context_value(node.get("value"), ctx)
                    else:
                        source = node.get("from") or node.get("source") or ""
                        base = _cap_get_context_value(source, ctx) if source else (ctx.get("last_response") or ctx.get("last_result"))
                        path = str(node.get("path") or "")
                        if path.startswith("response."):
                            path = path.split(".", 1)[1]
                        value = _get_by_path(base, path) if path and path not in {"response", "$", "."} else base
                    last = {"ok": True, "return": value, "response": value, "final": ctx.get("last_result") or {}}
                    ctx["node_results"][node_id] = last
                    ctx["last_result"] = last
                    return last
                else:
                    return {"ok": False, "blocked": True, "detail": f"不支持的能力节点类型: {node_type}", "node": node_id}
            return last
        finally:
            ctx["item"] = old_item

    out = await run_nodes(nodes, dict(fields or {}))
    mapped = _cap_apply_output_mapping(cap, ctx, out.get("response") if "response" in out else out)
    if mapped is not out:
        out = {**out, "response": mapped, "output": mapped, "structured_output": mapped}
    return {
        **out,
        "capability": cap.get("name") or cap.get("kind") or "",
        "capability_kind": cap.get("kind") or "",
        "plan": True,
        "steps": len([n for n in _iter_capability_plan_nodes(nodes) if n.get("type") == "call"]),
    }


def _select_api_request_for_capability(api_request: dict, name: str | None) -> tuple[dict | None, dict | None, str]:
    """按 capability 裁剪运行工作流；旧调用不传 capability 时保持完整执行。"""
    cap = _find_capability(api_request, name)
    if not name:
        return api_request, None, ""
    if cap is None:
        return None, None, f"未知 capability: {name}"
    kind = str(cap.get("kind") or cap.get("name") or "")
    if kind == "list_options":
        return api_request, cap, ""

    wanted = [str(x) for x in (cap.get("step_ids") or []) if str(x or "").strip()]
    if not wanted:
        wanted = _capability_node_step_ids(cap)
    full_steps = list(api_request.get("steps") or [])
    if not full_steps:
        single_id = str(api_request.get("step_id") or "")
        if wanted and single_id and single_id not in wanted:
            return None, cap, f"Capability `{cap.get('name') or kind}` 未绑定当前请求步骤"
        out = copy.deepcopy(api_request)
        out["capability"] = cap.get("name") or kind
        out["capability_kind"] = kind
        out["capabilities"] = [cap]
        if kind in {"query_status", "list_options", "validate_batch"}:
            out.pop("fact_check", None)
        return out, cap, ""

    if not wanted:
        if kind in {"submit", "submit_batch"}:
            return api_request, cap, ""
        return None, cap, f"Capability `{cap.get('name') or kind}` 缺少 step_ids，无法确定要执行哪些接口"

    wanted_set = set(wanted)
    old_to_new: dict[int, int] = {}
    selected: list[dict] = []
    for old_idx, st in enumerate(full_steps):
        if str(st.get("step_id") or "") in wanted_set:
            old_to_new[old_idx] = len(selected)
            selected.append(copy.deepcopy(st))
    if not selected:
        return None, cap, f"Capability `{cap.get('name') or kind}` 没有命中任何可执行步骤"

    for new_idx, st in enumerate(selected):
        remapped_links = []
        for lk in st.get("links") or []:
            old_src = lk.get("source_step")
            if old_src not in old_to_new:
                continue
            new_src = old_to_new[old_src]
            if new_src >= new_idx:
                continue
            item = dict(lk)
            item["source_step"] = new_src
            remapped_links.append(item)
        if remapped_links:
            st["links"] = remapped_links
        else:
            st.pop("links", None)
    return _workflow_with_steps(api_request, selected, cap), cap, ""


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
    fields = dict(fields or {})
    capability = (
        fields.pop("__capability", None)
        or fields.pop("_capability", None)
        or fields.pop("capability", None)
    )
    cap = None
    if capability:
        selected, cap, error = _select_api_request_for_capability(api_request, capability)
        if error:
            return {"ok": False, "blocked": True, "detail": error, "capability": capability}
        failures = _capability_precondition_failures(cap, fields)
        if failures:
            return {"ok": False, "blocked": True, "detail": "；".join(failures), "capability": capability}
        if cap and (cap.get("kind") or cap.get("name")) == "list_options":
            field = fields.get("field") or fields.get("param") or fields.get("name")
            if not field:
                return {"ok": False, "blocked": True, "detail": "list_options capability 需要传 field"}
            options = await fetch_field_options(
                api_request,
                str(field),
                base_url=kw.get("base_url", ""),
                storage_state=kw.get("storage_state"),
                token_key=kw.get("token_key"),
                verify=kw.get("verify", True),
            )
            return {"ok": True, "capability": capability, **options}
        api_request = selected or api_request
    runner = execute_api_workflow if api_request.get("steps") else execute_api_request
    if cap and _capability_has_structured_plan(cap):
        return await _execute_capability_plan(api_request, fields, cap=cap, kw=kw)
    if cap and _capability_batch_enabled(cap) and isinstance(fields.get("entries") or fields.get("items"), list):
        return await _execute_capability_batch(api_request, fields, cap=cap, runner=runner, kw=kw)
    out = await runner(api_request, fields, **kw)
    if cap and cap.get("output_mapping"):
        steps = list(api_request.get("steps") or [])
        responses_by_step: dict = {}
        if steps and out.get("steps"):
            for st in steps:
                sid = str(st.get("step_id") or "")
                if sid and st.get("response_json") is not None:
                    responses_by_step[sid] = st.get("response_json")
        if not responses_by_step:
            sid = str(api_request.get("step_id") or "")
            if sid:
                responses_by_step[sid] = out.get("response")
        ctx = {
            "fields": fields,
            "vars": {},
            "node_results": {},
            "responses_by_step": responses_by_step,
            "last_response": out.get("response"),
            "last_result": out,
        }
        mapped = _cap_apply_output_mapping(cap, ctx, out.get("response") if "response" in out else out)
        out = {**out, "response": mapped, "output": mapped, "structured_output": mapped}
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
                       selects: list[dict] | None = None, identity: list[dict] | None = None,
                       typed: dict | None = None) -> dict:
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
                                 identity=identity if last else None,
                                 typed=typed if last else None)
        st = apir or {}
        if w.get("response_json") is not None:
            st["response_json"] = w["response_json"]         # 供修复期校验 link 的 source_path(引用必须真实)
        steps.append(st)
    for lk in discover_step_links(writes):                   # 步间数据流挂到目标步
        steps[lk["target_step"]].setdefault("links", []).append(
            {"target_path": lk["target_path"], "target_tokens": lk.get("target_tokens"),
             "source_step": lk["source_step"],
             "source_path": lk["source_path"], "source_tokens": lk.get("source_tokens")})
    return {"steps": steps}
