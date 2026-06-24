"""Phase 2:编码契约静态校验 coder_lint + 接进生成闸门 的安全网测试。

coder_lint 是 Phase 2 重写提示词的**确定性安全网**:把原来散在提示词里的祈使规则,变成机器强制 +
精确反馈。这里穷举每条规则的命中/不命中,并验证它已接进 GenerationLoop 的闸门(_gates)。

零误报优先:凡是「合法但被误判」的反例(import 别名、stdlib from-import、RESTful 路径插值)都必须放行。
"""

from __future__ import annotations

from types import SimpleNamespace

from dano.generation.coder_lint import scan_source

_CLEAN = (
    "import json\n"
    "import httpx\n"
    "def run(inputs, creds):\n"
    "    base = inputs['__base_url__']\n"
    "    headers = {'Authorization': 'Bearer ' + creds['token']}\n"
    "    with httpx.Client() as c:\n"
    "        r = c.post(base + '/submit', json={'code': 1}, headers=headers)\n"
    "    return {'code': r.json().get('code'), 'id': 1}\n"
)


# ─────────────────────────── 通过(零误报)───────────────────────────
def test_clean_adapter_passes():
    assert scan_source(_CLEAN) == []


def test_import_alias_not_flagged():
    src = "import httpx as h\ndef run(inputs, creds):\n    return h.get('x')\n"
    assert scan_source(src) == []


def test_from_import_stdlib_not_flagged():
    src = "from datetime import datetime\ndef run(inputs, creds):\n    return {'t': datetime.now()}\n"
    assert scan_source(src) == []


def test_local_shadowing_not_flagged():
    # json 是局部赋值(非库调用),不应判「没 import json」
    src = "def run(inputs, creds):\n    json = {'a': 1}\n    return {'v': json['a']}\n"
    assert scan_source(src) == []


def test_restful_path_interpolation_not_flagged():
    # 关键:RESTful 路径插值 /users/{id} 是对的,coder_lint **刻意不判**(留给提示词 grounding)
    src = ("import httpx\n"
           "def run(inputs, creds):\n"
           "    uid = inputs['id']\n"
           "    return httpx.get(f\"{inputs['__base_url__']}/users/{uid}\").json()\n")
    assert scan_source(src) == []


def test_syntax_error_silent():
    assert scan_source("def run(inputs, creds)\n    return 1") == []   # 语法错交给 vuln


# ─────────────────────────── 命中(普适硬错）───────────────────────────
def test_missing_run_flagged():
    f = scan_source("import httpx\nx = 1\n")
    assert any("缺入口函数" in m for m in f)


def test_async_run_flagged():
    f = scan_source("async def run(inputs, creds):\n    return {}\n")
    assert any("async" in m for m in f)


def test_run_too_few_args_flagged():
    f = scan_source("def run(inputs):\n    return {}\n")
    assert any("两个参数" in m for m in f)


def test_swallow_error_flagged():
    src = ("def run(inputs, creds):\n"
           "    try:\n        return do()\n"
           "    except Exception as e:\n        return {'_adapter_error': str(e)}\n")
    assert any("吞进返回值" in m for m in scan_source(src))


def test_missing_import_flagged():
    src = "def run(inputs, creds):\n    return httpx.get('x').json()\n"
    f = scan_source(src)
    assert any("没 import httpx" in m for m in f)


def test_findings_deduped():
    src = ("def run(inputs, creds):\n"
           "    a = httpx.get('x')\n    b = httpx.get('y')\n    return {}\n")
    f = [m for m in scan_source(src) if "httpx" in m]
    assert len(f) == 1                                   # 同一个缺库只报一次


# ─────────────────── 接进生成闸门:_gates 调 lint_adapter ───────────────────
class _FakeT:
    """注入式假工具集:沙箱/漏洞过,lint 由参数决定 —— 验证闸门把 lint 接在 vuln 与 review 之间。"""

    def __init__(self, *, lint_findings: list[str]) -> None:
        self.lint_findings = lint_findings
        self.review_called = False

    async def sandbox_test_adapter(self, run_id, params):
        return {"passed": True, "validation_run_ids": ["v-sandbox"]}

    async def vuln_scan(self, run_id, params):
        return {"passed": True, "validation_run_ids": ["v-vuln"], "findings": []}

    async def lint_adapter(self, run_id, params):
        return {"passed": not self.lint_findings,
                "validation_run_ids": ["v-lint"], "findings": self.lint_findings}

    async def request_review(self, run_id, params):
        self.review_called = True
        return {"all_passed": True, "verdicts": [], "review_run_ids": ["r-1"]}


def _loop():
    from dano.generation.controller import GenerationLoop
    return GenerationLoop(coder=None)


def _goal():
    return SimpleNamespace(run_id="run-1", flow="submit_x", test_input={})


async def test_gate_rejects_on_lint_finding():
    T = _FakeT(lint_findings=["缺入口函数:必须有模块级 def run(inputs, creds) -> dict"])
    ok, reasons, val_ids, review_ids, kind = await _loop()._gates(T, _goal(), "draft-1")
    assert ok is False and kind == "code"
    assert any("编码契约未过" in r for r in reasons)
    assert "v-lint" in val_ids                            # lint 证据已并入
    assert T.review_called is False                       # lint 失败即短路,不进评审


async def test_gate_passes_lint_then_reviews():
    T = _FakeT(lint_findings=[])
    ok, reasons, val_ids, review_ids, kind = await _loop()._gates(T, _goal(), "draft-1")
    assert ok is True and review_ids == ["r-1"]
    assert T.review_called is True                        # lint 过 → 继续三模型评审
    assert {"v-sandbox", "v-vuln", "v-lint"} <= set(val_ids)


# ─────────────────── 2b:提示词分节重写,grounding 规则不丢 ───────────────────
def test_evidence_prompt_sectioned_and_keeps_grounding():
    from dano.generation.coder import evidence_codegen_prompt
    from dano.shared.asset_bodies import PlanBody
    p = PlanBody(flow="submit_x", strategy="workflow_bpmn", success_rule="response.code == 200",
                 steps=["发起", "提交"], contract={"k": "v"}, evidence={})
    out = evidence_codegen_prompt(p, [])
    assert "【一、入口与安全】" in out and "【二、返回形状】" in out and "【三、按证据落实】" in out
    assert "response.code == 200" in out                  # 成败规则注入
    assert "请求体示例" in out and "__templateId__" in out and "空操作" in out   # grounding 规则保留
    assert "def run(inputs, creds)" in out                # 入口契约(下沉为简述但仍在)
