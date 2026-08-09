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
  assert.match(html, /Assessment Catalog/);
  assert.match(html, /Storage Assessment/);
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
  assert.match(page, /Run assessment/);
  assert.match(page, /Cancel run/);
  assert.match(page, /ONE-SECOND TELEMETRY/);
  assert.equal(JSON.parse(packageJson).version, "0.2.0");
  assert.doesNotMatch(`${page}\n${layout}\n${readme}`, /\bqualification lab\b|\b0\.1\.0-alpha\b|\bMVP\b/i);
  assert.match(readme, /cloud-to-controller performance measurement\s+is disabled by policy/i);
  assert.match(readme, /Version `0\.2\.0`/);
  await access(new URL("../public/og.png", import.meta.url));
  await assert.rejects(access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)));
});

test("keeps public product copy English-only", async () => {
  const files = await Promise.all([
    "../README.md",
    "../app/page.tsx",
    "../cloudmark/profiles.py",
    "../docs/ASSESSMENT_CATALOG.md",
    "../docs/ROADMAP.md",
    "../docs/USER_GUIDE.md",
  ].map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  const publicCopy = files.join("\n");
  const vietnameseCharacters = /[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/u;
  assert.doesNotMatch(publicCopy, vietnameseCharacters);
  assert.doesNotMatch(publicCopy, /\b(theo|khong|trong|tren|voi|chay|phien|duoc)\b/i);
  assert.doesNotMatch(publicCopy, /\.vi\.md|vi-VN|lang=["']vi["']/i);
});
