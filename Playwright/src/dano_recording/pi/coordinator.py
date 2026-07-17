"""Recording-scoped planner and isolated reviewer Pi sessions."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
from enum import StrEnum
from typing import Any, Awaitable, Callable
from uuid import uuid4

from dano_recording.capture.redaction import RedactionPolicy
from dano_recording.pi_semantic_ops import ALLOWED_OPERATIONS

from .events import PiSessionStatus
from .sessions import PiSidecarClient

StateProvider = Callable[[str], Awaitable[dict[str, Any]]]
SubmissionHandler = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]


class PiPlanMode(StrEnum):
    INITIAL = "initial"
    REPLAN = "replan"
    STEP_NAMING = "step_naming"
    BUSINESS_DESCRIPTION = "business_description"
    RECOMMENDATIONS = "llm_recommendations"


_NAMING_OPERATIONS = frozenset({
    "set_step_name",
    "set_step_title",
    "set_capability_name",
    "set_capability_title",
    "set_capability_description",
})
_PLAN_MODE_OPERATIONS: dict[PiPlanMode, frozenset[str]] = {
    PiPlanMode.INITIAL: ALLOWED_OPERATIONS,
    PiPlanMode.REPLAN: ALLOWED_OPERATIONS,
    PiPlanMode.STEP_NAMING: _NAMING_OPERATIONS,
    PiPlanMode.BUSINESS_DESCRIPTION: frozenset({"set_business_description"}),
    PiPlanMode.RECOMMENDATIONS: frozenset(),
}


@dataclass(slots=True)
class _Binding:
    recording_id: str
    role: str


@dataclass(slots=True)
class _ActiveTurn:
    expected_revision: int
    mode: str
    task_mode: PiPlanMode | None = None
    allowed_operations: frozenset[str] = frozenset()
    inflight: bool = False
    committed: bool = False
    attempted: bool = False


class RecordingPiCoordinator:
    ROLES = ("planner", "acceptance", "security", "compliance")

    def __init__(
        self,
        *,
        client: PiSidecarClient,
        state_provider: StateProvider,
        submission_handler: SubmissionHandler,
        event_sink: EventSink | None = None,
    ) -> None:
        self.client = client
        self.state_provider = state_provider
        self.submission_handler = submission_handler
        self.event_sink = event_sink
        self.bindings: dict[str, _Binding] = {}
        self.sessions: dict[tuple[str, str], PiSessionStatus] = {}
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.active_turns: dict[str, _ActiveTurn] = {}

    async def ensure_sessions(
        self,
        recording_id: str,
        persisted: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, PiSessionStatus]:
        result: dict[str, PiSessionStatus] = {}
        persisted = persisted or {}
        for role in self.ROLES:
            key = (recording_id, role)
            status = self.sessions.get(key)
            if status is None:
                prior = persisted.get(role) or {}
                session_id = str(prior.get("session_id") or uuid4())
                bound = self.bindings.get(session_id)
                while bound is not None and (
                    bound.recording_id != recording_id or bound.role != role
                ):
                    # Persisted/corrupt duplicate identities must never collapse
                    # two reviewer roles (or two recordings) into one session.
                    session_id = str(uuid4())
                    bound = self.bindings.get(session_id)
                status = PiSessionStatus(
                    session_id=session_id,
                    role=role,
                    session_path=str(prior.get("session_path") or ""),
                )
                opened = await self.client.open_session(
                    session_id=status.session_id,
                    recording_id=recording_id,
                    role=role,
                    session_path=status.session_path,
                )
                status.session_path = str(opened.get("session_path") or status.session_path)
                self.sessions[key] = status
                self.bindings[status.session_id] = _Binding(recording_id, role)
            result[role] = status
        return result

    async def _record_cancelled_turn(
        self,
        recording_id: str,
        status: PiSessionStatus,
    ) -> None:
        """Expose cancellation as durable progress instead of a silent task exit."""

        status.state = "cancelled"
        status.last_error = "turn cancelled"
        # Revoke write authority before awaiting the sidecar abort RPC.
        self.active_turns.pop(status.session_id, None)
        # Cancelling the Python analysis task must abort the native AgentSession
        # too. Otherwise the detached sidecar turn could still call a write tool
        # after the user saw a successful cancellation.
        cancel_task = asyncio.create_task(self.client.cancel(status.session_id))
        try:
            await asyncio.shield(cancel_task)
        except (Exception, asyncio.CancelledError):
            # Removing the active turn below is the final write-authority guard
            # if the sidecar itself disappeared during cancellation.
            pass
        if self.event_sink is None:
            return
        try:
            await self.event_sink(recording_id, {
                "session_id": status.session_id,
                "role": status.role,
                "turn": status.turn,
                "event": {"type": "turn_cancelled", "aborted": True},
                "status": status.model_dump(mode="json"),
            })
        except Exception:
            # Cancellation of the model turn is authoritative even if the
            # optional event transport is temporarily unavailable.
            pass

    async def plan(
        self,
        recording_id: str,
        revision: int,
        *,
        mode: PiPlanMode = PiPlanMode.INITIAL,
    ) -> dict[str, Any]:
        if not isinstance(mode, PiPlanMode):
            raise TypeError("mode must be a PiPlanMode")
        sessions = await self.ensure_sessions(recording_id)
        status = sessions["planner"]
        async with self.locks[status.session_id]:
            status.state = "running"
            self.active_turns[status.session_id] = _ActiveTurn(
                revision,
                "semantic",
                task_mode=mode,
                allowed_operations=_PLAN_MODE_OPERATIONS[mode],
            )
            if mode is PiPlanMode.STEP_NAMING:
                task_scope = (
                    "This naming turn may use only set_step_name, set_step_title, "
                    "set_capability_name, set_capability_title and "
                    "set_capability_description."
                )
            elif mode is PiPlanMode.BUSINESS_DESCRIPTION:
                task_scope = (
                    "This description turn may use only set_business_description."
                )
            elif mode is PiPlanMode.RECOMMENDATIONS:
                task_scope = (
                    "This recommendation turn is read-only. Inspect unresolved review "
                    "items and submit an empty operations list."
                )
            else:
                task_scope = (
                    "For initial/replan turns, legally fill flow goal/action/business_description, "
                    "step name/title, capability name/title/description, schemas, field axes and "
                    "capability membership through their dedicated operations. Use the flow's "
                    "lineage_id as target_uuid for flow-level operations."
                )
            prompt = (
                "You are the semantic planner for one Dano recording. Query only the evidence you need with "
                "list_transactions/get_transaction, trace_field, trace_control, trace_submit_path, "
                "get_request_response, get_enum_evidence, search_js_binding and list_unbound_requests. "
                "Never infer an ID or evidence reference, never request secrets or raw JavaScript, and never "
                "overwrite any manual semantic axis. "
                f"{task_scope} Submit one atomic whitelist batch by calling "
                "apply_semantic_operations exactly once, even when the operations list is empty.\n"
                f"Task mode: {mode.value}; expected revision: {revision}."
            )
            try:
                out = await self.client.prompt(session_id=status.session_id, prompt=prompt, revision=revision)
                turn = self.active_turns[status.session_id]
                if not turn.committed:
                    raise RuntimeError("Pi semantic turn completed without its single atomic commit")
                status.turn = int(out.get("turn") or status.turn)
                status.session_path = str(out.get("session_path") or status.session_path)
                return out
            except asyncio.CancelledError:
                await self._record_cancelled_turn(recording_id, status)
                raise
            except Exception as exc:
                status.last_error = str(exc)
                raise
            finally:
                if status.state != "cancelled":
                    status.state = "idle"
                self.active_turns.pop(status.session_id, None)

    async def repair(self, recording_id: str, revision: int) -> dict[str, Any]:
        sessions = await self.ensure_sessions(recording_id)
        status = sessions["planner"]
        async with self.locks[status.session_id]:
            status.state = "running"
            self.active_turns[status.session_id] = _ActiveTurn(
                revision,
                "semantic",
                allowed_operations=ALLOWED_OPERATIONS,
            )
            try:
                out = await self.client.prompt(
                    session_id=status.session_id,
                    revision=revision,
                    prompt=(
                        "Read get_validation_report, then query only referenced evidence. Submit exactly one "
                        "apply_semantic_operations batch containing the smallest safe whitelist repair. Never "
                        "change facts, raw requests, or a manual field axis."
                    ),
                )
                if not self.active_turns[status.session_id].committed:
                    raise RuntimeError("Pi repair turn completed without its single atomic commit")
                return out
            except asyncio.CancelledError:
                await self._record_cancelled_turn(recording_id, status)
                raise
            except Exception as exc:
                status.last_error = str(exc)
                raise
            finally:
                if status.state != "cancelled":
                    status.state = "idle"
                self.active_turns.pop(status.session_id, None)

    async def review(self, recording_id: str, revision: int) -> list[dict[str, Any]]:
        sessions = await self.ensure_sessions(recording_id)

        async def one(role: str) -> dict[str, Any]:
            status = sessions[role]
            async with self.locks[status.session_id]:
                status.state = "running"
                self.active_turns[status.session_id] = _ActiveTurn(revision, "review")
                try:
                    result = await self.client.prompt(
                        session_id=status.session_id,
                        revision=revision,
                        prompt=(
                            f"Act only as the isolated {role} reviewer. Read the deterministic validation and "
                            "query referenced evidence only. Do not mutate the draft. Submit exactly one grounded "
                            "submit_recording_review verdict. Review findings are advisory; never invent a "
                            "ContractFault or evidence ID."
                        ),
                    )
                    if not self.active_turns[status.session_id].committed:
                        raise RuntimeError(f"{role} review completed without its single submission")
                    return {"role": role, **result}
                except asyncio.CancelledError:
                    await self._record_cancelled_turn(recording_id, status)
                    raise
                except Exception as exc:
                    status.last_error = str(exc)
                    raise
                finally:
                    if status.state != "cancelled":
                        status.state = "idle"
                    self.active_turns.pop(status.session_id, None)

        return list(await asyncio.gather(*(one(role) for role in self.ROLES[1:])))

    async def handle_tool(self, session_id: str, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        binding = self.bindings.get(session_id)
        if binding is None:
            raise PermissionError("unknown Pi session")
        recording_id, role = binding.recording_id, binding.role
        read_tools = {
            "list_transactions", "get_transaction", "get_request_response", "trace_control",
            "trace_field", "trace_submit_path", "get_enum_evidence", "search_js_binding",
            "list_unbound_requests", "get_validation_report",
        }
        if tool in read_tools:
            state = await self.state_provider(recording_id)
            return _select_tool_view(tool, state, params)
        allowed = {
            "planner": {"apply_semantic_operations"},
            "acceptance": {"submit_recording_review"},
            "security": {"submit_recording_review"},
            "compliance": {"submit_recording_review"},
        }
        if tool not in allowed[role]:
            raise PermissionError(f"{role} session cannot call {tool}")
        submission = _bounded_json(params, max_bytes=256_000)
        turn = self.active_turns.get(session_id)
        required_mode = "review" if tool == "submit_recording_review" else "semantic"
        if turn is None or turn.mode != required_mode:
            raise PermissionError(f"{tool} is not allowed outside its active Pi turn")
        if tool == "apply_semantic_operations":
            operations = submission.get("operations")
            if not isinstance(operations, list):
                raise ValueError(
                    "apply_semantic_operations requires a top-level operations list"
                )
            forbidden = sorted({
                str(operation.get("op") or "")
                for operation in operations
                if isinstance(operation, dict)
                and str(operation.get("op") or "") not in turn.allowed_operations
            })
            if forbidden:
                task_mode = turn.task_mode.value if turn.task_mode else "repair"
                raise PermissionError(
                    f"operations {forbidden} are not allowed for Pi task mode "
                    f"{task_mode}"
                )
        expected = int(submission.get("expected_revision", -1))
        if expected != turn.expected_revision:
            raise ValueError(
                f"Pi turn revision conflict: expected {expected}, active {turn.expected_revision}"
            )
        if turn.attempted or turn.inflight or turn.committed:
            raise ValueError("each Pi turn permits exactly one submission")
        # From this point the outcome can be ambiguous (for example the
        # repository committed and the response transport failed). A second
        # tool call in the same model turn is therefore never allowed.
        turn.attempted = True
        turn.inflight = True
        if tool == "submit_recording_review":
            submission["role"] = role
            # Session identity is server-owned evidence that the three reviewers
            # were actually isolated; a model cannot choose or spoof this value.
            submission["pi_session_id"] = session_id
            submission["_server_model_id"] = self.client.model_id
            server_tool = tool
        else:
            server_tool = "submit_recording_plan"
        try:
            result = await self.submission_handler(recording_id, server_tool, submission)
        except Exception:
            turn.inflight = False
            raise
        turn.inflight = False
        turn.committed = True
        return result

    async def handle_event(self, message: dict[str, Any]) -> None:
        session_id = str(message.get("session_id") or "")
        binding = self.bindings.get(session_id)
        if not binding:
            return
        status = self.sessions[(binding.recording_id, binding.role)]
        event = dict(message.get("event") or {})
        event_type = str(event.get("type") or "")
        status.turn = int(message.get("turn") or status.turn)
        if event_type == "tool_execution_start":
            status.tool_calls += 1
        elif event_type == "auto_retry_start":
            status.retries += 1
        elif event_type == "compaction_end" and not event.get("aborted"):
            status.compactions += 1
        elif event_type in {"turn_end", "message_end"}:
            usage = event.get("usage") or (event.get("message") or {}).get("usage")
            status.usage.add(usage)
        if event.get("errorMessage") or event.get("finalError"):
            status.last_error = str(event.get("errorMessage") or event.get("finalError"))
        if self.event_sink:
            await self.event_sink(binding.recording_id, {
                "session_id": session_id,
                "role": binding.role,
                "turn": status.turn,
                "event": event,
                "status": status.model_dump(mode="json"),
            })

    def status(self, recording_id: str) -> dict[str, Any]:
        return {
            role: value.model_dump(mode="json")
            for (rid, role), value in self.sessions.items()
            if rid == recording_id
        }


_PI_TOOL_OMITTED_KEYS = frozenset({
    "rawjavascript", "rawjs", "javascript", "scriptsource", "scripttext",
    "sourcetext", "rawsource", "sourcecontent", "sourcecontents",
    "sourcescontent", "requestbody", "responsebody", "postdata", "body",
    "bodytemplate", "bodysource", "responsejson", "requestheaders",
    "responseheaders", "headers", "cookies", "setcookie", "valueref",
    "redactedsample", "samplevalue", "sampleinputs",
})
_PI_TOOL_REDACTION = RedactionPolicy()


def _pi_safe_tool_value(value: Any, *, key: str | None = None) -> Any:
    """Defense-in-depth projection for every Pi read-tool response.

    The application state provider already returns a narrow projection. This
    second independent boundary makes raw bodies/code and common plaintext
    secret/identity values unrepresentable even for a faulty provider.
    """

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        parent_key = str(key or "").casefold().replace("_", "").replace("-", "")
        schema_property_container = parent_key in {
            "properties", "patternproperties", "definitions", "$defs",
        }
        for raw_key, item in value.items():
            item_key = str(raw_key)
            normalised = item_key.casefold().replace("_", "").replace("-", "")
            if normalised in _PI_TOOL_OMITTED_KEYS and not schema_property_container:
                continue
            output[item_key] = _pi_safe_tool_value(item, key=item_key)
        return output
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_pi_safe_tool_value(item) for item in value]
    if isinstance(value, bytes):
        return {"omitted": "binary", "size": len(value)}
    if isinstance(value, str):
        return _PI_TOOL_REDACTION.redact_value(value, key=key)
    return value


def _bounded_tool_json(value: Any, *, max_bytes: int) -> dict[str, Any]:
    return _bounded_json(_pi_safe_tool_value(value), max_bytes=max_bytes)


def _select_tool_view(tool: str, state: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    projection = state.get("pi_projection") or state
    requests = [value for value in projection.get("requests") or [] if isinstance(value, dict)]
    steps = [value for value in projection.get("steps") or [] if isinstance(value, dict)]
    field_evidence = [value for value in state.get("field_evidence") or [] if isinstance(value, dict)]
    if tool == "list_transactions":
        transactions = projection.get("transactions") or []
        return _bounded_tool_json({"items": transactions}, max_bytes=250_000)
    if tool == "get_transaction":
        wanted = str(params.get("transaction_uuid") or "")
        candidates = projection.get("transactions") or []
        items = [value for value in candidates if wanted in {
            str(value.get("transaction_uuid") or ""),
            str(value.get("transaction_id") or ""),
        }]
        return _bounded_tool_json({"items": items}, max_bytes=250_000)
    if tool == "get_request_response":
        wanted = str(params.get("request_uuid") or "")
        allowed_keys = {
            "request_id", "request_definition_id", "observation_id", "method", "path", "url",
            "status", "request_schema", "response_schema", "content_type", "disposition", "role",
            "evidence_ids", "action_id", "initiator",
        }
        items = [{key: value[key] for key in allowed_keys if key in value} for value in requests if wanted in {
            str(value.get("request_id") or ""), str(value.get("request_definition_id") or ""),
            str(value.get("observation_id") or ""),
        }]
        return _bounded_tool_json({"items": items}, max_bytes=250_000)
    if tool == "trace_control":
        wanted = str(params.get("control_uuid") or "")
        items = [value for value in field_evidence if wanted in {
            str(value.get("control_uuid") or ""), str(value.get("control_evidence_id") or ""),
            *[str(item) for item in value.get("control_evidence_ids") or []],
        }]
        return _bounded_tool_json({"items": items}, max_bytes=250_000)
    if tool in {"trace_field", "trace_submit_path"}:
        wanted = str(params.get("field_uuid") or "")
        items = [value for value in field_evidence if wanted in {
            str(value.get("field_uuid") or ""), str(value.get("field_contract_id") or ""),
            str(value.get("field_id") or ""),
        }]
        related_steps = [value for value in steps if any(
            wanted in {
                str(field.get("field_uuid") or ""), str(field.get("field_contract_id") or ""),
                str(field.get("field_id") or ""),
            } for field in value.get("params") or [] if isinstance(field, dict)
        )]
        return _bounded_tool_json({"items": items, "steps": related_steps}, max_bytes=350_000)
    if tool == "get_enum_evidence":
        wanted = str(params.get("field_uuid") or "")
        items = state.get("enum_evidence") or []
        return _bounded_tool_json({"items": [item for item in items if wanted in {
            str(item.get("field_uuid") or ""), str(item.get("field_contract_id") or ""),
            str(item.get("field_id") or ""),
        }]}, max_bytes=250_000)
    if tool == "search_js_binding":
        query = str(params.get("query") or "").lower()
        wanted = str(params.get("field_uuid") or "")
        bindings = projection.get("js_bindings") or state.get("js_bindings") or []
        items = [deepcopy(value) for value in bindings if isinstance(value, dict) and (
            not wanted or wanted in {str(value.get("field_uuid") or ""), str(value.get("field_contract_id") or "")}
        ) and query in json.dumps(value, ensure_ascii=False, default=str).lower()]
        # state_provider has already removed raw source; enforce that boundary a
        # second time before returning static binding summaries to Pi.
        for item in items:
            for key in ("source", "raw_javascript", "script_text", "sourcesContent"):
                item.pop(key, None)
        return _bounded_tool_json({"items": items}, max_bytes=250_000)
    if tool == "list_unbound_requests":
        bound = {str(value.get("request_id") or "") for value in steps}
        items = [value for value in requests if str(value.get("request_id") or "") not in bound and str(
            value.get("disposition") or value.get("role") or ""
        ) not in {"ignored_resource", "unsupported"}]
        return _bounded_tool_json({"items": items}, max_bytes=350_000)
    return _bounded_tool_json(state.get("validation") or {}, max_bytes=250_000)


def _bounded_json(value: Any, *, max_bytes: int) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False, default=str).encode()
    if len(raw) > max_bytes:
        raise ValueError(f"Pi tool payload exceeds {max_bytes} bytes")
    return json.loads(raw)
