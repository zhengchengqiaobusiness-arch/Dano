"""方式B 升级:抓"提交请求" → 参数化成可调用的内部接口(无 DOM 回放,框架无关)。

无 API 页面其实是 SPA:点提交时网页向它自己后端发了个写请求(带表单值的 JSON)。把那个请求抓下来,
请求体里**等于用户填的值**的字段 → 变成参数;内部 ID/token 等保持常量。回放就是直接发这个请求。
不依赖控件长相,比录 DOM 点击稳得多。

本模块是纯函数(不碰浏览器),便于离线测试。
"""

from __future__ import annotations

import json

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
# 提交无关的高频路径(登录/校验码/字典/心跳等),挑提交请求时排除
_NOISE = ("/login", "/captcha", "/getInfo", "/dict/", "/heartbeat", "/refresh", "/upload",
          "/sse", "/socket", "/ws", ".png", ".jpg", ".css", ".js")


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


def json_write_requests(requests: list[dict]) -> list[dict]:
    """抓到的请求里所有「带 JSON body 的写请求」(候选提交请求),保序。供前端列出来手选用哪个。"""
    out: list[dict] = []
    for r in requests:
        if (r.get("method") or "").upper() in _WRITE and _parse_body(r.get("post_data")) is not None:
            out.append(r)
    return out


def pick_submit_request(requests: list[dict], samples: dict) -> dict | None:
    """从抓到的请求里挑"提交请求":写方法 + JSON body + 含最多用户填的值。都不含则取最后一个写请求。"""
    sample_vals = {str(v) for v in samples.values() if v not in ("", None)}
    best, best_score, last_write = None, -1, None
    for r in requests:
        if (r.get("method") or "").upper() not in _WRITE:
            continue
        url = r.get("url") or ""
        if any(n in url for n in _NOISE):
            continue
        body = _parse_body(r.get("post_data"))
        if body is None:
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


import re as _re

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


def flatten_body(post_data: str | None, samples: dict | None = None) -> list[dict]:
    """把请求体拍平成叶子字段列表 + 参数建议,供前端勾选。任意嵌套(dict/list)→ 点路径。

    suggest_param 判定:① 对上用户填的值 → 一定建议;② 否则按 key/值语义:日期/时间 key → 建议;
    内部 id/key 结尾、或值像常量(雪花id/uuid/snake标识)→ 不建议;其余非空 → 建议。
    """
    body = _parse_body(post_data)
    if body is None:
        return []
    samples = samples or {}
    val2field = {str(v): k for k, v in samples.items() if v not in ("", None)}
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
            matched = sv in val2field
            time_like = bool(_TIME_KEY.search(key))
            const = (not time_like) and (bool(_ID_KEY.search(key)) or _is_const_value(node))
            out.append({"path": path, "key": key, "value": sv,
                        "suggest_param": bool(matched or (not const and sv != "")),
                        "suggest_name": val2field.get(sv, key)})

    walk(body, "")
    return out


def build_api_request(req: dict, param_map: dict, base_url: str = "") -> dict | None:
    """param_map: {字段点路径 → 参数名}。把这些路径的叶子替换成 {{参数名}},其余原样。

    返回 {method, path, url, content_type, body_template, params, sample_inputs(路径原值)}。
    """
    body = _parse_body(req.get("post_data"))
    if body is None:
        return None
    params: list[str] = []
    samples: dict[str, str] = {}

    def walk(node, path):
        if isinstance(node, dict):
            return {k: walk(v, f"{path}.{k}" if path else k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        if path in param_map:
            name = param_map[path]
            params.append(name)
            samples[name] = "" if node is None else str(node)
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
    return {"method": (req.get("method") or "POST").upper(), "path": path, "url": url,
            "content_type": req.get("content_type", "application/json"),
            "body_template": templ, "params": list(dict.fromkeys(params)), "sample_inputs": samples,
            "auth_headers": extract_auth_headers(req.get("headers"))}


def substitute(template, fields: dict):
    """把 body_template 里的 {{字段}} 占位用运行期 fields 的值填回(整值替换,保持类型)。"""
    if isinstance(template, dict):
        return {k: substitute(v, fields) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute(x, fields) for x in template]
    if isinstance(template, str) and template.startswith("{{") and template.endswith("}}"):
        key = template[2:-2]
        return fields.get(key, template)
    return template


def _auth_headers(storage_state: dict | None, host: str, token_key: str = "Admin-Token") -> dict:
    """从登录态快照构造鉴权头:同域 cookie 全带上 + token(cookie/localStorage)→ Authorization Bearer。"""
    headers: dict[str, str] = {}
    if not storage_state:
        return headers
    pairs: list[str] = []
    tok = ""
    for c in storage_state.get("cookies") or []:
        cd = (c.get("domain") or "").lstrip(".")
        if host and cd and cd not in host and host not in cd:
            continue
        pairs.append(f"{c.get('name')}={c.get('value')}")
        if c.get("name") == token_key:
            tok = c.get("value", "")
    if not tok:
        for o in storage_state.get("origins") or []:
            for it in o.get("localStorage") or []:
                if it.get("name") == token_key:
                    tok = it.get("value", "")
    if pairs:
        headers["Cookie"] = "; ".join(pairs)
    if tok:
        headers["Authorization"] = "Bearer " + tok
    return headers


async def execute_api_request(api_request: dict, fields: dict, *, base_url: str = "",
                              storage_state: dict | None = None, send: bool = True,
                              verify: bool = True, token_key: str = "Admin-Token") -> dict:
    """参数填回 body_template,带登录态发请求(send=True)或只校验参数齐全(send=False,dry,写安全)。"""
    body = substitute(api_request.get("body_template"), fields)
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
    return {"ok": 200 <= r.status_code < 300, "status": r.status_code, "response": data,
            "method": method, "url": url}
