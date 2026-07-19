"""租户登记与系统类型模板目录。"""

from dano.registry.models import (
    SYSTEM_TEMPLATES,
    SystemTemplate,
    TenantRecord,
    all_system_templates,
    get_system_template,
    register_system_template,
)
from dano.registry.store import InMemoryRegistry, PgRegistry

__all__ = [
    "TenantRecord",
    "SystemTemplate",
    "SYSTEM_TEMPLATES",
    "register_system_template",
    "all_system_templates",
    "get_system_template",
    "InMemoryRegistry",
    "PgRegistry",
]
