"""Phase 5 · 导出事实校验器(⑩):LLM 剧本不准发明脚本/参数;确定性渲染天然 grounded。纯离线。"""
from __future__ import annotations

from dano.generation.playbook import Operation, PlaybookSpec
from dano.generation.playbook_writer import render_playbook_md, validate_playbook_facts

_ACTIONS = {"submit_leave", "query_status"}
_FIELDS = {"leaveDays", "title"}


def test_clean_playbook_passes():
    md = ("用 `bash scripts/submit_leave.sh --leaveDays 3 --title x --confirm`;"
          "查 `bash scripts/query_status.sh`;自检 `bash scripts/diagnose.sh`。")
    assert validate_playbook_facts(md, actions=_ACTIONS, fields=_FIELDS) == []


def test_invented_script_flagged():
    md = "调 `bash scripts/delete_everything.sh`"
    issues = validate_playbook_facts(md, actions=_ACTIONS, fields=_FIELDS)
    assert any("delete_everything" in i for i in issues)


def test_invented_flag_flagged():
    md = "`bash scripts/submit_leave.sh --leaveDays 3 --secretToken abc`"
    issues = validate_playbook_facts(md, actions=_ACTIONS, fields=_FIELDS)
    assert any("--secretToken" in i for i in issues)


def test_markdown_hr_not_flagged():
    # frontmatter / 分隔线 --- 不应被当成参数
    md = "---\nname: x\n---\n# 标题\n`bash scripts/submit_leave.sh --title t`"
    assert validate_playbook_facts(md, actions=_ACTIONS, fields=_FIELDS) == []


def test_deterministic_render_is_grounded():
    do = Operation(op="submit_leave", title="提交请假", write=True,
                   fields=[{"name": "leaveDays", "label": "天数", "required": True},
                           {"name": "title", "label": "标题", "required": True}])
    query = Operation(op="query_status", title="查状态", write=False, fields=[])
    spec = PlaybookSpec(business="leave", label="请假", subsystem="A-OA",
                        operations=[do, query], do=do)
    md = render_playbook_md(spec, "dano-a-oa-leave")
    actions = {o.op for o in spec.operations}
    fields = {f["name"] for o in spec.operations for f in o.fields}
    # 确定性渲染的剧本必须自洽:不引用任何不存在的脚本/参数
    assert validate_playbook_facts(md, actions=actions, fields=fields) == []
