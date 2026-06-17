"""隔离 runner:把 goal 模式生成的适配器源码,在受控子进程里跑一次。

M0 安全边界(明确、诚实):
- **进程隔离**:每次跑都是独立子进程,崩溃/挂死不影响主进程;
- **超时**:超时即 kill,返回失败(防生成代码死循环);
- **凭证隔离**:凭证只经 stdin 注入,**绝不写进源码、不进 argv、不进子进程环境**;
- **环境最小化**:子进程只继承白名单环境变量(PATH 等),**平台密钥(DANO_* 等)不下传**给生成代码;
- 入口契约固定:`run(inputs: dict, creds: dict) -> dict`。

M0 暂不做(留给容器后端 ContainerBackend):网络出口策略、只读文件系统、系统调用限制。
适配器需要访问企业 API,故网络放行;真正的出口收敛需容器/代理,后续里程碑再加。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_MARKER = "__DANO_RESULT__"

# 子进程只继承这些环境变量(平台密钥一律不下传给生成代码)
_ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "windir", "ComSpec",
    "TEMP", "TMP", "TMPDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS", "OS", "HOMEDRIVE", "HOMEPATH", "LANG", "LC_ALL",
    "PYTHONUTF8", "PYTHONIOENCODING",
)

# 子进程引导:从 stdin 读 {inputs, credentials},加载适配器源码,调入口,结果经 marker 行回传。
_BOOTSTRAP = r"""
import sys, json, importlib.util, traceback
_path, _entry = sys.argv[1], sys.argv[2]
_raw = sys.stdin.read()
try:
    _payload = json.loads(_raw) if _raw.strip() else {}
except Exception:
    _payload = {}
_inputs = _payload.get("inputs") or {}
_creds = _payload.get("credentials") or {}
try:
    _spec = importlib.util.spec_from_file_location("dano_adapter", _path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _fn = getattr(_mod, _entry)
    _out = _fn(_inputs, _creds)
    sys.stdout.write("\n" + "__DANO_RESULT__" + json.dumps({"ok": True, "output": _out}, default=str) + "\n")
except Exception as _e:
    sys.stdout.write("\n" + "__DANO_RESULT__" + json.dumps(
        {"ok": False, "error": "%s: %s" % (type(_e).__name__, _e),
         "trace": traceback.format_exc()[:2000]}, default=str) + "\n")
"""


@dataclass
class AdapterRunResult:
    """一次适配器执行的结果(二态 + 证据)。"""

    ok: bool
    output: Any | None
    error: str | None
    stdout: str
    duration_s: float


def _child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_WHITELIST if k in os.environ}
    env["PYTHONUTF8"] = "1"          # 保证子进程 UTF-8,避免中文乱码
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


class AdapterRunner:
    """隔离 runner(M0 子进程后端)。

    timeout_s:单次执行超时;python:解释器(默认当前)。后续可注入 ContainerBackend。
    """

    def __init__(self, *, timeout_s: float = 30.0, python: str | None = None) -> None:
        self._timeout = timeout_s
        self._py = python or sys.executable

    async def run(self, *, source: str, inputs: dict[str, Any],
                  credentials: dict[str, str], entry: str = "run") -> AdapterRunResult:
        """在隔离子进程里跑一次适配器。凭证仅经 stdin,不进源码/argv/子进程环境。"""
        t0 = time.monotonic()
        tmp = tempfile.NamedTemporaryFile("w", suffix="_adapter.py", delete=False, encoding="utf-8")
        try:
            tmp.write(source)
            tmp.close()
            proc = await asyncio.create_subprocess_exec(
                self._py, "-c", _BOOTSTRAP, tmp.name, entry,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_child_env(),
            )
            payload = json.dumps({"inputs": inputs, "credentials": credentials}).encode("utf-8")
            try:
                out_b, err_b = await asyncio.wait_for(
                    proc.communicate(input=payload), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                dt = time.monotonic() - t0
                log.warning("adapter.run.timeout", entry=entry, timeout_s=self._timeout)
                return AdapterRunResult(ok=False, output=None,
                                        error=f"timeout>{self._timeout}s", stdout="", duration_s=dt)
            stdout = out_b.decode("utf-8", "replace")
            dt = time.monotonic() - t0
            result = self._parse(stdout)
            if result is None:
                stderr = err_b.decode("utf-8", "replace")[:1000]
                return AdapterRunResult(ok=False, output=None,
                                        error=f"no_result(rc={proc.returncode}): {stderr}",
                                        stdout=stdout[:2000], duration_s=dt)
            log.info("adapter.run", entry=entry, ok=result.get("ok"), duration_s=round(dt, 3))
            return AdapterRunResult(
                ok=bool(result.get("ok")), output=result.get("output"),
                error=result.get("error"), stdout=stdout[:2000], duration_s=dt)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    @staticmethod
    def _parse(stdout: str) -> dict | None:
        """从子进程 stdout 取最后一条 marker 行(隔离适配器自身的打印)。"""
        for line in reversed(stdout.splitlines()):
            if line.startswith(_MARKER):
                try:
                    return json.loads(line[len(_MARKER):])
                except json.JSONDecodeError:
                    return None
        return None
