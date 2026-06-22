"""五类资产体的声明式 schema(对应文档第四节表)。

关键纪律:资产是**数据,不是写死的代码分支**。执行层是通用解释器,消费这些声明式
规格跑业务,绝不为某公司写 if/else。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    # 工作流步骤:只在复合流程里被串用、**不能独立跑**(如提交步需上一步的 taskId)。
    # → 发布闸门放宽到"连得通即可"(沙箱/评审由复合 sandbox_test_workflow 整链验证);目录里**永不单独露出**。
    workflow_step: bool = Field(default=False, description="是否为复合流程的隐藏步骤(连接器)")
    business: str = Field(default="", description="所属业务(同业务归一本剧本;空=独立能力)")
    visibility: str = Field(default="catalog",
                            description="catalog=对外业务能力 / internal=内部步骤(前置查询等),不发现/导出/直调")


def asset_internal(body: dict) -> bool:
    """资产是否为内部步骤,不进目录 / 导出 / 直调:`workflow_step` 或 `visibility==internal`。

    单一判据:网关目录、function-calling tools、导出、生命周期登记,全用它过滤——
    前置查询(开表单/查模板/查余额)标 internal 后,任何入口都不再泄漏成平级 skill。
    """
    return bool(body.get("workflow_step") or body.get("visibility") == "internal")


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
    holidays: list[str] = Field(default_factory=list, description="日历源:法定节假日(运行期注入 compute business_days)")


# ─────────────────────── ⑤ 页面脚本(无 API,流程8)───────────────────────
class PageAction(BaseModel):
    """页面动作。仅元素/文本/DOM 定位,绝不用坐标。"""

    op: str = Field(description="goto/fill/select/upload/click/wait/verify")
    locator: str | None = Field(default=None, description="语义定位:role/text/DOM 路径")
    value: str | None = None


class PageScriptBody(BaseModel):
    actions: list[PageAction]
    dom_fingerprint: str = Field(description="结构指纹,执行前校验改版的基线")


# ─────────────────────── ⑥ 复合流程 Skill(阶段2 + DSL v2:声明式业务逻辑)───────────────────────
StepKind = Literal["call", "compute", "branch", "foreach", "select"]


class Invariant(BaseModel):
    """业务不变量(前置校验 / 事后正确性)。check 为 safe_eval 布尔表达式;
    给了 evidence 则先回查真实系统再判(grounded),否则只看当前上下文。"""

    check: str = Field(description="布尔表达式(safe_eval),真=通过")
    message: str = Field(default="", description="不通过时给用户/审计的说明")
    evidence: dict | None = Field(default=None, description="{query_action, params} 回查真实系统(grounded);None=只看上下文")


class WorkflowStep(BaseModel):
    """流程一步(DSL v2:带类型节点)。kind 缺省 'call' → 向后兼容旧的纯调用步。

    来源语法(inputs 的值 / over):
      - 'const:<字面量>'         常量(如 const:200)
      - 'field:<名>'            用户业务字段(如 field:leaveDays)
      - 'step:<动作>.<点路径>'   某步响应体里的值(如 step:start_leave_flow.data.taskId)
      - 'var:<名>'              compute 产出的派生变量(如 var:leave_days)
      - 'item:<点路径>'          foreach 当前项里的值(点路径可空 = 整项)
      - 'select:<名>'           select 选中项绑定的值
    """

    kind: StepKind = Field(default="call", description="节点类型")
    # kind=call:调用已发布连接器
    action: str | None = Field(default=None, description="连接器动作名(call)")
    inputs: dict[str, str] = Field(default_factory=dict, description="目标参数路径 → 来源(call)")
    # kind=compute:派生计算(只准审计函数)
    outputs: dict[str, str] = Field(default_factory=dict, description="变量名 → 审计表达式(compute)")
    # kind=branch:条件分支
    condition: str | None = Field(default=None, description="布尔表达式(branch)")
    then: list[WorkflowStep] = Field(default_factory=list, description="条件真时的子步骤(branch)")
    otherwise: list[WorkflowStep] = Field(default_factory=list, description="条件假时的子步骤(branch)")
    # kind=foreach:批量
    over: str | None = Field(default=None, description="遍历来源(列表)(foreach)")
    as_var: str = Field(default="item", description="当前项变量名,来源用 item:(foreach)")
    steps: list[WorkflowStep] = Field(default_factory=list, description="每项执行的子步骤(foreach)")
    # kind=select:从候选里选(消歧)
    from_action: str | None = Field(default=None, description="候选来源:已发布查询动作(select)")
    list_path: str | None = Field(default=None, description="响应里候选列表的点路径(select)")
    label_template: str | None = Field(default=None, description="候选展示模板,如 '{name}-{dept}'(select)")
    bind: str | None = Field(default=None, description="选中项绑定到的变量名,来源用 select:(select)")

    @model_validator(mode="after")
    def _check_kind(self) -> WorkflowStep:
        required = {
            "call": ("action",), "compute": ("outputs",), "branch": ("condition",),
            "foreach": ("over",), "select": ("from_action", "bind"),
        }[self.kind]
        missing = [f for f in required if not getattr(self, f)]
        if missing:
            raise ValueError(f"kind={self.kind} 缺必填字段: {missing}")
        return self


WorkflowStep.model_rebuild()


class WorkflowSkillBody(BaseModel):
    """复合流程 Skill:把多步连接器编排成一个面向用户的业务能力(如「提交请假」)。

    执行层是通用解释器(DSL v2):前置不变量 → 按 steps 顺序跑(call/compute/branch/foreach/select)
    → 业务不变量;前一步输出按 step: 映射喂后一步。绝不为某家公司写 if/else。
    """

    action: str = Field(description="复合 Skill 名,如 submit_leave")
    title: str = Field(default="", description="人类可读标题")
    steps: list[WorkflowStep] = Field(description="有序步骤(至少 1 步)")
    user_fields: list[str] = Field(default_factory=list, description="用户需提供的业务字段")
    field_docs: dict[str, str] = Field(default_factory=dict, description="业务字段→语义描述(阶段4)")
    field_types: dict[str, str] = Field(default_factory=dict, description="业务字段→JSON 类型(信源 schema)")
    field_mappings: list[dict] = Field(default_factory=list,
                                       description="可追溯字段映射(标准字段→目标点路径+类型+来源 schema_ref),§16")
    required_fields: list[str] = Field(default_factory=list, description="必填业务字段")
    business: str = Field(default="", description="所属业务(导出归组用)")
    business_meta: dict = Field(default_factory=dict, description="业务规则(x-flow/审批链)→ 导出的审批/前置/确认段")
    visibility: str = Field(default="catalog", description="catalog=对外业务能力 / internal=内部(一般不用,复合流程默认对外)")
    risk_level: RiskLevel = RiskLevel.L3
    success_rule: str | None = Field(default=None, description="每步成败判定表达式;None=HTTP 2xx")
    # DSL v2:前置/事后不变量 + 写前预览
    preconditions: list[Invariant] = Field(default_factory=list, description="办理前不变量:不过则拒、不写")
    invariants: list[Invariant] = Field(default_factory=list, description="办理后业务正确性不变量")
    preview: bool = Field(default=False, description="写操作:执行前回显将提交内容待确认")
    goal: dict = Field(default_factory=dict, description="结构化业务目标(意图/成功标准/候选步/禁止步),接入期据材料生成")


class GoalBody(BaseModel):
    """结构化业务目标(接入期据材料动态生成):是复合流程的 grounding 锚。

    既给 pi/解释器"要达成什么"(success_criteria),也给出红线(forbidden_steps:删除/审批他人/越权),
    draft_workflow 校验步骤不得命中 forbidden_steps。内容全部来自接入材料,代码不写死业务字面量。
    """

    goal_id: str
    business_type: str = ""
    selected_template: str = ""
    intent: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    optional_inputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    candidate_steps: list[str] = Field(default_factory=list)
    forbidden_steps: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.L3
    requires_confirmation: bool = True


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
    business_meta: dict = Field(default_factory=dict, description="业务规则(来自 x-flow:审批链/校验/驳回/记账),供导出剧本的前置/错误/事后确认段")
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
