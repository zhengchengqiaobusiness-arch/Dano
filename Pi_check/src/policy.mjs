/**
 * PI 是唯一语义决策者；旧录制逻辑绝不启动。
 *
 * 本文件是不可绕过的 PI-only 硬保护。任何语义判断都必须来自 PI。
 * 本目录禁止启动、导入或回退到工作目录之外的旧录制分析链。
 */

export const PI_ONLY_NOTICE = "PI 是唯一语义决策者；旧录制逻辑绝不启动。";

export function logPiOnly(message) {
  process.stdout.write(`[PI-only] ${message}\n`);
}

export const PI_ONLY_POLICY = Object.freeze({
  semanticAuthority: "pi",
  legacyRecordingMustNeverStart: true,
  codeMayInferCapabilities: false,
  codeMayInferFields: false,
  codeMayInferDependencies: false,
  codeMayRepairPiOutput: false,
  codeMayCreateFallbackOutput: false,
  codeMayStartWithoutPi: false,
  piFailureProducesResult: false,
  secondResultPathExists: false,
});

export class PiRequiredError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "PiRequiredError";
    this.code = "PI_REQUIRED";
  }
}

export class RecordingFailedError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "RecordingFailedError";
    this.code = "RECORDING_FAILED";
    this.publicMessage = "PI 未完成，本次录制失败，没有产出能力";
  }
}

export function assertPiOnlyPolicy() {
  if (PI_ONLY_POLICY.semanticAuthority !== "pi") {
    throw new Error("PI-only 策略被篡改：semanticAuthority 必须是 pi");
  }
  if (PI_ONLY_POLICY.legacyRecordingMustNeverStart !== true) {
    throw new Error("旧录制逻辑绝不启动 标志被篡改");
  }
  const mustStayFalse = [
    "codeMayInferCapabilities",
    "codeMayInferFields",
    "codeMayInferDependencies",
    "codeMayRepairPiOutput",
    "codeMayCreateFallbackOutput",
    "codeMayStartWithoutPi",
    "piFailureProducesResult",
    "secondResultPathExists",
  ];
  for (const key of mustStayFalse) {
    if (PI_ONLY_POLICY[key] !== false) {
      throw new Error(`PI-only 策略被篡改：${key} 必须为 false`);
    }
  }
}

export function assertNeverStartLegacy() {
  assertPiOnlyPolicy();
  if (PI_ONLY_POLICY.legacyRecordingMustNeverStart !== true) {
    throw new Error("旧录制逻辑绝不启动");
  }
}

export function publicFailureMessage() {
  return "PI 未完成，本次录制失败，没有产出能力";
}
