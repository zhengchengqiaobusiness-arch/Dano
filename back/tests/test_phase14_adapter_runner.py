"""M0 验收:代码适配器地基 —— 隔离 runner + AdapterBody/PlanBody 校验。

纯单测(无 PG/无网络):验证 goal 模式生成的代码能被**安全**地跑:
- 正常返回;凭证经 stdin 注入(不进源码);
- **平台密钥不下传**给生成代码(env 最小化);
- 死循环超时被 kill;异常被捕获为二态失败;
- AdapterBody/PlanBody 能过资产体校验。
"""

from __future__ import annotations

import os

import pytest

from dano.execution.adapter import AdapterRunner
from dano.schemas.validate import PLAN_BODY_MODEL, SchemaError, validate_asset_body
from dano.shared.enums import AssetType


async def test_runner_runs_and_returns_output():
    src = "def run(inputs, creds):\n    return {'echo': inputs}\n"
    res = await AdapterRunner().run(source=src, inputs={"a": 1}, credentials={})
    assert res.ok is True
    assert res.output == {"echo": {"a": 1}}


async def test_creds_via_stdin_and_platform_secret_not_leaked(monkeypatch):
    # 平台密钥设进父进程环境;生成代码不应能读到(env 最小化)
    monkeypatch.setenv("DANO_PI_API_KEY", "SECRET-XYZ")
    src = (
        "import os\n"
        "def run(inputs, creds):\n"
        "    return {'token': creds.get('token'),\n"
        "            'leaked': os.environ.get('DANO_PI_API_KEY', '<absent>')}\n"
    )
    res = await AdapterRunner().run(source=src, inputs={}, credentials={"token": "T-123"})
    assert res.ok is True
    assert res.output["token"] == "T-123"          # 凭证经 stdin 注入到位
    assert res.output["leaked"] == "<absent>"       # 平台密钥未下传给生成代码
    assert "T-123" not in src                        # 凭证从不写进源码


async def test_runner_timeout_kills_runaway():
    src = "import time\ndef run(inputs, creds):\n    time.sleep(5)\n    return {}\n"
    res = await AdapterRunner(timeout_s=0.6).run(source=src, inputs={}, credentials={})
    assert res.ok is False
    assert "timeout" in (res.error or "")


async def test_runner_captures_exception_as_failure():
    src = "def run(inputs, creds):\n    raise ValueError('boom')\n"
    res = await AdapterRunner().run(source=src, inputs={}, credentials={})
    assert res.ok is False
    assert "ValueError" in (res.error or "") and "boom" in (res.error or "")


async def test_adapter_isolates_own_stdout_from_result():
    # 适配器自己打印不应污染结果解析(marker 行隔离)
    src = "def run(inputs, creds):\n    print('hello from adapter')\n    return {'v': 42}\n"
    res = await AdapterRunner().run(source=src, inputs={}, credentials={})
    assert res.ok is True and res.output == {"v": 42}
    assert "hello from adapter" in res.stdout


def test_adapter_body_validates():
    body = {
        "action": "submit_leave", "strategy": "simple_http",
        "source": "def run(inputs, creds):\n    return {}\n",
        "entry": "run", "user_fields": ["title"], "required_fields": ["title"],
        "success_rule": "response.code == 200",
        "fact_check": {"endpoint": "/q", "assert_expr": "response.total > 0"},
    }
    m = validate_asset_body(AssetType.ADAPTER, body)
    assert m.action == "submit_leave" and m.entry == "run"
    assert m.fact_check.assert_expr == "response.total > 0"


def test_adapter_body_rejects_missing_source():
    with pytest.raises(SchemaError):
        validate_asset_body(AssetType.ADAPTER, {"action": "x", "strategy": "simple_http"})


def test_plan_body_validates():
    plan = PLAN_BODY_MODEL.model_validate({
        "flow": "submit_leave", "strategy": "workflow_bpmn",
        "steps": ["startFlow", "saveForm", "submit"],
        "required_fields": ["title", "leaveType"],
        "fact_check": {"endpoint": "/flowXmlAndNode", "assert_expr": "apply_completed == true"},
    })
    assert plan.flow == "submit_leave" and plan.fact_check.retries == 5
