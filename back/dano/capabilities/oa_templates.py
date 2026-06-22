"""OA 框架模板库(阶段1):识别常见 OA/工作流系统,套用其鉴权/基础设施/成败约定。

定位:接入一份 Swagger 时,先认出"这是哪种 OA 框架"(如 RuoYi-Flowable),自动:
- 标出该框架的基础设施接口(登录/验证码/路由等),不暴露成业务 Skill;
- 注入该框架的成败判定规则(如 RuoYi 用 HTTP200 + body.code==200,而非通用单号)。

不臆造:只用 spec 里能客观判定的特征(路径前缀 / schema 名 / tags)匹配模板。
扩展:实现 OATemplate 子类 + register_oa_template() 即可接新框架(钉钉/泛微/企微…)。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import structlog

from dano.shared.asset_bodies import WorkflowSkillBody

log = structlog.get_logger(__name__)


# element-ui 控件 → JSON 类型(动态表单是字段类型的**权威信源**,比按字段名猜更准):
_EL_JSON_TYPE = {"el-input-number": "number", "el-slider": "number", "el-rate": "number",
                 "el-switch": "boolean"}
# 选项类控件 → 枚举字段(值来自该业务真实枚举,接入时解析 options,不硬编码)
_EL_ENUM = {"el-select", "el-radio-group", "el-radio", "el-checkbox-group", "el-checkbox"}


def _el_json_type(tag: str) -> tuple[str, bool]:
    """element-ui 控件 tag → (JSON 类型, 是否枚举)。日期/文本/级联 → string;数值控件 → number。"""
    if tag in _EL_JSON_TYPE:
        return _EL_JSON_TYPE[tag], False
    if tag in _EL_ENUM:
        return "string", True
    return "string", False


def _walk_vmodel_fields(node: object, out: list[dict]) -> None:
    """递归遍历 element-ui 表单设计器结构,凡带字段模型(__vModel__/vModel)的控件收为一个字段。

    每个字段除 key/label/type(原始控件)外,再给出 json_type(权威类型)与 enum(是否选项类)。
    """
    if isinstance(node, dict):
        vm = node.get("__vModel__") or node.get("vModel")
        if isinstance(vm, str) and vm:
            cfg = node.get("__config__") if isinstance(node.get("__config__"), dict) else {}
            tag = str(cfg.get("tag") or node.get("tag") or "")
            jtype, is_enum = _el_json_type(tag)
            out.append({"key": vm, "label": str(cfg.get("label") or node.get("label") or vm),
                        "type": tag, "json_type": jtype, "enum": is_enum})
        for v in node.values():
            _walk_vmodel_fields(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_vmodel_fields(v, out)


def _ruoyi_form_fields(form_info: object) -> list[dict]:
    """RuoYi 表单探针返回 → 字段清单 [{key,label,type}];data.formData 是 JSON 串(表单设计器结构)。"""
    if not isinstance(form_info, dict) or form_info.get("code") not in (None, 200, 0):
        return []
    data = form_info.get("data") if isinstance(form_info.get("data"), dict) else {}
    raw = data.get("formData")
    conf: object = raw
    if isinstance(raw, str):
        try:
            conf = json.loads(raw)
        except Exception:  # noqa: BLE001
            conf = {}
    schema = conf.get("formData") if isinstance(conf, dict) and "formData" in conf else conf
    fields: list[dict] = []
    _walk_vmodel_fields(schema, fields)
    seen: set[str] = set()
    return [f for f in fields if not (f["key"] in seen or seen.add(f["key"]))]


class OATemplate(ABC):
    """一种 OA 框架的识别模板。"""

    name: str = "generic"

    @abstractmethod
    def matches(self, spec: dict[str, Any]) -> bool:
        """该 spec 是否属于本框架(只用客观特征判定)。"""

    def success_rule(self) -> str | None:
        """运行期判定调用成败的断言表达式;None=只用 HTTP 2xx。"""
        return None

    def infrastructure_patterns(self) -> tuple[str, ...]:
        """本框架额外的基础设施关键词(动作名/路径命中 → 不暴露成业务 Skill)。"""
        return ()

    def workflows(self) -> list[WorkflowSkillBody]:
        """本框架的复合流程 Skill 配方(多步连接器编排成一个业务能力,阶段2)。

        只声明配方;接入时若配方引用的连接器动作未全部发布,则跳过该复合 Skill。
        """
        return []

    # ── 系统特定的"复合契约"知识(主流程零字面量:接入编排只问 dialect,不写死端点)──
    def contract_tokens(self) -> tuple[str, ...]:
        """本框架共享的"契约/查询"端点子串(收窄候选端点用);通用框架无 → ()。"""
        return ()

    def submit_endpoints(self) -> tuple[str, ...]:
        """复合提交契约涉及的有序端点(最后一个 = 最终提交步);无复合契约 → ()。"""
        return ()

    async def discover_contract(self, template_id: str, base_url: str, token: str, *, get=None):  # noqa: ANN001, ANN201
        """运行时探出该业务的真实提交契约 {fields, submit_example, success_rule, steps};
        非工作流框架 / 探不到 → None(上层回退原行为)。系统特定的探测逻辑全在子类。"""
        return None

    def form_probe_path(self, template_id: str) -> str | None:
        """该业务模板"动态表单"的只读探针路径(系统特定);通用框架无 → None。"""
        return None

    def parse_form_fields(self, probe_response: object) -> list[dict]:
        """把表单探针返回解析成字段清单 [{key,label,type}];非本框架结构 → []。"""
        return []

    def parse_approval_chain(self, spec: dict[str, Any], template_id: str) -> dict:
        """从文档(散文/表格)解析某模板的审批链 → business_meta;非本框架/解析不出 → {}。

        兜底来源:x-flow 没写时,有些框架把审批链写在发起端点的 description 里(表格/箭头),
        把它结构化成 {flow, templateId, approvalChain, thresholds},供导出渲染审批段(非臆造)。
        """
        return {}

    def template_list_paths(self) -> tuple[str, ...]:
        """查询目标系统"业务流程模板清单"的只读端点(系统特定);通用框架无 → ()。"""
        return ()

    def parse_template_list(self, payload: object) -> list[dict]:
        """模板清单接口返回 → [{templateId,name,type,defKey,enableFlag}];非本框架结构 → []。"""
        return []

    def template_ids(self, spec: dict[str, Any]) -> list[str]:
        """从 spec 静态枚举出本框架的全部流程 templateId(供流程发现动态提案);取不到 → []。

        系统特定(各框架放枚举的地方不同):RuoYi 在 StartFlowReq.templateId.enum。
        没有就返回空——生鲜 CRUD swagger 不强造复合流程。
        """
        return []

    def template_id_in(self, spec: dict[str, Any], body_json: str) -> str:
        """从一段工作流体 JSON 里定位本框架实际用的 templateId:枚举(template_ids)命中者为准。

        系统特定的"模板枚举在哪 / 命名约定"全在 dialect,主流程零字面量;取不到 → ""。
        """
        for t in self.template_ids(spec):
            if t and t in body_json:
                return t
        return ""


class RuoYiFlowableTemplate(OATemplate):
    """RuoYi-Vue + Flowable 工作流(请假/审批等 BPMN 流程)。

    特征:路径含 /workflow/ 或 /flowable/,或 components.schemas 有 AjaxResult。
    成败:RuoYi 统一返回 HTTP 200 + body.code(200成功/500失败);列表类无 code 字段,
          故规则写成"有 code 就必须 200,没有 code 则靠 HTTP 2xx"。
    """

    name = "ruoyi-flowable"

    def matches(self, spec: dict[str, Any]) -> bool:
        paths = " ".join(spec.get("paths", {}) or {}).lower()
        schemas = (spec.get("components", {}) or {}).get("schemas", {}) or {}
        return "/workflow/" in paths or "/flowable/" in paths or "AjaxResult" in schemas

    def success_rule(self) -> str | None:
        return "response.code == null or response.code == 200"

    def infrastructure_patterns(self) -> tuple[str, ...]:
        return ("captcha", "getinfo", "getrouters", "logout")

    def workflows(self) -> list[WorkflowSkillBody]:
        """不再内置任何业务配方:复合流程由 `discover_flows` 按 spec 的 templateId + 审批链**动态发现**,
        业务字段/模板/规则全部来自接入材料(swagger / 动态表单 / 模板元数据 / 业务规则),代码零业务字面量。
        """
        return []

    def contract_tokens(self) -> tuple[str, ...]:
        return ("startflow", "/biz/form", "/biz/flow", "form/info", "form/save",
                "flow/submit", "listprocess", "/draft", "/todo", "/done")

    def submit_endpoints(self) -> tuple[str, ...]:
        # 有序:发起 → 最终提交(最后一个 = 提交步,证据里的 request_example 挂它)
        return ("/workflow/handle/startFlow", "/biz/flow/submit")

    async def discover_contract(self, template_id: str, base_url: str, token: str, *, get=None):  # noqa: ANN001, ANN201
        from dano.onboarding.contract_synth import synthesize_contract
        return await synthesize_contract(template_id, base_url, token, get=get)

    def form_probe_path(self, template_id: str) -> str | None:
        return f"/biz/form/info?businessId=&templateId={template_id}" if template_id else None

    def parse_form_fields(self, probe_response: object) -> list[dict]:
        return _ruoyi_form_fields(probe_response)

    def template_ids(self, spec: dict[str, Any]) -> list[str]:
        enum = ((((spec.get("components", {}) or {}).get("schemas", {}) or {})
                 .get("StartFlowReq", {}) or {}).get("properties", {}).get("templateId", {}) or {}).get("enum") or []
        return [t for t in enum if isinstance(t, str)]

    def template_id_in(self, spec: dict[str, Any], body_json: str) -> str:
        t = super().template_id_in(spec, body_json)
        if t:
            return t
        import re
        m = re.search(r"([A-Za-z][\w-]*_template)", body_json)   # RuoYi 命名约定兜底(*_template)
        return m.group(1) if m else ""

    def template_list_paths(self) -> tuple[str, ...]:
        return ("/template/template/list?pageNum=1&pageSize=500", "/template/template/select")

    def parse_template_list(self, payload: object) -> list[dict]:
        if not isinstance(payload, dict) or payload.get("code") not in (None, 200, 0):
            return []
        rows = payload.get("rows") or payload.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("records") or rows.get("list") or []
        out: list[dict] = []
        for it in rows if isinstance(rows, list) else []:
            if not isinstance(it, dict):
                continue
            tid = it.get("id") or it.get("templateId") or it.get("defKey")
            if tid is None:
                continue
            out.append({"templateId": str(tid),
                        "name": it.get("name") or it.get("templateName") or str(tid),
                        "type": it.get("typeName") or it.get("type") or "",
                        "defKey": it.get("defKey") or "",
                        "enableFlag": str(it.get("enableFlag", ""))})
        seen: set[str] = set()
        return [t for t in out if not (t["templateId"] in seen or seen.add(t["templateId"]))]

    def parse_approval_chain(self, spec: dict[str, Any], template_id: str) -> dict:
        """解析 /workflow/handle/startFlow 的 description 里"流程目录"表格行(templateId → 审批链)。

        行形如:`| 采购申请 | purchase_template | 发起人填表 → 直属主管 → 〔金额>5000 时〕行政审批 → … |`
        → {flow, templateId, approvalChain:[{step,condition?}], thresholds:[{field,gt,adds}]}。解析不出 → {}。
        """
        import re
        try:
            tid = (template_id or "").strip().strip("`")
            if not tid:
                return {}
            op = (((spec.get("paths") or {}).get("/workflow/handle/startFlow") or {}).get("post") or {})
            desc = op.get("description") or ""
            flow_name, chain_text = "", ""
            for line in desc.splitlines():
                if "|" not in line:
                    continue
                cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
                if len(cells) >= 3 and cells[1] == tid:
                    flow_name, chain_text = cells[0], cells[2]
                    break
            if not chain_text:
                return {}
            approval: list[dict] = []
            thresholds: list[dict] = []
            for seg in (s.strip() for s in re.split(r"[→➔➜]", chain_text) if s.strip()):
                # 只在箭头处切(绝不切金额里的 >);逐段抽条件 + 步骤名。
                cond = None
                m = re.search(r"〔(.+?)〕", seg)
                step = re.sub(r"〔.+?〕", "", seg)
                step = re.sub(r"[(（].*?[)）]", "", step).strip()    # 去掉(动态·部门负责人)等注解
                if m:
                    raw = m.group(1).replace("大于等于", "≥").replace("不小于", "≥").replace("大于", ">")
                    tm = re.search(r"([><≥≤]=?)\s*(\d+)", raw)
                    if tm:
                        num = int(tm.group(2))
                        key = "gte" if ("≥" in tm.group(1) or ">=" in tm.group(1)) else "gt"
                        cond = f"amount{'≥' if key == 'gte' else '>'}{num}"
                        thresholds.append({"field": "amount", key: num, "adds": step})
                if step and step not in ("发起人填表", "发起人", "结束", "系统结束", "填表"):
                    approval.append({"step": step, **({"condition": cond} if cond else {})})
            if not approval:
                return {}
            return {"flow": flow_name, "templateId": tid,
                    "approvalChain": approval, "thresholds": thresholds}
        except Exception:  # noqa: BLE001 - 解析兜底:任何异常都退回空(绝不让脏数据进 business_meta)
            return {}


# 注册表(靠前优先)。新增框架插到最前。
_TEMPLATES: list[OATemplate] = [RuoYiFlowableTemplate()]


def register_oa_template(template: OATemplate) -> None:
    _TEMPLATES.insert(0, template)


def all_templates() -> list[OATemplate]:
    """已注册的全部方言(靠前优先)。无 spec 可匹配时(如接入向导只有 base_url+token)按序试探用。"""
    return list(_TEMPLATES)


def match_template(spec: dict[str, Any]) -> OATemplate | None:
    """匹配 spec 所属的 OA 框架模板;无匹配返回 None(走通用规则)。"""
    if not isinstance(spec, dict):
        return None
    for t in _TEMPLATES:
        try:
            if t.matches(spec):
                log.info("oa_template.matched", template=t.name)
                return t
        except Exception:  # noqa: BLE001 - 模板匹配不应让接入崩
            continue
    return None
