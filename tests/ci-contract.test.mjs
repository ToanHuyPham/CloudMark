import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import yaml from "js-yaml";

test("keeps CI least-privilege, immutable, cross-platform, and non-load-bearing", async () => {
  const workflowText = await readFile(
    new URL("../.github/workflows/ci.yml", import.meta.url),
    "utf8",
  );
  const workflow = yaml.load(workflowText);

  assert.deepEqual(workflow.permissions, { contents: "read" });
  assert.equal(workflow.jobs.python.strategy["fail-fast"], false);
  assert.deepEqual(
    workflow.jobs.python.strategy.matrix.include,
    [
      { os: "ubuntu-latest", python: "3.9" },
      { os: "ubuntu-latest", python: "3.13" },
      { os: "windows-latest", python: "3.13" },
    ],
  );

  const steps = Object.values(workflow.jobs).flatMap((job) => job.steps || []);
  const actions = steps.filter((step) => step.uses).map((step) => step.uses);
  assert.ok(actions.length >= 3);
  assert.ok(actions.every((action) => /@[0-9a-f]{40}$/.test(action)));
  const pnpmSetup = steps.find((step) => step.name === "Set up pnpm and Node.js");
  assert.equal(pnpmSetup.with.runtime, "node@22.23.2");
  assert.equal(pnpmSetup.with["require-lockfile"], true);

  const commands = steps.map((step) => step.run || "").join("\n");
  assert.match(commands, /ruff check cloudmark tests_python scripts/);
  assert.match(commands, /coverage run -m unittest discover -s tests_python -v/);
  assert.match(commands, /coverage report/);
  assert.match(commands, /pnpm run lint/);
  assert.match(commands, /pnpm run typecheck/);
  assert.match(commands, /pnpm run validate:openapi/);
  assert.match(commands, /pnpm test/);
  assert.doesNotMatch(commands, /\b(fio|iperf3|pgbench|sysbench|h2load|redis-benchmark)\b/);
});
