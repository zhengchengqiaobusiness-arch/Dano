import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { mkdtemp, rm } from "node:fs/promises";
import { BrowserRecorder } from "../src/browser/recorder.js";

async function withRecorder(html: string, run: (recorder: BrowserRecorder) => Promise<void>, complete = true) {
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
    await recorder.start(`http://127.0.0.1:${address.port}/`, "out-apply", undefined, ["query", "create", "delete"], complete);
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

test("editable date ranges commit exact times to the selected endpoint, not next month's day", async () => {
  await withRecorder(`<!doctype html><form class="el-form"><div class="el-form-item"><label>起止时间</label>
    <div class="el-date-editor el-range-editor el-date-editor--datetimerange">
      <input class="el-range-input" placeholder="开始时间" value="2026-09-07 00:00:00" onfocus="document.getElementById('panel').hidden=false">
      <input class="el-range-input" placeholder="结束时间" value="2026-10-09 23:59:59">
    </div></div></form>
    <div id="panel" hidden class="el-picker-panel el-date-range-picker"><header>2026 年 9 月</header>
      <table><tbody><tr><td class="available"><span class="cell">8</span></td></tr></tbody></table>
      <header>2026 年 10 月</header><table><tbody><tr><td class="available" onclick="document.querySelector('input').value='2026-10-08 00:00:00'"><span class="cell">8</span></td></tr></tbody></table>
      <button onclick="this.parentElement.hidden=true">确定</button>
    </div>
    <script>document.querySelectorAll('input').forEach((input,index)=>input.addEventListener('change',()=>document.title=index+':'+input.value));</script>`, async recorder => {
    await recorder.control({ action: "fill", selector: "placeholder=开始时间", value: "2026-09-08 09:00:00" });
    await recorder.control({ action: "fill", selector: "placeholder=结束时间", value: "2026-09-08 18:00:00" });
    const snapshot: any = await recorder.control({ action: "snapshot" });
    assert.deepEqual(snapshot.formFields.filter((field: any) => field.kind === "date").map((field: any) => field.value),
      ["2026-09-08 09:00:00", "2026-09-08 18:00:00"]);
    assert.equal(snapshot.title, "1:2026-09-08 18:00:00", "component change event must receive the requested end time");
  }, false);
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

test("manual row selection records that row without emitting unrelated input changes", async () => {
  await withRecorder(`<!doctype html><input id="focused" autofocus value="原字段" oninput="document.title='synthetic-input'">
    <table style="position:fixed;left:0;top:100px"><tbody>
      <tr><td><span class="el-checkbox" style="display:block;width:100px;height:30px">□</span></td><td>000025 甲用户</td></tr>
      <tr><td><span class="el-checkbox" style="display:block;width:100px;height:30px" onmousedown="event.preventDefault()">□</span></td><td>000021 乙用户</td></tr>
    </tbody></table>`, async recorder => {
    const result: any = await recorder.manualControl({ action: "click", x: 20, y: 148 });
    assert.match(result.observed?.label || "", /000021.*乙用户/, JSON.stringify(result.observed));
    assert.doesNotMatch(result.title, /synthetic-input/);
  });
});

test("snapshot exposes actionable labels for icon-only chooser rows", async () => {
  await withRecorder(`<!doctype html><div role="dialog" class="el-dialog">
    <h2>选择抄送用户</h2><input placeholder="请输入用户名称"><button>搜索</button>
    <table><tbody>
      <tr class="vxe-body--row"><td><span class="vxe-cell--checkbox"><span class="vxe-checkbox--icon" onclick="document.title='selected-first'">□</span></span></td><td>000025</td><td>甲用户</td></tr>
      <tr class="vxe-body--row"><td><span class="vxe-cell--checkbox"><span class="vxe-checkbox--icon" onclick="document.title='selected-second'">□</span></span></td><td>000021</td><td>乙用户</td></tr>
    </tbody></table><button>确认</button>
  </div>`, async recorder => {
    const snapshot: any = await recorder.control({ action: "snapshot" });
    const row = snapshot.controls.find((control: any) => control.tag === "tr" && control.label?.includes("000021") && control.label.includes("乙用户"));
    assert.ok(row, "real candidate rows must have grounded selectors without guessing from screenshots");
    await recorder.control({ action: "click", selector: row.selector });
    const after: any = await recorder.control({ action: "snapshot" });
    assert.equal(after.title, "selected-second", "the supplied selector must activate this row's checkbox");
  }, false);
});
