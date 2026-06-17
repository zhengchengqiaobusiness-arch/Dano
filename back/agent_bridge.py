"""Dano pi 桥 · Python 宿主(Phase 0 打样 + 可复用驱动)。

职责:
- 起一个**仅 127.0.0.1**的工具回调服务(/_agent/tools/{name}),带临时令牌 + 工具白名单。
- spawn `node agent/run_pi.mjs`,**env 白名单**注入(只给 OS 必需 + DANO_AGENT_*/PI_*,
  绝不把父进程的 DB 密码/生产密钥透传给 Node/Pi)。
- 经 JSONL 协议:写 start_run 到 stdin,从 stdout 读事件(stderr 是日志,分流)。
- 回收子进程(正常/超时/异常)。

直接运行本文件 = 跑 Phase 0 验收检查(stub 模式,不需 LLM)。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

BACK = Path(__file__).resolve().parent
ALLOWED_TOOLS = {"parse_spec"}

# env 白名单:OS 必需(Node 启动要)+ 本次 run 注入。其余(DB/密钥)一律不传。
_OS_ENV_WHITELIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "windir", "ComSpec",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS", "OS", "HOMEDRIVE", "HOMEPATH",
)


# ───────────────── 工具真实实现(Python 侧)─────────────────
def tool_parse_spec(params: dict) -> dict:
    """解析已导入的接口文档,返回动作清单(Phase 0 用 ruoyi spec 真解析)。"""
    spec = yaml.safe_load((BACK / "examples" / "ruoyi_oa.yaml").read_text(encoding="utf-8"))
    methods = {"get", "post", "put", "patch", "delete"}
    actions = []
    for path, ops in (spec.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for m, op in ops.items():
            if m.lower() in methods and isinstance(op, dict):
                actions.append({
                    "name": op.get("operationId") or f"{m.lower()}_{path}",
                    "method": m.upper(), "endpoint": path,
                })
    return {"system_instance_id": params.get("system_instance_id"),
            "count": len(actions), "actions": actions}


_TOOLS = {"parse_spec": tool_parse_spec}


# ───────────────── 仅本机的工具回调服务 ─────────────────
class _ToolServer:
    def __init__(self, token: str, run_id: str):
        self.token, self.run_id = token, run_id
        self.calls: list[dict] = []          # 收到的工具调用(供验收检查)
        host_self = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):        # 静音默认 stdout 日志
                pass

            def do_POST(self):
                if self.headers.get("X-Agent-Token") != host_self.token:
                    return self._json(401, {"error": "bad_token"})
                name = self.path.rsplit("/", 1)[-1]
                if name not in ALLOWED_TOOLS:
                    return self._json(404, {"error": "tool_not_allowed"})
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if body.get("run_id") != host_self.run_id:
                    return self._json(403, {"error": "run_id_mismatch"})
                host_self.calls.append({"tool": name, "run_id": body.get("run_id"),
                                        "tool_call_id": body.get("tool_call_id")})
                out = _TOOLS[name](body.get("params") or {})
                return self._json(200, out)

            def _json(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = HTTPServer(("127.0.0.1", 0), H)
        self.port = self._server.server_address[1]
        self._t = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *a):
        self._server.shutdown()


# ───────────────── 驱动一次 run ─────────────────
def run_once(prompt: str, *, run_id: str = "run-001", context: dict | None = None,
             stub: bool = True, timeout_s: float = 30.0, crash: bool = False) -> dict:
    """spawn pi 桥跑一次,返回 {events, stderr, returncode, tool_calls}。"""
    token = secrets.token_hex(16)
    with _ToolServer(token, run_id) as srv:
        child_env = {k: os.environ[k] for k in _OS_ENV_WHITELIST if k in os.environ}
        child_env.update({
            "DANO_AGENT_TOKEN": token,
            "DANO_AGENT_BASE_URL": f"http://127.0.0.1:{srv.port}",
            "DANO_AGENT_RUN_ID": run_id,
            "PI_STUB": "1" if stub else "0",
        })
        # 真实模式:透传 LLM 凭证(pi 需要)。这些是显式给的、属白名单;DB/其他密钥仍不传。
        if not stub:
            for k in ("DANO_PI_API_KEY", "DANO_PI_BASE_URL", "DANO_PI_MODEL", "DANO_PI_PROVIDER"):
                if k in os.environ:
                    child_env[k] = os.environ[k]
        if crash:
            child_env["DANO_AGENT_BASE_URL"] = "http://127.0.0.1:1"  # 故意打不通 → 工具异常
        proc = subprocess.Popen(
            ["node", str(BACK / "agent" / "run_pi.mjs")],
            cwd=str(BACK), env=child_env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        start = json.dumps({"type": "start_run", "run_id": run_id, "prompt": prompt,
                            "context": context or {"system_instance_id": "a-oa"},
                            "budget": {"timeout_s": int(timeout_s)}})
        try:
            out, err = proc.communicate(input=start + "\n", timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return {"events": [], "stderr": err, "returncode": -1, "tool_calls": srv.calls,
                    "timed_out": True}
        events, bad_lines = [], []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                bad_lines.append(line)
        return {"events": events, "stderr": err, "returncode": proc.returncode,
                "tool_calls": srv.calls, "bad_stdout_lines": bad_lines}


# ───────────────── Phase 0 验收检查(stub,无需 LLM)─────────────────
def _phase0_checks() -> int:
    import time

    def show(n, ok, detail=""):
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  {detail}")
        return ok

    print("=== Phase 0 桥验收(stub 模式,确定性,不需 LLM)===")
    ok_all = True

    r = run_once("解析 RuoYi 动作清单")
    events = r["events"]
    types = [e.get("type") for e in events]

    ok_all &= show("1 spawn/通信/退出", r["returncode"] == 0 and bool(events),
                   f"returncode={r['returncode']} events={types}")
    ok_all &= show("5 工具调用带 run_id", any(c["run_id"] == "run-001" for c in r["tool_calls"]),
                   f"tool_calls={r['tool_calls']}")
    completed = next((e for e in events if e.get("type") == "run_completed"), None)
    tool_res = (completed or {}).get("tool_result", {})
    actions = json.loads(tool_res.get("content", [{}])[0].get("text", "{}")).get("actions", []) if tool_res else []
    ok_all &= show("6 Python 返回结构化结果", bool(actions) and completed and completed.get("status") == "completed",
                   f"动作数={len(actions)}")
    ok_all &= show("9 stdout 纯 JSONL", not r.get("bad_stdout_lines"),
                   f"非JSON行={r.get('bad_stdout_lines')}")

    # 8 异常:工具回调打不通 → run failed,进程仍回收(returncode 0,事件含 failed)
    rc = run_once("故意失败", crash=True)
    failed_ev = next((e for e in rc["events"] if e.get("type") == "run_completed"), {})
    ok_all &= show("8 异常可回收(工具失败→run failed)",
                   rc["returncode"] == 0 and failed_ev.get("status") == "failed",
                   f"status={failed_ev.get('status')} err={failed_ev.get('error','')[:40]}")

    # 10 连跑 10 次无僵尸
    import subprocess as sp
    before = _count_node()
    t0 = time.time()
    for i in range(10):
        run_once(f"第{i}次", run_id=f"run-{i:03d}")
    after = _count_node()
    ok_all &= show("10 连跑10次无僵尸 node", after <= before,
                   f"node进程 before={before} after={after} 耗时={time.time()-t0:.1f}s")

    print(f"\n结论:{'全部通过 ✅(确定性桥可靠)' if ok_all else '有失败 ❌'}")
    print("注:验收 #2/#3/#4/#7(真实 LLM 初始化/skill加载/pi自主选工具/最终输出)需 LLM key,待提供后跑真实模式。")
    return 0 if ok_all else 1


def _count_node() -> int:
    import subprocess as sp
    try:
        out = sp.run(["tasklist", "/FI", "IMAGENAME eq node.exe"], capture_output=True, text=True).stdout
        return out.count("node.exe")
    except Exception:
        return 0


def _phase0_real_checks() -> int:
    """真实模式验收(#2/#3/#4/#7),需 DANO_PI_API_KEY。"""
    print("=== Phase 0 真实模式验收(pi 真调 LLM,需 key)===")
    if not os.environ.get("DANO_PI_API_KEY"):
        print("  [SKIP] 未设 DANO_PI_API_KEY,跳过真实模式")
        return 0
    ok_all = True

    def show(n, ok, detail=""):
        nonlocal ok_all
        ok_all &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}  {detail}")

    prompt = ("用 parse_spec 工具解析系统实例 a-oa 的接口文档,拿到动作清单后,"
              "用一句话总结一共有多少个动作。")
    r = run_once(prompt, run_id="real-001", context={"system_instance_id": "a-oa"},
                 stub=False, timeout_s=120.0)
    completed = next((e for e in r["events"] if e.get("type") == "run_completed"), {})
    err = (r["stderr"] or "")[-400:]

    show("2 真实会话初始化 + 模型可用", completed.get("status") == "completed",
         f"status={completed.get('status')} err={completed.get('error','')} {('stderr尾:'+err) if completed.get('status')!='completed' else ''}")
    show("4 pi 自主调用 parse_spec",
         any(c["tool"] == "parse_spec" and c["run_id"] == "real-001" for c in r["tool_calls"]),
         f"tool_calls={[c['tool'] for c in r['tool_calls']]}")
    show("7 pi 产出最终结构化输出", bool(completed.get("final_text")),
         f"final_text={(completed.get('final_text') or '')[:120]!r}")
    show("3 skills 加载", bool(completed.get("skills_loaded")),
         f"skills_loaded={completed.get('skills_loaded')}")
    print(f"\n真实模式结论:{'全部通过 ✅' if ok_all else '有失败(看上面 detail)❌'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    import sys
    if "--real" in sys.argv:
        raise SystemExit(_phase0_real_checks())
    raise SystemExit(_phase0_checks())
