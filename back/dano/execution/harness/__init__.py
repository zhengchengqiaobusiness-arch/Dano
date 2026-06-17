"""子智能体运行壳(harness):强制四重隔离 + 凭证引用注入 + 断言 + 留痕。"""

from dano.execution.harness.harness import Harness, tool_name_for

__all__ = ["Harness", "tool_name_for"]
