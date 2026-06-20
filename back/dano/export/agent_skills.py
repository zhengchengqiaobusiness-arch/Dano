"""把已上架 Skill 导出为**官方 skill-creator 格式**的 Agent Skill(.agents/skills/<name>/)。

用法:
  python -m dano.export.agent_skills --tenant codegen-oa --out <pi仓库>/.agents/skills

每个 skill = 一个文件夹(skill-creator 规范:渐进式披露 + 脚本 + references):
  SKILL.md           —— frontmatter(pushy description/触发场景)+ 逐字段参数表 + 输出契约 + 确认工作流 + 示例 + 故障排除
  references/QUICKREF.md / README.md  —— 速查卡 + 详细说明(字段含义/事实核查解读)
  scripts/dano_call.py  —— 真逻辑:逐字段 flags + --confirm + --diagnose,POST Dano /v1/tools/call,末行打印稳定 JSON 状态
  scripts/submit.sh / submit.ps1     —— 转发到 dano_call.py 的薄壳

真执行(适配器→目标系统 + 三模型闸门 + 事实核查)都在 Dano 侧;本端无业务逻辑、不碰 OA 凭证,
只带 X-Tenant-Key 调 Dano。密钥经环境变量(DANO_URL / DANO_TENANT_KEY),不写进文件。
打包成 .skill:用 skill-creator 的 `python -m scripts.package_skill <此文件夹>`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

import structlog

from dano.assets.repository import AssetRepository
from dano.catalog.manifest import SkillManifest, build_manifests, tool_name_of
from dano.orchestrator.skills import SkillRegistry
from dano.shared.enums import Subsystem

log = structlog.get_logger(__name__)
ALL_SUBSYSTEMS = [Subsystem.OA, Subsystem.TICKET, Subsystem.REIMBURSE]


def _slug(skill_id: str) -> str:
    """skill_id(如 A-OA.submit_leave)→ 文件夹名(kebab,如 dano-a-oa-submit-leave)。"""
    s = ("dano-" + skill_id).lower().replace(".", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _fields(m: SkillManifest) -> tuple[list[str], set[str], dict]:
    props = (m.parameters or {}).get("properties", {}) or {}
    required = set((m.parameters or {}).get("required", []) or [])
    return list(props), required, props


def _flags(m: SkillManifest) -> str:
    keys, _, _ = _fields(m)
    return " ".join(f"--{k} <{k}>" for k in keys)


# ─────────────────────────── SKILL.md ───────────────────────────
def _skill_md(m: SkillManifest, slug: str) -> str:
    tool = tool_name_of(m.name)
    confirm = m.requires_confirmation
    keys, required, props = _fields(m)
    if keys:
        rows = "\n".join(
            f"| `{k}` | {'是' if k in required else '否'} | {(props[k] or {}).get('description', '') or k} |"
            for k in keys)
        table = "| 参数 | 必填 | 说明 |\n|---|---|---|\n" + rows
        ex_args = "{" + ", ".join(f'"{k}": "<{k}>"' for k in keys) + "}"
    else:
        table, ex_args = "(无业务参数)", "{}"
    flags = _flags(m)
    cflag = " --confirm" if confirm else ""
    confirm_note = ("\n> ⚠ 高风险写操作:**执行前必须向用户复述将提交的内容并取得同意**,确认后再带 `--confirm` 调用。\n"
                    if confirm else "")
    desc = (f"{m.description}。当用户想办理「{m.title}」或相关 {m.subsystem} 操作时,**务必使用本 skill**,"
            f"即使用户没有明确说出 skill 名或接口名。")
    return f"""---
name: {slug}
description: {desc}
compatibility: 需 python3 + 能访问 Dano 网关;通过 Dano 执行真实动作(写操作经确认 + 事实核查)
metadata:
  source: dano:{m.name}
  tool: {tool}
  risk_level: {m.risk_level}
  requires_confirmation: {str(confirm).lower()}
---

# {m.title}

这是 Dano **已上架 Skill 的代理**。真正的执行(适配器调用目标系统 + 三模型闸门 + 事实核查)都在 Dano 侧完成;本端只收集参数、调用 Dano,**不实现业务逻辑、不接触目标系统凭证**。
{confirm_note}
## 何时使用
{m.description}

## 参数
{table}

> `__base_url__`、流程模板、申请人身份(来自登录凭证)、调用凭证等由 Dano 运行期注入,**不需要也不应**由你提供。

## 如何执行
1. 与用户确认意图,收集上面的**必填**参数。{('高风险:先复述将提交的内容并取得同意。' if confirm else '')}
2. 运行脚本(自动带 `X-Tenant-Key` 调 Dano):
   - bash:`bash scripts/submit.sh {flags}{cflag}`
   - PowerShell:`pwsh scripts/submit.ps1 {flags}{cflag}`
   - 自检:`bash scripts/submit.sh --diagnose`
3. 读脚本输出的**最后一行 JSON 状态**,据下表行动,再把结果转述给用户。

## 输出契约(脚本末行 JSON)
| status | 含义 | 你应做的 |
|---|---|---|
| `succeeded` | 真实执行且事实核查通过 | 告知成功,附 `output` 里的单号 / procInsId |
| `need_confirm` | 写操作未确认被拦 | 向用户确认后,**带 `--confirm` 重跑** |
| `failed` | 失败(见 `reason`) | 把 reason 告知用户;缺参/凭证按故障排除处理,**勿谎报成功** |

示例:
```json
{{"status": "succeeded", "state": "completed", "output": {{}}, "fact_check": {{"passed": true}}}}
{{"status": "failed", "reason": "缺必填: reason"}}
```

## 示例
**Input:** 用户说"帮我提交一条{m.title}"。
**调用:** `bash scripts/submit.sh {flags}{cflag}`
**参数 JSON(等价):** `{ex_args}`

## 故障排除
| 现象 | 处理 |
|---|---|
| `DANO_URL/DANO_TENANT_KEY 未设置` | 让部署方配好这两个环境变量(勿写进文件) |
| `HTTP 401` / 凭证无效 | Dano「运行配置」里该租户 OA token 失效,重配 |
| `缺必填: …` | 补齐"参数"表里的必填项再调 |
| `事实核查未过` | Dano 判定没真生效(疑似空操作),把原始返回给用户,**勿报成功** |

## 运行前置(环境变量,部署方配置,勿写进文件)
- `DANO_URL`:Dano 网关地址,如 `http://localhost:8077`
- `DANO_TENANT_KEY`:本租户 api_key(作 `X-Tenant-Key`)

速查见 `references/QUICKREF.md`,详细说明见 `references/README.md`。
"""


# ─────────────────────────── references ───────────────────────────
def _quickref(m: SkillManifest) -> str:
    flags = _flags(m)
    cflag = " --confirm" if m.requires_confirmation else ""
    return f"""# {m.title} · 速查

正常用脚本入口,不要手拼 curl。

## 自检
```bash
bash scripts/submit.sh --diagnose
```

## 提交
```bash
bash scripts/submit.sh {flags}{cflag}
```

## 常见状态(末行 JSON)
```json
{{"status": "succeeded", "state": "completed", "output": {{}}}}
{{"status": "need_confirm"}}
{{"status": "failed", "reason": "..."}}
```
"""


def _readme(m: SkillManifest) -> str:
    keys, required, props = _fields(m)
    field_lines = "\n".join(
        f"- `{k}`（{'必填' if k in required else '可选'}）:{(props[k] or {}).get('description', '') or k}"
        for k in keys) or "- (无业务参数)"
    return f"""# {m.title} — 详细说明

`source: dano:{m.name}` · 风险 {m.risk_level} · {'写操作需确认' if m.requires_confirmation else '读操作'}

## 字段
{field_lines}

不需要填的(Dano 运行期注入):流程模板、`__base_url__`、调用凭证;**申请人**取自登录凭证(谁的 token 就是谁申请),不作参数。

## 执行与判定
脚本把字段组装成 `arguments`,POST 到 Dano `/v1/tools/call`(带 `X-Tenant-Key`)。Dano 侧:风险闸门(写操作要 `--confirm`)→ 隔离执行适配器 → **事实核查**回查是否真生效。脚本末行 JSON 的 `status` 是唯一可信结论:
- `succeeded`:接口成功**且**事实核查通过(真生效);`output` 含单号/procInsId。
- `need_confirm`:写操作没带 `--confirm` 被拦;向用户确认后重跑。
- `failed`:含 `reason`;`事实核查未过` 表示疑似空操作,**不要**因为接口回了 200 就报成功。

## 环境变量(部署方配置,勿写进文件)
- `DANO_URL` / `DANO_TENANT_KEY`
"""


# ─────────────────────────── scripts ───────────────────────────
_PY_TEMPLATE = r'''#!/usr/bin/env python3
"""由 Dano 自动生成:调用已上架 Skill「__TITLE__」(真实执行在 Dano 侧)。

逐字段 flags -> 组装 arguments -> POST Dano /v1/tools/call;最后一行打印 JSON 状态供 agent 解析。
凭证 / 模板 / base_url / 申请人身份由 Dano 注入,本端不接触。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

TOOL = "__TOOL__"
FIELDS = __FIELDS__
REQUIRED = __REQUIRED__
CONFIRM_DEFAULT = __CONFIRM__


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="调用 Dano skill " + TOOL)
    for f in FIELDS:
        ap.add_argument("--" + f, default=None)
    ap.add_argument("--json", dest="raw", default=None, help="直接给 arguments JSON(覆盖逐字段)")
    ap.add_argument("--confirm", action="store_true", default=CONFIRM_DEFAULT)
    ap.add_argument("--diagnose", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("DANO_URL")
    key = os.environ.get("DANO_TENANT_KEY")
    if not url or not key:
        _emit({"status": "failed", "reason": "DANO_URL/DANO_TENANT_KEY 未设置(部署方配置,勿写进文件)"})
        sys.exit(2)
    url = url.rstrip("/")

    if args.diagnose:
        try:
            with urllib.request.urlopen(url + "/health", timeout=10) as r:
                ok = r.status == 200
            _emit({"status": "diagnose_done", "dano_url": url, "health_ok": ok, "tenant_key_set": bool(key)})
        except Exception as e:
            _emit({"status": "failed", "reason": "网关不可达: %s" % e})
            sys.exit(2)
        return

    if args.raw:
        try:
            arguments = json.loads(args.raw)
        except Exception as e:
            _emit({"status": "failed", "reason": "--json 不是合法 JSON: %s" % e})
            sys.exit(2)
    else:
        arguments = {f: getattr(args, f) for f in FIELDS if getattr(args, f) is not None}

    missing = [f for f in REQUIRED if f not in arguments or arguments[f] in (None, "")]
    if missing:
        _emit({"status": "failed", "reason": "缺必填: %s" % ", ".join(missing)})
        sys.exit(1)

    payload = json.dumps({"name": TOOL, "arguments": arguments, "confirm": bool(args.confirm)}).encode("utf-8")
    req = urllib.request.Request(
        url + "/v1/tools/call", data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Tenant-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _emit({"status": "failed", "reason": "HTTP %s: %s" % (e.code, e.read().decode("utf-8")[:300])})
        sys.exit(1)
    except Exception as e:
        _emit({"status": "failed", "reason": str(e)})
        sys.exit(1)

    state = res.get("state")
    audit = res.get("audit") or {}
    fc = audit.get("fact_check")
    output = (res.get("exec_result") or {}).get("structured_output")
    if state == "completed":
        _emit({"status": "succeeded", "state": state, "output": output, "fact_check": fc})
    elif state == "cancelled" or "确认" in (res.get("message") or ""):
        _emit({"status": "need_confirm", "state": state, "message": res.get("message")})
    else:
        _emit({"status": "failed", "state": state, "reason": res.get("message"), "fact_check": fc})


if __name__ == "__main__":
    main()
'''


def _dano_call_py(m: SkillManifest) -> str:
    keys, required, _ = _fields(m)
    return (_PY_TEMPLATE
            .replace("__TITLE__", m.title)
            .replace("__TOOL__", tool_name_of(m.name))
            .replace("__FIELDS__", json.dumps(keys, ensure_ascii=False))
            .replace("__REQUIRED__", json.dumps([k for k in keys if k in required], ensure_ascii=False))
            .replace("__CONFIRM__", "True" if m.requires_confirmation else "False"))


_SUBMIT_SH = """#!/usr/bin/env bash
# 由 Dano 自动生成:转发到 dano_call.py(真逻辑)。python3 不在则回退 python。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" "$DIR/dano_call.py" "$@"
"""

_SUBMIT_PS1 = """# 由 Dano 自动生成:转发到 dano_call.py(真逻辑)。
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$dir/dano_call.py" @args
"""


def _chmod_x(path: Path) -> None:
    try:
        import os
        import stat
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _write_skill(out_dir: Path, m: SkillManifest) -> Path:
    slug = _slug(m.name)
    folder = out_dir / slug
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "references").mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(_skill_md(m, slug), encoding="utf-8")
    (folder / "references" / "QUICKREF.md").write_text(_quickref(m), encoding="utf-8")
    (folder / "references" / "README.md").write_text(_readme(m), encoding="utf-8")
    py = folder / "scripts" / "dano_call.py"
    py.write_text(_dano_call_py(m), encoding="utf-8", newline="\n")
    _chmod_x(py)
    sh = folder / "scripts" / "submit.sh"
    sh.write_text(_SUBMIT_SH, encoding="utf-8", newline="\n")
    _chmod_x(sh)
    (folder / "scripts" / "submit.ps1").write_text(_SUBMIT_PS1, encoding="utf-8")
    return folder


# ─────────────────────────── 业务剧本 skill(多操作合成一本)───────────────────────────
def _op_sh(action: str) -> str:
    return ("#!/usr/bin/env bash\n# 由 Dano 自动生成:转发到 %s.py(真逻辑)。\n"
            "set -euo pipefail\n"
            'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            "if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi\n"
            'exec "$PY" "$DIR/%s.py" "$@"\n' % (action, action))


def _op_ps1(action: str) -> str:
    return ("# 由 Dano 自动生成:转发到 %s.py。\n"
            "$dir = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
            'python "$dir/%s.py" @args\n' % (action, action))


def _biz_label(business: str, manifests: list[SkillManifest]) -> str:
    """业务展示名:优先用写操作(办理)的标题,退而用业务键清理。"""
    writes = [m for m in manifests if m.requires_confirmation]
    if writes and writes[0].title:
        return writes[0].title
    s = re.sub(r"^(submit|create|apply|demo|do)[_-]+", "", business.lower())
    return s.replace("_", " ").strip() or business


_DIAGNOSE_SH = """#!/usr/bin/env bash
# 由 Dano 自动生成:剧本自检(能不能走这条路)。转发到某操作脚本的 --diagnose。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" "$DIR/__ENTRY__.py" --diagnose
"""

_DIAGNOSE_PS1 = """# 由 Dano 自动生成:剧本自检。转发到某操作脚本的 --diagnose。
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$dir/__ENTRY__.py" --diagnose
"""


def _biz_quickref(business: str, manifests: list[SkillManifest]) -> str:
    label = _biz_label(business, manifests)
    lines = "\n".join(f"# {m.title}\nbash scripts/{m.action}.sh {_flags(m)}"
                      f"{' --confirm' if m.requires_confirmation else ''}" for m in manifests)
    return f"""# {label} · 速查

每个操作一个脚本(写操作加 `--confirm`):
```bash
{lines}
```
自检:`bash scripts/<操作>.sh --diagnose`
"""


def _biz_readme(subsystem: str, business: str, manifests: list[SkillManifest]) -> str:
    label = _biz_label(business, manifests)
    blocks = []
    for m in manifests:
        keys, required, props = _fields(m)
        fl = "\n".join(f"  - `{k}`（{'必填' if k in required else '可选'}）:"
                       f"{(props[k] or {}).get('description', '') or k}" for k in keys) or "  - (无业务参数)"
        blocks.append(f"### `{m.action}` — {m.title}（{'写·需确认' if m.requires_confirmation else '读'}）\n{fl}")
    return f"""# {label} — 业务操作集详细说明

`business: {business}` · 子系统 {subsystem} · 共 {len(manifests)} 个操作。
每个操作把字段组装成 `arguments`,POST 到 Dano `/v1/tools/call`(带 `X-Tenant-Key`);
末行 JSON 的 `status` 是唯一可信结论(succeeded / need_confirm / failed)。

## 各操作字段
{chr(10).join(blocks)}

## 环境变量(部署方配置,勿写进文件)
- `DANO_URL` / `DANO_TENANT_KEY`
"""


def _write_business_skill(out_dir: Path, subsystem: str, business: str,
                          manifests: list[SkillManifest], *, md_text: str | None = None) -> Path:
    """同业务多 adapter → 一本剧本 skill(多操作脚本 + 六段式剧本 SKILL.md)。

    md_text 给定则用它(LLM 动态撰写的);否则用 PlaybookSpec 确定性渲染(grounded 兜底)。
    """
    from dano.generation.playbook import build_playbook
    from dano.generation.playbook_writer import render_playbook_md
    slug = _slug(f"{subsystem}.{business}")
    folder = out_dir / slug
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "references").mkdir(parents=True, exist_ok=True)
    if md_text is None:
        spec = build_playbook(subsystem, business, manifests)
        md_text = render_playbook_md(spec, slug)
    (folder / "SKILL.md").write_text(md_text, encoding="utf-8")
    (folder / "references" / "QUICKREF.md").write_text(_biz_quickref(business, manifests), encoding="utf-8")
    (folder / "references" / "README.md").write_text(_biz_readme(subsystem, business, manifests), encoding="utf-8")
    entry = (manifests[0].action if manifests else "diagnose")   # 自检入口:转发任一操作的 --diagnose
    (folder / "scripts" / "diagnose.sh").write_text(
        _DIAGNOSE_SH.replace("__ENTRY__", entry), encoding="utf-8", newline="\n")
    _chmod_x(folder / "scripts" / "diagnose.sh")
    (folder / "scripts" / "diagnose.ps1").write_text(
        _DIAGNOSE_PS1.replace("__ENTRY__", entry), encoding="utf-8")
    for m in manifests:                                       # 每操作一个脚本入口(像 lanxin)
        py = folder / "scripts" / f"{m.action}.py"
        py.write_text(_dano_call_py(m), encoding="utf-8", newline="\n")
        _chmod_x(py)
        sh = folder / "scripts" / f"{m.action}.sh"
        sh.write_text(_op_sh(m.action), encoding="utf-8", newline="\n")
        _chmod_x(sh)
        (folder / "scripts" / f"{m.action}.ps1").write_text(_op_ps1(m.action), encoding="utf-8")
    return folder


# ─────────────────────────── index 路由(总台,自动生成)───────────────────────────
def _index_md(entries: list[dict], slug: str) -> str:
    """业务总台:列出所有业务剧本 + 触发词,把用户意图路由到对应剧本。无业务专属逻辑。"""
    rows = "\n".join(f"| {e['label']} | `{e['folder']}` | {e['ops']} 个操作 |" for e in entries)
    table = "| 业务 | 剧本目录 | 规模 |\n|---|---|---|\n" + rows
    names = "、".join(e["label"] for e in entries) or "(暂无)"
    return f"""---
name: {slug}
description: OA 业务总台:统一入口,把用户意图路由到具体业务剧本({names})。当用户提到办理/查询任一 OA 业务时,先看本目录选对剧本。
metadata:
  source: dano:index
  businesses: {len(entries)}
---

# OA 业务剧本总台

这是所有已生成业务剧本的**路由目录**。用户说要办什么,在下表里找到对应业务,
打开它的剧本目录(各自一本自包含 skill),按那本剧本的六段流程办。

## 业务目录
{table}

> 每本剧本都含:①自检 ②办理前校验 ③办理(需确认) ④错误处置 ⑤事后确认 ⑥缺失恢复。
> 找不到对应业务就如实告知用户"没有这个业务的 skill",**不要臆造**。
"""


def _write_index(out_dir: Path, entries: list[dict]) -> str:
    slug = "dano-oa-index"
    folder = out_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(_index_md(entries, slug), encoding="utf-8")
    return slug


async def write_skills(tenant: str, out_dir: str, *, rich: bool = True) -> list[str]:
    """核心:读该租户已上架 Skill 写成官方格式 skill;**不管连接池**(供已持有池的网关复用)。

    带 business 标签的 adapter **按业务归组成一本自包含剧本 skill**(多操作);其余各自一个单动作 skill。
    rich=True:每本剧本的 SKILL.md 用 LLM 据 PlaybookSpec **动态撰写**(失败回退确定性渲染);
    rich=False:直接确定性渲染(测试/离线用,不调 LLM)。每业务独立 try/except,一个失败不连累其它。
    最后自动生成 index 路由总台。
    """
    from collections import defaultdict

    from dano.generation.playbook import build_playbook
    from dano.generation.playbook_writer import render_playbook_md, write_playbook_md
    repo = AssetRepository()
    reg = await SkillRegistry.from_store(repo, tenant=tenant, subsystems=ALL_SUBSYSTEMS)
    manifests = build_manifests(reg.skills)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    groups: dict = defaultdict(list)
    standalone: list[SkillManifest] = []
    for m in manifests:
        (groups[(m.subsystem, m.business)].append(m) if getattr(m, "business", "")
         else standalone.append(m))
    written: list[str] = []
    index_entries: list[dict] = []
    for (sub, biz), ms in groups.items():
        try:                                                 # 每业务独立:一个崩不连累其它
            slug = _slug(f"{sub}.{biz}")
            spec = build_playbook(sub, biz, ms)
            md = (await write_playbook_md(spec, slug)) if rich else render_playbook_md(spec, slug)
            folder = _write_business_skill(out, sub, biz, ms, md_text=md)
            log.info("export.business_skill", business=biz, subsystem=sub,
                     ops=[m.action for m in ms], folder=folder.name)
            written.append(folder.name)
            index_entries.append({"label": spec.label, "folder": folder.name, "ops": len(ms)})
        except Exception as e:  # noqa: BLE001
            log.warning("export.business_skill_failed", business=biz, subsystem=sub, error=str(e))
    for m in standalone:
        try:
            written.append(_write_skill(out, m).name)
        except Exception as e:  # noqa: BLE001
            log.warning("export.standalone_failed", action=m.action, error=str(e))
    if index_entries:                                        # 自动生成 index 路由总台
        written.append(_write_index(out, index_entries))
    log.info("export.agent_skills", tenant=tenant, out=str(out),
             count=len(written), businesses=len(groups), standalone=len(standalone))
    return written


async def export(tenant: str, out_dir: str) -> list[str]:
    """命令行入口:自管连接池(init→write→close);返回写出的文件夹名列表。"""
    from dano.infra.db import close_pool, init_pool
    await init_pool()
    try:
        return await write_skills(tenant, out_dir)
    finally:
        await close_pool()


def main() -> None:
    ap = argparse.ArgumentParser(description="导出已上架 Skill 为官方 skill-creator 格式 skill(.agents/skills/)")
    ap.add_argument("--tenant", required=True, help="租户名,如 codegen-oa")
    ap.add_argument("--out", required=True, help="输出目录,通常是 <pi仓库>/.agents/skills")
    args = ap.parse_args()
    written = asyncio.run(export(args.tenant, args.out))
    print(f"已导出 {len(written)} 个 skill 到 {args.out}:")
    for w in written:
        print("  -", w)
    if not written:
        print("  (该租户没有已上架 Skill;先在「接入系统」生成并上架)")


if __name__ == "__main__":
    main()
