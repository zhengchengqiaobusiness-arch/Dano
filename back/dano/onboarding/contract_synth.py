"""契约合成器:勾选业务后,从**运行时**探出真实提交契约(不靠 swagger 写明、不硬编码业务字段)。

为什么:RuoYi 类 OA 的提交契约(表单字段 + 两步结构 + 成功约定)往往**不在 swagger 里**,
但运行时 `GET /biz/form/info?templateId=X` 返回**完整表单定义**(每个字段的 __vModel__/label/tag/required)。
本模块据此为**任意业务**现场拼出一份提交契约(字段 + 提交示例 + 成功判定),喂给 LLM 生成。

泛化:不写死任何业务字段——字段全部来自 form/info 真实返回;换家公司、换个流程,只要是这类
框架(发起→提交、变量装 flowTask.variables、成功 code==200),探一次 form/info 就能拼出它的契约。
失败/拿不到字段 → 返回 None,上层回退原行为(不阻断)。
"""

from __future__ import annotations

import json

import structlog

log = structlog.get_logger(__name__)

# element-ui 组件 → 字段类型(只为给模型提示类型,非强约束)
_TAG_TYPE = {
    "el-input-number": "number", "el-input": "string", "el-select": "string",
    "el-date-picker": "string", "el-switch": "boolean", "el-checkbox-group": "array",
    "el-radio-group": "string", "el-cascader": "string", "el-time-picker": "string",
    "el-slider": "number", "el-rate": "number",
}


def parse_form_fields(form_info_data: object) -> list[dict]:
    """从 /biz/form/info 的 data(其 formData 是一段 JSON 字符串)解析出表单字段清单。"""
    if not isinstance(form_info_data, dict):
        return []
    raw = form_info_data.get("formData")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, dict):
        return []
    fields_node = ((raw.get("formData") or raw).get("fields")
                   if isinstance(raw.get("formData") or raw, dict) else None) or raw.get("fields") or []
    out: list[dict] = []
    for f in fields_node:
        if not isinstance(f, dict):
            continue
        name = f.get("__vModel__")
        if not name:
            continue
        cfg = f.get("__config__") or {}
        out.append({"name": str(name), "label": str(cfg.get("label") or name),
                    "type": _TAG_TYPE.get(cfg.get("tag"), "string"),
                    "required": bool(cfg.get("required"))})
    return out


def _make_get(base_url: str, token: str):
    import httpx

    from dano.infra.http import tls_verify
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def get(path: str, params: dict | None = None) -> object:
        try:
            async with httpx.AsyncClient(timeout=20, verify=tls_verify()) as c:
                r = await c.get(base + path, params=params, headers=headers)
            j = r.json()
            return j.get("data") if isinstance(j, dict) else j
        except Exception as e:  # noqa: BLE001 - 探不到不阻断,回退原行为
            log.info("contract_synth.probe_failed", path=path, error=str(e))
            return None
    return get


async def synthesize_contract(template_id: str, base_url: str, token: str, *, get=None) -> dict | None:  # noqa: ANN001
    """探 form/info → 拼出该业务的提交契约 {fields, submit_example, success_rule, steps};失败 → None。

    契约形状是该框架真实的"发起→提交":startFlow 拿 taskId/procInsId/procDefId,再 /biz/flow/submit
    带 operateType=200 + flowTask{...,variables:{表单字段}} + template。variables 的键 = form/info 的字段名。
    """
    if not (template_id and base_url):
        return None
    if get is None:
        get = _make_get(base_url, token)
    data = await get("/biz/form/info", {"templateId": template_id})
    fields = parse_form_fields(data)
    if not fields:                                        # 拿不到字段 → 不硬拼,回退
        log.info("contract_synth.no_fields", template_id=template_id)
        return None
    variables = {f["name"]: f"<{f['label']}>" for f in fields}
    submit_example = {
        "operateType": "200",
        "flowTask": {
            "taskId": "<startFlow.data.taskId>", "procInsId": "<startFlow.data.procInsId>",
            "defId": "<startFlow.data.procDefId>", "templateId": template_id,
            "title": "<单据标题>", "comment": "提交申请", "variables": variables,
        },
        "template": {"id": template_id, "defId": "<startFlow.data.procDefId>"},
    }
    contract = {
        "template_id": template_id,
        "fields": fields,
        "success_rule": "response.code == 200",           # 框架真实约定(grounded,非猜)
        "submit_example": submit_example,
        "steps": [
            "① POST /workflow/handle/startFlow {templateId} → 取 data.taskId / data.procInsId / data.procDefId",
            "② POST /biz/flow/submit(照 submit_example;variables 填用户业务值;<startFlow.data.x> 用①的返回)",
            "返回里把业务码 code 与 procInsId 放顶层,供成败判定/事实核查",
        ],
    }
    log.info("contract_synth.done", template_id=template_id, fields=[f["name"] for f in fields])
    return contract
