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
  assert.match(html, /Database Assessment/);
  assert.match(html, /Web &amp; API Assessment/);
  assert.match(html, /Cloud → controller test disabled/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton|qualification lab/i);
});

test("keeps production metadata and project policy explicit", async () => {
  const [page, layout, styles, packageJson, readme] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../README.md", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(layout, /CloudMark — Infrastructure Assessment Platform/);
  assert.match(layout, /og-darkblue-v1\.png/);
  assert.match(page, /cloud_to_controller_network_test/);
  assert.match(page, /FULL-STACK INFRASTRUCTURE COVERAGE/);
  assert.match(page, /domainCounts\.total \|\| 17/);
  assert.match(page, /Run assessment/);
  assert.match(page, /Cancel run/);
  assert.match(page, /ONE-SECOND TELEMETRY/);
  assert.match(page, /LOCAL SATURATION EXECUTORS/);
  assert.match(page, /EXCLUSIVE LOAD POLICY/);
  assert.match(page, /EXECUTION TARGET/);
  assert.match(page, /EVIDENCE-GATED SUITABILITY/);
  assert.match(page, /PROVIDER EVALUATION READINESS/);
  assert.match(page, /REPEATED-WINDOW OBSERVATIONS/);
  assert.match(page, /COMPARISON CONTRACT/);
  assert.match(page, /ENGINE \/ TOOL CONTRACT/);
  assert.match(page, /provider-observations-v4/);
  assert.match(page, /SYSTEM RESOLVER/);
  assert.match(page, /QUEUE STEERING/);
  assert.match(page, /network-v9/i);
  assert.match(page, /DYNAMIC REVERSE PROXY/);
  assert.match(page, /HTTP\/2 NEGOTIATION/);
  assert.match(page, /web-http-v2/i);
  assert.match(page, /TRANSACTION TAIL LATENCY/);
  assert.match(page, /database-postgresql-v2/i);
  assert.match(page, /LOGICAL BACKUP &amp; RESTORE/);
  assert.match(page, /database-postgresql-recovery-v1/i);
  assert.match(page, /database-postgresql-checkpoint-v1/i);
  assert.match(page, /CHECKPOINT ISOLATION/);
  assert.match(page, /selectedMySQL/);
  assert.match(page, /MYSQL\/MARIADB VALIDITY/);
  assert.match(page, /Provider Comparison/);
  assert.match(page, /CloudMark does not rank providers/);
  assert.match(page, /profiles\.database\?\.\[selectedDatabaseProfile\]/);
  assert.match(page, /profiles\.web\?\.\[selectedWebProfile\]/);
  assert.match(page, /suitability\?\.targets/);
  assert.match(page, /missing_evidence_is_zero/);
  assert.match(page, /Conditional fit/);
  assert.match(styles, /@media \(max-width: 900px\)/);
  assert.match(styles, /\.sidebar nav[^}]*display: flex[^}]*overflow-x: auto/);
  assert.match(styles, /overflow-x: hidden/);
  assert.doesNotMatch(styles, /\.nav-item:not\(\.active\)\s*\{\s*font-size:\s*0/);
  assert.match(styles, /--ink:\s*#02050b/);
  assert.match(styles, /--panel:\s*#07101d/);
  assert.match(styles, /--lime:\s*#4f8cff/);
  assert.match(styles, /--text:\s*#f4f8ff/);
  assert.match(styles, /--type-micro:\s*11px/);
  assert.match(styles, /--type-body:\s*15px/);
  assert.doesNotMatch(styles, /font-size:\s*[789]px/);
  assert.equal(JSON.parse(packageJson).version, "0.5.0");
  assert.doesNotMatch(`${page}\n${layout}\n${readme}`, /\bqualification lab\b|\b0\.1\.0-alpha\b|\bMVP\b/i);
  assert.match(readme, /cloud-to-controller performance measurement\s+is disabled by policy/i);
  assert.match(readme, /Version `0\.5\.0`/);
  await access(new URL("../public/og-darkblue-v1.png", import.meta.url));
  await assert.rejects(access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)));
});

test("keeps public product copy English-only", async () => {
  const files = await Promise.all([
    "../README.md",
    "../app/page.tsx",
    "../cloudmark/profiles.py",
    "../docs/ASSESSMENT_CATALOG.md",
    "../docs/COMPUTE_MEMORY_METHODOLOGY.md",
    "../docs/DATABASE_METHODOLOGY.md",
    "../docs/POSTGRES_CHECKPOINT_METHODOLOGY.md",
    "../docs/MYSQL_METHODOLOGY.md",
    "../docs/WEB_METHODOLOGY.md",
    "../docs/SUITABILITY_METHODOLOGY.md",
    "../docs/REMOTE_EXECUTION.md",
    "../docs/ROADMAP.md",
    "../docs/USER_GUIDE.md",
  ].map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  const publicCopy = files.join("\n");
  const vietnameseCharacters = /[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/u;
  assert.doesNotMatch(publicCopy, vietnameseCharacters);
  assert.doesNotMatch(publicCopy, /\b(theo|khong|trong|tren|voi|chay|phien|duoc)\b/i);
  assert.doesNotMatch(publicCopy, /\.vi\.md|vi-VN|lang=["']vi["']/i);
});
