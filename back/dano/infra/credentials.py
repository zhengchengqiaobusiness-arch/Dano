"""运行期凭证解析:配了 Vault 走真实 Vault,否则 dev 回退 config.py 的 runtime_credentials + 进程内表。

红线:平台只存 vault:// 引用,真实取值优先 Vault。
- require_vault=true:必须从 Vault 取,失败即报错(fail closed,不回退)。
- vault_token 配了但非强制:Vault 优先,失败回退本地表(便于灰度/混合环境)。
- 都没配:config.py runtime_credentials + 进程内表(仅 dev/试点;**不落文件、不读 .env**)。
"""

from __future__ import annotations

import structlog

from dano.config import get_settings
from dano.infra import vault

log = structlog.get_logger(__name__)

_vault_client: "vault.VaultClient | None" = None


def _get_vault() -> "vault.VaultClient":
    global _vault_client
    if _vault_client is None:
        _vault_client = vault.VaultClient()
    return _vault_client


_RUNTIME_CREDS: dict = {}    # 进程内:接入时 set_runtime_credential 写入(不落文件、不进 .env)


def _cred_table() -> dict:
    """运行期凭证表:config.py 的 runtime_credentials 为基,叠加接入时进程内写入的。"""
    base = dict(get_settings().runtime_credentials or {})
    base.update(_RUNTIME_CREDS)
    return base


def _env_lookup(ref: str) -> dict:
    """键 = vault:// 后的全路径(如 demo/oa),与历史一致。"""
    path = ref[len(vault.VAULT_SCHEME):] if vault.is_vault_ref(ref) else ref
    return _cred_table().get(path, {})


def set_runtime_credential(path: str, creds: dict) -> None:
    """把一份运行期凭证写进**进程内凭证表**(键=vault path 段,如 abc/oa)。

    用途:接入时拿到的 OA token(来自接入页)落进运行期凭证库,否则运行期 invoke 解析不到 token
    (`Bearer ` 为空 → Illegal header value)。强制 Vault(require_vault)时不走这条——
    生产必须正规写 Vault,此处只补 dev/试点缺口。凭证只进进程内存,不落文件、不进 .env。
    """
    s = get_settings()
    if s.require_vault or not creds:
        return
    _RUNTIME_CREDS[path] = {**_RUNTIME_CREDS.get(path, {}), **{k: v for k, v in creds.items() if v}}
    log.info("creds.runtime_stored", path=path, keys=sorted(creds.keys()))


def resolve_credentials(refs: dict[str, str]) -> dict[str, str]:
    """把若干凭证引用解析成明文凭证(运行期受控环境内)。refs:{用途名: vault://...}。"""
    s = get_settings()
    use_vault = bool(s.vault_token) or s.require_vault
    out: dict[str, str] = {}
    for ref in refs.values():
        if use_vault and vault.is_vault_ref(ref):
            try:
                out.update(_get_vault().read_secret(ref))
                continue
            except Exception as e:  # noqa: BLE001
                if s.require_vault:
                    raise RuntimeError(f"Vault 取凭证失败且 require_vault=true: {ref}: {e}") from e
                log.warning("creds.vault_failed_fallback_env", ref=ref, error=str(e))
        out.update(_env_lookup(ref))     # Vault 未配 / 非强制时取值失败 → dev env 回退
    return out
