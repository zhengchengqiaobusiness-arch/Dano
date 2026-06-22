// Dano pi 桥 · 自定义工具(极薄):每个工具只把请求 HTTP 代理回 Python,真实逻辑全在 Python。
// 安全:只打本机 + 带本次 run 的临时令牌(DANO_AGENT_TOKEN),env 由父进程白名单注入。
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const BASE = process.env.DANO_AGENT_BASE_URL;
const TOKEN = process.env.DANO_AGENT_TOKEN;
const RUN_ID = process.env.DANO_AGENT_RUN_ID;

export async function callPython(name, params, toolCallId) {
  const res = await fetch(`${BASE}/_agent/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Agent-Token": TOKEN },
    body: JSON.stringify({ run_id: RUN_ID, tool_call_id: toolCallId, params }),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`tool ${name} HTTP ${res.status}: ${text}`);
  return JSON.parse(text);
}

// 把一个 Python 工具包成 pi 工具(execute 即 HTTP 代理)。
function proxyTool({ name, label, description, parameters }) {
  return defineTool({
    name, label, description, parameters,
    execute: async (toolCallId, params) => {
      const out = await callPython(name, params, toolCallId);
      return { content: [{ type: "text", text: JSON.stringify(out, null, 2) }], isError: false };
    },
  });
}

export const customTools = [
  proxyTool({
    name: "parse_spec", label: "解析接口",
    description: "解析已导入的接口文档,返回业务动作清单(已过滤登录/验证码等基础设施)。",
    parameters: Type.Object({ system_instance_id: Type.String() }),
  }),
  proxyTool({
    name: "draft_connector", label: "建连接器草案",
    description: "为一个动作生成连接器草案(声明式资产体),返回 asset_draft_id。" +
      "**复合流程的步骤动作**(如提交步,不能独立跑)传 as_step=true:发布只需连得通、免单独沙箱/评审," +
      "且永不单独上架(由复合流程隐藏);真实校验交 sandbox_test_workflow 整链。",
    parameters: Type.Object({
      system_instance_id: Type.String(), action: Type.String(),
      as_step: Type.Optional(Type.Boolean()),
    }),
  }),
  proxyTool({
    name: "sandbox_test", label: "沙箱验证",
    description: "对连接器草案做连接测试+沙箱试跑(测试账号),返回 validation_run_ids 与是否通过。" +
      "写接口须传 sample_inputs(有效入参,如 {templateId:'...'}),否则真实系统会拒;" +
      "通过=HTTP 2xx 且业务码成功(按 success_rule)。",
    parameters: Type.Object({
      asset_draft_id: Type.String(),
      sample_inputs: Type.Optional(Type.Record(Type.String(), Type.Any())),
      as_step: Type.Optional(Type.Boolean()),   // 复合步骤:只做连接测试(整链由 sandbox_test_workflow 验)
    }),
  }),
  proxyTool({
    name: "get_action_schema", label: "看动作结构",
    description: "看一个动作的请求/响应结构(含嵌套字段)与示例,用于发现流程时构造 io 映射。",
    parameters: Type.Object({ system_instance_id: Type.String(), action: Type.String() }),
  }),
  proxyTool({
    name: "draft_workflow", label: "编排复合流程",
    description: "把多步编排成一个复合业务 Skill(DSL v2:声明式业务逻辑)。每个 step 有 kind(缺省 call):\n" +
      "- call:{action, inputs(目标路径→来源)}\n" +
      "- compute:{outputs(变量名→审计表达式,只准用 business_days/days_between/sum_/round_/min_/max_ 等审计函数)}\n" +
      "- branch:{condition(布尔表达式), then[子步], otherwise[子步]}\n" +
      "- foreach:{over(列表来源), as_var(当前项变量名), steps[子步]}\n" +
      "- select:{from_action(查询动作), list_path, label_template, bind(选中项绑定的变量名)}\n" +
      "来源前缀:const:常量 / field:用户字段 / step:动作.出参点路径 / var:compute产出 / item:foreach当前项 / select:选中值。\n" +
      "preconditions/invariants 各为不变量数组 {check(布尔表达式), message, evidence?{query_action, params}}:" +
      "前置不过则拒、不写;事后不变量回查证实真生效。**动作必须已发布、表达式只准用已声明字段/变量+审计函数**(臆造会被 grounding 拒)。",
    parameters: Type.Object({
      system_instance_id: Type.String(), action: Type.String(), title: Type.String(),
      user_fields: Type.Array(Type.String()), required_fields: Type.Array(Type.String()),
      steps: Type.Array(Type.Object({
        kind: Type.Optional(Type.String()),
        action: Type.Optional(Type.String()),
        inputs: Type.Optional(Type.Record(Type.String(), Type.String())),
        outputs: Type.Optional(Type.Record(Type.String(), Type.String())),
        condition: Type.Optional(Type.String()),
        then: Type.Optional(Type.Array(Type.Any())),
        otherwise: Type.Optional(Type.Array(Type.Any())),
        over: Type.Optional(Type.String()), as_var: Type.Optional(Type.String()),
        steps: Type.Optional(Type.Array(Type.Any())),
        from_action: Type.Optional(Type.String()), list_path: Type.Optional(Type.String()),
        label_template: Type.Optional(Type.String()), bind: Type.Optional(Type.String()),
      })),
      preconditions: Type.Optional(Type.Array(Type.Object({
        check: Type.String(), message: Type.Optional(Type.String()), evidence: Type.Optional(Type.Any()),
      }))),
      invariants: Type.Optional(Type.Array(Type.Object({
        check: Type.String(), message: Type.Optional(Type.String()), evidence: Type.Optional(Type.Any()),
      }))),
      preview: Type.Optional(Type.Boolean()),
      success_rule: Type.Optional(Type.String()),
    }),
  }),
  proxyTool({
    name: "sandbox_test_workflow", label: "验证复合流程",
    description: "用测试账号把复合流程整条按序 dry-run,返回 validation_run_ids 与是否通过。test_input 给流程级测试字段值。",
    parameters: Type.Object({
      asset_draft_id: Type.String(),
      test_input: Type.Record(Type.String(), Type.Any()),
    }),
  }),
  proxyTool({
    name: "get_policy_doc", label: "取制度原文",
    description: "返回该系统实例登记的制度文件原文,供抽取声明式规则。",
    parameters: Type.Object({ system_instance_id: Type.String() }),
  }),
  proxyTool({
    name: "get_business_rules", label: "取业务规则",
    description: "返回人工登记的业务规则(阈值/审批链)+ 日历源(holidays)。" +
      "据此 grounding 分支 condition / 前置·不变量 / compute 的 business_days,**不要臆造规则**。",
    parameters: Type.Object({ system_instance_id: Type.String() }),
  }),
  proxyTool({
    name: "get_selected_flows", label: "取勾选业务",
    description: "返回用户**人工勾选的业务**(templateId + 测试值)。**只针对这些业务**发现/编排复合流程;" +
      "sandbox_test_workflow 用这些测试值当 cases。返回空才可对全量业务自主发现。",
    parameters: Type.Object({ system_instance_id: Type.String() }),
  }),
  proxyTool({
    name: "draft_policy", label: "建制度规则草案",
    description: "把制度抽成声明式规则数组存草案。每条 rule:{rule_id, description, " +
      "condition(对输入字段的布尔表达式,如 'days > 15'), effect(放行|拦截|转审批)}。",
    parameters: Type.Object({
      system_instance_id: Type.String(),
      rules: Type.Array(Type.Object({
        rule_id: Type.String(), description: Type.String(),
        condition: Type.String(), effect: Type.String(),
      })),
    }),
  }),
  proxyTool({
    name: "test_policy_cases", label: "跑制度用例",
    description: "用关键用例验证规则:每条 case {fields(输入字段), expect(放行|拦截|转审批)}。" +
      "用运行期同一闸门判定;全通过才返回 passed=true 与 validation_run_ids,据此才能发布。",
    parameters: Type.Object({
      asset_draft_id: Type.String(),
      cases: Type.Array(Type.Object({
        fields: Type.Record(Type.String(), Type.Any()), expect: Type.String(),
      })),
    }),
  }),
  proxyTool({
    name: "request_review", label: "三模型评审",
    description: "沙箱通过后、发布前,对草案跑三模型评审(成果验收/漏洞检测/合规审核,各用不同模型)。" +
      "返回 all_passed、每审 passed 与 reasons、review_run_ids。任一审 reject 则按 reasons 修正后重测重审,不得发布。",
    parameters: Type.Object({ asset_draft_id: Type.String() }),
  }),
  proxyTool({
    name: "publish_asset", label: "发布资产",
    description: "发布草案(硬关卡:须附沙箱验证通过的 validation_run_ids + 三模型评审全通过的 review_run_ids;后端重读校验)。",
    parameters: Type.Object({
      asset_draft_id: Type.String(),
      validation_run_ids: Type.Array(Type.String()),
      review_run_ids: Type.Array(Type.String()),
    }),
  }),
  proxyTool({
    name: "draft_adapter", label: "建适配器草案",
    description: "存一份可执行适配器代码草案(goal 模式编码产物)。入口必须是 run(inputs, creds)->dict;" +
      "凭证从 creds 取,**任何密钥都不得写进 source**。",
    parameters: Type.Object({
      system_instance_id: Type.String(), action: Type.String(), source: Type.String(),
      strategy: Type.Optional(Type.String()), entry: Type.Optional(Type.String()),
      success_rule: Type.Optional(Type.String()),
      user_fields: Type.Optional(Type.Array(Type.String())),
      required_fields: Type.Optional(Type.Array(Type.String())),
    }),
  }),
  proxyTool({
    name: "sandbox_test_adapter", label: "沙箱测适配器",
    description: "在隔离 runner 跑适配器(测试账号),按 success_rule 判成败,返回 passed/validation_run_ids/reasons;" +
      "测不过按 reasons 修复后重测。test_input 给业务字段示例值。",
    parameters: Type.Object({
      asset_draft_id: Type.String(),
      test_input: Type.Optional(Type.Record(Type.String(), Type.Any())),
    }),
  }),
  proxyTool({
    name: "vuln_scan", label: "漏洞校验",
    description: "对适配器源码做静态扫描(危险调用/命令注入/硬编码密钥),返回 passed/validation_run_ids/findings;" +
      "有 findings 必须按其修复后重扫。发布需附本步的 validation_run_ids。",
    parameters: Type.Object({ asset_draft_id: Type.String() }),
  }),
];
