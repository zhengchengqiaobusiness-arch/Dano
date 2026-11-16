import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("https://xiaoluo.example/web/", {
      headers: {
        accept: "text/html",
        host: "xiaoluo.example",
        "x-forwarded-host": "xiaoluo.example",
        "x-forwarded-proto": "https",
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the Xiaoluo assistant product site", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>小络助手 — 一句话完成跨系统业务办理<\/title>/);
  assert.match(html, /一句话，完成/);
  assert.match(html, /跨系统业务办理/);
  assert.match(html, /从一段对话，/);
  assert.match(html, /形成可追踪的业务结果/);
  assert.match(html, /酒店申请只是一个示例/);
  assert.match(html, /现有系统保持不变/);
  assert.match(html, /老旧系统也能快速接入/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("links to the same-origin assistant without a deployment-specific host", async () => {
  const html = await (await render()).text();
  assert.match(html, /class="nav-cta" href="\/"/);
  assert.match(html, /class="button primary" href="\/"/);
  assert.doesNotMatch(html, /https?:\/\/(?:\d{1,3}\.){3}\d{1,3}(?:[:/])/);
});

test("ships the complete production-demo flow and share metadata", async () => {
  const response = await render();
  const html = await response.text();

  for (const image of [
    "xiaoluo-logo.png",
    "start-input.jpg",
    "application-form.jpg",
    "confirm.jpg",
    "result.jpg",
    "og.png",
  ]) {
    await access(new URL(`../public/${image}`, import.meta.url));
  }

  assert.match(html, /员工发起/);
  assert.match(html, /申请信息自动生成/);
  assert.match(html, /员工核对并确认/);
  assert.match(html, /结果回到对话/);
  assert.match(html, /https:\/\/xiaoluo\.example\/web\/og\.png/);
  assert.match(html, /property="og:image:width" content="1200"/);
  assert.match(html, /property="og:image:height" content="630"/);
  assert.match(html, /name="twitter:card" content="summary_large_image"/);
});

test("renders production links and assets under the configured base path", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /href="\/web\/"[^>]*aria-label="小络助手首页"/);
  assert.match(html, /src="\/web\/xiaoluo-logo\.png"/);
  assert.match(html, /src="\/web\/start-input\.jpg"/);
  assert.match(html, /href="\/web\/assets\//);
  assert.doesNotMatch(html, /(?:src|href)="\/(?:assets|xiaoluo-logo|start-input|application-form|confirm|result|og)/);
});

test("keeps hash navigation interruptible through a single router handler", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /import Link from ["']next\/link["'];/);

  for (const anchor of ["value", "workflow", "case", "scenes", "integration"]) {
    assert.match(page, new RegExp(`<Link[^>]*href=["']#${anchor}["']`));
  }

  assert.doesNotMatch(page, /<a[^>]*href=["']#/);
  assert.doesNotMatch(page, /onClick|preventDefault|scrollIntoView/);
  assert.doesNotMatch(css, /scroll-behavior\s*:\s*smooth/i);
  assert.doesNotMatch(css, /(?:html|body)\s*\{[^}]*overflow(?:-y)?\s*:\s*hidden/is);
});

test("lays out workflow arrows as dedicated grid connectors", async () => {
  const [page, css] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /className="workflow-connector"/);
  assert.match(page, /className="config-connector"/);
  assert.match(css, /\.workflow-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\) 32px\) minmax\(0, 1fr\)/s);
  assert.match(css, /\.config-flow\s*\{[^}]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\) 28px\) minmax\(0, 1fr\)/s);
  assert.doesNotMatch(css, /\.(?:workflow-connector|config-connector)\s*\{[^}]*position:\s*absolute/s);
});
