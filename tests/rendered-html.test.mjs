import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the CloudMark dashboard shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>CloudMark — Infrastructure Assessment Platform<\/title>/i);
  assert.match(html, /CloudMark/);
  assert.match(html, /Infrastructure assessment/);
  assert.match(html, /Danh mục đánh giá/);
  assert.match(html, /Đánh giá Storage/);
  assert.match(html, /Cloud → controller test disabled/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton|qualification lab/i);
});

test("keeps production metadata and project policy explicit", async () => {
  const [page, layout, packageJson, readme] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../README.md", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(layout, /CloudMark — Infrastructure Assessment Platform/);
  assert.match(layout, /og\.png/);
  assert.match(page, /cloud_to_controller_network_test/);
  assert.match(page, /FULL-STACK INFRASTRUCTURE COVERAGE/);
  assert.match(page, /domainCounts\.total \|\| 17/);
  assert.doesNotMatch(`${page}\n${layout}\n${readme}`, /\bqualification lab\b|\b0\.1\.0-alpha\b|\bMVP\b/i);
  assert.match(readme, /cloud-to-controller performance measurement\s+is disabled by policy/i);
  await access(new URL("../public/og.png", import.meta.url));
  await assert.rejects(access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)));
});
