"""全局枚举。集中定义,避免散落字符串字面量。"""

from __future__ import annotations

from enum import StrEnum


class AssetType(StrEnum):
    """五类企业资产(对应流程 2/3/4/5/8)。"""

    FIELD_MAPPING = "field_mapping"   # 字段映射(流程2)
    CONNECTOR = "connector"           # API 连接器(流程3)
    POLICY_RULE = "policy_rule"       # 制度规则(流程4)
    ENV_PROFILE = "env_profile"       # 环境画像(流程5)
    PAGE_SCRIPT = "page_script"       # 页面脚本,无 API(流程8)
    WORKFLOW = "workflow"             # 复合流程 Skill:多步连接器编排成一个业务能力(阶段2)
    ADAPTER = "adapter"               # 代码适配器:goal 模式自动生成的可执行 Skill(隔离 runner 执行)


class ValidationStatus(StrEnum):
    """资产验证状态。published 前不得被运行期 Worker 消费。"""

    DRAFT = "draft"
    TESTING = "testing"
    VERIFIED = "verified"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class RiskLevel(StrEnum):
    """风险等级(流程6 闸门):L4/L5 拒绝,L3 需确认卡片,L1/L2 直接执行。"""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


class Subsystem(StrEnum):
    """系统实例标识(作用域的一部分)。**开放**:任意租户的任意系统都可作为一个 subsystem,
    不再限于 A 公司三件套 —— OA/TICKET/REIMBURSE 只是原型常量,新系统用任意字符串即可。

    `_missing_` 让 `Subsystem("任意系统key")` 返回一个动态成员(而非抛 ValueError),从而 `Subsystem(sid)`
    在全库各处构造点都通用;pydantic v2 校验 `subsystem: Subsystem` 字段时也会经此放行未登记值(已实测)。
    动态成员不进 `_member_map_`(`list(Subsystem)` 仍只列三件套原型),但 `.value`/相等/哈希都按字符串正常工作。
    """

    OA = "A-OA"
    TICKET = "A-工单"
    REIMBURSE = "A-报销"

    @classmethod
    def _missing_(cls, value: object) -> "Subsystem | None":
        if isinstance(value, str):
            member = str.__new__(cls, value)
            member._name_ = value
            member._value_ = value
            return member
        return None


class FailureClass(StrEnum):
    """流程10 失败快速分类。决定可当场恢复 / 转流程11 / 转人工。"""

    LOGIN = "login"               # 登录 → 限次重试
    NETWORK = "network"           # 短暂网络 → 限次重试
    PAGE_FIELD = "page_field"     # 页面/字段变更 → 流程11 自愈
    PERMISSION = "permission"     # 权限 → 转人工
    PARAM = "param"               # 参数 → 转人工
    CONFIG = "config"             # 配置 → 转人工
    SYSTEM = "system"             # 系统 → 转人工


class SkillState(StrEnum):
    """Skill 生命周期状态(流程12)。不允许「AI 生成草稿直接用」。"""

    TEMPLATE = "模板"
    COPIED = "复制到A公司"
    BOUND = "绑定资产"
    TESTING = "测试中"
    PENDING_RELEASE = "待发布"
    PUBLISHED = "已发布"
    RUNNING = "运行中"
    SUSPENDED = "异常暂停"
    RETIRED = "已下线"


class RecoveryAction(StrEnum):
    """流程10 失败恢复决策。"""

    RETRY = "retry"            # 登录/短暂网络 → 限次受控重试
    REGENERATE = "regenerate"  # 页面/字段变更 → 流程11 自愈
    HUMAN = "human"            # 权限/参数/配置/系统 → 转人工


class MatchKind(StrEnum):
    """字段映射命中方式(流程2 置信判据):别名命中 > 子串匹配 > 同名。"""

    ALIAS = "alias"
    SUBSTRING = "substring"
    SAME_NAME = "same_name"


class Outcome(StrEnum):
    """断言二态铁律:执行结果只有跑通 / 跑不通,不留模糊空间。"""

    PASSED = "passed"
    FAILED = "failed"


class AuthKind(StrEnum):
    """鉴权适配器库的选项(库中选,不自造)。"""

    SSO = "sso"       # OA
    TOKEN = "token"   # 工单


class TaskState(StrEnum):
    """运行期一次任务的终态(流程6)。"""

    COMPLETED = "completed"            # 跑通 + 事实核查通过
    ANSWERED = "answered"             # 问事:知识检索合成答案返回(仅信息)
    FAILED = "failed"                 # 跑不通 / 验证不通过 → 流程10
    DRIFT = "drift"                   # 页面/字段漂移,执行前指纹不一致 → 转流程11
    REJECTED = "rejected"             # 制度/风险闸门拒绝(L4/L5 或规则拦截)
    CANCELLED = "cancelled"           # L3 确认卡片被用户取消
    NEEDS_INPUT = "needs_input"       # 缺必填且追问超限 → 转人工
    NEEDS_SELECT = "needs_select"     # 复合流程消歧:候选>1 未选 → 返回候选待前端/agent 选(DSL v2)
    NEEDS_KNOWLEDGE = "needs_knowledge"  # 问事 → 转知识检索子智能体(M3)
    CAPABILITY_GAP = "capability_gap"    # 做事但无对应动作 Skill → 新增 Skill(流程12)
    TRANSFER_HUMAN = "transfer_human"    # 兜底转人工
