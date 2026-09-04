"""8077 录制入口的 PI-only 后端。

现有 PageRecorder 仍连 ws://127.0.0.1:8077/onboarding/page/record。
本模块拉起 Pi_check，并把该 WebSocket 原样代理过去。
旧 RecordingGateway / analyze-recording-evidence / submit_recording_plan 不得再启动。
能力内容只来自 PI 提交；这里只做进程、传输和落库。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from dano.infra.run_logging import emit_run_event
from dano.shared.enums import AssetType, Subsystem
from dano.shared.models import Scope

log = structlog.get_logger(__name__)

PI_ONLY_NOTICE = "PI 是唯一语义决策者；旧录制逻辑绝不启动。"
DEFAULT_PORT = 18080
HEALTH_TIMEOUT_SEC = 25
WS_MAX_BYTES = 32 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[3]
PI_CHECK_ROOT = REPO_ROOT / "Pi_check"


def should_adopt_existing(*, explicit_url: bool, own_process_alive: bool, healthy: bool) -> bool:
    if own_process_alive and healthy:
        return True
    return bool(explicit_url and healthy)


def explicit_sidecar_url() -> bool:
    return bool(str(os.environ.get("PI_CHECK_URL") or "").strip())


def listen_pids(port: int) -> list[int]:
    if os.name != "nt":
        return []
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: list[int] = []
    needle = f":{int(port)} "
    for line in (completed.stdout or "").splitlines():
        if "LISTENING" not in line.upper() or needle not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid != 4 and pid not in pids:
            pids.append(pid)
    return pids


def free_listen_port(port: int, keep_pid: int | None = None) -> None:
    for pid in listen_pids(port):
        if keep_pid and pid == keep_pid:
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )


def sidecar_enabled() -> bool:
    if os.environ.get("PI_CHECK_DISABLE") == "1":
        return False
    if os.environ.get("PI_CHECK_FORCE") == "1":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if "pytest" in sys.modules:
        return False
    return True


def sidecar_port() -> int:
    raw = os.environ.get("PI_CHECK_PORT") or os.environ.get("PI_CHECK_INTERNAL_PORT") or ""
    try:
        port = int(raw)
    except ValueError:
        port = DEFAULT_PORT
    return port if port > 0 else DEFAULT_PORT


def sidecar_base_url() -> str:
    configured = str(os.environ.get("PI_CHECK_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://127.0.0.1:{sidecar_port()}"


def sidecar_ws_url() -> str:
    parsed = urlparse(sidecar_base_url())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/onboarding/page/record"


def sidecar_child_env(base: dict[str, str] | None = None, settings: Any = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    if settings is None:
        try:
            from dano.config import get_settings

            settings = get_settings()
        except Exception:  # noqa: BLE001
            settings = None
    if settings is not None:
        key = str(getattr(settings, "pi_api_key", "") or "").strip()
        base_url = str(getattr(settings, "pi_base_url", "") or "").strip()
        model = str(getattr(settings, "pi_model", "") or "").strip()
        provider = str(getattr(settings, "pi_provider", "") or "").strip() or "openai-compat"
        if key:
            env["DANO_PI_API_KEY"] = key
            env.setdefault("PI_API_KEY", key)
        if base_url:
            env["DANO_PI_BASE_URL"] = base_url
            env.setdefault("PI_BASE_URL", base_url)
        if model:
            env["DANO_PI_MODEL"] = model
            env.setdefault("PI_MODEL", model)
        env["DANO_PI_PROVIDER"] = provider
        env.setdefault("PI_PROVIDER", provider)
    env["PI_CHECK_PORT"] = str(sidecar_port())
    env["DANO_GATEWAY_SIDECAR"] = "1"
    return env


def _json_object(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def adapt_pi_check_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not row.get("id"):
        return None
    return {
        "id": str(row.get("id") or ""),
        "action": str(row.get("action") or row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "goal_summary": str(row.get("goal_summary") or "")[:80],
        "capability_count": int(row.get("capability_count") or 0),
        "request_count": int(row.get("request_count") or 0),
        "created_at": str(row.get("created_at") or ""),
        "published": bool(row.get("published")),
        "machine_verification_ran": False,
        "machine_verification_required": False,
        "machine_verification_status": "",
        "skill_lifecycle": "stage_six_done",
        "notice": PI_ONLY_NOTICE,
    }


def pi_result_storage_body(
    *,
    action: str,
    title: str,
    goal: Any,
    tenant: str,
    subsystem: str,
    draft: dict[str, Any],
    request_count: int = 0,
) -> dict[str, Any]:
    from dano.onboarding.recording_results import RECORDING_RESULT_KIND, recording_display_title
    from dano.onboarding.recording_workflow import _draft_fingerprint

    goal_payload = dict(goal) if isinstance(goal, dict) else {"text": str(goal or "")}
    capabilities = list(draft.get("capabilities") or []) if isinstance(draft, dict) else []
    return {
        "kind": RECORDING_RESULT_KIND,
        "action": action,
        "title": recording_display_title(user_title=title, draft=draft),
        "goal": goal_payload,
        "tenant": tenant,
        "subsystem": subsystem,
        "flow_spec": draft,
        "capability_count": len(capabilities),
        "request_count": int(request_count or 0),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "published": False,
        "machine_verification_ran": False,
        "machine_verification_required": False,
        "fingerprint": _draft_fingerprint(draft),
        "skill_export_description": "",
        "skill_export_description_origin": "generated",
        "notice": PI_ONLY_NOTICE,
        "recording_backend": "pi_check",
    }


async def persist_pi_submitted_result(
    *,
    tenant: str,
    subsystem: str,
    action: str,
    title: str,
    goal: Any,
    draft: dict[str, Any],
    request_count: int = 0,
    recording_id: str = "",
) -> Any | None:
    if not isinstance(draft, dict) or not list(draft.get("capabilities") or []):
        return None
    from dano.assets.drafts import DraftStore
    from dano.onboarding.recording_results import recording_result_asset_key

    scope = Scope(tenant=tenant or "", subsystem=Subsystem(subsystem or "oa"))
    return await DraftStore().save_draft(
        run_id=recording_id or action or "pi-check",
        scope=scope,
        asset_type=AssetType.PAGE_SCRIPT,
        asset_key=recording_result_asset_key(action or recording_id or "pi-check"),
        body=pi_result_storage_body(
            action=action or recording_id,
            title=title,
            goal=goal,
            tenant=tenant or "",
            subsystem=scope.subsystem.value,
            draft=draft,
            request_count=request_count,
        ),
    )


@dataclass
class RecordingBridgeContext:
    tenant: str = ""
    subsystem: str = ""
    title: str = ""
    goal: str = ""
    action: str = ""
    start_url: str = ""
    persist: Any = persist_pi_submitted_result
    fetch_detail: Any = None

    def observe_client(self, payload: dict[str, Any]) -> None:
        if str(payload.get("type") or "") != "start":
            return
        self.tenant = str(payload.get("tenant") or self.tenant)
        self.title = str(payload.get("title") or self.title)
        self.goal = str(payload.get("goal_text") or payload.get("title") or self.goal)
        self.action = str(payload.get("resume_action") or self.action)
        self.start_url = str(payload.get("start_url") or self.start_url)
        configured = payload.get("subsystem")
        if configured:
            self.subsystem = str(configured)
        elif not self.subsystem:
            from dano.gateway.app import _recording_subsystem

            try:
                self.subsystem = _recording_subsystem(self.tenant, configured, self.start_url)
            except ValueError:
                self.subsystem = ""

    async def rewrite_upstream(self, raw: str) -> str:
        payload = _json_object(raw)
        kind = str(payload.get("type") or "")
        if kind == "thought":
            text = str(payload.get("text") or "").strip()
            if text:
                emit_run_event("pi_check.thought", stage="recording", summary=text)
            return raw
        if kind == "snapshot":
            snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
            progress = snapshot.get("progress") if isinstance(snapshot.get("progress"), dict) else {}
            label = str(progress.get("label") or snapshot.get("error") or "").strip()
            if label:
                emit_run_event("pi_check.progress", stage="recording", summary=label)
            return raw
        if kind != "recording_result_saved":
            return raw
        summary = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        recording_id = str(summary.get("id") or "")
        detail = None
        fetcher = self.fetch_detail or fetch_pi_check_detail
        if recording_id:
            detail = await fetcher(recording_id)
        draft = detail.get("draft") if isinstance(detail, dict) and isinstance(detail.get("draft"), dict) else None
        if draft is None:
            return raw
        if not self.action:
            self.action = str(summary.get("action") or recording_id)
        try:
            saved = await self.persist(
                tenant=self.tenant,
                subsystem=self.subsystem,
                action=self.action,
                title=self.title or str(summary.get("title") or ""),
                goal=self.goal or str(summary.get("goal_summary") or ""),
                draft=draft,
                request_count=int(summary.get("request_count") or detail.get("request_count") or 0),
                recording_id=recording_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("pi_check.persist_failed", error=str(exc), recording_id=recording_id)
            return raw
        if saved is None:
            return raw
        from dano.onboarding.recording_results import recording_result_summary

        payload["result"] = recording_result_summary(saved)
        emit_run_event(
            "pi_check.result_persisted",
            stage="recording",
            status="succeeded",
            summary=f"PI 已提交 {payload['result'].get('capability_count') or 0} 项能力，已写入历史结果",
            recording_id=recording_id,
            action=self.action,
        )
        return json.dumps(payload, ensure_ascii=False)


class PiCheckSidecar:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.adopted = False
        self.ready = False
        self.last_error = ""
        self._log_task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "backend": "pi_check",
            "notice": PI_ONLY_NOTICE,
            "ready": self.ready,
            "adopted": self.adopted,
            "url": sidecar_base_url(),
            "ws": sidecar_ws_url(),
            "pid": None if self.process is None else self.process.pid,
            "error": self.last_error,
        }

    def _own_process_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def ensure_started(self) -> None:
        if not sidecar_enabled():
            self.last_error = "PI-only sidecar disabled"
            self.ready = False
            emit_run_event(
                "pi_check.disabled",
                stage="system",
                status="warning",
                summary="PI-only 录制未拉起（测试或显式关闭）",
            )
            return
        healthy = await self.healthy()
        if should_adopt_existing(
            explicit_url=explicit_sidecar_url(),
            own_process_alive=self._own_process_alive(),
            healthy=healthy,
        ):
            self.adopted = not self._own_process_alive()
            self.ready = True
            self.last_error = ""
            emit_run_event(
                "pi_check.ready",
                stage="system",
                status="succeeded",
                summary="PI-only 录制已接管 8077 入口",
                details=self.status(),
            )
            return
        if healthy and not self._own_process_alive():
            emit_run_event(
                "pi_check.replace_orphan",
                stage="system",
                status="warning",
                summary="发现未带网关凭证的旧 Pi_check，正在替换",
                details={"port": sidecar_port()},
            )
        await self._stop_owned()
        await asyncio.to_thread(free_listen_port, sidecar_port())
        await self._spawn()
        await self._wait_healthy()
        self.ready = True
        self.last_error = ""
        emit_run_event(
            "pi_check.ready",
            stage="system",
            status="succeeded",
            summary="PI-only 录制已接管 8077 入口",
            details=self.status(),
        )

    async def stop(self) -> None:
        self.ready = False
        await self._stop_owned()

    async def _stop_owned(self) -> None:
        if self._log_task is not None:
            self._log_task.cancel()
            await asyncio.gather(self._log_task, return_exceptions=True)
            self._log_task = None
        process = self.process
        self.process = None
        if process is None or self.adopted:
            self.adopted = False
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{sidecar_base_url()}/health")
            if response.status_code != 200:
                return False
            payload = response.json()
            return bool(payload.get("ok"))
        except Exception:  # noqa: BLE001
            return False

    async def list_results(self, subsystem: str = "") -> list[dict[str, Any]]:
        if not self.ready:
            return []
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{sidecar_base_url()}/v1/recording-results",
                    params={"subsystem": subsystem} if subsystem else None,
                )
            if response.status_code != 200:
                return []
            rows = response.json()
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(rows, list):
            return []
        adapted = [adapt_pi_check_summary(row) for row in rows]
        return [row for row in adapted if row is not None]

    async def fetch_result(self, result_id: str) -> dict[str, Any] | None:
        return await fetch_pi_check_detail(result_id)

    async def remove_result(self, result_id: str) -> bool:
        key = str(result_id or "").strip()
        if not key or not self.ready:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.delete(f"{sidecar_base_url()}/v1/recording-results/{key}")
            return response.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def _spawn(self) -> None:
        if not PI_CHECK_ROOT.is_dir():
            raise RuntimeError(f"找不到 Pi_check 目录: {PI_CHECK_ROOT}")
        node = shutil.which("node")
        if not node:
            raise RuntimeError("未找到 node，无法启动 PI-only 录制")
        if not (PI_CHECK_ROOT / "node_modules").exists():
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("未找到 npm，无法安装 Pi_check 依赖")
            emit_run_event(
                "pi_check.installing",
                stage="system",
                status="started",
                summary="正在安装 Pi_check 依赖",
            )
            completed = await asyncio.to_thread(
                subprocess.run,
                [npm, "install"],
                cwd=str(PI_CHECK_ROOT),
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr or completed.stdout or "npm install 失败")
        env = sidecar_child_env()
        emit_run_event(
            "pi_check.credentials",
            stage="system",
            status="started",
            summary="已把网关 PI 凭证交给 Pi_check",
            details={
                "key_set": bool(env.get("DANO_PI_API_KEY")),
                "provider": env.get("DANO_PI_PROVIDER") or "",
                "model": env.get("DANO_PI_MODEL") or "",
                "base_url_set": bool(env.get("DANO_PI_BASE_URL")),
            },
        )
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = await asyncio.create_subprocess_exec(
            node,
            "src/server.mjs",
            cwd=str(PI_CHECK_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **kwargs,
        )
        self.adopted = False
        if self.process.stdout is not None:
            self._log_task = asyncio.create_task(self._pump_logs(self.process.stdout))
        emit_run_event(
            "pi_check.spawned",
            stage="system",
            status="started",
            summary="已启动 PI-only 录制进程",
            details={"pid": self.process.pid, "port": sidecar_port()},
        )

    async def _wait_healthy(self) -> None:
        deadline = asyncio.get_running_loop().time() + HEALTH_TIMEOUT_SEC
        while asyncio.get_running_loop().time() < deadline:
            if self.process is not None and self.process.returncode is not None:
                raise RuntimeError(f"PI-only 录制进程提前退出，code={self.process.returncode}")
            if await self.healthy():
                return
            await asyncio.sleep(0.25)
        raise RuntimeError(f"PI-only 录制未在 {HEALTH_TIMEOUT_SEC}s 内就绪：{sidecar_base_url()}")

    async def _pump_logs(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            emit_run_event("pi_check.log", stage="recording", summary=text)


_SIDECAR = PiCheckSidecar()


def get_sidecar() -> PiCheckSidecar:
    return _SIDECAR


async def fetch_pi_check_detail(result_id: str) -> dict[str, Any] | None:
    key = str(result_id or "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{sidecar_base_url()}/v1/recording-results/{key}")
        if response.status_code != 200:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


async def proxy_recording_websocket(ws: Any) -> None:
    """把现有录制页 WebSocket 接到 Pi_check。旧分析链不得进入。"""

    sidecar = get_sidecar()
    await ws.accept()
    if not sidecar.ready and sidecar_enabled():
        try:
            await sidecar.ensure_started()
        except Exception as exc:  # noqa: BLE001
            sidecar.last_error = str(exc)
            log.exception("pi_check.sidecar_start_failed", error=str(exc))
    if not sidecar.ready:
        await ws.send_json({
            "type": "error",
            "detail": sidecar.last_error or "PI-only 录制未启动，旧录制逻辑不会回退启动",
        })
        await ws.close()
        return

    emit_run_event(
        "pi_check.proxy_open",
        stage="recording",
        status="started",
        summary="录制 WebSocket 已接到 Pi_check，旧 RecordingGateway 不会启动",
        details={"ws": sidecar_ws_url()},
    )
    context = RecordingBridgeContext(fetch_detail=sidecar.fetch_result)
    try:
        import websockets
    except ImportError as exc:
        await ws.send_json({
            "type": "error",
            "detail": "网关缺少 websockets，无法把录制转到 Pi_check",
        })
        await ws.close()
        raise RuntimeError("websockets 未安装") from exc

    try:
        upstream = await _connect_pi_check_ws(sidecar)
        try:
            async def client_to_upstream() -> None:
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        await upstream.close()
                        return
                    text = message.get("text")
                    data = message.get("bytes")
                    if text is None and data is not None:
                        await upstream.send(data)
                        continue
                    if text is None:
                        continue
                    payload = _json_object(text)
                    if str(payload.get("type") or "") == "resume_verification":
                        await ws.send_json({
                            "type": "error",
                            "detail": "继续分析已关闭。请重新录制，由 PI 提交能力。",
                        })
                        await upstream.close()
                        return
                    context.observe_client(payload)
                    await upstream.send(text)

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await ws.send_bytes(message)
                        continue
                    await ws.send_text(await context.rewrite_upstream(str(message)))

            tasks = [
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            try:
                await upstream.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        sidecar.ready = False
        sidecar.last_error = str(exc)
        log.exception("pi_check.proxy_failed", error=str(exc))
        try:
            await ws.send_json({
                "type": "error",
                "detail": f"PI-only 录制连接失败：{exc}。旧录制逻辑不会回退启动",
            })
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


async def _connect_pi_check_ws(sidecar: PiCheckSidecar):
    import websockets

    last: Exception | None = None
    for attempt in range(2):
        if not await sidecar.healthy():
            sidecar.ready = False
            try:
                await sidecar.ensure_started()
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
        try:
            return await websockets.connect(sidecar_ws_url(), max_size=WS_MAX_BYTES)
        except Exception as exc:  # noqa: BLE001
            last = exc
            sidecar.ready = False
            emit_run_event(
                "pi_check.proxy_retry",
                stage="recording",
                status="warning",
                summary="Pi_check WebSocket 断开，正在重新拉起",
                details={"error": str(exc), "attempt": attempt + 1},
            )
            try:
                await sidecar.ensure_started()
            except Exception as restart_exc:  # noqa: BLE001
                last = restart_exc
    raise last or RuntimeError("无法连接 Pi_check")


def record_ws_uses_legacy_gateway() -> bool:
    from dano.gateway import app as gateway_app

    source = inspect.getsource(gateway_app.record_ws)
    forbidden = (
        "RecordingSessionRegistry",
        "RecordingPiSession",
        "analyze-recording-evidence",
        "submit_recording_plan",
    )
    return any(token in source for token in forbidden)
