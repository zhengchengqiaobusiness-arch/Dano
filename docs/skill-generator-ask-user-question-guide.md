# `ask_user_question`：OA Skill 生成指南

本文档供负责生成 OA Skill 的后端模型阅读。后端模型应根据本文档生成
可被 Dano 加载的 Skill；实际的 `ask_user_question` 调用发生在生成后的 Skill
被 Dano 使用时，而不是 Skill 生成阶段。

目标是让生成出的 Skill 只描述准确、规范、可执行的工具调用，不臆造 OA
能力、接口参数或字段映射。本文档中的参数名、JSON Schema、状态和错误 code
保持实现中的英文原名，其余说明使用中文。

## 工具用途

在已经确认 OA Skill 覆盖用户所需业务动作后，以下情况应原生调用
`ask_user_question`：

- 继续办理前必须取得用户输入；
- 需要用户填写一个字段或一组相关字段；
- 需要用户从静态选项或 OA 接口返回的选项中选择；
- 需要用户核对已提交分组表单的最新答案。

以下情况不要调用：

- 答案已能从当前对话或已读取的业务数据中确定；
- 对应 OA Skill 尚未确认支持该业务动作；
- 想让用户判断接口是否存在、字段是否必填或字段如何映射——这些应在生成
  Skill 时由已确认的 OA 能力定义；
- 只需向用户说明结果，不需要新的输入；
- 想用普通文本、Markdown、XML 或 JSON 代码块模拟提问。

每次模型响应最多原生调用一次 `ask_user_question`。需要多个相关答案时，必须
使用一个 `title + questions[]` 分组表单。每个非确认问题都要提供来自当前业务
上下文的非空推荐 `default`；`required` 只决定用户能否清空或省略答案。

## 三种调用形状

1. **单问题**：顶层提供 `question`、`default` 和适用于该控件的字段配置。
2. **分组表单**：顶层只提供非空 `title` 与非空 `questions[]`；每个 item
   使用唯一 `id`，所有字段配置都放在对应 item 内。
3. **最终确认**：此前分组表单返回 `answered.formId` 后，在同一 Assistant Turn
   内调用 `confirm:true + formIds[]`。不要重复问题、答案、选项或字段配置。

普通句子或单个业务选择的确认使用 `radio` 单问题，不使用 `confirm:true`。
`confirm:true` 只确认此前已提交的分组表单。

## Canonical 请求 JSON Schema

下面的三个分支互斥，并且只允许列出的 canonical 字段。`options` 在运行时允许
非空字符串或 `{id,label,extra?}`；生成 Skill 时应优先使用带稳定 `id` 的对象，
通常提供至少两个有效选项，并让 `default` 引用 option ID。

<!-- schema:request -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "optionId": {
      "anyOf": [
        { "type": "string", "minLength": 1 },
        { "type": "number" }
      ]
    },
    "option": {
      "anyOf": [
        { "type": "string", "minLength": 1 },
        {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "id": { "$ref": "#/$defs/optionId" },
            "label": { "type": "string", "minLength": 1 },
            "extra": { "type": "object", "additionalProperties": true }
          },
          "required": ["id", "label"]
        }
      ]
    },
    "defaultValue": {
      "anyOf": [
        { "type": "string", "minLength": 1 },
        { "type": "number" },
        {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/optionId" }
        }
      ]
    },
    "dataSource": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "type": { "const": "api" },
        "endpoint": { "type": "string", "minLength": 1 },
        "method": { "enum": ["GET", "POST"] },
        "params": { "type": "object", "additionalProperties": true },
        "searchParam": { "type": "string", "minLength": 1 },
        "pageParam": { "type": "string", "minLength": 1 },
        "pageSizeParam": { "type": "string", "minLength": 1 },
        "pageSize": { "type": "number", "minimum": 1 },
        "resultPath": { "type": "string", "minLength": 1 },
        "totalPath": { "type": "string", "minLength": 1 },
        "idField": { "type": "string", "minLength": 1 },
        "labelField": { "type": "string", "minLength": 1 },
        "childrenField": { "type": "string", "minLength": 1 },
        "extraFields": {
          "type": "array",
          "items": { "type": "string", "minLength": 1 }
        }
      },
      "required": ["type", "endpoint"]
    },
    "controlShape": {
      "anyOf": [
        {
          "properties": {
            "inputType": { "enum": ["text", "textarea"] },
            "default": { "type": "string", "minLength": 1 }
          },
          "not": {
            "anyOf": [
              { "required": ["options"] },
              { "required": ["dateFormat"] },
              { "required": ["dataSource"] },
              { "required": ["multiple"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "const": "date" },
            "default": { "type": "string", "minLength": 1 }
          },
          "required": ["inputType", "dateFormat"],
          "not": {
            "anyOf": [
              { "required": ["options"] },
              { "required": ["fieldAssist"] },
              { "required": ["dataSource"] },
              { "required": ["multiple"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "enum": ["radio", "select", "treeSelect"] },
            "multiple": { "const": false },
            "default": { "$ref": "#/$defs/optionId" }
          },
          "required": ["inputType", "options"],
          "not": {
            "anyOf": [
              { "required": ["fieldAssist"] },
              { "required": ["dateFormat"] },
              { "required": ["dataSource"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "const": "checkbox" },
            "multiple": { "const": true },
            "default": {
              "type": "array",
              "minItems": 1,
              "items": { "$ref": "#/$defs/optionId" }
            }
          },
          "required": ["inputType", "options"],
          "not": {
            "anyOf": [
              { "required": ["fieldAssist"] },
              { "required": ["dateFormat"] },
              { "required": ["dataSource"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "enum": ["select", "treeSelect"] },
            "multiple": { "const": true },
            "default": {
              "type": "array",
              "minItems": 1,
              "items": { "$ref": "#/$defs/optionId" }
            }
          },
          "required": ["inputType", "options", "multiple"],
          "not": {
            "anyOf": [
              { "required": ["fieldAssist"] },
              { "required": ["dateFormat"] },
              { "required": ["dataSource"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "enum": ["select", "treeSelect"] },
            "multiple": { "const": false },
            "default": { "$ref": "#/$defs/optionId" }
          },
          "required": ["inputType", "dataSource"],
          "not": {
            "anyOf": [
              { "required": ["fieldAssist"] },
              { "required": ["dateFormat"] }
            ]
          }
        },
        {
          "properties": {
            "inputType": { "enum": ["select", "treeSelect"] },
            "multiple": { "const": true },
            "default": {
              "type": "array",
              "minItems": 1,
              "items": { "$ref": "#/$defs/optionId" }
            }
          },
          "required": ["inputType", "dataSource", "multiple"],
          "not": {
            "anyOf": [
              { "required": ["fieldAssist"] },
              { "required": ["dateFormat"] }
            ]
          }
        }
      ]
    },
    "questionItem": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "question": { "type": "string", "minLength": 1 },
        "options": {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/option" }
        },
        "inputType": {
          "enum": ["text", "textarea", "date", "radio", "checkbox", "select", "treeSelect"]
        },
        "fieldAssist": { "type": "boolean" },
        "dateFormat": { "type": "string", "minLength": 1 },
        "dataSource": { "$ref": "#/$defs/dataSource" },
        "multiple": { "type": "boolean" },
        "required": { "type": "boolean" },
        "default": { "$ref": "#/$defs/defaultValue" }
      },
      "allOf": [{ "$ref": "#/$defs/controlShape" }],
      "required": ["id", "question", "default"]
    },
    "singleQuestion": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "question": { "type": "string", "minLength": 1 },
        "options": {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/option" }
        },
        "inputType": {
          "enum": ["text", "textarea", "date", "radio", "checkbox", "select", "treeSelect"]
        },
        "fieldAssist": { "type": "boolean" },
        "dateFormat": { "type": "string", "minLength": 1 },
        "dataSource": { "$ref": "#/$defs/dataSource" },
        "multiple": { "type": "boolean" },
        "required": { "type": "boolean" },
        "default": { "$ref": "#/$defs/defaultValue" }
      },
      "allOf": [{ "$ref": "#/$defs/controlShape" }],
      "required": ["question", "default"]
    },
    "groupedForm": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "title": { "type": "string", "minLength": 1 },
        "questions": {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/questionItem" }
        }
      },
      "required": ["title", "questions"]
    },
    "confirmation": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "confirm": { "const": true },
        "formIds": {
          "type": "array",
          "minItems": 1,
          "items": { "type": "string", "minLength": 1 }
        }
      },
      "required": ["confirm", "formIds"]
    }
  },
  "anyOf": [
    { "$ref": "#/$defs/singleQuestion" },
    { "$ref": "#/$defs/groupedForm" },
    { "$ref": "#/$defs/confirmation" }
  ]
}
```

控件规则：

- `text` 的 Field Assist 默认关闭，`textarea` 默认开启；只在文本控件上使用
  boolean `fieldAssist`。
- `date` 必须提供 `dateFormat`。格式必须包含年、月、日；时间格式必须同时包含
  24 小时制小时和分钟，不支持秒与时区。非空 `default` 必须匹配格式。
- `radio` 是静态单选，`checkbox` 是静态多选；`select` 与 `treeSelect` 可使用
  静态 `options` 或 `dataSource`。`multiple:true` 返回 ID 数组。
- 选项 label 为“其他”或 `Other` 时，用户可以提交一个不在 options 中的自定义
  值；多选最多包含一个自定义值。
- 单问题字段配置位于顶层；分组字段配置全部位于各自的 `questions[]` item。

## 可执行示例

每个示例先说明当前已知业务上下文，再给出原生工具参数、预期 canonical Card
Request 或返回值。示例日期均来自示例中的业务记录，不代表当前日期。

### E01 单行文本与显式 Field Assist

场景：OA Skill 已确认支持客户拜访申请，用户已说明申请标题应为“华东区客户拜访申请”。

<!-- example:E01 kind:request -->
```json
{
  "question": "申请标题是什么？",
  "inputType": "text",
  "fieldAssist": true,
  "required": true,
  "default": "华东区客户拜访申请"
}
```

<!-- example:E01 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "text",
  "question": "申请标题是什么？",
  "fieldAssist": true,
  "required": true,
  "default": "华东区客户拜访申请"
}
```

<!-- example:E01 kind:result -->
```json
{
  "status": "answered",
  "answer": "华东区重点客户拜访申请"
}
```

### E02 多行文本与显式关闭 Field Assist

场景：OA Skill 需要可选的合规备注；用户已给出默认说明，且该字段必须保留原文，
不需要生成或润色。

<!-- example:E02 kind:request -->
```json
{
  "question": "请核对合规备注",
  "inputType": "textarea",
  "fieldAssist": false,
  "required": false,
  "default": "材料已由法务复核"
}
```

<!-- example:E02 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "text",
  "question": "请核对合规备注",
  "inputType": "textarea",
  "fieldAssist": false,
  "default": "材料已由法务复核"
}
```

### E03 一次填写完整分组表单

场景：员工正在更正历史档案。当前档案记录的入职日期为 `2021-06-18`，历史
登记时间为 `2024-10-18 09:30`；这些值是业务记录，不是“今天”。

<!-- example:E03 kind:request -->
```json
{
  "title": "员工档案更正申请",
  "questions": [
    {
      "id": "employee_name",
      "question": "员工姓名",
      "inputType": "text",
      "required": true,
      "default": "张三"
    },
    {
      "id": "change_reason",
      "question": "更正原因",
      "inputType": "textarea",
      "required": true,
      "default": "纠正历史档案中的入职信息"
    },
    {
      "id": "employment_date",
      "question": "档案中的入职日期",
      "inputType": "date",
      "dateFormat": "yyyy-MM-dd",
      "required": true,
      "default": "2021-06-18"
    },
    {
      "id": "recorded_at",
      "question": "历史登记时间",
      "inputType": "date",
      "dateFormat": "yyyy-MM-dd HH:mm",
      "required": true,
      "default": "2024-10-18 09:30"
    },
    {
      "id": "employment_type",
      "question": "用工类型",
      "inputType": "radio",
      "options": [
        { "id": "full_time", "label": "正式员工" },
        { "id": "contractor", "label": "外包人员" }
      ],
      "required": true,
      "default": "full_time"
    },
    {
      "id": "systems",
      "question": "需要同步更正的系统",
      "inputType": "checkbox",
      "options": [
        { "id": "hr", "label": "人事系统" },
        { "id": "payroll", "label": "薪酬系统" }
      ],
      "multiple": true,
      "required": true,
      "default": ["hr"]
    },
    {
      "id": "department",
      "question": "主部门",
      "inputType": "select",
      "options": [
        { "id": "dep_sales", "label": "销售部" },
        { "id": "dep_finance", "label": "财务部" }
      ],
      "multiple": false,
      "required": true,
      "default": "dep_sales"
    }
  ]
}
```

<!-- example:E03 kind:card -->
```json
{
  "batch": true,
  "title": "员工档案更正申请",
  "questions": [
    {
      "id": "employee_name",
      "kind": "text",
      "question": "员工姓名",
      "fieldAssist": false,
      "required": true,
      "default": "张三"
    },
    {
      "id": "change_reason",
      "kind": "text",
      "question": "更正原因",
      "fieldAssist": true,
      "inputType": "textarea",
      "required": true,
      "default": "纠正历史档案中的入职信息"
    },
    {
      "id": "employment_date",
      "kind": "date",
      "question": "档案中的入职日期",
      "dateFormat": "yyyy-MM-dd",
      "required": true,
      "default": "2021-06-18"
    },
    {
      "id": "recorded_at",
      "kind": "date",
      "question": "历史登记时间",
      "dateFormat": "yyyy-MM-dd HH:mm",
      "required": true,
      "default": "2024-10-18 09:30"
    },
    {
      "id": "employment_type",
      "kind": "single",
      "question": "用工类型",
      "options": [
        { "id": "full_time", "label": "正式员工" },
        { "id": "contractor", "label": "外包人员" }
      ],
      "required": true,
      "default": "full_time"
    },
    {
      "id": "systems",
      "kind": "multiple",
      "question": "需要同步更正的系统",
      "options": [
        { "id": "hr", "label": "人事系统" },
        { "id": "payroll", "label": "薪酬系统" }
      ],
      "required": true,
      "default": ["hr"]
    },
    {
      "id": "department",
      "kind": "select",
      "question": "主部门",
      "options": [
        { "id": "dep_sales", "label": "销售部" },
        { "id": "dep_finance", "label": "财务部" }
      ],
      "required": true,
      "default": "dep_sales"
    }
  ]
}
```

<!-- example:E03 kind:result -->
```json
{
  "status": "answered",
  "formId": "employee-profile-form-call",
  "answer": {
    "employee_name": "张三",
    "change_reason": "纠正历史档案中的入职信息",
    "employment_date": "2021-06-18",
    "recorded_at": "2024-10-18 09:30",
    "employment_type": "full_time",
    "systems": ["hr", "payroll"],
    "department": "dep_sales"
  }
}
```

### E04 普通业务确认使用 radio

场景：Skill 已准备好一段通知，但尚未发送；只需确认“立即发送”还是“保存草稿”。

<!-- example:E04 kind:request -->
```json
{
  "question": "如何处理这份通知？",
  "inputType": "radio",
  "options": [
    { "id": "send_now", "label": "立即发送" },
    { "id": "save_draft", "label": "保存草稿" }
  ],
  "required": true,
  "default": "save_draft"
}
```

<!-- example:E04 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "single",
  "question": "如何处理这份通知？",
  "options": [
    { "id": "send_now", "label": "立即发送" },
    { "id": "save_draft", "label": "保存草稿" }
  ],
  "required": true,
  "default": "save_draft"
}
```

### E05 单选中的自定义“其他”

场景：报销类型允许选择标准类型，也允许用户填写一个自定义类型。

<!-- example:E05 kind:request -->
```json
{
  "question": "请选择报销类型",
  "inputType": "radio",
  "options": [
    { "id": "travel", "label": "差旅费" },
    { "id": "office", "label": "办公费" },
    { "id": "other", "label": "其他" }
  ],
  "required": true,
  "default": "travel"
}
```

<!-- example:E05 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "single",
  "question": "请选择报销类型",
  "options": [
    { "id": "travel", "label": "差旅费" },
    { "id": "office", "label": "办公费" },
    { "id": "other", "label": "其他" }
  ],
  "required": true,
  "default": "travel"
}
```

<!-- example:E05 kind:result -->
```json
{
  "status": "answered",
  "answer": "客户活动物料费"
}
```

### E06 多选中的一个自定义回答

场景：资产申请可选择标准用途，并允许补充最多一个自定义用途。

<!-- example:E06 kind:request -->
```json
{
  "question": "请选择资产用途",
  "inputType": "checkbox",
  "options": [
    { "id": "development", "label": "研发" },
    { "id": "testing", "label": "测试" },
    { "id": "other", "label": "其他" }
  ],
  "multiple": true,
  "required": true,
  "default": ["development"]
}
```

<!-- example:E06 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "multiple",
  "question": "请选择资产用途",
  "options": [
    { "id": "development", "label": "研发" },
    { "id": "testing", "label": "测试" },
    { "id": "other", "label": "其他" }
  ],
  "required": true,
  "default": ["development"]
}
```

<!-- example:E06 kind:result -->
```json
{
  "status": "answered",
  "answer": ["development", "客户演示"]
}
```

### E07 GET 远程 select

场景：Skill 已确认员工查询接口及字段映射，用户需要选择一名审批人。

<!-- example:E07 kind:request -->
```json
{
  "question": "请选择审批人",
  "inputType": "select",
  "dataSource": {
    "type": "api",
    "endpoint": "/api/oa/employees",
    "method": "GET",
    "params": { "status": "active" },
    "searchParam": "keyword",
    "pageParam": "page",
    "pageSizeParam": "pageSize",
    "pageSize": 20,
    "resultPath": "data.items",
    "totalPath": "data.total",
    "idField": "employeeId",
    "labelField": "displayName",
    "extraFields": ["departmentName", "jobTitle"]
  },
  "multiple": false,
  "required": true,
  "default": "employee-1001"
}
```

<!-- example:E07 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "select",
  "question": "请选择审批人",
  "options": [],
  "dataSource": {
    "type": "api",
    "endpoint": "/api/oa/employees",
    "method": "GET",
    "params": { "status": "active" },
    "searchParam": "keyword",
    "pageParam": "page",
    "pageSizeParam": "pageSize",
    "pageSize": 20,
    "resultPath": "data.items",
    "totalPath": "data.total",
    "idField": "employeeId",
    "labelField": "displayName",
    "extraFields": ["departmentName", "jobTitle"]
  },
  "required": true,
  "default": "employee-1001"
}
```

### E08 POST 远程多选 treeSelect

场景：Skill 已确认组织树接口；资产授权需要选择一个或多个组织节点。

<!-- example:E08 kind:request -->
```json
{
  "question": "请选择授权组织",
  "inputType": "treeSelect",
  "dataSource": {
    "type": "api",
    "endpoint": "/api/oa/organizations/search",
    "method": "POST",
    "params": { "includeDisabled": false },
    "searchParam": "query",
    "pageParam": "pageIndex",
    "pageSizeParam": "limit",
    "pageSize": 50,
    "resultPath": "payload.nodes",
    "totalPath": "payload.total",
    "idField": "orgId",
    "labelField": "orgName",
    "childrenField": "children",
    "extraFields": ["orgCode", "managerName"]
  },
  "multiple": true,
  "required": true,
  "default": ["org-sales-east"]
}
```

<!-- example:E08 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "multiple",
  "question": "请选择授权组织",
  "options": [],
  "dataSource": {
    "type": "api",
    "endpoint": "/api/oa/organizations/search",
    "method": "POST",
    "params": { "includeDisabled": false },
    "searchParam": "query",
    "pageParam": "pageIndex",
    "pageSizeParam": "limit",
    "pageSize": 50,
    "resultPath": "payload.nodes",
    "totalPath": "payload.total",
    "idField": "orgId",
    "labelField": "orgName",
    "childrenField": "children",
    "extraFields": ["orgCode", "managerName"]
  },
  "inputType": "treeSelect",
  "required": true,
  "default": ["org-sales-east"]
}
```

### E09 可选字段被清空后的分组结果

场景：请假申请必须填写原因，交接说明可选。用户清空了预填的交接说明。

<!-- example:E09 kind:request -->
```json
{
  "title": "请假申请",
  "questions": [
    {
      "id": "reason",
      "question": "请假原因",
      "inputType": "textarea",
      "required": true,
      "default": "家庭事务"
    },
    {
      "id": "handover_note",
      "question": "工作交接说明",
      "inputType": "text",
      "required": false,
      "default": "无需交接"
    }
  ]
}
```

<!-- example:E09 kind:card -->
```json
{
  "batch": true,
  "title": "请假申请",
  "questions": [
    {
      "id": "reason",
      "kind": "text",
      "question": "请假原因",
      "fieldAssist": true,
      "inputType": "textarea",
      "required": true,
      "default": "家庭事务"
    },
    {
      "id": "handover_note",
      "kind": "text",
      "question": "工作交接说明",
      "fieldAssist": false,
      "default": "无需交接"
    }
  ]
}
```

<!-- example:E09 kind:result -->
```json
{
  "status": "answered",
  "formId": "leave-application-form-call",
  "answer": {
    "reason": "家庭事务",
    "handover_note": ""
  }
}
```

分组表单按 `questions[].id` 映射答案。可选字段若未提交可不出现在 answer 中；
若显式清空文本或多选，则分别返回空字符串或空数组。

### E10 第二份待确认表单

场景：同一 Assistant Turn 中，用户还提交了一份费用报销申请。

<!-- example:E10 kind:request -->
```json
{
  "title": "费用报销申请",
  "questions": [
    {
      "id": "amount",
      "question": "报销金额",
      "inputType": "text",
      "required": true,
      "default": "1280.00"
    },
    {
      "id": "category",
      "question": "费用类别",
      "inputType": "select",
      "options": [
        { "id": "travel", "label": "差旅费" },
        { "id": "office", "label": "办公费" }
      ],
      "multiple": false,
      "required": true,
      "default": "travel"
    }
  ]
}
```

<!-- example:E10 kind:card -->
```json
{
  "batch": true,
  "title": "费用报销申请",
  "questions": [
    {
      "id": "amount",
      "kind": "text",
      "question": "报销金额",
      "fieldAssist": false,
      "required": true,
      "default": "1280.00"
    },
    {
      "id": "category",
      "kind": "select",
      "question": "费用类别",
      "options": [
        { "id": "travel", "label": "差旅费" },
        { "id": "office", "label": "办公费" }
      ],
      "required": true,
      "default": "travel"
    }
  ]
}
```

<!-- example:E10 kind:result -->
```json
{
  "status": "answered",
  "formId": "expense-application-form-call",
  "answer": {
    "amount": "1280.00",
    "category": "travel"
  }
}
```

### E11 确认一份表单、返回修改并重新确认

E09 已返回具体 `formId` `leave-application-form-call`。该值只代表本段完整示例；
生成出的 Skill 必须使用当前 Assistant Turn 中工具实际返回的 `formId`。

<!-- example:E11 kind:confirmation-request -->
```json
{
  "confirm": true,
  "formIds": ["leave-application-form-call"]
}
```

<!-- example:E11 kind:confirmation-card -->
```json
{
  "batch": false,
  "kind": "confirm",
  "id": "confirmation",
  "title": "请假申请确认",
  "confirmationOfToolCallId": "leave-application-form-call",
  "questions": [
    {
      "id": "reason",
      "kind": "text",
      "question": "请假原因",
      "fieldAssist": true,
      "inputType": "textarea",
      "required": true,
      "default": "家庭事务"
    },
    {
      "id": "handover_note",
      "kind": "text",
      "question": "工作交接说明",
      "fieldAssist": false,
      "default": "无需交接"
    }
  ],
  "answer": {
    "reason": "家庭事务",
    "handover_note": ""
  },
  "forms": [
    {
      "formId": "leave-application-form-call",
      "title": "请假申请",
      "questions": [
        {
          "id": "reason",
          "kind": "text",
          "question": "请假原因",
          "fieldAssist": true,
          "inputType": "textarea",
          "required": true,
          "default": "家庭事务"
        },
        {
          "id": "handover_note",
          "kind": "text",
          "question": "工作交接说明",
          "fieldAssist": false,
          "default": "无需交接"
        }
      ],
      "answer": {
        "reason": "家庭事务",
        "handover_note": ""
      }
    }
  ]
}
```

用户选择“返回修改”后，Dano 重新展示可编辑 Form Revision。用户保存的新答案：

<!-- example:E11 kind:revision-answer -->
```json
{
  "reason": "陪同家人就医",
  "handover_note": "项目事项已交接给李四"
}
```

再次确认时必须使用最新保存答案：

<!-- example:E11 kind:revision-card -->
```json
{
  "batch": false,
  "kind": "confirm",
  "id": "confirmation",
  "title": "请假申请确认",
  "confirmationOfToolCallId": "leave-application-form-call",
  "questions": [
    {
      "id": "reason",
      "kind": "text",
      "question": "请假原因",
      "fieldAssist": true,
      "inputType": "textarea",
      "required": true,
      "default": "家庭事务"
    },
    {
      "id": "handover_note",
      "kind": "text",
      "question": "工作交接说明",
      "fieldAssist": false,
      "default": "无需交接"
    }
  ],
  "answer": {
    "reason": "陪同家人就医",
    "handover_note": "项目事项已交接给李四"
  },
  "forms": [
    {
      "formId": "leave-application-form-call",
      "title": "请假申请",
      "questions": [
        {
          "id": "reason",
          "kind": "text",
          "question": "请假原因",
          "fieldAssist": true,
          "inputType": "textarea",
          "required": true,
          "default": "家庭事务"
        },
        {
          "id": "handover_note",
          "kind": "text",
          "question": "工作交接说明",
          "fieldAssist": false,
          "default": "无需交接"
        }
      ],
      "answer": {
        "reason": "陪同家人就医",
        "handover_note": "项目事项已交接给李四"
      }
    }
  ]
}
```

<!-- example:E11 kind:result -->
```json
{
  "status": "confirmed",
  "answer": {
    "reason": "陪同家人就医",
    "handover_note": "项目事项已交接给李四"
  },
  "confirmationOfToolCallId": "leave-application-form-call",
  "forms": [
    {
      "formId": "leave-application-form-call",
      "answer": {
        "reason": "陪同家人就医",
        "handover_note": "项目事项已交接给李四"
      }
    }
  ]
}
```

`forms[]` 是最终 authoritative 答案。确认后使用它继续业务流程，不要恢复旧答案。

### E12 一次确认多份表单

E09 和 E10 均在同一 Assistant Turn 中已提交。

<!-- example:E12 kind:confirmation-request -->
```json
{
  "confirm": true,
  "formIds": [
    "leave-application-form-call",
    "expense-application-form-call"
  ]
}
```

<!-- example:E12 kind:confirmation-card -->
```json
{
  "batch": false,
  "kind": "confirm",
  "id": "confirmation",
  "title": "确认 2 份表单",
  "confirmationOfToolCallId": "leave-application-form-call",
  "questions": [
    {
      "id": "reason",
      "kind": "text",
      "question": "请假原因",
      "fieldAssist": true,
      "inputType": "textarea",
      "required": true,
      "default": "家庭事务"
    },
    {
      "id": "handover_note",
      "kind": "text",
      "question": "工作交接说明",
      "fieldAssist": false,
      "default": "无需交接"
    }
  ],
  "answer": {
    "reason": "家庭事务",
    "handover_note": ""
  },
  "forms": [
    {
      "formId": "leave-application-form-call",
      "title": "请假申请",
      "questions": [
        {
          "id": "reason",
          "kind": "text",
          "question": "请假原因",
          "fieldAssist": true,
          "inputType": "textarea",
          "required": true,
          "default": "家庭事务"
        },
        {
          "id": "handover_note",
          "kind": "text",
          "question": "工作交接说明",
          "fieldAssist": false,
          "default": "无需交接"
        }
      ],
      "answer": {
        "reason": "家庭事务",
        "handover_note": ""
      }
    },
    {
      "formId": "expense-application-form-call",
      "title": "费用报销申请",
      "questions": [
        {
          "id": "amount",
          "kind": "text",
          "question": "报销金额",
          "fieldAssist": false,
          "required": true,
          "default": "1280.00"
        },
        {
          "id": "category",
          "kind": "select",
          "question": "费用类别",
          "options": [
            { "id": "travel", "label": "差旅费" },
            { "id": "office", "label": "办公费" }
          ],
          "required": true,
          "default": "travel"
        }
      ],
      "answer": {
        "amount": "1280.00",
        "category": "travel"
      }
    }
  ]
}
```

<!-- example:E12 kind:result -->
```json
{
  "status": "confirmed",
  "answer": {
    "reason": "家庭事务",
    "handover_note": ""
  },
  "confirmationOfToolCallId": "leave-application-form-call",
  "forms": [
    {
      "formId": "leave-application-form-call",
      "answer": {
        "reason": "家庭事务",
        "handover_note": ""
      }
    },
    {
      "formId": "expense-application-form-call",
      "answer": {
        "amount": "1280.00",
        "category": "travel"
      }
    }
  ]
}
```

### E13 一次修正全部参数问题

下面的失败调用漏掉两个分组字段 ID。它只用于演示失败处理，不可作为调用模板。

<!-- example:E13 kind:invalid-request -->
```json
{
  "title": "出差申请",
  "questions": [
    { "question": "目的地", "default": "上海" },
    { "question": "出差事由", "default": "客户拜访" }
  ]
}
```

<!-- example:E13 kind:failure -->
```json
{
  "status": "invalid",
  "error": {
    "code": "invalid_question_arguments",
    "category": "validation",
    "message": "Question fields contain invalid arguments.",
    "retryable": true,
    "issues": [
      {
        "code": "missing_question_id",
        "path": "questions[0].id",
        "message": "Grouped question field id is required."
      },
      {
        "code": "missing_question_id",
        "path": "questions[1].id",
        "message": "Grouped question field id is required."
      }
    ]
  }
}
```

只有 `retryable:true` 才允许一次 replacement call；必须读取并一次修正所有
`issues[].path`：

<!-- example:E13 kind:request -->
```json
{
  "title": "出差申请",
  "questions": [
    { "id": "destination", "question": "目的地", "default": "上海" },
    { "id": "reason", "question": "出差事由", "default": "客户拜访" }
  ]
}
```

<!-- example:E13 kind:card -->
```json
{
  "batch": true,
  "title": "出差申请",
  "questions": [
    {
      "id": "destination",
      "kind": "text",
      "question": "目的地",
      "fieldAssist": false,
      "default": "上海"
    },
    {
      "id": "reason",
      "kind": "text",
      "question": "出差事由",
      "fieldAssist": false,
      "default": "客户拜访"
    }
  ]
}
```

### E14 展示超时后重试同一 canonical 调用

<!-- example:E14 kind:failure -->
```json
{
  "status": "invalid",
  "error": {
    "code": "question_presentation_timeout",
    "category": "lifecycle",
    "message": "The accepted question card was not presented in time.",
    "retryable": true,
    "issues": [
      {
        "code": "presentation_timeout",
        "message": "Retry with one corrected native ask_user_question call."
      }
    ],
    "terminalCode": "QUESTION_PRESENTATION_TIMEOUT"
  }
}
```

此时允许一次 replacement call；参数保持 canonical：

<!-- example:E14 kind:request -->
```json
{
  "question": "是否保存当前草稿？",
  "inputType": "radio",
  "options": [
    { "id": "save", "label": "保存" },
    { "id": "discard", "label": "不保存" }
  ],
  "required": true,
  "default": "save"
}
```

<!-- example:E14 kind:card -->
```json
{
  "batch": false,
  "id": "answer",
  "kind": "single",
  "question": "是否保存当前草稿？",
  "options": [
    { "id": "save", "label": "保存" },
    { "id": "discard", "label": "不保存" }
  ],
  "required": true,
  "default": "save"
}
```

### E15-E17 终止与取消

展示重试耗尽后停止：

<!-- example:E15 kind:failure -->
```json
{
  "status": "invalid",
  "error": {
    "code": "question_presentation_failed",
    "category": "lifecycle",
    "message": "Dano could not display the question card after bounded retries.",
    "retryable": false,
    "issues": [
      {
        "code": "presentation_failed",
        "message": "Stop this response and let the user retry."
      }
    ],
    "terminalCode": "QUESTION_PRESENTATION_FAILED"
  }
}
```

参数校验重试耗尽后停止：

<!-- example:E16 kind:failure -->
```json
{
  "status": "invalid",
  "error": {
    "code": "question_validation_failed",
    "category": "lifecycle",
    "message": "Repeated invalid ask_user_question calls exhausted automatic retries.",
    "retryable": false,
    "issues": [
      {
        "code": "validation_retry_exhausted",
        "message": "Stop this response and let the user retry."
      },
      {
        "code": "missing_question_text",
        "path": "question",
        "message": "Question text is required."
      }
    ],
    "sourceCode": "invalid_question_arguments",
    "terminalCode": "QUESTION_VALIDATION_FAILED"
  }
}
```

用户主动取消卡片会返回成功结果 `cancelled`，当前流程立即停止：

<!-- example:E17 kind:result -->
```json
{
  "status": "cancelled"
}
```

如果 Assistant Turn 被中止，则模型收到不可重试的结构化失败，同样停止：

<!-- example:E17 kind:failure -->
```json
{
  "status": "invalid",
  "error": {
    "code": "question_cancelled",
    "category": "lifecycle",
    "message": "The question flow was cancelled.",
    "retryable": false,
    "issues": [
      {
        "code": "cancelled",
        "message": "Question was aborted or the coordinator was disposed."
      }
    ],
    "terminalCode": "ASK_USER_QUESTION_CANCELLED"
  }
}
```

## Result JSON Schema

<!-- schema:result -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "answer": {
      "anyOf": [
        { "type": "string" },
        { "type": "number" },
        {
          "type": "array",
          "items": {
            "anyOf": [{ "type": "string" }, { "type": "number" }]
          }
        },
        { "type": "boolean" }
      ]
    },
    "answerObject": {
      "type": "object",
      "additionalProperties": { "$ref": "#/$defs/answer" }
    },
    "confirmedForm": {
      "type": "object",
      "properties": {
        "formId": { "type": "string" },
        "answer": { "$ref": "#/$defs/answerObject" }
      },
      "required": ["formId", "answer"]
    },
    "errorIssue": {
      "type": "object",
      "properties": {
        "code": {
          "enum": [
            "invalid_request_shape",
            "invalid_questions_json",
            "invalid_questions_shape",
            "invalid_question_item",
            "conflicting_aliases",
            "missing_question_id",
            "duplicate_question_id",
            "missing_question_text",
            "invalid_input_type",
            "invalid_options",
            "duplicate_option_id",
            "missing_choice_source",
            "invalid_default",
            "invalid_date_format",
            "invalid_data_source",
            "invalid_confirmation_target",
            "duplicate_tool_call",
            "presentation_timeout",
            "presentation_failed",
            "validation_retry_exhausted",
            "cancelled"
          ]
        },
        "path": { "type": "string" },
        "message": { "type": "string" }
      },
      "required": ["code", "message"]
    },
    "error": {
      "type": "object",
      "properties": {
        "code": {
          "enum": [
            "invalid_question_arguments",
            "invalid_confirmation_source",
            "duplicate_question_call",
            "question_presentation_timeout",
            "question_presentation_failed",
            "question_validation_failed",
            "question_cancelled"
          ]
        },
        "category": {
          "enum": ["validation", "confirmation", "duplicate_call", "lifecycle"]
        },
        "message": { "type": "string" },
        "retryable": { "type": "boolean" },
        "issues": {
          "type": "array",
          "minItems": 1,
          "items": { "$ref": "#/$defs/errorIssue" }
        },
        "sourceCode": {
          "enum": [
            "invalid_question_arguments",
            "invalid_confirmation_source",
            "duplicate_question_call",
            "question_presentation_timeout",
            "question_presentation_failed",
            "question_validation_failed",
            "question_cancelled"
          ]
        },
        "terminalCode": {
          "enum": [
            "QUESTION_PRESENTATION_TIMEOUT",
            "QUESTION_PRESENTATION_FAILED",
            "QUESTION_VALIDATION_FAILED",
            "ASK_USER_QUESTION_CANCELLED"
          ]
        },
        "context": {
          "type": "object",
          "properties": {
            "receivedShape": {
              "type": "object",
              "properties": {
                "formIds": { "type": "string" },
                "formId": { "type": "string" }
              },
              "required": ["formIds", "formId"]
            },
            "ignoredReasons": {
              "type": "array",
              "items": { "type": "string" }
            },
            "fallbackAttempted": { "type": "boolean" }
          }
        }
      },
      "required": ["code", "category", "message", "retryable", "issues"]
    }
  },
  "anyOf": [
    {
      "type": "object",
      "properties": {
        "status": { "const": "answered" },
        "formId": { "type": "string" },
        "answer": {
          "anyOf": [
            { "$ref": "#/$defs/answer" },
            { "$ref": "#/$defs/answerObject" }
          ]
        }
      },
      "required": ["status", "answer"]
    },
    {
      "type": "object",
      "properties": {
        "status": { "const": "confirmed" },
        "answer": { "$ref": "#/$defs/answerObject" },
        "confirmationOfToolCallId": { "type": "string" },
        "forms": {
          "type": "array",
          "items": { "$ref": "#/$defs/confirmedForm" }
        }
      },
      "required": ["status", "answer", "confirmationOfToolCallId", "forms"]
    },
    {
      "type": "object",
      "properties": { "status": { "const": "cancelled" } },
      "required": ["status"]
    },
    {
      "type": "object",
      "properties": {
        "status": { "const": "invalid" },
        "error": { "$ref": "#/$defs/error" }
      },
      "required": ["status", "error"]
    }
  ]
}
```

## 失败处理规则

| `error.code` | `retryable` | 处理 |
| --- | --- | --- |
| `invalid_question_arguments` | `true` | 读取全部 `issues[].path`，一次修正全部问题后替换调用。 |
| `invalid_confirmation_source` | `true` | 先取得同一 Assistant Turn 中已提交分组表单的真实 `formId`。 |
| `duplicate_question_call` | `true` | 合并为一次 `questions[]` 调用。 |
| `question_presentation_timeout` | `true` | 最多重试一次相同的 canonical 调用。 |
| `question_presentation_failed` | `false` | 停止当前响应。 |
| `question_validation_failed` | `false` | 校验重试已耗尽，停止当前响应。 |
| `question_cancelled` | `false` | 停止当前流程，等待用户的新消息。 |

不要自动重试 `retryable:false`，也不要在用户取消后换一种方式继续提问。

## 能力覆盖矩阵

“预期”列对应示例中经过自动验证的 canonical Card Request 或 Result。

| 能力 ID | 能力 | 示例 | 预期 |
| --- | --- | --- | --- |
| `call.single` | 顶层单问题 | [E01](#e01-单行文本与显式-field-assist) | `batch:false` Card Request |
| `call.grouped` | `title + questions[]` 分组表单 | [E03](#e03-一次填写完整分组表单) | `batch:true` Card Request |
| `call.confirmation` | `confirm:true + formIds[]` | [E11](#e11-确认一份表单返回修改并重新确认) | Confirmation Card Request |
| `text.single-line` | `text` | [E01](#e01-单行文本与显式-field-assist) | `kind:text` |
| `text.textarea` | `textarea` | [E02](#e02-多行文本与显式关闭-field-assist), [E03](#e03-一次填写完整分组表单) | `inputType:textarea` |
| `field-assist.default-off` | 单行默认关闭 | [E03](#e03-一次填写完整分组表单) | `fieldAssist:false` |
| `field-assist.default-on` | textarea 默认开启 | [E03](#e03-一次填写完整分组表单) | `fieldAssist:true` |
| `field-assist.explicit-on` | 显式开启 | [E01](#e01-单行文本与显式-field-assist) | `fieldAssist:true` |
| `field-assist.explicit-off` | 显式关闭 | [E02](#e02-多行文本与显式关闭-field-assist) | `fieldAssist:false` |
| `date.date-only` | 仅日期 | [E03](#e03-一次填写完整分组表单) | `dateFormat:yyyy-MM-dd` |
| `date.date-time-minute` | 日期时间到分钟 | [E03](#e03-一次填写完整分组表单) | `dateFormat:yyyy-MM-dd HH:mm` |
| `date.default-format` | default 匹配 dateFormat | [E03](#e03-一次填写完整分组表单) | 两个 date Card items |
| `date.result-string` | 日期提交值保持字符串 | [E03](#e03-一次填写完整分组表单) | grouped result strings |
| `choice.radio` | radio 单选 | [E04](#e04-普通业务确认使用-radio) | `kind:single` |
| `choice.checkbox` | checkbox 多选 | [E06](#e06-多选中的一个自定义回答) | `kind:multiple` |
| `choice.select` | select | [E07](#e07-get-远程-select), [E10](#e10-第二份待确认表单) | `kind:select` |
| `choice.tree-select` | treeSelect | [E08](#e08-post-远程多选-treeselect) | multiple tree Card Request |
| `choice.stable-option` | 稳定 id/label option | [E04](#e04-普通业务确认使用-radio) | canonical options |
| `choice.default-id` | default 使用 option ID | [E04](#e04-普通业务确认使用-radio) | `default:save_draft` |
| `custom.single` | 单个自定义“其他”回答 | [E05](#e05-单选中的自定义其他) | custom scalar result |
| `custom.multiple` | 多选最多一个自定义回答 | [E06](#e06-多选中的一个自定义回答) | ID 与 custom string 数组 |
| `data-source.get` | GET | [E07](#e07-get-远程-select) | GET dataSource projection |
| `data-source.post` | POST | [E08](#e08-post-远程多选-treeselect) | POST dataSource projection |
| `data-source.params` | 固定 params | [E07](#e07-get-远程-select), [E08](#e08-post-远程多选-treeselect) | params retained |
| `data-source.search` | 搜索参数 | [E07](#e07-get-远程-select) | `searchParam` retained |
| `data-source.pagination` | 分页参数与 page size | [E07](#e07-get-远程-select) | page fields retained |
| `data-source.result-path` | 结果路径 | [E07](#e07-get-远程-select) | `resultPath` retained |
| `data-source.total-path` | 总数路径 | [E07](#e07-get-远程-select) | `totalPath` retained |
| `data-source.id-label` | ID/label 字段映射 | [E07](#e07-get-远程-select) | mapping retained |
| `data-source.children` | children 字段映射 | [E08](#e08-post-远程多选-treeselect) | `childrenField` retained |
| `data-source.extra-fields` | extraFields | [E07](#e07-get-远程-select), [E08](#e08-post-远程多选-treeselect) | extra fields retained |
| `selection.single` | `multiple:false` | [E07](#e07-get-远程-select) | scalar default/result |
| `selection.multiple` | `multiple:true` | [E06](#e06-多选中的一个自定义回答), [E08](#e08-post-远程多选-treeselect) | `kind:multiple` |
| `selection.multiple-default` | 多选 default 数组 | [E06](#e06-多选中的一个自定义回答) | default ID array |
| `required.true` | 必填 | [E01](#e01-单行文本与显式-field-assist) | `required:true` |
| `required.false` | 可选 | [E02](#e02-多行文本与显式关闭-field-assist), [E09](#e09-可选字段被清空后的分组结果) | required omitted in Card |
| `default.text` | 文本 default | [E01](#e01-单行文本与显式-field-assist) | non-empty string |
| `default.date` | 日期 default | [E03](#e03-一次填写完整分组表单) | formatted strings |
| `default.single-choice` | 单选 default | [E04](#e04-普通业务确认使用-radio) | stable ID |
| `default.multiple-choice` | 多选 default | [E06](#e06-多选中的一个自定义回答) | non-empty ID array |
| `optional.cleared` | 可选字段清空 | [E09](#e09-可选字段被清空后的分组结果) | empty string result |
| `ownership.single-top-level` | 单问题配置在顶层 | [E01](#e01-单行文本与显式-field-assist) | single Card Request |
| `ownership.grouped-item` | 分组配置在 item 内 | [E03](#e03-一次填写完整分组表单) | grouped Card Request |
| `ownership.grouped-id-map` | 唯一 ID 映射答案 | [E03](#e03-一次填写完整分组表单) | object answer keys |
| `result.single` | 单问题 scalar answer | [E01](#e01-单行文本与显式-field-assist) | `status:answered` scalar |
| `result.grouped` | 分组 object answer | [E03](#e03-一次填写完整分组表单) | object answer |
| `result.form-id` | 分组 formId | [E09](#e09-可选字段被清空后的分组结果) | concrete `formId` |
| `result.multiple` | 多选 ID 数组 | [E06](#e06-多选中的一个自定义回答) | array result |
| `result.date` | 日期字符串 | [E03](#e03-一次填写完整分组表单) | date strings |
| `result.custom` | 自定义选择值 | [E05](#e05-单选中的自定义其他), [E06](#e06-多选中的一个自定义回答) | custom strings |
| `result.cancelled` | cancelled | [E17](#e15-e17-终止与取消) | `status:cancelled` |
| `result.invalid` | invalid | [E13](#e13-一次修正全部参数问题) | structured invalid result |
| `confirmation.single-form` | 确认一份表单 | [E11](#e11-确认一份表单返回修改并重新确认) | one confirmation form |
| `confirmation.multiple-forms` | 一次确认多份表单 | [E12](#e12-一次确认多份表单) | two confirmation forms |
| `confirmation.latest-answer` | 使用最新保存答案 | [E11](#e11-确认一份表单返回修改并重新确认) | revision Card answer |
| `confirmation.return-modify` | 返回修改 | [E11](#e11-确认一份表单返回修改并重新确认) | revision answer |
| `confirmation.reconfirm` | 修改后重新确认 | [E11](#e11-确认一份表单返回修改并重新确认) | revised confirmation Card |
| `confirmation.authoritative-forms` | authoritative forms[] | [E11](#e11-确认一份表单返回修改并重新确认), [E12](#e12-一次确认多份表单) | confirmed result forms |
| `failure.correct-all-paths` | 一次修正所有 issue paths | [E13](#e13-一次修正全部参数问题) | replacement Card Request |
| `failure.timeout-retry` | 展示超时后重试 | [E14](#e14-展示超时后重试同一-canonical-调用) | retryable failure + Card |
| `failure.presentation-stop` | 展示失败后停止 | [E15](#e15-e17-终止与取消) | non-retryable failure |
| `failure.validation-stop` | 校验重试耗尽后停止 | [E16](#e15-e17-终止与取消) | non-retryable failure |
| `failure.cancel-stop` | 取消后停止 | [E17](#e15-e17-终止与取消) | cancelled/terminal failure |
