"""复合步骤连接器:as_step 标记 workflow_step(发布闸门放宽 + 目录隐藏)。纯离线。"""
from __future__ import annotations

from dano.agent_tools.connector_builder import build_connector_body
from dano.capabilities.doc_parser import ActionSpec


def test_as_step_marks_workflow_step():
    a = ActionSpec(name="submit_x", method="POST", endpoint="/biz/x")
    assert build_connector_body(a, tenant="t", subsystem="A-OA", as_step=True).workflow_step is True
    assert build_connector_body(a, tenant="t", subsystem="A-OA").workflow_step is False
