# 动态选项：生成期规范

本文档只约束合同里 `option_source` / `dataSource` 如何进入成品表单并在运行期可调用。生成期必须阅读；成品 **不得** 携带本文档。

字段名、`dataSource` 的键、HTTP 方法保持英文原名。提问控件合同见 `skill-generator-ask-user-question-guide.md`。

## 必须保留 dataSource

调用方字段只要合同声明了选项接口（`option_source`、`x-dano-option-source`、`dataSource`），`references/INPUT_FORMS.md` 里对应 question **必须保留完整** `dataSource`：

- `type`: `api`
- `endpoint`
- `method`（`GET` 或 `POST`）
- `params`（合同声明的固定参数）
- `searchParam` / `pageParam` / `pageSizeParam` / `pageSize`（有则写）
- `resultPath` / `totalPath`
- `idField` / `labelField`
- `childrenField` / `extraFields`（有则写）

禁止：

- 把脚本返回的 `options` 填进 question 后删除 `dataSource`
- 写「从本次工具参数中移除该 question 的 dataSource」
- 用录制样本 options 冒充运行时候选
- 候选为空或多条且要求单条时默默取第一条

## 运行期怎么打这个接口

选项接口和业务接口走同一套鉴权（见 `skill-generator-auth-and-token.md`）。

- Agent：可先跑 `python scripts/<capability>.py --list-options <字段>`，把返回的 `options` **额外**放进 question；**保留** `dataSource`。
- 页面：按 `dataSource` + 当前 token（包内 `auth.local.json` 或 Dano 代理）刷新下拉。

接口失败：停问并说明，不得用历史样本顶上。

## OPTIONS.md

只说明运行时如何取候选、失败时停问、多结果不得默认第一条。不要贴本场人员/部门样例当默认值。
