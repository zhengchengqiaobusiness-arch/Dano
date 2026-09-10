import assert from "node:assert/strict";
import test from "node:test";
import { describeExportFailure } from "./exportError.ts";

test("导出失败展示请求地址和网关 detail", () => {
  const lines = describeExportFailure({
    config: { method: "post", url: "/v1/pi-recordings/rec_1/export-skill" },
    response: { status: 400, data: { detail: "无效的录制结果 ID" } },
  });
  assert.equal(lines[0], "请求 POST /v1/pi-recordings/rec_1/export-skill");
  assert.equal(lines[1], "HTTP 400");
  assert.ok(lines.includes("无效的录制结果 ID"));
});

test("导出失败没有响应时展示本地错误", () => {
  const lines = describeExportFailure({ message: "Network Error" });
  assert.ok(lines.includes("Network Error"));
});
