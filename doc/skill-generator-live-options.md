# 活选项：OA Skill 生成指南

本文档供负责核对消费者 Skill 的模型阅读。合同里声明了 `option_source` 的调用方字段，运行时必须打真正的选项接口，不得把录制样本冻成选项。

## INPUT_FORMS 必须保留 dataSource

合同字段若来自选项接口，`references/INPUT_FORMS.md` **必须保留完整 `dataSource`**：

- endpoint
- method
- params
- resultPath
- idField
- labelField
- 合同已声明的 `childrenField` 及其它映射

这些由运输层从录制合同投影。禁止重写 INPUT_FORMS 把 dataSource 删掉。

禁止：

- 「把 options 填进 question 后删除 dataSource」
- 「拉完候选再删 dataSource」
- 把录制当时看到的几条写成静态 `options` 并宣称已解决

用户看见 label，接口接收稳定 id 或合同声明的值。树形结果必须按 `childrenField` 展平后再提问。

## 预取与刷新

助手必须先用本包鉴权拉候选，再提问。不要让问句自己裸打 dataSource。

```text
python scripts/flow.py --list-options <capability_id> <field>
```

选项接口和业务接口走同一套 `auth.local.json` / `DANO_AUTH_HEADERS`。401 按鉴权规范停问。

## 失败即停

选项接口失败、空列表、或无法映射到稳定 id：

- 停问用户
- 不得默默取第一条
- 不得用录制样本冒充实时选项
- 不得把未识别来源写成可执行默认

`references/OPTIONS.md` 只写「何时打哪条 option_source、如何映射、失败怎么停」，不要贴真实 token，不要写死一批录制 label。
