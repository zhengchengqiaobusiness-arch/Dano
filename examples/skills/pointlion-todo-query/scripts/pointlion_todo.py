#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only PointLion / Yudao BPM todo query client.

Dano adaptation: requests use the current Assistant Turn Credential Broker.
Run inside Dano; no OA token is supplied to Python.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit
from dano_provider import request as provider_request, ProviderError

try:
    import config as local_config
except ImportError as exc:
    raise RuntimeError("缺少 scripts/config.py，Skill 包不完整") from exc

VERSION = "3.0.0"
DEFAULT_API_PREFIX = local_config.API_PREFIX
DEFAULT_TODO_PATH = local_config.TODO_PATH
DEFAULT_CATEGORY_PATH = local_config.CATEGORY_PATH
DEFAULT_PROCESS_DEFINITION_PATH = local_config.PROCESS_DEFINITION_PATH
DEFAULT_DICT_DATA_PATH = local_config.DICT_DATA_PATH


class PointLionError(RuntimeError):
    pass


class ApiError(PointLionError):
    pass


class EnumResolutionError(PointLionError):
    pass


@dataclass
class ApiConfig:
    api_prefix: str
    tenant_id: Optional[str]
    tenant_header: str
    todo_path: str
    category_path: str
    process_definition_path: str
    dict_data_path: str


def _strip_slashes(value: str) -> str:
    return value.strip().strip("/")


def _join_api_url(base_url: str, api_prefix: str, path: str) -> str:
    base = base_url.rstrip("/")
    prefix = "/" + _strip_slashes(api_prefix) if _strip_slashes(api_prefix) else ""
    p = "/" + _strip_slashes(path)
    # If base_url already includes the configured prefix, do not duplicate it.
    if prefix and base.lower().endswith(prefix.lower()):
        return base + p
    return base + prefix + p


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _unwrap_common_result(payload: Any) -> Any:
    """Unwrap Yudao/PointLion CommonResult, while accepting plain JSON APIs."""
    if not isinstance(payload, dict):
        return payload
    if "code" in payload:
        code = payload.get("code")
        # Yudao branches/forks commonly use 0 or 200 for success.
        if code not in (0, 200, "0", "200", None):
            message = payload.get("msg") or payload.get("message") or "接口返回业务错误"
            raise ApiError(f"API code={code}: {message}")
        if "data" in payload:
            return payload.get("data")
    return payload


def _extract_list(data: Any) -> List[Dict[str, Any]]:
    """Accept list, PageResult.list, records, rows, or nested data wrappers."""
    data = _unwrap_common_result(data)
    if data is None:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("list", "records", "rows", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        if "data" in data:
            return _extract_list(data["data"])
    raise ApiError(f"无法识别列表返回结构: {_compact_json(data)[:500]}")


def _extract_page(data: Any) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
    data = _unwrap_common_result(data)
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
        return items, len(items), {"list": items, "total": len(items)}
    if not isinstance(data, dict):
        raise ApiError(f"无法识别分页返回结构: {_compact_json(data)[:500]}")

    items: Optional[List[Dict[str, Any]]] = None
    for key in ("list", "records", "rows", "items"):
        if isinstance(data.get(key), list):
            items = [x for x in data[key] if isinstance(x, dict)]
            break
    if items is None and isinstance(data.get("data"), (dict, list)):
        return _extract_page(data["data"])
    if items is None:
        # Empty PageResult variants sometimes omit list.
        if data.get("total") in (0, "0"):
            items = []
        else:
            raise ApiError(f"分页结果缺少 list/records/rows/items: {_compact_json(data)[:500]}")

    total_raw = data.get("total", data.get("totalCount", len(items)))
    try:
        total = int(total_raw)
    except (TypeError, ValueError):
        total = len(items)
    return items, total, data


class PointLionClient:
    def __init__(self, config: ApiConfig):
        self.config = config
        self.responses: List[Dict[str, Any]] = []

    def _request_json(self, path: str, params: Optional[Sequence[Tuple[str, Any]]] = None) -> Any:
        url = _join_api_url("", self.config.api_prefix, path)
        if params:
            clean: List[Tuple[str, str]] = []
            for key, value in params:
                if value is None or value == "":
                    continue
                clean.append((key, str(value)))
            if clean:
                url += "?" + urlencode(clean)

        headers = {"Accept": "application/json, text/plain, */*"}
        if self.config.tenant_id:
            headers[self.config.tenant_header] = self.config.tenant_id
        target = urlsplit(url)
        relative_path = target.path + ("?" + target.query if target.query else "")
        try:
            result = provider_request("GET", relative_path, headers=headers)
        except ProviderError as exc:
            raise ApiError(f"{exc.code}: {exc}") from None
        self.responses.append({"path": target.path, "httpStatus": result["status"]})
        if not 200 <= result["status"] < 300:
            raise ApiError(f"HTTP {result['status']} {target.path}")
        text = result["body"]

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ApiError(f"接口没有返回 JSON: {url}; body={text[:500]!r}") from exc
        self.responses[-1]["businessCode"] = payload.get("code") if isinstance(payload, dict) else None
        return payload

    def categories(self) -> List[Dict[str, Any]]:
        return _extract_list(self._request_json(self.config.category_path))

    def process_definitions(self) -> List[Dict[str, Any]]:
        return _extract_list(self._request_json(self.config.process_definition_path))

    def dict_data(self) -> List[Dict[str, Any]]:
        return _extract_list(self._request_json(self.config.dict_data_path))

    def query_todos(self, params: Sequence[Tuple[str, Any]]) -> Tuple[List[Dict[str, Any]], int, Any]:
        payload = self._request_json(self.config.todo_path, params)
        unwrapped = _unwrap_common_result(payload)
        items, total, page = _extract_page(unwrapped)
        return items, total, page


def _norm_text(value: Any) -> str:
    s = "" if value is None else str(value)
    return re.sub(r"[\s_\-—–/\\]+", "", s).casefold()


def _first(item: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and value != "":
            return value
    return None


def _enum_candidates(kind: str, items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if kind == "category":
        for item in items:
            out.append({
                "id": item.get("id"),
                "name": _first(item, ("name", "label")),
                "code": _first(item, ("code", "value")),
                "raw": dict(item),
            })
    elif kind == "process":
        for item in items:
            out.append({
                "id": _first(item, ("id", "processDefinitionId")),
                "name": _first(item, ("name", "processDefinitionName", "label")),
                "key": _first(item, ("key", "processDefinitionKey", "value")),
                "category": item.get("category"),
                "categoryName": item.get("categoryName"),
                "version": item.get("version"),
                "suspensionState": item.get("suspensionState"),
                "raw": dict(item),
            })
    else:
        raise ValueError(kind)
    return out


def resolve_enum(value: str, kind: str, raw_items: Sequence[Mapping[str, Any]], *, category_code: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """Resolve display name/code/key/id to canonical API value.

    Matching order: exact canonical value -> exact display name/id -> normalized
    equality -> unique substring match. Ambiguous matches fail instead of guessing.
    """
    candidates = _enum_candidates(kind, raw_items)
    if kind == "process" and category_code:
        in_category = [x for x in candidates if x.get("category") in (None, "", category_code)]
        if in_category:
            candidates = in_category

    canonical = "code" if kind == "category" else "key"
    display = "name"
    target = str(value).strip()

    def exact(field: str) -> List[Dict[str, Any]]:
        return [x for x in candidates if x.get(field) is not None and str(x.get(field)).strip() == target]

    for field in (canonical, display, "id"):
        matches = exact(field)
        if matches:
            canonical_values = {str(x.get(canonical)) for x in matches if x.get(canonical) not in (None, "")}
            # Different deployed versions of the same process key are semantically
            # equivalent for todo filtering. Prefer the highest version instead of
            # treating them as an ambiguity.
            if len(canonical_values) == 1:
                chosen = max(matches, key=lambda x: int(x.get("version") or 0))
                resolved = chosen.get(canonical)
                return str(resolved), chosen
            if len(matches) == 1:
                resolved = matches[0].get(canonical)
                if resolved in (None, ""):
                    raise EnumResolutionError(f"枚举项 {target!r} 缺少 {canonical} 字段")
                return str(resolved), matches[0]
            raise EnumResolutionError(_format_ambiguous(value, kind, matches))

    nt = _norm_text(target)
    normalized = [
        x for x in candidates
        if any(_norm_text(x.get(f)) == nt for f in (canonical, display, "id") if x.get(f) is not None)
    ]
    if normalized:
        canonical_values = {str(x.get(canonical)) for x in normalized if x.get(canonical) not in (None, "")}
        if len(canonical_values) == 1:
            chosen = max(normalized, key=lambda x: int(x.get("version") or 0))
            return str(chosen.get(canonical)), chosen
        if len(normalized) == 1:
            resolved = normalized[0].get(canonical)
            if resolved in (None, ""):
                raise EnumResolutionError(f"枚举项 {target!r} 缺少 {canonical} 字段")
            return str(resolved), normalized[0]
        raise EnumResolutionError(_format_ambiguous(value, kind, normalized))

    # Fuzzy contains is intentionally conservative and only succeeds when unique.
    fuzzy = []
    if nt:
        for x in candidates:
            values = [_norm_text(x.get(f)) for f in (canonical, display, "id") if x.get(f) is not None]
            if any(nt in v or v in nt for v in values if v):
                fuzzy.append(x)
    if fuzzy:
        canonical_values = {str(x.get(canonical)) for x in fuzzy if x.get(canonical) not in (None, "")}
        if len(canonical_values) == 1:
            chosen = max(fuzzy, key=lambda x: int(x.get("version") or 0))
            return str(chosen.get(canonical)), chosen
        if len(fuzzy) == 1:
            resolved = fuzzy[0].get(canonical)
            if resolved in (None, ""):
                raise EnumResolutionError(f"枚举项 {target!r} 缺少 {canonical} 字段")
            return str(resolved), fuzzy[0]
        raise EnumResolutionError(_format_ambiguous(value, kind, fuzzy))

    short = _format_candidates(kind, candidates[:30])
    raise EnumResolutionError(
        f"无法识别{_kind_cn(kind)} {value!r}。可用值：\n{short}"
    )


def _kind_cn(kind: str) -> str:
    return "流程分类" if kind == "category" else "所属流程"


def _format_candidates(kind: str, candidates: Sequence[Mapping[str, Any]]) -> str:
    if not candidates:
        return "  (接口返回空列表)"
    lines = []
    for x in candidates:
        if kind == "category":
            lines.append(f"  - {x.get('name')} => code={x.get('code')} (id={x.get('id')})")
        else:
            lines.append(
                f"  - {x.get('name')} => key={x.get('key')} "
                f"(id={x.get('id')}, category={x.get('category')})"
            )
    return "\n".join(lines)


def _format_ambiguous(value: str, kind: str, matches: Sequence[Mapping[str, Any]]) -> str:
    return f"{_kind_cn(kind)} {value!r} 匹配到多个枚举，拒绝猜测：\n" + _format_candidates(kind, matches)


def _parse_date_text(value: str, *, end: bool) -> str:
    raw = value.strip()
    # Chinese date: 2026年9月4日
    m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", raw)
    if m:
        raw = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    raw = raw.replace("/", "-").replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", raw):
        dt = datetime.strptime(raw, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d ") + ("23:59:59" if end else "00:00:00")
    # Accept seconds/minutes; normalize output.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt.endswith("%M"):
                dt = dt.replace(second=59 if end else 0)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    raise PointLionError(
        f"无法识别时间 {value!r}；支持 YYYY-MM-DD、YYYY-MM-DD HH:MM[:SS]、YYYY年M月D日"
    )


def _get_nested(obj: Any, *paths: str) -> Any:
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if not isinstance(cur, Mapping) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur is not None and cur != "":
            return cur
    return None


def _summary_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, list):
        normalized = []
        for entry in value:
            if isinstance(entry, Mapping):
                normalized.append({"key": entry.get("key"), "value": entry.get("value")})
            else:
                normalized.append({"key": None, "value": entry})
        return normalized
    if isinstance(value, Mapping):
        return [{"key": str(k), "value": v} for k, v in value.items()]
    return [{"key": None, "value": value}]


def _summary_text(value: Any) -> Optional[str]:
    items = _summary_value(value)
    if not items:
        return None
    parts = []
    for item in items:
        key = item.get("key")
        val = item.get("value")
        if val is None:
            val = ""
        if key is not None and str(key).strip():
            parts.append(f"{key}: {val}")
        else:
            parts.append(str(val))
    return " | ".join(parts)


def _build_dict_map(items: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for item in items:
        dtype = item.get("dictType")
        value = item.get("value")
        label = item.get("label")
        if dtype is None or value is None or label is None:
            continue
        result.setdefault(str(dtype), {})[str(value)] = str(label)
    return result


def _dict_label(dict_map: Optional[Mapping[str, Mapping[str, str]]], dtype: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if dict_map:
        label = dict_map.get(dtype, {}).get(str(value))
        if label is not None:
            return label
    return None


def normalize_task(task: Mapping[str, Any], dict_map: Optional[Mapping[str, Mapping[str, str]]] = None) -> Dict[str, Any]:
    pi = task.get("processInstance") if isinstance(task.get("processInstance"), Mapping) else {}
    pd = pi.get("processDefinition") if isinstance(pi.get("processDefinition"), Mapping) else {}

    # billCode is a business/process variable in newer RuoYi Office / PointLion-like
    # todo responses. Keep fallbacks narrowly scoped to billCode variables only.
    bill_code = _get_nested(
        task,
        "processInstance.billCode",
        "processInstance.variables.billCode",
        "processInstance.processVariables.billCode",
        "variables.billCode",
        "processVariables.billCode",
        "billCode",
    )
    summary = _get_nested(
        task,
        "processInstance.summary",
        "processInstance.variables.summary",
        "processInstance.processVariables.summary",
        "summary",
    )
    process_name = _get_nested(task, "processInstance.name", "processInstance.processDefinition.name")
    start_user_name = _get_nested(
        task,
        "processInstance.startUser.nickname",
        "processInstance.startUser.name",
        "processInstance.startUserNickname",
        "processInstance.startUserName",
    )
    start_time = _get_nested(task, "processInstance.createTime", "processInstance.startTime")

    normalized = {
        "display": {
            "单据编号": bill_code,
            "流程": process_name,
            "摘要": _summary_text(summary),
            "发起人": start_user_name,
            "发起时间": start_time,
            "当前任务": task.get("name"),
            "接收时间": task.get("createTime"),
        },
        "task": {
            "id": task.get("id"),
            "name": task.get("name"),
            "status": task.get("status"),
            "statusLabel": _dict_label(dict_map, "bpm_task_status", task.get("status")),
            "taskDefinitionKey": _first(task, ("taskDefinitionKey", "definitionKey")),
            "createTime": task.get("createTime"),
            "endTime": task.get("endTime"),
            "durationInMillis": task.get("durationInMillis"),
            "reason": task.get("reason"),
            "processInstanceId": _first(task, ("processInstanceId",)),
            "assigneeUser": task.get("assigneeUser"),
            "ownerUser": task.get("ownerUser"),
        },
        "processInstance": {
            "id": pi.get("id"),
            "name": pi.get("name"),
            "billCode": bill_code,
            "summary": _summary_value(summary),
            "status": pi.get("status"),
            "statusLabel": _dict_label(dict_map, "bpm_process_instance_status", pi.get("status")),
            "category": _first(pi, ("category",)) or pd.get("category"),
            "categoryName": _first(pi, ("categoryName",)) or pd.get("categoryName"),
            "businessKey": pi.get("businessKey"),
            "createTime": pi.get("createTime"),
            "startTime": pi.get("startTime"),
            "endTime": pi.get("endTime"),
            "startUser": pi.get("startUser"),
            "processDefinition": {
                "id": _first(pd, ("id", "processDefinitionId")) or pi.get("processDefinitionId"),
                "key": _first(pd, ("key", "processDefinitionKey")) or pi.get("processDefinitionKey"),
                "name": _first(pd, ("name", "processDefinitionName")) or pi.get("processDefinitionName"),
                "version": pd.get("version"),
                "suspensionState": pd.get("suspensionState"),
                "suspensionStateLabel": {1: "激活", 2: "挂起", "1": "激活", "2": "挂起"}.get(pd.get("suspensionState")),
            },
        },
    }
    return normalized


def _build_query_from_args(client: PointLionClient, args: argparse.Namespace) -> Tuple[List[Tuple[str, Any]], Dict[str, Any]]:
    page_no = args.page
    page_size = args.page_size
    task_name = args.task_name
    category_input = args.category
    category_code = args.category_code
    process_input = args.process
    process_key = args.process_key
    start_time = args.start_time
    end_time = args.end_time

    if args.query_json:
        try:
            q = json.loads(args.query_json)
        except json.JSONDecodeError as exc:
            raise PointLionError(f"--query-json 不是合法 JSON: {exc}") from exc
        if not isinstance(q, dict):
            raise PointLionError("--query-json 必须是 JSON 对象")
        aliases = {
            "name": "task_name", "taskName": "task_name", "任务名称": "task_name", "当前任务": "task_name",
            "category": "category", "流程分类": "category",
            "categoryCode": "category_code", "流程分类代码": "category_code",
            "process": "process", "所属流程": "process", "流程": "process",
            "processDefinitionKey": "process_key", "processKey": "process_key", "流程定义Key": "process_key",
            "pageNo": "page_no", "page": "page_no", "页码": "page_no",
            "pageSize": "page_size", "每页": "page_size",
        }
        target = {
            "task_name": task_name, "category": category_input, "category_code": category_code,
            "process": process_input, "process_key": process_key, "page_no": page_no, "page_size": page_size,
        }
        for key, value in q.items():
            if key in ("createTime", "发起时间"):
                if isinstance(value, list) and len(value) == 2:
                    start_time, end_time = value[0], value[1]
                elif isinstance(value, str) and "~" in value:
                    start_time, end_time = [x.strip() for x in value.split("~", 1)]
                else:
                    raise PointLionError(f"{key} 必须是 [开始时间,结束时间] 或 '开始 ~ 结束'")
                continue
            mapped = aliases.get(key)
            if not mapped:
                raise PointLionError(f"--query-json 含未知字段 {key!r}；拒绝静默忽略")
            target[mapped] = value
        task_name = target["task_name"]
        category_input = target["category"]
        category_code = target["category_code"]
        process_input = target["process"]
        process_key = target["process_key"]
        page_no = int(target["page_no"])
        page_size = int(target["page_size"])

    if page_no < 1:
        raise PointLionError("page/pageNo 必须 >= 1")
    if page_size < 1:
        raise PointLionError("pageSize 必须 >= 1")

    resolved_meta: Dict[str, Any] = {}
    if category_input and category_code:
        raise PointLionError("--category 与 --category-code 二选一")
    if process_input and process_key:
        raise PointLionError("--process 与 --process-key 二选一")

    if category_input:
        cats = client.categories()
        category_code, category_match = resolve_enum(str(category_input), "category", cats)
        resolved_meta["category"] = {
            "input": category_input,
            "code": category_code,
            "name": category_match.get("name"),
            "id": category_match.get("id"),
        }
    elif category_code:
        category_code = str(category_code)
        resolved_meta["category"] = {"input": category_code, "code": category_code, "resolution": "raw-code"}

    if process_input:
        defs = client.process_definitions()
        process_key, process_match = resolve_enum(str(process_input), "process", defs, category_code=category_code)
        resolved_meta["process"] = {
            "input": process_input,
            "key": process_key,
            "name": process_match.get("name"),
            "id": process_match.get("id"),
            "category": process_match.get("category"),
        }
    elif process_key:
        process_key = str(process_key)
        resolved_meta["process"] = {"input": process_key, "key": process_key, "resolution": "raw-key"}

    if bool(start_time) ^ bool(end_time):
        raise PointLionError("发起时间是区间：--start-time 与 --end-time 必须同时提供")
    if start_time and end_time:
        start_time = _parse_date_text(str(start_time), end=False)
        end_time = _parse_date_text(str(end_time), end=True)
        if start_time > end_time:
            raise PointLionError("开始时间不能晚于结束时间")

    params: List[Tuple[str, Any]] = [
        ("pageNo", page_no),
        ("pageSize", page_size),
    ]
    if task_name:
        params.append(("name", task_name))
    if category_code:
        params.append(("category", category_code))
    if process_key:
        params.append(("processDefinitionKey", process_key))
    if start_time and end_time:
        # Axios qs.stringify(params, { allowDots: true }) serializes arrays using
        # indexed brackets; Spring binds this to createTime[0]/createTime[1].
        params.append(("createTime[0]", start_time))
        params.append(("createTime[1]", end_time))

    canonical = {
        "pageNo": page_no,
        "pageSize": page_size,
        "name": task_name or None,
        "category": category_code or None,
        "processDefinitionKey": process_key or None,
        "createTime": [start_time, end_time] if start_time and end_time else None,
    }
    resolved_meta["canonicalQuery"] = canonical
    return params, resolved_meta


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    all_rows = [list(headers)] + [["" if v is None else str(v) for v in row] for row in rows]
    widths = [0] * len(headers)
    for row in all_rows:
        for i, value in enumerate(row):
            widths[i] = min(36, max(widths[i], len(value)))

    def crop(s: str, w: int) -> str:
        return s if len(s) <= w else s[: max(1, w - 1)] + "…"

    lines = []
    for idx, row in enumerate(all_rows):
        cells = [crop(str(row[i]), widths[i]).ljust(widths[i]) for i in range(len(headers))]
        lines.append(" | ".join(cells).rstrip())
        if idx == 0:
            lines.append("-+-".join("-" * w for w in widths))
    return "\n".join(lines)


def cmd_enums(client: PointLionClient, args: argparse.Namespace) -> int:
    categories = _enum_candidates("category", client.categories())
    processes = _enum_candidates("process", client.process_definitions())
    dict_items = client.dict_data() if args.with_dicts else []
    bpm_dict_items = [x for x in dict_items if str(x.get("dictType", "")).startswith("bpm_")]
    # Raw backend fields remain available in raw; canonical values are top-level.
    result = {
        "category": [
            {k: x.get(k) for k in ("id", "name", "code")} | {"raw": x.get("raw")}
            for x in categories
        ],
        "processDefinition": [
            {k: x.get(k) for k in ("id", "name", "key", "category", "categoryName", "version", "suspensionState")} | {"raw": x.get("raw")}
            for x in processes
        ],
        "dictData": bpm_dict_items,
    }
    if args.format == "json":
        _print_json(result)
    else:
        print("[流程分类] name => code")
        print(_format_candidates("category", categories))
        print("\n[所属流程] name => key")
        print(_format_candidates("process", processes))
        if args.with_dicts:
            print("\n[BPM 字典枚举] dictType: value => label")
            for item in bpm_dict_items:
                print(f"  - {item.get('dictType')}: {item.get('value')} => {item.get('label')}")
    return 0


def cmd_resolve(client: PointLionClient, args: argparse.Namespace) -> int:
    out: Dict[str, Any] = {}
    category_code: Optional[str] = None
    if args.category:
        category_code, match = resolve_enum(args.category, "category", client.categories())
        out["category"] = {
            "input": args.category, "code": category_code, "name": match.get("name"), "id": match.get("id")
        }
    if args.process:
        key, match = resolve_enum(args.process, "process", client.process_definitions(), category_code=category_code)
        out["processDefinition"] = {
            "input": args.process, "key": key, "name": match.get("name"), "id": match.get("id"),
            "category": match.get("category"), "categoryName": match.get("categoryName"),
        }
    _print_json(out)
    return 0


def cmd_query(client: PointLionClient, args: argparse.Namespace) -> int:
    params, resolved = _build_query_from_args(client, args)
    items, total, page_raw = client.query_todos(params)
    if args.format == "raw":
        _print_json(page_raw)
        return 0

    enum_warning = None
    dict_map: Dict[str, Dict[str, str]] = {}
    if not args.no_decode_dicts:
        try:
            dict_map = _build_dict_map(client.dict_data())
        except PointLionError as exc:
            # Todo data itself is more important than cosmetic labels. Preserve raw
            # numeric enum values and make the missing dictionary explicit.
            enum_warning = str(exc)
    normalized = [normalize_task(item, dict_map) for item in items]
    result: Dict[str, Any] = {
        "total": total,
        "count": len(items),
        "resolved": resolved,
        "enumWarning": enum_warning,
        "items": normalized,
    }
    if args.include_raw:
        for i, raw in enumerate(items):
            result["items"][i]["raw"] = raw

    if args.format == "json":
        _print_json(result)
    else:
        rows = []
        for item in normalized:
            d = item["display"]
            rows.append([
                d.get("单据编号"), d.get("流程"), d.get("摘要"), d.get("发起人"),
                d.get("发起时间"), d.get("当前任务"), d.get("接收时间"),
            ])
        print(_table(rows, ["单据编号", "流程", "摘要", "发起人", "发起时间", "当前任务", "接收时间"]))
        print(f"\n共 {total} 条，本页 {len(items)} 条")
    return 0


def cmd_doctor(client: PointLionClient, args: argparse.Namespace) -> int:
    checks: Dict[str, Any] = {
        "apiPrefix": client.config.api_prefix,
        "endpoints": {},
        "auth": "Dano Login Session via Credential Broker",
        "tenantHeader": client.config.tenant_header if client.config.tenant_id else None,
    }
    failures = 0
    try:
        cats = client.categories()
        checks["endpoints"]["categorySimpleList"] = {
            "ok": True, "path": client.config.category_path, "count": len(cats),
            "hasNameCode": all(("name" in x and ("code" in x or "value" in x)) for x in cats) if cats else True,
        }
    except Exception as exc:
        failures += 1
        checks["endpoints"]["categorySimpleList"] = {"ok": False, "path": client.config.category_path, "error": str(exc)}
    try:
        defs = client.process_definitions()
        checks["endpoints"]["processDefinitionSimpleList"] = {
            "ok": True, "path": client.config.process_definition_path, "count": len(defs),
            "hasNameKey": all(("name" in x and ("key" in x or "processDefinitionKey" in x or "value" in x)) for x in defs) if defs else True,
        }
    except Exception as exc:
        failures += 1
        checks["endpoints"]["processDefinitionSimpleList"] = {"ok": False, "path": client.config.process_definition_path, "error": str(exc)}
    dict_map: Dict[str, Dict[str, str]] = {}
    try:
        dict_items = client.dict_data()
        dict_map = _build_dict_map(dict_items)
        checks["endpoints"]["dictDataSimpleList"] = {
            "ok": True, "path": client.config.dict_data_path, "count": len(dict_items),
            "bpmTaskStatusCount": len(dict_map.get("bpm_task_status", {})),
            "bpmProcessInstanceStatusCount": len(dict_map.get("bpm_process_instance_status", {})),
        }
    except Exception as exc:
        failures += 1
        checks["endpoints"]["dictDataSimpleList"] = {"ok": False, "path": client.config.dict_data_path, "error": str(exc)}
    try:
        items, total, _ = client.query_todos([("pageNo", 1), ("pageSize", 1)])
        sample = normalize_task(items[0], dict_map) if items else None
        checks["endpoints"]["todoPage"] = {
            "ok": True, "path": client.config.todo_path, "total": total,
            "sampleFieldDetection": sample,
        }
    except Exception as exc:
        failures += 1
        checks["endpoints"]["todoPage"] = {"ok": False, "path": client.config.todo_path, "error": str(exc)}
    checks["responses"] = client.responses
    checks["ok"] = failures == 0
    _print_json(checks)
    return 0 if failures == 0 else 2


def _config_from_args(args: argparse.Namespace) -> ApiConfig:
    configured_tenant = str(getattr(local_config, "TENANT_ID", "") or "").strip()
    tenant_id = args.tenant_id or configured_tenant
    return ApiConfig(
        api_prefix=args.api_prefix if args.api_prefix is not None else str(getattr(local_config, "API_PREFIX", DEFAULT_API_PREFIX)),
        tenant_id=tenant_id or None,
        tenant_header=args.tenant_header or str(getattr(local_config, "TENANT_HEADER", "tenant-id")),
        todo_path=args.todo_path or str(getattr(local_config, "TODO_PATH", DEFAULT_TODO_PATH)),
        category_path=args.category_path or str(getattr(local_config, "CATEGORY_PATH", DEFAULT_CATEGORY_PATH)),
        process_definition_path=args.process_definition_path or str(getattr(local_config, "PROCESS_DEFINITION_PATH", DEFAULT_PROCESS_DEFINITION_PATH)),
        dict_data_path=args.dict_data_path or str(getattr(local_config, "DICT_DATA_PATH", DEFAULT_DICT_DATA_PATH)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PointLion OA / BPM 我的待办只读查询（支持动态枚举识别）"
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--api-prefix", help=f"API 前缀，默认 {DEFAULT_API_PREFIX}")
    parser.add_argument("--tenant-id", help=argparse.SUPPRESS)
    parser.add_argument("--tenant-header", help=argparse.SUPPRESS)
    parser.add_argument("--todo-path", help=f"待办接口路径，默认 {DEFAULT_TODO_PATH}")
    parser.add_argument("--category-path", help=f"流程分类枚举接口，默认 {DEFAULT_CATEGORY_PATH}")
    parser.add_argument("--process-definition-path", help=f"流程定义枚举接口，默认 {DEFAULT_PROCESS_DEFINITION_PATH}")
    parser.add_argument("--dict-data-path", help=f"系统字典枚举接口，默认 {DEFAULT_DICT_DATA_PATH}")

    sub = parser.add_subparsers(dest="command", required=True)

    p_enums = sub.add_parser("enums", help="读取流程分类、所属流程的动态枚举")
    p_enums.add_argument("--format", choices=("json", "table"), default="json")
    p_enums.add_argument("--with-dicts", action="store_true", help="同时读取所有 bpm_* 系统字典枚举")
    p_enums.set_defaults(func=cmd_enums)

    p_resolve = sub.add_parser("resolve", help="把中文名称/代码/ID 解析成后端 canonical code/key")
    p_resolve.add_argument("--category", help="流程分类：可传名称、code 或 id")
    p_resolve.add_argument("--process", help="所属流程：可传名称、key 或 id")
    p_resolve.set_defaults(func=cmd_resolve)

    p_query = sub.add_parser("query", help="查询我的待办")
    p_query.add_argument("--task-name", "--任务名称", dest="task_name", help="任务名称 -> name，例如 领导审批")
    p_query.add_argument("--category", "--流程分类", dest="category", help="流程分类名称/code/id；自动枚举解析")
    p_query.add_argument("--category-code", help="直接传 category code，不请求枚举解析")
    p_query.add_argument("--process", "--所属流程", dest="process", help="所属流程名称/key/id；自动枚举解析")
    p_query.add_argument("--process-key", help="直接传 processDefinitionKey，不请求枚举解析")
    p_query.add_argument("--start-time", "--开始时间", dest="start_time", help="发起时间开始")
    p_query.add_argument("--end-time", "--结束时间", dest="end_time", help="发起时间结束")
    p_query.add_argument("--page", type=int, default=1, help="pageNo，默认 1")
    p_query.add_argument("--page-size", type=int, default=10, help="pageSize，默认 10")
    p_query.add_argument(
        "--query-json",
        help='中文/英文条件 JSON，例如 {"任务名称":"领导审批","所属流程":"请假申请","发起时间":["2026-09-01","2026-09-04"]}',
    )
    p_query.add_argument("--format", choices=("json", "table", "raw"), default="json")
    p_query.add_argument("--include-raw", action="store_true", help="标准化结果同时附带服务器原始 task JSON")
    p_query.add_argument("--no-decode-dicts", action="store_true", help="不请求系统字典，不解析 task/process status 标签")
    p_query.set_defaults(func=cmd_query)

    p_doctor = sub.add_parser("doctor", help="只读校验 Dano 登录认证、四个查询接口和返回字段结构")
    p_doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = _config_from_args(args)
        client = PointLionClient(config)
        return int(args.func(client, args))
    except PointLionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
