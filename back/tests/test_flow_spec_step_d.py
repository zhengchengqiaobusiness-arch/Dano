"""Step D · LLM 命名 + 业务说明 + GET 表单手选 测试。"""

import pytest

from dano.execution.page.flow_spec import (
    FlowSpec, FlowStep, FlowLink, ParamField,
    rename_steps_with_llm, render_business_description,
    _derive_step_name, _derive_title,
)


class _StubLLM:
    """测试用 LLM stub,行为可控。"""
    def __init__(self, *, name_step=None, summarize=None, raise_exc=None):
        self.name_step_return = name_step
        self.summarize_return = summarize
        self.raise_exc = raise_exc
        self.name_step_calls = []
        self.summarize_calls = []

    def name_step(self, ctx):
        self.name_step_calls.append(ctx)
        if self.raise_exc:
            raise self.raise_exc
        return self.name_step_return

    def summarize_flow(self, ctx):
        self.summarize_calls.append(ctx)
        if self.raise_exc:
            raise self.raise_exc
        return self.summarize_return


def _step(method="POST", path="/api/submit", name="", params_count=0, risk="L3"):
    return FlowStep(
        step_id=path.replace("/", "_"), name=name, method=method, url=path, path=path,
        params=[],
        risk_level=risk,
    )


# ── Step D2: LLM 命名 ──
def test_derive_step_name_basic():
    assert _derive_step_name(_step(method="POST", path="/api/submit")) == "POST_submit"


def test_derive_step_name_with_params():
    s = FlowStep(method="POST", url="/api/submit", path="/api/submit",
                 params=[ParamField(path="x", key="x", value="1", type="string", required=True)])
    assert "含1字段" in _derive_step_name(s)


def test_rename_no_llm_uses_deterministic():
    s1 = _step(path="/api/leave/submit")
    s2 = _step(path="/api/leave/approve")
    spec = FlowSpec(flow_id="f", steps=[s1, s2])
    new = rename_steps_with_llm(spec, llm_client=None)
    assert new.steps[0].name == "POST_submit"
    assert new.steps[1].name == "POST_approve"


def test_rename_with_llm_uses_llm():
    s = _step(path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(name_step="提交请假")
    new = rename_steps_with_llm(spec, llm_client=llm)
    assert new.steps[0].name == "提交请假"
    assert len(llm.name_step_calls) == 1


def test_rename_truncates_long():
    s = _step()
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(name_step="x" * 200)
    new = rename_steps_with_llm(spec, llm_client=llm)
    assert len(new.steps[0].name) == 60


def test_rename_empty_string_falls_back():
    s = _step(path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(name_step="   ")
    new = rename_steps_with_llm(spec, llm_client=llm)
    assert new.steps[0].name == "POST_submit"


def test_rename_exception_falls_back():
    s = _step(path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(raise_exc=RuntimeError("timeout"))
    new = rename_steps_with_llm(spec, llm_client=llm)
    assert new.steps[0].name == "POST_submit"


def test_rename_does_not_mutate_input():
    s = _step(name="old", path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    rename_steps_with_llm(spec, llm_client=_StubLLM(name_step="新名"))
    assert spec.steps[0].name == "old"


# ── Step D3: 业务说明 ──
def test_no_llm_single_step():
    s = _step(name="提交", path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s], risk_level="L3")
    desc = render_business_description(spec)
    assert "# 业务流程说明" in desc
    assert "## 1. 业务目的" in desc
    assert "## 5. 执行步骤" in desc
    assert "提交" in desc
    assert "`POST /api/submit`" in desc


def test_no_llm_multi_step():
    s1, s2 = _step(name="启动", path="/api/start"), _step(name="提交", path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s1, s2], risk_level="L4")
    desc = render_business_description(spec)
    assert "2 个步骤" in desc
    assert "1. 启动" in desc and "2. 提交" in desc
    assert "L4" in desc


def test_default_purpose_removes_stale_step_count_suffix():
    s1, s2 = _step(name="GET_get", method="GET", path="/api/get"), _step(name="提交", path="/api/submit")
    spec = FlowSpec(flow_id="f", title="get 流程(3 步)", steps=[s1, s2], risk_level="L3")
    desc = render_business_description(spec)
    assert "2 个步骤" in desc
    assert "get 流程(3 步)" not in desc
    assert "get 流程" in desc


def test_derive_title_prefers_last_write_step_over_preread_get():
    s1 = _step(name="GET_get", method="GET", path="/admin-api/bpm/process-definition/get")
    s2 = _step(name="POST_submit-process", method="POST", path="/admin-api/oa/duty-leave/submit-process")
    assert _derive_title([s1, s2]) == "submit-process 流程(2 步)"


def test_no_llm_links_present():
    s1, s2 = _step(path="/a"), _step(path="/b")
    lk = FlowLink(link_id="l", source_step_id="s1", source_path="data.x",
                  target_step_id="s2", target_path="x", confirmed=False, confidence=0.85)
    spec = FlowSpec(flow_id="f", steps=[s1, s2], links=[lk])
    desc = render_business_description(spec)
    assert "## 6. 接口依赖关系" in desc
    assert "data.x" in desc
    assert "body.x" in desc


def test_no_steps_returns_empty_message():
    desc = render_business_description(FlowSpec(flow_id="f"))
    assert "未包含" in desc
    assert "## 9. 需要人工确认的问题" in desc


def test_with_llm_happy():
    s = _step(name="提交", path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(summarize="用户提交请假申请。")
    desc = render_business_description(spec, llm_client=llm)
    assert "用户提交请假申请。" in desc
    assert "## 2. 用户需要提供的参数" in desc
    assert "`POST /api/submit`" in desc


def test_with_llm_truncates():
    s = _step(path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(summarize="x" * 500)
    desc = render_business_description(spec, llm_client=llm)
    assert "x" * 240 in desc
    assert "## 5. 执行步骤" in desc


def test_with_llm_exception_falls_back():
    s = _step(name="提交", path="/api/submit")
    spec = FlowSpec(flow_id="f", steps=[s])
    llm = _StubLLM(raise_exc=RuntimeError("timeout"))
    desc = render_business_description(spec, llm_client=llm)
    assert "POST /api/submit" in desc


def test_template_no_business_literals():
    s1, s2 = _step(path="/a"), _step(path="/b")
    spec = FlowSpec(flow_id="f", steps=[s1, s2])
    desc = render_business_description(spec)
    for word in ["报销", "请假", "审批", "合同", "财务", "HR", "OA"]:
        assert word not in desc


# ── Step D4: GET 表单手选 ──
def _get_form_spec():
    def _gs(sid, p):
        return FlowStep(step_id=sid, name=f"读#{sid}", method="GET", url=p, path=p,
                        params=[], risk_level="L1")
    return FlowSpec(flow_id="f", title="(GET 表单待选)",
                    steps=[_gs("g1", "/api/leave/list"),
                           _gs("g2", "/api/reimburse/list"),
                           _gs("g3", "/api/contract/list")], links=[])














# ── flow_spec_for_get_form ──
