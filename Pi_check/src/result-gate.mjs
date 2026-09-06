/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 唯一最终提交入口。只做会话/编号/冻结/非空对象检查。
 * 禁止语义校验、字段补齐、名称改写、类型纠正、依赖重建或二次编译。
 */

import { createHash } from "node:crypto";
import { PI_ONLY_NOTICE, assertNeverStartLegacy } from "./policy.mjs";
import { piSubmittedCapabilities } from "./capability-presence.mjs";

export const SUBMIT_RECORDING_RESULT = "submit_recording_result";

export class SubmitRejectedError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "SubmitRejectedError";
    this.code = code;
  }
}

function isNonEmptyPlainObject(value) {
  return Boolean(
    value
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).length > 0,
  );
}

function isPlainObject(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

const SELECTABLE_HINT_RE = /部门树|树选择|树选|下拉|选择器|选择节点|选项接口|实时候选|tree\s*select|treeselect/i;
const ROW_ARRAY_HINT_RE = /可增行|添加.{0,12}(行|项|明细)|点[「"]添加|组装成.{0,12}数组/;
const SYSTEM_ROW_KEYS = new Set([
  "itemtype", "sort", "index", "seq", "order",
  "xrowkey", "x_row_key", "rowkey", "row_key",
]);

function optionEndpoint(node) {
  const source = isPlainObject(node)
    ? (
      isPlainObject(node["x-dano-option-source"]) ? node["x-dano-option-source"]
        : isPlainObject(node["x-options-source-meta"]) ? node["x-options-source-meta"]
          : isPlainObject(node.dataSource) ? node.dataSource
            : isPlainObject(node.option_source) ? node.option_source
              : node
    )
    : {};
  return String(source.source_url || source.endpoint || source.url || "").trim();
}

function hasWrittenOptionContract(node, param) {
  if (isPlainObject(node)) {
    if (Array.isArray(node.enum) && node.enum.length) return true;
    if (Array.isArray(node["x-enum-options"]) && node["x-enum-options"].length) return true;
    if (Array.isArray(node["x-options"]) && node["x-options"].length) return true;
    if (isPlainObject(node["x-enum-value-map"]) && Object.keys(node["x-enum-value-map"]).length) return true;
    if (optionEndpoint(node)) return true;
  }
  if (!isPlainObject(param)) return false;
  if (Array.isArray(param.enum_options) && param.enum_options.length) return true;
  if (isPlainObject(param.enum_value_map) && Object.keys(param.enum_value_map).length) return true;
  const source = isPlainObject(param.source) ? param.source : {};
  return Boolean(optionEndpoint(source) || optionEndpoint(source.option_source));
}

function objectArrayProperties(node) {
  const items = isPlainObject(node?.items) ? node.items : {};
  return isPlainObject(items.properties) ? items.properties : null;
}

function looksCollapsedRowArray(node, param) {
  const type = String(node?.type || param?.type || "").toLowerCase();
  if (type === "array" && objectArrayProperties(node)) return false;
  const text = [
    node?.description,
    node?.title,
    node?.label,
    param?.reason,
    param?.description,
    param?.label,
  ].map((item) => String(item || "")).join(" ");
  return ROW_ARRAY_HINT_RE.test(text);
}

function isSystemRowKey(key) {
  const leaf = String(key || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  return SYSTEM_ROW_KEYS.has(leaf) || leaf.endsWith("rowkey");
}

function fieldLooksSelectable(node, param) {
  const type = String(node?.type || param?.type || "").toLowerCase();
  const format = String(node?.format || "");
  if (type === "date" || type === "datetime" || format === "date" || format === "date-time") {
    return false;
  }
  const text = [
    node?.description,
    node?.title,
    node?.label,
    param?.reason,
    param?.description,
    param?.label,
  ].map((item) => String(item || "")).join(" ");
  return SELECTABLE_HINT_RE.test(text);
}

function findParamByKey(stepsById, stepIds, key) {
  for (const stepId of stepIds) {
    const step = stepsById.get(stepId);
    for (const param of Array.isArray(step?.params) ? step.params : []) {
      if (String(param?.key || "").trim() === key) return param;
    }
  }
  for (const step of stepsById.values()) {
    for (const param of Array.isArray(step?.params) ? step.params : []) {
      if (String(param?.key || "").trim() === key) return param;
    }
  }
  return null;
}

/**
 * 只检查现有录制页能读到的信封，不推断字段、不改写 result。
 * 拒收页面会画成“调用方提供 0”的私有结构。
 */
export function assertPageDisplayContract(result) {
  const capabilities = Array.isArray(result?.capabilities) ? result.capabilities : [];
  for (const capability of capabilities) {
    if (!isPlainObject(capability)) {
      throw new SubmitRejectedError("DISPLAY_CONTRACT", "每个 capability 必须是对象");
    }
    if (Array.isArray(capability.fields) && capability.fields.length) {
      throw new SubmitRejectedError(
        "DISPLAY_CONTRACT",
        "现有录制页不读 capabilities[].fields。调用方/系统字段必须写在 capability.input_schema.properties，以及关联 step 的 params 数组（每项含 key/path）",
      );
    }
    if (capability.request_refs != null) {
      if (!Array.isArray(capability.request_refs) || capability.request_refs.length === 0) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          "request_refs 必须是非空对象数组，每项含 step_id 和 usage，不能是 request_id 字符串",
        );
      }
      for (const ref of capability.request_refs) {
        if (typeof ref === "string" || !isPlainObject(ref) || !String(ref.step_id || "").trim()) {
          throw new SubmitRejectedError(
            "DISPLAY_CONTRACT",
            "request_refs 每项必须是 {step_id, usage} 对象，step_id 必须等于 steps[].step_id，不能填 request_id 字符串",
          );
        }
      }
    }
  }
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  const stepsById = new Map(
    steps
      .filter((step) => isPlainObject(step) && String(step.step_id || "").trim())
      .map((step) => [String(step.step_id).trim(), step]),
  );
  for (const step of steps) {
    if (!isPlainObject(step)) {
      throw new SubmitRejectedError("DISPLAY_CONTRACT", "每个 step 必须是对象");
    }
    if (step.params == null) continue;
    if (!Array.isArray(step.params)) {
      throw new SubmitRejectedError(
        "DISPLAY_CONTRACT",
        "steps[].params 必须是字段对象数组，不能是 {key: value} 映射",
      );
    }
    for (const param of step.params) {
      if (!isPlainObject(param) || !String(param.key || "").trim() || !String(param.path || "").trim()) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          "steps[].params 的每一项必须是对象，且同时包含 key 和 path",
        );
      }
    }
  }
  for (const capability of capabilities) {
    const properties = capability?.input_schema?.properties;
    if (!isPlainObject(properties)) continue;
    const callerKeys = new Set();
    const stepIds = [];
    if (Array.isArray(capability.request_refs)) {
      for (const ref of capability.request_refs) {
        const stepId = String(ref?.step_id || "").trim();
        if (stepId) stepIds.push(stepId);
      }
    }
    if (Array.isArray(capability.step_ids)) {
      for (const stepId of capability.step_ids) {
        const id = String(stepId || "").trim();
        if (id) stepIds.push(id);
      }
    }
    for (const stepId of stepIds) {
      const step = stepsById.get(stepId);
      for (const param of Array.isArray(step?.params) ? step.params : []) {
        if (param?.exposed_to_user === true && String(param.key || "").trim()) {
          callerKeys.add(String(param.key).trim());
        }
      }
    }
    for (const key of Object.keys(properties)) {
      if (callerKeys.size && !callerKeys.has(key)) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          `input_schema.properties.${key} 必须对应某个 exposed_to_user=true 的 param.key，不能编造 execute 请求里没有的键`,
        );
      }
      const node = properties[key];
      if (!isPlainObject(node)) continue;
      const param = findParamByKey(stepsById, stepIds, key);
      const sourceKind = String(
        node["x-dano-business-type"] || param?.source_kind || (isPlainObject(param?.source) ? param.source.kind : "") || "",
      );
      if (sourceKind === "api_option" && !hasWrittenOptionContract(node, param)) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          `input_schema.properties.${key} 是实时候选，必须写 x-dano-option-source.source_url，不能只写 type=number`,
        );
      }
      if (fieldLooksSelectable(node, param) && !hasWrittenOptionContract(node, param)) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          `input_schema.properties.${key} 是树/下拉选择字段，必须把选项合同写进 schema，不能只写 type=number 再把接口藏在说明里`,
        );
      }
      if (looksCollapsedRowArray(node, param)) {
        throw new SubmitRejectedError(
          "DISPLAY_CONTRACT",
          `input_schema.properties.${key} 是可增行，必须写成对象数组并给出 items.properties，不能收成一段字符串`,
        );
      }
      const itemProperties = objectArrayProperties(node);
      if (String(node.type || "").toLowerCase() === "array" && itemProperties) {
        const missingTitle = Object.entries(itemProperties).find(([itemKey, itemNode]) => {
          if (!isPlainObject(itemNode)) return true;
          return !String(itemNode.title || itemNode.label || "").trim();
        });
        if (missingTitle) {
          throw new SubmitRejectedError(
            "DISPLAY_CONTRACT",
            `input_schema.properties.${key}.items.properties.${missingTitle[0]} 必须有表头原文 title，不能只写键名`,
          );
        }
        const systemKey = Object.keys(itemProperties).find((itemKey) => isSystemRowKey(itemKey));
        if (systemKey) {
          throw new SubmitRejectedError(
            "DISPLAY_CONTRACT",
            `input_schema.properties.${key}.items.properties 不能包含行类型码/序号/行键 ${systemKey}，这些只留在系统 params`,
          );
        }
      }
    }
  }
}

/**
 * 只检查身份和编排信封：编号不重复、每个能力恰好一个 execute、execute 不共用。
 * 不判断“这场应该有几个能力”，不补能力。
 */
export function assertCapabilityIdentityContract(result) {
  const capabilities = Array.isArray(result?.capabilities) ? result.capabilities : [];
  const stepIds = new Set(
    (Array.isArray(result?.steps) ? result.steps : [])
      .map((step) => String(step?.step_id || "").trim())
      .filter(Boolean),
  );
  const seenIds = new Set();
  const executeOwners = new Set();
  for (const capability of capabilities) {
    if (!isPlainObject(capability)) {
      throw new SubmitRejectedError("IDENTITY_CONTRACT", "每个 capability 必须是对象");
    }
    const capabilityId = String(capability.capability_id || "").trim();
    if (capabilityId) {
      if (seenIds.has(capabilityId)) {
        throw new SubmitRejectedError(
          "IDENTITY_CONTRACT",
          "capability_id 不得重复。每个独立业务动作必须有自己的编号，不能把查询/撤回/删除写成同一个 id",
        );
      }
      seenIds.add(capabilityId);
    }
    const refs = capability.request_refs;
    if (refs == null) continue;
    if (!Array.isArray(refs) || refs.length === 0) {
      throw new SubmitRejectedError("IDENTITY_CONTRACT", "request_refs 必须是非空对象数组");
    }
    const executes = refs.filter((ref) => isPlainObject(ref) && (ref.usage || "execute") === "execute");
    if (executes.length !== 1) {
      throw new SubmitRejectedError(
        "IDENTITY_CONTRACT",
        "每个能力必须恰好一个 usage=execute 的 request_ref，不能 0 个也不能多个",
      );
    }
    const executeStepId = String(executes[0].step_id || "").trim();
    if (!executeStepId) {
      throw new SubmitRejectedError("IDENTITY_CONTRACT", "execute 的 step_id 不能为空");
    }
    if (executeOwners.has(executeStepId)) {
      throw new SubmitRejectedError(
        "IDENTITY_CONTRACT",
        "两个能力不能共用同一个 execute step_id。同一条接口服务新建和编辑时，必须拆成两个步骤",
      );
    }
    executeOwners.add(executeStepId);
    if (stepIds.size) {
      for (const ref of refs) {
        const stepId = String(ref?.step_id || "").trim();
        if (stepId && !stepIds.has(stepId)) {
          throw new SubmitRejectedError(
            "IDENTITY_CONTRACT",
            "request_refs.step_id 必须能在 steps 里找到，不能填 request_id 或不存在的步骤",
          );
        }
      }
    }
  }
}

export class ResultGate {
  constructor(files) {
    this.files = files;
    this.accepted = new Map();
  }

  /**
   * 仅接收 recording_id / final / result。
   * 系统回执单独落盘，不得写回 PI 的 result。
   */
  async submitRecordingResult({
    recordingId,
    expectedRecordingId,
    callerSessionId,
    expectedSessionId,
    final,
    result,
    frozen,
  }) {
    assertNeverStartLegacy();
    if (!expectedSessionId || callerSessionId !== expectedSessionId) {
      throw new SubmitRejectedError("PI_SESSION_MISMATCH", "请求不是来自当前 PI 会话");
    }
    if (!expectedRecordingId || recordingId !== expectedRecordingId) {
      throw new SubmitRejectedError("RECORDING_ID_MISMATCH", "PI 提交的录制编号不匹配");
    }
    if (final !== true) {
      throw new SubmitRejectedError("NOT_FINAL", "PI 提交非最终结果");
    }
    if (!isNonEmptyPlainObject(result)) {
      throw new SubmitRejectedError("EMPTY_RESULT", "PI 提交空结果");
    }
    if (!piSubmittedCapabilities(result)) {
      throw new SubmitRejectedError("EMPTY_CAPABILITIES", "PI 未提交任何能力，没有产出");
    }
    assertPageDisplayContract(result);
    assertCapabilityIdentityContract(result);
    if (!frozen) {
      throw new SubmitRejectedError("NOT_FROZEN", "证据尚未冻结时禁止提交最终结果");
    }
    if (this.accepted.has(expectedRecordingId) || await this.files.hasPiResult(expectedRecordingId)) {
      throw new SubmitRejectedError("ALREADY_ACCEPTED", "同一录制不得接收第二个最终结果");
    }

    const verbatim = structuredClone(result);
    await this.files.writePiResult(expectedRecordingId, verbatim);
    const stored = await this.files.readPiResult(expectedRecordingId);
    const resultSha256 = createHash("sha256").update(JSON.stringify(stored)).digest("hex");
    const receipt = {
      recording_id: expectedRecordingId,
      accepted_at: new Date().toISOString(),
      pi_session_id: expectedSessionId,
      status: "accepted",
      result_sha256: resultSha256,
      notice: PI_ONLY_NOTICE,
    };
    await this.files.writeReceipt(expectedRecordingId, receipt);
    this.accepted.set(expectedRecordingId, {
      recordingId: expectedRecordingId,
      piSessionId: expectedSessionId,
    });
    return { accepted: true, receipt };
  }
}
