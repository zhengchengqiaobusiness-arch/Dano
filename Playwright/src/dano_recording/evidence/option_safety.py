"""Privacy classification for captured choice collections."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


_PERSON_CONTEXT = re.compile(
    r"approver|approval|reviewer|assignee|employee|staff|member|owner|operator|"
    r"creator|user|person|people|full_?name|email|mobile|phone|"
    r"审批人|审核人|员工|成员|用户|负责人|经办人|人员|姓名|手机号|邮箱",
    re.IGNORECASE,
)
_PERSON_KEYS = re.compile(
    r"user_?id|employee_?id|member_?id|person_?id|approver_?id|reviewer_?id|"
    r"full_?name|email|mobile|phone|id_?card|passport",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9() .-]{6,}[0-9]$")


def is_identity_option_collection(
    *,
    context: str,
    options: Iterable[Mapping[str, Any]],
) -> bool:
    if _PERSON_CONTEXT.search(context):
        return True
    for option in options:
        if any(_PERSON_KEYS.search(str(key)) for key in option):
            return True
        for value in option.values():
            if isinstance(value, str):
                text = value.strip()
                if _EMAIL.fullmatch(text) or _PHONE.fullmatch(text):
                    return True
    return False


IDENTITY_OPTION_RESOLVER = "runtime_context.identity_directory.search"


__all__ = ["IDENTITY_OPTION_RESOLVER", "is_identity_option_collection"]
