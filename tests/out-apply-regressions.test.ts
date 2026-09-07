import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
import { BrowserRecorder } from "../src/browser/recorder.js";

async function withRecorder(html: string, run: (recorder: BrowserRecorder) => Promise<void>) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "out-apply-regression-"));
  const server = http.createServer((_request, response) => {
    response.setHeader("content-type", "text/html; charset=utf-8");
    response.end(html);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  const recorder = new BrowserRecorder({ rootDir: directory, dataDir: directory,
    recordingsDir: path.join(directory, "recordings"), catalogDir: path.join(directory, "catalog"),
    profileDir: path.join(directory, "profile"), headless: true, maxResponseBytes: 32768, openaiModel: "test" });
  try {
    await recorder.start(`http://127.0.0.1:${address.port}/`, "out-apply", undefined, ["query", "create", "delete"], true);
    await run(recorder);
  } finally {
    await recorder.stop().catch(() => {});
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(directory, { recursive: true, force: true });
  }
}

test("a completed whole-form attempt permits correcting a failed field", async () => {
  await withRecorder(`<!doctype html><form class="el-form"><div class="el-form-item">
    <label for="reason">事由</label><input id="reason" placeholder="请输入事由">
    </div><button type="button">添加明细</button></form>`, async recorder => {
    const blocked: any = await recorder.control({ action: "fill", selector: "label=事由", value: "验收标记" });
    assert.equal(blocked.requiresWholeForm, true);
    const attempt: any = await recorder.control({ action: "exercise-form" });
    assert.equal(attempt.ok, false, "fixture must retain a real incomplete form expansion");
    assert.ok(attempt.filled.length > 0, "the whole-form pass really ran");
    const repair: any = await recorder.control({ action: "fill", selector: "label=事由", value: "验收标记" });
    assert.notEqual(repair.requiresWholeForm, true, "partial failure must not lock out its own correction");
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.equal(snapshot.formFields.find((field: any) => field.label === "事由")?.value, "验收标记");
  });
});

test("nested person chooser exposes and activates its own controls, retaining the parent form", async () => {
  await withRecorder(`<!doctype html><form><label for="reason">事由</label><input id="reason" value="验收标记"></form>
    <div class="el-dialog" role="dialog" style="position:fixed;inset:0;background:white;z-index:10">
      <div class="el-dialog__title">抄送设置</div><input value="parent">
      <button aria-label="Close" onclick="document.title='wrong parent'">关闭</button><button>选择抄送人</button>
    </div>
    <div class="el-dialog" role="dialog" style="position:fixed;inset:20px;background:white;z-index:20">
      <div class="el-dialog__title">选择抄送用户</div>
      <label for="username">用户名称</label><input id="username"><button>搜索</button>
      <table><tbody><tr><td><input type="checkbox"></td><td>测试人员</td></tr></tbody></table>
      <button aria-label="Close" onclick="document.title='closed chooser';this.parentElement.remove()">关闭</button><button>确认</button>
    </div>`, async recorder => {
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.ok(snapshot.controls.some((control: any) => control.text === "搜索"), "active chooser search is visible to Pi");
    await recorder.control({ action: "click", selector: 'role=button[name="Close"]' });
    const after: any = await recorder.control({ action: "snapshot" });
    assert.equal(after.title, "closed chooser", "click must target the top dialog, never its covered parent");
  });
});
