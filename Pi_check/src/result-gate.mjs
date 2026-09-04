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
