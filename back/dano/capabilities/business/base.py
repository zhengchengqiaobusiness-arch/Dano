"""业务流程库公共件。

RuoYi-Flowable 所有 BPMN 业务(请假/出差/报销…)共享同一条**执行契约**:
  startFlow(templateId) → biz/form/info 取动态表单 → biz/form/save(双层 {formData, valData})得 businessId
  → biz/flow/submit(operateType=200, flowTask{...businessId})
成败不看接口字面「操作成功」(RuoYi 对任何输入都回 200),以**事实核查**为准(回查实例真流转)。

各业务**只在字段、模板、风险上区分**(见各自模块),不重复写执行机制——这才是"业务区分开"的正确粒度:
分的是业务语义,不是把同一套 3 步契约抄 N 遍。
"""

from __future__ import annotations

# RuoYi 统一成败规则:有 code 必须 200,无 code(列表类)靠 HTTP 2xx。
RUOYI_SUCCESS_RULE = "response.code == null or response.code == 200"
