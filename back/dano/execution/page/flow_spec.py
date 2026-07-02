"""Step A+B+C+D: FlowSpec 完整实现。

- Step A: 收敛函数 to_flow_spec（包含 GET 业务请求）
- Step B: 编辑函数 apply_flow_edits（字段/参数/链接/重排）
- Step C: 链接编辑支持
- Step D: GET 表单手选 + LLM 命名 + 业务说明
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from urllib.parse import urlparse, parse_qs

# 复用 request_capture 的纯函数
from dano.execution.page.request_capture import (
    _leaf_paths,
    _parse_body,
    _is_system_timestamp,
    auto_required_fields,
    classify_request_role,
    discover_step_links,
    extract_auth_headers,
    flatten_body,
    infer_success_rule,
    json_write_requests,
    looks_dangerous_write,
    looks_internal_param_name,
    looks_like_auth_write,
    looks_like_read_request,
    pick_submit_request,
    suggest_assignee_names,
    suggest_fact_check,
    suggest_identity,
    suggest_list_selects,
    suggest_select_names,
    suggest_selects,
    suggest_workflow_steps,
)


# ─────────── 数据模型 ───────────
class ParamField(BaseModel):
    path: str
    key: str
    value: str = ""
    type: str = "string"  # string/number/boolean/datetime/date/array/object/list-enum
    required: bool = True
    confidence: float = 0.0
    confidence_tier: str = "auto"
    name_source: str = "auto"
    description: str | None = None
    enum_options: list[str] | None = None
    # Step D: 三类字段分类
    # user_param: 用户参数(每次调用可能变,让 agent 传)
    # system_const: 系统常量(流程定义 ID/表单类型/固定状态码,不能让 agent 改)
    # runtime_var: 运行期变量(录制时有值,但不能冻结,运行期自动填)
    category: str = "user_param"  # user_param / system_const / runtime_var


class SelectBinding(BaseModel):
    param: str = ""
    path: str = ""
    source_url: str = ""
    value_key: str = ""
    label_key: str = ""
    category_key: str | None = None
    category_value: str | None = None
    dom_options: list[str] | None = None
    multi: bool = False
    element_template: dict[str, Any] | None = None
    label_subkey: str | None = None
    count: int = 0
    options: list[str] | None = None


class IdentityBinding(BaseModel):
    path: str
    source: str  # localStorage:userInfo.userId / cookie:JSESSIONID
    tokens: list[str | int] | None = None
    value: str | None = None


class SystemValue(BaseModel):
    path: str
    tokens: list[str | int] | None = None
    kind: str = "now_ms"


class FlowStep(BaseModel):
    step_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    method: str = "POST"
    url: str = ""
    path: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    content_type: str = "application/json"
    body_source: str = ""
    body_template: Any = None
    params: list[ParamField] = Field(default_factory=list)
    selects: list[SelectBinding] = Field(default_factory=list)
    identity: list[IdentityBinding] = Field(default_factory=list)
    system_values: list[SystemValue] = Field(default_factory=list)
    success_rule: dict[str, Any] | None = None
    risk_level: str = "L3"
    semantic_role: str = ""
    source_meta: dict[str, Any] = Field(default_factory=dict)
    fact_check: dict[str, Any] | None = None
    sample_inputs: dict[str, str] = Field(default_factory=dict)


class FlowLink(BaseModel):
    link_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_step_id: str = ""
    source_path: str = ""
    source_tokens: list[str | int] | None = None
    target_step_id: str = ""
    target_path: str = ""
    target_tokens: list[str | int] | None = None
    param_name: str | None = None
    confirmed: bool = False
    confidence: float = 0.0


class FlowSpec(BaseModel):
    flow_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant: str = ""
    subsystem: str = ""
    title: str = ""
    business_description: str = ""
    steps: list[FlowStep] = Field(default_factory=list)
    links: list[FlowLink] = Field(default_factory=list)
    goal: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "L3"
    meta: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


# ─────────── Step A: 收敛函数 ───────────
def _infer_type_from_value(value: str) -> str:
    if not value:
        return "string"
    if value.lower() in ("true", "false"):
        return "boolean"
    if re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        return "datetime"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return "date"
    try:
        float(value)
        return "number"
    except ValueError:
        pass
    return "string"


def _default_step_name(req: dict) -> str:
    url = req.get("url") or req.get("path") or ""
    method = (req.get("method") or "POST").upper()
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = url
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1] if segs else ""
    if not last:
        return f"{method}_未命名"
    last = last.split("?")[0].rsplit(".", 1)[0]
    return f"{method}_{last}"


def _path_from_url(url: str, base_url: str = "") -> str:
    if base_url and url.startswith(base_url):
        return url[len(base_url):] or "/"
    if url.startswith("http"):
        u = urlparse(url)
        return (u.path or "/") + (("?" + u.query) if u.query else "")
    return url or "/"


def _select_name_for_step(selects: list[dict], samples: dict) -> dict[str, str]:
    return suggest_select_names(selects, samples)


def _build_step_from_capture(
    req: dict,
    *,
    reads: list[dict],
    samples: dict,
    storage_state: dict | None,
    required_labels: set,
    dom_options: dict,
    step_index: int,
) -> FlowStep:
    method = (req.get("method") or "POST").upper()
    pd = req.get("post_data")
    body = _parse_body(pd)

    # 风险 + 语义角色
    role = classify_request_role(req)
    risk = role.get("riskLevel", "L3")

    # GET 请求：从 URL query string 提参
    if method == "GET" or body is None:
        list_paths: list[str] = []
        selects_raw: list[dict] = []
        iden_raw: list[dict] = []
        flat_fields = _params_from_get_query(req)
    else:
        # 列表多选先识别
        list_selects = suggest_list_selects(pd, reads or [], samples)
        list_paths = [s["path"] for s in list_selects]

        # 字段拍平
        flat_fields = flatten_body(pd, samples, required_labels, collapse_paths=list_paths)

        # select/选人
        selects_raw = suggest_selects(pd, reads or [], samples, skip_paths=list_paths) + list_selects

        # identity(运行期重取)
        iden_raw = suggest_identity(pd, storage_state, samples)

    # select 字段配中文名
    sel_names = _select_name_for_step(selects_raw, samples)

    # BPMN 审批人命名兜底
    assignee_names = suggest_assignee_names(pd, reads or [], samples)

    # select 元数据
    selects_meta: list[SelectBinding] = []
    for s in selects_raw:
        selects_meta.append(SelectBinding(
            param="",
            path=s.get("path", ""),
            source_url=s.get("source_url", ""),
            value_key=s.get("value_key", ""),
            label_key=s.get("label_key", ""),
            category_key=s.get("category_key"),
            category_value=s.get("category_value"),
            dom_options=s.get("dom_options") if s.get("dom_options") else None,
            multi=bool(s.get("multi")),
            element_template=s.get("element_template"),
            label_subkey=s.get("label_subkey"),
            count=int(s.get("count") or 0),
            options=list(s.get("options") or []),
        ))

    # identity
    identity_meta = [
        IdentityBinding(
            path=i.get("path", ""),
            source=i.get("source", ""),
            tokens=i.get("tokens"),
            value=i.get("value"),
        )
        for i in iden_raw
    ]

    # system_values
    sys_values: list[SystemValue] = []
    if body is not None:
        for path, tokens, _sv, raw in _leaf_paths(body):
            key = path.split(".")[-1].split("[")[0]
            if _is_system_timestamp(key, raw):
                kind = "now_ms" if len(str(raw)) == 13 else "now_s"
                sys_values.append(SystemValue(path=path, tokens=tokens, kind=kind))

    # success_rule
    sr = None
    if req.get("response_json") is not None:
        sr = infer_success_rule([{"json": req.get("response_json")}])

    # params
    params: list[ParamField] = []
    for f in flat_fields:
        path = f.get("path", "")
        ptype = f.get("type") or "string"
        if path in list_paths:
            ptype = "list-enum"

        # 字段中文名优先级
        nm = f.get("suggest_name") or f.get("key") or ""
        if path in sel_names:
            nm = sel_names[path]
            ns = "sample"
        elif path in assignee_names and (nm == f.get("key") or _looks_internal(nm)):
            nm = assignee_names[path]
            ns = "assignee"
        else:
            ns = f.get("name_source") or "auto"

        # Step D: 三类字段自动分类
        category = _classify_field_category(nm, path, f.get("value", ""))

        params.append(ParamField(
            path=path,
            key=nm,
            value=str(f.get("value") or ""),
            type=ptype,
            required=bool(f.get("required")),
            confidence=float(f.get("confidence") or 0.0),
            confidence_tier=f.get("confidence_tier") or "auto",
            name_source=ns,
            category=category,
        ))

    # 补回 select 元数据的 param 字段
    from dano.agent_tools.page_builder import assign_field_keys
    fb_paths = [p.path for p in params]
    param_keys = assign_field_keys(fb_paths)
    path2key = dict(zip(fb_paths, param_keys))
    for sb, sraw in zip(selects_meta, selects_raw):
        sb.param = path2key.get(sraw.get("path", ""), "")

    # sample_inputs
    sample_inputs = {p.key: p.value for p in params if p.value}

    # source_meta
    source_meta = {
        "method": method,
        "url": req.get("url") or "",
        "headers_count": len(req.get("headers") or {}),
        "captured_at": req.get("captured_at"),
        "response_status": req.get("response_status"),
    }

    full_url = req.get("url") or ""
    path = _path_from_url(full_url)

    return FlowStep(
        name=_default_step_name(req),
        method=method,
        url=full_url,
        path=path,
        headers=extract_auth_headers(req.get("headers")),
        content_type=req.get("content_type") or "application/json",
        body_source=pd or "",
        body_template=None,
        params=params,
        selects=selects_meta,
        identity=identity_meta,
        system_values=sys_values,
        success_rule=sr,
        risk_level=risk,
        semantic_role=role.get("semanticRole", ""),
        source_meta=source_meta,
        sample_inputs=sample_inputs,
    )


def _params_from_get_query(req: dict) -> list[dict]:
    """GET 请求：从 URL query string 提参。"""
    url = req.get("url") or ""
    if "?" not in url:
        return []
    try:
        u = urlparse(url)
        qs = parse_qs(u.query, keep_blank_values=True)
    except Exception:
        return []
    out: list[dict] = []
    for k, vals in qs.items():
        v = vals[0] if vals else ""
        out.append({
            "path": f"query.{k}",
            "key": k,
            "value": v,
            "type": "string",
            "required": True,
            "confidence": 0.7,
            "confidence_tier": "auto",
            "name_source": "auto",
        })
    return out


def _is_business_get(r: dict) -> bool:
    """判断是否是业务型 GET 请求（响应被后续步骤引用）。"""
    if (r.get("method") or "").upper() != "GET":
        return False
    if not r.get("response_json"):
        return False
    rj = r.get("response_json")
    if isinstance(rj, list):
        return False
    if isinstance(rj, dict):
        if isinstance(rj.get("data"), list):
            return False
    return True


def to_flow_spec(
    captured_requests: list[dict],
    *,
    reads: list[dict] | None = None,
    samples: dict | None = None,
    storage_state: dict | None = None,
    required_labels: set | None = None,
    dom_options: dict | None = None,
    tenant: str = "",
    subsystem: str = "",
) -> FlowSpec:
    """收敛：把 record_ws 现有产物 → FlowSpec（包含 GET 业务请求）。"""
    reads = reads or []
    samples = samples or {}
    required_labels = required_labels or set()
    dom_options = dom_options or {}

    # 1) 业务写请求
    write_cands = [
        c for c in json_write_requests(captured_requests)
        if not looks_like_auth_write(c.get("url") or "", c.get("post_data"))
        and not looks_like_read_request(c.get("url") or "")
    ]

    # 2) 业务 GET 请求
    get_cands = [r for r in captured_requests if _is_business_get(r)]

    cands = write_cands + get_cands

    if not cands:
        return FlowSpec(
            tenant=tenant,
            subsystem=subsystem,
            title="(未捕获到业务请求)",
            meta={
                "captured_total": len(captured_requests),
                "captured_write_candidates": 0,
                "reads_count": len(reads),
                "note": "录制未抓到任何业务写请求或业务 GET；用户可能未点提交，或页面是纯 GET 表单",
            },
        )

    # 3) 自动建议流程步
    write_idxs = suggest_workflow_steps(write_cands, samples) if write_cands else []
    if not write_idxs:
        if write_cands:
            submit = pick_submit_request(write_cands, samples)
            if submit is not None:
                write_idxs = [write_cands.index(submit)]
        if not write_idxs and write_cands:
            write_idxs = [len(write_cands) - 1]

    # GET 全部入（它们响应被 step 引用）
    all_step_idxs = write_idxs + [len(write_cands) + i for i in range(len(get_cands))]

    # 4) 每条 → FlowStep
    step_objs: list[FlowStep] = []
    idx_to_step_id: dict[int, str] = {}
    for pos, gi in enumerate(all_step_idxs):
        req = cands[gi]
        st = _build_step_from_capture(
            req,
            reads=reads,
            samples=samples,
            storage_state=storage_state,
            required_labels=required_labels,
            dom_options=dom_options,
            step_index=pos,
        )
        step_objs.append(st)
        idx_to_step_id[gi] = st.step_id

    # 5) 多步 link（自动值驱动）
    link_objs: list[FlowLink] = []
    if len(step_objs) > 1:
        try:
            raw_links = discover_step_links(cands)
            for lk in raw_links:
                src_pos, tgt_pos = lk.get("source_step"), lk.get("target_step")
                if src_pos not in idx_to_step_id or tgt_pos not in idx_to_step_id:
                    continue
                link_objs.append(FlowLink(
                    source_step_id=idx_to_step_id[src_pos],
                    source_path=lk.get("source_path", ""),
                    source_tokens=lk.get("source_tokens"),
                    target_step_id=idx_to_step_id[tgt_pos],
                    target_path=lk.get("target_path", ""),
                    target_tokens=lk.get("target_tokens"),
                    param_name=None,
                    confirmed=False,
                    confidence=0.85,
                ))
        except Exception:
            link_objs = []

    # 6) 流程整体风险
    overall = "L1"
    for st in step_objs:
        rl = st.risk_level
        if rl == "L4":
            overall = "L4"
            break
        if rl == "L3" and overall != "L4":
            overall = "L3"

    # 7) fact_check
    fc = suggest_fact_check(samples, reads)
    if step_objs and fc:
        step_objs[-1].fact_check = fc

    # 8) title
    title = _derive_title(step_objs)

    return FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title=title,
        business_description="",
        steps=step_objs,
        links=link_objs,
        goal={},
        risk_level=overall,
        meta={
            "captured_total": len(captured_requests),
            "captured_write_candidates": len(write_cands),
            "captured_business_gets": len(get_cands),
            "captured_workflow_steps": len(step_objs),
            "reads_count": len(reads),
            "schema_version": 1,
        },
    )


def _derive_title(steps: list[FlowStep]) -> str:
    if not steps:
        return ""
    first = steps[0]
    try:
        url = first.url or first.path
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = first.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    if not last:
        return first.name or "(未命名)"
    if len(steps) > 1:
        return f"{last} 流程({len(steps)} 步)"
    return last


def flow_spec_to_summary(spec: FlowSpec) -> dict:
    return {
        "flow_id": spec.flow_id,
        "title": spec.title,
        "step_count": len(spec.steps),
        "link_count": len(spec.links),
        "risk_level": spec.risk_level,
        "schema_version": spec.schema_version,
        "steps": [
            {
                "step_id": s.step_id,
                "name": s.name,
                "method": s.method,
                "path": s.path,
                "risk_level": s.risk_level,
                "param_count": len(s.params),
                "select_count": len(s.selects),
                "identity_count": len(s.identity),
            }
            for s in spec.steps
        ],
        "links": [
            {
                "link_id": l.link_id,
                "source_step_id": l.source_step_id,
                "source_path": l.source_path,
                "target_step_id": l.target_step_id,
                "target_path": l.target_path,
                "confirmed": l.confirmed,
                "confidence": l.confidence,
            }
            for l in spec.links
        ],
        "meta": spec.meta,
    }


# ─────────── Step B+C: 编辑函数 ───────────
def _find_step(spec: FlowSpec, step_id: str) -> FlowStep:
    for step in spec.steps:
        if step.step_id == step_id:
            return step
    available = [s.step_id for s in spec.steps]
    raise ValueError(f"step not found: {step_id} (available: {available})")


def _find_param(step: FlowStep, param_path: str) -> ParamField:
    for param in step.params:
        if param.path == param_path:
            return param
    raise ValueError(f"param not found: {param_path} in step {step.step_id}")


def _find_link(spec: FlowSpec, link_id: str) -> FlowLink:
    for link in spec.links:
        if link.link_id == link_id:
            return link
    available = [l.link_id for l in spec.links]
    raise ValueError(f"link not found: {link_id} (available: {available})")


def _validate_link_endpoint(spec: FlowSpec, step_id: str, label: str) -> None:
    if not any(s.step_id == step_id for s in spec.steps):
        raise ValueError(f"{label} step not found: {step_id}")


def _ensure_unique_link(spec: FlowSpec, link: FlowLink) -> None:
    dup = any(
        existing.source_step_id == link.source_step_id
        and existing.target_step_id == link.target_step_id
        and existing.source_path == link.source_path
        and existing.target_path == link.target_path
        and existing.link_id != link.link_id
        for existing in spec.links
    )
    if dup:
        raise ValueError("duplicate link (same source/target/path exists)")


def apply_flow_edits(spec: FlowSpec, edits: list[dict[str, Any]]) -> FlowSpec:
    """应用编辑列表，返回新 FlowSpec（深拷贝）。"""
    if not edits:
        return spec

    new_spec = spec.model_copy(deep=True)

    for edit in edits:
        op = edit.get("op")

        # 重排步骤
        if op == "reorder_steps":
            order = edit.get("step_ids")
            if not isinstance(order, list):
                raise ValueError("reorder_steps missing step_ids list")
            existing_ids = {s.step_id for s in new_spec.steps}
            new_order_ids = set(order)
            if existing_ids != new_order_ids or len(order) != len(new_spec.steps):
                raise ValueError(
                    f"reorder_steps must include exactly all existing step_ids; "
                    f"got {sorted(new_order_ids)}, expected {sorted(existing_ids)}"
                )
            by_id = {s.step_id: s for s in new_spec.steps}
            new_spec.steps = [by_id[sid] for sid in order]
            continue

        # 链接编辑
        if edit.get("link_id"):
            link_id = edit["link_id"]
            if op == "update":
                link = _find_link(new_spec, link_id)
                field = edit.get("field")
                value = edit.get("value")
                if not field:
                    raise ValueError("link update missing field")
                if field == "confirmed":
                    link.confirmed = bool(value)
                elif field == "param_name":
                    link.param_name = str(value) if value is not None else None
                elif field == "source_path":
                    _validate_link_endpoint(new_spec, link.source_step_id, "source")
                    link.source_path = str(value)
                elif field == "target_path":
                    _validate_link_endpoint(new_spec, link.target_step_id, "target")
                    link.target_path = str(value)
                elif hasattr(link, field):
                    setattr(link, field, value)
                else:
                    raise ValueError(f"unknown link field: {field}")
                continue

            if op == "remove":
                link = _find_link(new_spec, link_id)
                new_spec.links.remove(link)
                continue

        # 添加链接
        if op == "add" and edit.get("link"):
            link_data = dict(edit["link"])
            link_data.setdefault("source_step_id", "")
            link_data.setdefault("target_step_id", "")
            link_data.setdefault("source_path", "")
            link_data.setdefault("target_path", "")
            _validate_link_endpoint(new_spec, link_data["source_step_id"], "source")
            _validate_link_endpoint(new_spec, link_data["target_step_id"], "target")
            try:
                new_link = FlowLink(**link_data)
            except ValidationError as e:
                raise ValueError(f"invalid link data: {e}")
            _ensure_unique_link(new_spec, new_link)
            new_spec.links.append(new_link)
            continue

        # 步骤/参数编辑
        step_id = edit.get("step_id")
        if not step_id:
            raise ValueError("edit missing step_id")

        step = _find_step(new_spec, step_id)

        if op == "update":
            param_path = edit.get("param_path")
            field = edit.get("field")
            value = edit.get("value")

            if not field:
                raise ValueError("update edit missing field")

            if param_path:
                # 参数级编辑
                param = _find_param(step, param_path)
                if field == "key":
                    old_key = param.key
                    param.key = str(value)
                    param.name_source = "manual"
                    if old_key in step.sample_inputs:
                        step.sample_inputs[param.key] = step.sample_inputs.pop(old_key)
                elif field == "value":
                    param.value = str(value)
                    step.sample_inputs[param.key] = param.value
                elif field == "type":
                    param.type = str(value)
                elif field == "required":
                    param.required = bool(value)
                elif hasattr(param, field):
                    setattr(param, field, value)
                else:
                    raise ValueError(f"unknown param field: {field}")
            else:
                # 步骤级编辑
                if field == "url":
                    step.url = str(value)
                elif field == "method":
                    step.method = str(value).upper()
                elif field == "headers":
                    step.headers = dict(value)
                elif field == "content_type":
                    step.content_type = str(value)
                elif field == "name":
                    step.name = str(value)
                elif hasattr(step, field):
                    setattr(step, field, value)
                else:
                    raise ValueError(f"unknown step field: {field}")
            continue

        elif op == "add":
            param_data = edit.get("param")
            if not param_data:
                raise ValueError("add edit missing param")
            if "type" not in param_data and "value" in param_data:
                param_data["type"] = _infer_type_from_value(param_data["value"])
            try:
                new_param = ParamField(**param_data)
            except ValidationError as e:
                raise ValueError(f"invalid param data: {e}")
            step.params.append(new_param)
            if new_param.value:
                step.sample_inputs[new_param.key] = new_param.value
            continue

        elif op == "remove":
            param_path = edit.get("param_path")
            if not param_path:
                raise ValueError("remove edit missing param_path")
            param = _find_param(step, param_path)
            step.params.remove(param)
            if param.key in step.sample_inputs:
                del step.sample_inputs[param.key]
            continue

        else:
            raise ValueError(f"unknown edit op: {op}")

    # 验证
    try:
        FlowSpec.model_validate(new_spec.model_dump())
    except ValidationError as e:
        raise ValueError(f"invalid spec after edits: {e}")

    return new_spec


def _looks_internal(name: str) -> bool:
    return looks_internal_param_name(name) if name else False


def _classify_field_category(key: str, path: str, value: str) -> str:
    """Step D: 三类字段自动分类。

    优先级: runtime_var > system_const > user_param

    - runtime_var: 运行期变量 (taskId, draftId, token, createTime 等)
    - system_const: 系统常量 (billType, formType, processDefinitionKey 等)
    - user_param: 用户参数 (默认)
    """
    k = key.lower()

    # 1. runtime_var: 运行期变量 (最高优先级)
    runtime_var_patterns = [
        "taskid", "draftid", "instanceid", "processinstanceid",
        "token", "accesstoken", "refreshtoken", "jsessionid",
        "createtime", "updatetime", "submittime", "modifytime",
        "createby", "updateby", "operator",
    ]
    if k in runtime_var_patterns or any(x in k for x in runtime_var_patterns):
        return "runtime_var"

    # 2. system_const: 系统常量
    system_const_patterns = [
        "processdefinitionkey", "processdefinitionid", "billtype", "formtype",
        "flowtype", "flowstatus", "businesstype",
        "applytype", "leavetype", "reimbursetype", "expense_type",
        "template_id", "formid", "menuid",
    ]
    if k in system_const_patterns or any(x in k for x in system_const_patterns):
        return "system_const"

    # 3. user_param: 用户参数 (默认)
    return "user_param"


# ─────────── Step D: GET 表单手选 ───────────
def flow_spec_for_get_form(
    reads: list[dict],
    *,
    tenant: str = "",
    subsystem: str = "",
) -> FlowSpec:
    steps = []
    for idx, r in enumerate(reads[:5]):
        try:
            step = _build_step_from_capture(
                r, reads=[], samples={}, storage_state=None,
                required_labels=set(), dom_options={}, step_index=idx,
            )
            step.name = f"读#{idx+1} {step.path or '(无路径)'}"
            steps.append(step)
        except Exception:
            continue
    return FlowSpec(
        tenant=tenant,
        subsystem=subsystem,
        title="(GET 表单待选)",
        steps=steps,
        links=[],
        meta={
            "step_d": True,
            "reads_count": len(reads),
            "note": "GET 表单没有写操作，从左侧抓到的读接口中选一条作主流程",
        },
    )


def pick_manual(spec: FlowSpec, picked_step_id: str) -> FlowSpec:
    target = None
    for st in spec.steps:
        if st.step_id == picked_step_id:
            target = st
            break
    if target is None:
        raise ValueError(f"step not found: {picked_step_id}")

    new_steps = [target] + [s for s in spec.steps if s.step_id != picked_step_id]
    new_links = [lk for lk in spec.links if lk.source_step_id == picked_step_id]

    new_spec = spec.model_copy(deep=True)
    new_spec.steps = new_steps
    new_spec.links = new_links
    new_spec.title = f"{target.name or _default_step_name({'url': target.url, 'method': target.method})}(GET)"
    new_spec.risk_level = "L1"
    new_spec.meta = {**(spec.meta or {}), "picked_step_id": picked_step_id, "manual_pick": True}
    return new_spec


# ─────────── Step D: LLM 命名 + 业务说明 ───────────
def _derive_step_name(step: FlowStep) -> str:
    url = step.url or step.path
    try:
        path = urlparse(url).path if url.startswith("http") else url
    except Exception:
        path = step.path
    segs = [s for s in (path or "").split("/") if s]
    last = segs[-1].split("?")[0] if segs else ""
    method = (step.method or "POST").upper()
    if not last:
        return f"{method}_未命名"
    if step.params:
        return f"{method}_{last}(含{len(step.params)}字段)"
    return f"{method}_{last}"


def rename_steps_with_llm(spec: FlowSpec, *, llm_client: Any | None = None) -> FlowSpec:
    new_spec = spec.model_copy(deep=True)
    for i, st in enumerate(new_spec.steps):
        if llm_client is not None:
            try:
                ctx = {
                    "method": st.method,
                    "path": st.path,
                    "params": [{"key": p.key, "type": p.type} for p in st.params[:10]],
                    "selects_count": len(st.selects),
                    "system_values_count": len(st.system_values),
                    "risk_level": st.risk_level,
                    "semantic_role": st.semantic_role,
                }
                named = llm_client.name_step(ctx)
                if isinstance(named, str) and named.strip():
                    st.name = named.strip()[:60]
                    continue
            except Exception:
                pass
        st.name = _derive_step_name(st)
    return new_spec


def render_business_description(spec: FlowSpec, *, llm_client: Any | None = None) -> str:
    if llm_client is not None:
        try:
            ctx = {
                "title": spec.title,
                "steps": [
                    {"name": s.name, "method": s.method, "path": s.path,
                     "params_count": len(s.params), "risk_level": s.risk_level}
                    for s in spec.steps
                ],
                "links_count": len(spec.links),
                "risk_level": spec.risk_level,
            }
            desc = llm_client.summarize_flow(ctx)
            if isinstance(desc, str) and desc.strip():
                return desc.strip()[:300]
        except Exception:
            pass

    if not spec.steps:
        return "本流程未包含任何操作步骤。"
    lines = [f"本流程包含 {len(spec.steps)} 步操作:"]
    for i, st in enumerate(spec.steps, 1):
        nm = st.name or _derive_step_name(st)
        lines.append(f"{i}. {nm}({st.method} {st.path})")
    if spec.links:
        lines.append(f"共 {len(spec.links)} 条数据流串联各步。")
    lines.append(f"整体风险等级:{spec.risk_level}。")
    return "\n".join(lines)