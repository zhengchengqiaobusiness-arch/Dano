---
name: onboard-page
description: 接入一个无 API 的页面型系统:侦察表单、生成页面脚本、沙箱回放、过硬关卡发布。只用测试账号,绝不碰生产写。
---

# 接入页面型系统(阶段一 · 流程8 · 无 API)

你是 Dano 的资产工厂。把一个**没有开放接口**的系统页面(报销页、老 OA 页等)转成
**已验证、可发布**的页面脚本资产。定位只用语义(role/label/placeholder/text),**绝不用坐标**。

## 流程(对每个页面流程走一遍)
1. `scout_page(system_instance_id, start_url)` → 拿候选字段 / 提交按钮 / 结构指纹 / `suggested_steps`。
2. 决定字段映射与成功标志(看 fields 的 label/name 判断每个输入对应什么业务字段;看页面判断提交成功的标志元素/文本)。
   - `suggested_steps` 是确定性兜底,可直接用;需要时改 `field`(绑定的业务字段名)、删无关步、补 `success_marker`。
3. `draft_page_script(system_instance_id, action, steps, dom_fingerprint, start_url, success_marker, title)`
   → 拿 `asset_draft_id`、`risk_level`、`needs_review`(Python 已确定性建好声明式脚本;含提交步=写页面 L3)。
4. `sandbox_replay(asset_draft_id, sample_inputs)` → 拿 `validation_run_ids` 与 `passed`。
   - 写页面默认 **dry 回放**(填字段 + 断言提交按钮在位,不真点提交),`mode="dry"`;这是正常的。
5. 若 `needs_review` 为真(写页面)→ `request_review(asset_draft_id)` 跑三模型评审,拿 `review_run_ids` 与 `all_passed`;
   查询页面跳过此步(`review_run_ids` 传空)。
6. 若回放通过(且写页面三审通过)→ `publish_asset(asset_draft_id, validation_run_ids, review_run_ids)` 发布;
   否则按 reasons / verdicts 修正后重测重审;实在过不了就跳过并记原因。
7. 一句话总结发布了哪些页面 Skill、跳过了哪些。

## 纪律(红线)
- **只用测试账号**:回放只走 `sandbox_replay`(它强制 sandbox/test 证据),绝不在生产页面真提交。
- **DOM 成功 ≠ 业务成功**:页面"提交成功"提示不等于业务真生效;故写页面默认 L3(运行期提交前必确认),
  且发布只给 weak 级保证 —— 别声称"已确认生效"。
- **不自报通过**:发布只能传 `sandbox_replay` 的 `validation_run_ids` + `request_review` 的 `review_run_ids`,
  不要自己声称"已验证/已评审"(后端会重读校验,自报无效)。
- **指纹即基线**:`draft_page_script` 用 `scout_page` 返回的 `dom_fingerprint`,别自己编 —— 它是运行期检测页面改版漂移的基线。
- **失败不硬上**:某页面回放或评审不过,先按 reasons 修正重试;仍不过就跳过,继续其余,最后如实汇报。
