"""Phase 0+1:提示词 JSON 模式 + 健壮抽取 + 数据分隔 的回归测试。

覆盖:
- prompt_utils:对裸数组 / 对象包裹数组 / 围栏 / 噪声 / 对象 都能抠对,坏输入返回空容器。
- openai_text_spawn(json_mode):带 response_format;模型回 400 时自动降级去掉它重试。
- 三个"数组型"调用点(业务剖析 / OA 画像 / 文档抽取)改对象包裹后仍正确解析。
- 跨企业/跨框架无关:测试只用通用占位,不绑定任何具体业务/系统。
"""

from __future__ import annotations

import httpx

from dano.shared.prompt_utils import extract_json_array, extract_json_obj, wrap_data


# ─────────────────────────── prompt_utils ───────────────────────────
def test_extract_array_raw_and_wrapped():
    assert extract_json_array('[{"a": 1}]') == [{"a": 1}]
    assert extract_json_array('{"operations": [{"op": "x"}]}') == [{"op": "x"}]
    assert extract_json_array('{"items": [1, 2, 3]}') == [1, 2, 3]
    assert extract_json_array('{"endpoints": [{"endpoint": "/a"}]}') == [{"endpoint": "/a"}]


def test_extract_array_fenced_and_noise():
    assert extract_json_array("说明:\n```json\n[1, 2]\n```\n谢谢") == [1, 2]
    assert extract_json_array('随便 [\n  {"op": "a"}\n] 结尾') == [{"op": "a"}]


def test_extract_array_bad_inputs():
    assert extract_json_array("") == []
    assert extract_json_array("没有 JSON") == []
    assert extract_json_array('{"name": "x"}') == []          # 对象但无 list 值


def test_extract_obj():
    d = extract_json_obj('{"name": "x", "success_rule": "response.code == 200"}')
    assert d["name"] == "x" and d["success_rule"] == "response.code == 200"
    assert extract_json_obj("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert extract_json_obj('前缀 {"a": 1} 后缀') == {"a": 1}
    assert extract_json_obj("") == {}
    assert extract_json_obj("[1, 2]") == {}                   # 数组不是对象


def test_wrap_data_delimiters():
    w = wrap_data("DOC", "任意文档原文")
    assert "<<<DOC>>>" in w and "<<<END_DOC>>>" in w and "任意文档原文" in w


# ─────────────────────────── openai_text_spawn(json_mode)───────────────────────────
class _Resp:
    def __init__(self, status: int, content: str = '{"ok": true}') -> None:
        self.status_code = status
        self.headers: dict = {}
        self.text = "err-body"
        self._c = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._c}}]}


def _fake_httpx(monkeypatch, responder, calls):
    class _Client:
        def __init__(self, *a, **k) -> None: ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):  # noqa: A002
            calls.append(dict(json))
            return responder(json)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def _fake_settings(monkeypatch) -> None:
    import dano.config as config

    class _S:
        pi_api_key = "k"
        pi_base_url = "http://example/v1"
        pi_model = "m"
    monkeypatch.setattr(config, "get_settings", lambda: _S())


async def test_json_mode_sets_response_format(monkeypatch):
    from dano.generation import coder
    _fake_settings(monkeypatch)
    calls: list[dict] = []
    _fake_httpx(monkeypatch, lambda payload: _Resp(200), calls)
    out = await coder.openai_text_spawn("给我 JSON", json_mode=True)
    assert out == '{"ok": true}'
    assert calls[0].get("response_format") == {"type": "json_object"}


async def test_json_mode_downgrades_on_400(monkeypatch):
    from dano.generation import coder
    _fake_settings(monkeypatch)
    calls: list[dict] = []

    def responder(payload):
        return _Resp(400) if "response_format" in payload else _Resp(200)
    _fake_httpx(monkeypatch, responder, calls)
    out = await coder.openai_text_spawn("给我 JSON", json_mode=True, max_attempts=3)
    assert out == '{"ok": true}'                    # 降级后仍拿到结果
    assert "response_format" in calls[0]             # 首次带 JSON 模式
    assert "response_format" not in calls[-1]        # 不支持 → 去掉重试


async def test_text_mode_has_no_response_format(monkeypatch):
    from dano.generation import coder
    _fake_settings(monkeypatch)
    calls: list[dict] = []
    _fake_httpx(monkeypatch, lambda payload: _Resp(200, content="<ADAPTER>x</ADAPTER>"), calls)
    await coder.openai_text_spawn("写代码", json_mode=False)
    assert "response_format" not in calls[0]


# ─────────────────── 三个数组型调用点:对象包裹仍正确解析 ───────────────────
async def test_business_profiler_object_wrapped():
    from dano.generation.business_profiler import _EXAMPLE, _PROMPT, profile_business
    rendered = _PROMPT.format(business="任意业务", lines="x", example=_EXAMPLE)   # format 不抛(转义正确)
    assert '{"operations"' in rendered
    actions = [{"name": "doSubmit", "method": "POST", "endpoint": "/x/submit", "summary": "提交"}]

    async def fake(prompt: str) -> str:
        assert "doSubmit" in prompt
        return '{"operations": [{"op": "submit_x", "write": true, "endpoints": ["doSubmit"], "purpose": "提交"}]}'
    ops = await profile_business("任意业务", actions, spawn=fake)
    assert ops and ops[0]["op"] == "submit_x" and ops[0]["endpoints"] == ["doSubmit"]


async def test_oa_profile_object_wrapped():
    from dano.generation.oa_profile import _PROMPT, build_oa_profile
    assert '{"capabilities"' in _PROMPT.format(framework="任意框架", kinds="-", lines="x")
    actions = [{"name": "myTodo", "method": "GET", "endpoint": "/todo/list", "summary": "待办"}]

    async def fake(prompt: str) -> str:
        return '{"capabilities": [{"kind": "query_my_todo", "method": "GET", "endpoint": "/todo/list", "name": "myTodo"}]}'
    prof = await build_oa_profile(actions, framework="any", spawn=fake)   # probe=None → 不做存在性探测
    assert "query_my_todo" in [c.kind for c in prof.capabilities]


async def test_ingest_doc_object_wrapped_and_delimited():
    from dano.onboarding.ingest import _llm_doc_to_actions

    async def fake(prompt: str) -> str:
        assert "<<<DOC>>>" in prompt and "<<<END_DOC>>>" in prompt    # 文档被当数据包裹
        return '{"endpoints": [{"name": "create_x", "method": "post", "endpoint": "/x", "summary": "建"}]}'
    actions = await _llm_doc_to_actions("接口:POST /x 创建", fake)
    assert actions and actions[0]["endpoint"] == "/x" and actions[0]["method"] == "POST"
