"""阶段1 grep 门禁:编排主流程**零系统字面量**(RuoYi 等端点只准活在 dialect)。

现状(诚实):本切片只清干净了编排入口 service.py。其余主流程文件(evidence/strategies/
coder/planner 的 prompt 等)仍含 RuoYi 字面量,作为阶段1后续切片逐个迁入 dialect 后,
把它们从 _PENDING 移到 _CLEAN 并扩大门禁范围。
"""
from __future__ import annotations

from pathlib import Path

_BACK = Path(__file__).resolve().parents[1]

# 系统特定端点字面量(RuoYi-Flowable 等):不得出现在已清理的主流程文件
_FORBIDDEN = ("/biz/flow", "/biz/form", "/workflow/handle", "startflow",
              "form/info", "form/save", "_contract_tokens")

# 已清理、必须保持零字面量的主流程文件(随切片推进扩大)
_CLEAN = (
    "dano/onboarding/service.py",       # 切片1:编排入口
    "dano/onboarding/evidence.py",      # 切片2:证据采集的表单探针
    "dano/onboarding/discovery.py",     # 复合流程动态发现(走 dialect.template_ids)
    "dano/gateway/app.py",              # 网关:模板/表单清单走 dialect
    "dano/catalog/manifest.py",         # 清单:类型/内部字段判据无系统字面量
    "dano/export/agent_skills.py",      # 导出:剧本渲染无系统字面量
    "dano/orchestrator/skills.py",      # 注册表:可见性走 asset_internal
    "dano/agent_tools/tools.py",        # pi 工具:templateId 定位全委托 dialect.template_id_in
    "dano/agent_tools/connector_builder.py",
)


def test_clean_main_flow_has_no_system_literals():
    offenders: dict[str, list[str]] = {}
    for rel in _CLEAN:
        text = (_BACK / rel).read_text(encoding="utf-8").lower()
        hits = [tok for tok in _FORBIDDEN if tok in text]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"主流程含系统字面量,应迁入 dialect: {offenders}"
