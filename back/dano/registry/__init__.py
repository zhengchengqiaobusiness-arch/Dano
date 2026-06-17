"""租户与系统实例登记(流程1 第1–3步)。

文档流程1 前三步:建租户 → 选系统类型模板 → 创建系统实例。本模块把这三步落成
可审计的真实记录(PG 持久化),接入(流程1 第4步起)针对已登记实例导入材料生成资产。
"""

from dano.registry.models import (
    SYSTEM_TEMPLATES,
    SystemInstance,
    SystemTemplate,
    TenantRecord,
)
from dano.registry.store import InMemoryRegistry, PgRegistry, RegistryStore

__all__ = [
    "TenantRecord",
    "SystemInstance",
    "SystemTemplate",
    "SYSTEM_TEMPLATES",
    "RegistryStore",
    "InMemoryRegistry",
    "PgRegistry",
]
