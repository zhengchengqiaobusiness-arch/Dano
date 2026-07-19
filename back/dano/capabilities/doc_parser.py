"""文档解析器:把异构接口文档统一成结构化中间表示(侦察段①)。

可扩展适配器架构(SpecAdapter):按文档自动选解析器,把不同规格归一成 ActionSpec。
内置:
- OpenAPI 3.x(servers / requestBody.content / components.schemas,解析 $ref)
- Swagger 2.0(host+basePath+schemes / parameters[in=body].schema / definitions,解析 $ref)
当前只支持内置 Swagger 2.0 与 OpenAPI 3.x 适配器。

表单、制度、PDF 和截图等非结构化材料由浏览器侦察链处理。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _sanitize_name(name: str) -> str:
    """动作名安全化:只留 [A-Za-z0-9_-],其余(/ . 空格等)折成下划线。

    skill_id = {subsystem}.{action} 且要进 URL 路径(/v1/skills/{id}/invoke),
    故动作名不能含 / 或 .;无 operationId 的接口(fallback=method_path)尤其需要。
    """
    s = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "action"


class ActionSpec(BaseModel):
    name: str
    method: str = "POST"
    endpoint: str
    params_in: list[str] = Field(default_factory=list)
    required_in: list[str] = Field(default_factory=list)  # params_in 中的必填子集
    params_out: list[str] = Field(default_factory=list)
    error_codes: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)         # 文档 tags(供端点分类)
    summary: str = ""                                      # 文档 summary(标题/分类)
    field_docs: dict[str, str] = Field(default_factory=dict)  # 入参名→语义描述(阶段4)


# ─────────────────────────── $ref 解析(两规格共用) ───────────────────────────
def _resolve_ref(spec: dict[str, Any], node: Any) -> dict[str, Any]:
    """解析 {"$ref": "#/definitions/X" | "#/components/schemas/X"} → 目标 schema。

    仅处理本文档内引用(#/...);外部引用不支持,返回空。
    """
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            return {}
        seen.add(ref)
        cur: Any = spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")  # JSON Pointer 转义
            if not isinstance(cur, dict) or part not in cur:
                return {}
            cur = cur[part]
        node = cur
    return node if isinstance(node, dict) else {}


def _schema_props(spec: dict[str, Any], schema: Any) -> list[str]:
    """从一个 schema 抽属性名,解析 $ref / allOf(合并)。"""
    schema = _resolve_ref(spec, schema)
    if not schema:
        return []
    props: list[str] = list(schema.get("properties", {}).keys())
    for sub in schema.get("allOf", []):
        props += _schema_props(spec, sub)
    return props


def _schema_required(spec: dict[str, Any], schema: Any) -> list[str]:
    """从一个 schema 抽必填属性名,解析 $ref / allOf(合并)。"""
    schema = _resolve_ref(spec, schema)
    if not schema:
        return []
    req: list[str] = list(schema.get("required", []))
    for sub in schema.get("allOf", []):
        req += _schema_required(spec, sub)
    return req


def _schema_field_docs(spec: dict[str, Any], schema: Any) -> dict[str, str]:
    """从一个 schema 抽 {属性名: 描述},解析 $ref / allOf(合并)。"""
    schema = _resolve_ref(spec, schema)
    if not schema:
        return {}
    docs: dict[str, str] = {}
    for name, prop in (schema.get("properties", {}) or {}).items():
        if isinstance(prop, dict) and prop.get("description"):
            docs[name] = str(prop["description"])
    for sub in schema.get("allOf", []):
        docs.update(_schema_field_docs(spec, sub))
    return docs


# ─────────────────────────── 适配器抽象 + 内置实现 ───────────────────────────
class SpecAdapter(ABC):
    """一种接口规格的解析适配器。新增规格只需实现本类并注册。"""

    name: str = "spec"

    @abstractmethod
    def detect(self, spec: dict[str, Any]) -> bool:
        """本适配器能否解析该文档。"""

    @abstractmethod
    def parse_actions(self, spec: dict[str, Any]) -> list[ActionSpec]:
        ...

class OpenAPI3Adapter(SpecAdapter):
    """OpenAPI 3.x:servers + requestBody.content.*.schema + responses.content.*.schema。"""

    name = "openapi3"

    def detect(self, spec: dict[str, Any]) -> bool:
        return str(spec.get("openapi", "")).startswith("3")

    def parse_actions(self, spec: dict[str, Any]) -> list[ActionSpec]:
        actions: list[ActionSpec] = []
        for path, methods in spec.get("paths", {}).items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                params_in = [p.get("name", "") for p in op.get("parameters", [])]
                required_in = [
                    p.get("name", "") for p in op.get("parameters", []) if p.get("required")
                ]
                body_schema = (
                    op.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                params_in += _schema_props(spec, body_schema)
                required_in += _schema_required(spec, body_schema)
                field_docs = {p.get("name", ""): p["description"]
                              for p in op.get("parameters", []) if p.get("description")}
                field_docs.update(_schema_field_docs(spec, body_schema))
                params_out: list[str] = []
                error_codes: list[int] = []
                for code, resp in op.get("responses", {}).items():
                    code_int = _as_code(code)
                    if code_int is None:
                        continue
                    if code_int >= 400:
                        error_codes.append(code_int)
                    else:
                        out_schema = (
                            resp.get("content", {})
                            .get("application/json", {})
                            .get("schema", {})
                        )
                        params_out += _schema_props(spec, out_schema)
                actions.append(
                    _action(op, method, path, params_in, params_out, error_codes,
                            required_in, field_docs)
                )
        return actions

class Swagger2Adapter(SpecAdapter):
    """Swagger 2.0:host+basePath+schemes / parameters[in=body].schema / responses.schema / definitions。"""

    name = "swagger2"

    def detect(self, spec: dict[str, Any]) -> bool:
        return str(spec.get("swagger", "")).startswith("2")

    def parse_actions(self, spec: dict[str, Any]) -> list[ActionSpec]:
        actions: list[ActionSpec] = []
        for path, methods in spec.get("paths", {}).items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                params_in: list[str] = []
                required_in: list[str] = []
                field_docs: dict[str, str] = {}
                for p in op.get("parameters", []):
                    if not isinstance(p, dict):
                        continue
                    if p.get("in") == "body":
                        params_in += _schema_props(spec, p.get("schema", {}))
                        required_in += _schema_required(spec, p.get("schema", {}))
                        field_docs.update(_schema_field_docs(spec, p.get("schema", {})))
                    elif p.get("name"):
                        params_in.append(p["name"])  # query/path/header/formData
                        if p.get("required"):
                            required_in.append(p["name"])
                        if p.get("description"):
                            field_docs[p["name"]] = p["description"]
                params_out: list[str] = []
                error_codes: list[int] = []
                for code, resp in op.get("responses", {}).items():
                    code_int = _as_code(code)
                    if code_int is None:
                        continue
                    if code_int >= 400:
                        error_codes.append(code_int)
                    elif isinstance(resp, dict):
                        params_out += _schema_props(spec, resp.get("schema", {}))
                actions.append(
                    _action(op, method, path, params_in, params_out, error_codes,
                            required_in, field_docs)
                )
        return actions

# 适配器注册表(靠前的优先匹配)。OpenAPI3 兜底:无版本键的最小化 spec 按 3.x 解析。
_ADAPTERS: list[SpecAdapter] = [Swagger2Adapter(), OpenAPI3Adapter()]
_FALLBACK = OpenAPI3Adapter()


def _adapter_for(spec: dict[str, Any]) -> SpecAdapter:
    for a in _ADAPTERS:
        if a.detect(spec):
            return a
    return _FALLBACK  # 容忍最小化 spec(仅 paths,无 openapi/swagger 版本键)


# ─────────────────────────── 对外 API ───────────────────────────
def parse_openapi(spec: dict[str, Any]) -> list[ActionSpec]:
    """从接口文档抽动作清单。自动识别 Swagger 2.0 / OpenAPI 3.x。"""
    return _adapter_for(spec).parse_actions(spec)


# ─────────────────────────── 内部小工具 ───────────────────────────
def _as_code(code: Any) -> int | None:
    try:
        return int(code)
    except (ValueError, TypeError):
        return None


def _action(op: dict, method: str, path: str, params_in: list[str],
            params_out: list[str], error_codes: list[int],
            required_in: list[str] | None = None,
            field_docs: dict[str, str] | None = None) -> ActionSpec:
    deduped_in = [p for p in dict.fromkeys(params_in) if p]  # 去重保序
    req = set(required_in or [])
    docs = field_docs or {}
    return ActionSpec(
        name=_sanitize_name(op.get("operationId") or f"{method.lower()}_{path}"),
        method=method.upper(),
        endpoint=path,
        params_in=deduped_in,
        required_in=[p for p in deduped_in if p in req],  # 仅保留确属入参的必填项
        params_out=[p for p in dict.fromkeys(params_out) if p],
        error_codes=error_codes,
        tags=[str(t) for t in (op.get("tags") or [])],
        summary=str(op.get("summary") or ""),
        field_docs={k: v for k, v in docs.items() if k in deduped_in and v},
    )
