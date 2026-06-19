"""五类资产体的声明式 schema(对应文档第四节表)。

关键纪律:资产是**数据,不是写死的代码分支**。执行层是通用解释器,消费这些声明式
规格跑业务,绝不为某公司写 if/else。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from dano.shared.enums import AuthKind, MatchKind, RiskLevel


# ─────────────────────── 断言契约(声明式·机器可判·二态)───────────────────────
class Assertion(BaseModel):
    """单条断言。expr 是机器可判的声明式表达式,运行期只判 true/false。"""

    name: str
    expr: str = Field(description="声明式表达式,如 'response.request_id != null'")


class Assertions(BaseModel):
    """某动作的前置/后置断言集,由 pi coding 在生成连接器时一并产出(流程3)。"""

    pre: list[Assertion] = Field(default_factory=list, description="前置:字段齐全/余额≥申请天数/认证通过")
    post: list[Assertion] = Field(default_factory=list, description="后置:单号非空/status∈期望集/HTTP 2xx")


# ─────────────────────── ① 字段映射(流程2)───────────────────────
class FieldMapping(BaseModel):
    platform_std: str = Field(description="平台标准字段,如 applicant / start_time / amount")
    system_field: str = Field(description="系统真实字段,如 vacation_type / leaveCategory")
    match_kind: MatchKind
    confidence: float = Field(ge=0, le=1)


class FieldMappingBody(BaseModel):
    mappings: list[FieldMapping]


# ─────────────────────── ② API 连接器(流程3,主路径资产)───────────────────────
class FieldBinding(BaseModel):
    """连接器入参/出参与平台标准字段的绑定。"""

    param: str
    platform_std: str
    location: str = Field(default="body", description="body / query / path / header")
    required: bool = Field(default=True, description="该入参是否必填(来自接口规格 required)")


class FailureHandling(BaseModel):
    retryable_codes: list[int] = Field(default_factory=list)
    max_retries: int = 2


class ConnectorBody(BaseModel):
    endpoint: str
    method: str = "POST"
    auth_kind: AuthKind = Field(description="鉴权适配器库选项,库中选不自造")
    auth_ref: str = Field(description="凭证引用,如 vault://a-corp/oa(平台只存引用)")
    action: str = Field(description="动作名,如 create_leave / query_balance")
    title: str = Field(default="", description="人类可读标题(来自接口 summary,阶段4)")
    field_bindings: list[FieldBinding] = Field(default_factory=list)
    field_docs: dict[str, str] = Field(default_factory=dict, description="标准字段→语义描述(来自接口 schema,阶段4)")
    failure_handling: FailureHandling = Field(default_factory=FailureHandling)
    risk_level: RiskLevel = RiskLevel.L1
    assertions: Assertions = Field(default_factory=Assertions)
    required_mcp: list[str] = Field(default_factory=list, description="该动作所需的 MCP server(MCP 隔离校验)")


# ─────────────────────── ③ 制度规则(流程4)───────────────────────
class PolicyRule(BaseModel):
    """声明式规则数据(上限/是否需发票/审批链)。"""

    rule_id: str
    description: str
    condition: str = Field(description="声明式条件表达式")
    effect: str = Field(description="放行 / 拦截 / 转审批")


class PolicyRuleBody(BaseModel):
    rules: list[PolicyRule]


# ─────────────────────── ④ 环境画像(流程5)───────────────────────
class AuthConfig(BaseModel):
    """运行时鉴权握手配置(库中选,不自造)。属于环境画像,描述「怎么登进这个系统」。

    - Token:credentials 直接给 token;或给 apikey + token_path 由系统换取 token。
    - SSO:credentials 直接给 session;或给 username/password + login_path 表单登录换 session。
    """

    kind: AuthKind = AuthKind.TOKEN
    # Token 方式
    token_path: str | None = Field(default=None, description="用 apikey 换 token 的 endpoint(可选)")
    token_header: str = "Authorization"
    token_prefix: str = "Bearer "
    token_field: str = Field(default="token", description="换取响应里 token 的字段名")
    token_ttl_seconds: int = 3600
    # SSO 方式
    login_path: str | None = Field(default=None, description="SSO 表单登录 endpoint(可选)")
    username_field: str = "username"
    password_field: str = "password"
    session_cookie_header: str = "Cookie"


class CredentialPolicy(BaseModel):
    """凭证撤销/过期策略(流程5 第4步)。平台只存策略与引用,不持明文。"""

    expires_at: str | None = Field(default=None, description="过期时间 ISO8601;None=长期")
    rotation_days: int | None = Field(default=None, description="轮换周期(天)")
    revoked: bool = False


class EnvProfileBody(BaseModel):
    deploy: str = Field(description="部署方式")
    worker_location: str = Field(description="Worker 位置")
    intranet_access: str = Field(description="内网访问方式")
    account_type: str
    min_privilege: list[str] = Field(default_factory=list, description="最小权限清单")
    base_url: str = Field(default="", description="系统基址(运行时拼 endpoint),来自部署信息")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="鉴权握手配置")
    credential_policy: CredentialPolicy = Field(default_factory=CredentialPolicy, description="撤销/过期策略")


# ─────────────────────── ⑤ 页面脚本(无 API,流程8)───────────────────────
class PageAction(BaseModel):
    """页面动作。仅元素/文本/DOM 定位,绝不用坐标。"""

    op: str = Field(description="goto/fill/select/upload/click/wait/verify")
    locator: str | None = Field(default=None, description="语义定位:role/text/DOM 路径")
    value: str | None = None


class PageScriptBody(BaseModel):
    actions: list[PageAction]
    dom_fingerprint: str = Field(description="结构指纹,执行前校验改版的基线")


# ─────────────────────── ⑥ 复合流程 Skill(阶段2:多步编排成一个业务能力)───────────────────────
class WorkflowStep(BaseModel):
    """流程一步:调用一个已发布连接器动作,入参按来源映射拼装。

    inputs:目标参数路径 → 来源。目标支持嵌套点路径(如 'flowTask.taskId')。
    来源语法:
      - 'field:<名>'   取用户提供的业务字段(如 field:leaveDays)
      - 'step:<动作>.<点路径>'  取上一步响应体里的值(如 step:start_leave_flow.data.taskId)
      - 'const:<字面量>'  常量(如 const:200)
    """

    action: str = Field(description="本步调用的连接器动作名(须为同作用域已发布连接器)")
    inputs: dict[str, str] = Field(default_factory=dict, description="目标参数路径 → 来源表达式")


class WorkflowSkillBody(BaseModel):
    """复合流程 Skill:把多步连接器编排成一个面向用户的业务能力(如「提交请假」)。

    执行层是通用解释器:按 steps 顺序跑,前一步输出按 step: 映射喂给后一步,
    全步成功才算成功(套用成败规则)。绝不为某家公司写 if/else。
    """

    action: str = Field(description="复合 Skill 名,如 submit_leave")
    title: str = Field(default="", description="人类可读标题")
    steps: list[WorkflowStep] = Field(description="有序步骤(至少 1 步)")
    user_fields: list[str] = Field(default_factory=list, description="用户需提供的业务字段")
    field_docs: dict[str, str] = Field(default_factory=dict, description="业务字段→语义描述(阶段4)")
    required_fields: list[str] = Field(default_factory=list, description="必填业务字段")
    risk_level: RiskLevel = RiskLevel.L3
    success_rule: str | None = Field(default=None, description="每步成败判定表达式;None=HTTP 2xx")


# ─────────────────────── 事实核查(流程9·声明式)───────────────────────
class FactCheckSpec(BaseModel):
    """回查确认副作用真的生效(不信接口返回的『操作成功』)。

    执行:按 method 调 endpoint(模板可引用入参/前序输出),对响应跑 assert_expr;
    submit 多为异步,故带轮询(retries/backoff)再判失败,避免「成功了只是查太早」。
    """

    endpoint: str = Field(description="回查端点,可含 {占位}")
    method: str = "GET"
    params_template: dict[str, str] = Field(default_factory=dict, description="查询参数模板")
    assert_expr: str = Field(description="对响应的布尔表达式,真=确认生效")
    retries: int = 5
    backoff_s: float = 0.8


# ─────────────────────── 生成方案(goal 模式·定方案产物)───────────────────────
class PlanBody(BaseModel):
    """goal 模式「定方案」阶段产物:可被评审/驳回的方案,先过审再编码。

    纪律:方案描述「做什么、按什么契约、怎么判成败、怎么事实核查」,不含可执行代码。
    """

    flow: str = Field(description="目标业务流程名,如 submit_leave")
    strategy: str = Field(description="选用的生成策略名,如 workflow_bpmn / simple_http")
    steps: list[str] = Field(default_factory=list, description="拆解出的步骤(人类可读)")
    contract: dict = Field(default_factory=dict, description="探测/逆向得到的接口契约要点")
    user_fields: list[str] = Field(default_factory=list, description="用户需提供的业务字段")
    required_fields: list[str] = Field(default_factory=list, description="必填业务字段")
    field_docs: dict[str, str] = Field(default_factory=dict, description="字段→语义描述(供前端/LLM/导出)")
    consts: dict = Field(default_factory=dict, description="运行期注入的内部常量(如 __templateId__),非用户字段")
    evidence: dict = Field(default_factory=dict, description="v3:裁剪后的证据(端点/表单字段/样例返回),供编码器据实写码")
    success_rule: str | None = Field(default=None, description="成败判定表达式")
    fact_check: FactCheckSpec | None = Field(default=None, description="事实核查规格")


# ─────────────────────── 代码适配器(goal 模式·编码产物)───────────────────────
class AdapterBody(BaseModel):
    """goal 模式「编码」阶段产物:自动生成的可执行适配器,经隔离 runner 执行。

    约束:源码内**零凭证**(运行期注入);入口签名固定 run(inputs: dict, creds: dict) -> dict;
    成败以 success_rule + fact_check 为准,不信接口字面成功。
    """

    action: str = Field(description="Skill 名,如 submit_leave")
    title: str = Field(default="", description="人类可读标题")
    business: str = Field(default="", description="所属业务(同业务多操作 adapter 导出时归为一个 skill)")
    strategy: str = Field(description="生成该适配器的策略名")
    language: str = Field(default="python", description="实现语言(M0 仅 python)")
    source: str = Field(description="适配器源码;入口为 entry 指定的函数")
    entry: str = Field(default="run", description="入口函数名,签名 run(inputs, creds)->dict")
    input_schema: dict = Field(default_factory=dict, description="入参 JSON Schema(供前端/校验)")
    user_fields: list[str] = Field(default_factory=list, description="用户需提供的业务字段")
    required_fields: list[str] = Field(default_factory=list, description="必填业务字段")
    field_docs: dict[str, str] = Field(default_factory=dict, description="字段→语义描述(供前端/LLM/导出)")
    consts: dict = Field(default_factory=dict, description="运行期注入的内部常量(如 __templateId__),非用户字段")
    risk_level: RiskLevel = RiskLevel.L3
    success_rule: str | None = Field(default=None, description="成败判定表达式;None=HTTP 2xx")
    fact_check: FactCheckSpec | None = Field(default=None, description="事实核查规格")
    plan_ref: str | None = Field(default=None, description="对应方案 PlanBody 的 asset_draft_id")
