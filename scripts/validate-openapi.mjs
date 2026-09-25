import { readFile } from "node:fs/promises";

import yaml from "js-yaml";

const repositoryRoot = new URL("../", import.meta.url);
const specText = await readFile(new URL("openapi/cloudmark-v1.yaml", repositoryRoot), "utf8");
const packageManifest = JSON.parse(
  await readFile(new URL("package.json", repositoryRoot), "utf8"),
);
const pyproject = await readFile(new URL("pyproject.toml", repositoryRoot), "utf8");
const document = yaml.load(specText);

function fail(message) {
  throw new Error(`OpenAPI validation failed: ${message}`);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

if (!isObject(document) || document.openapi !== "3.1.0") {
  fail("the document must be an OpenAPI 3.1.0 object");
}

const pythonVersion = pyproject.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (!pythonVersion || document.info?.version !== pythonVersion || packageManifest.version !== pythonVersion) {
  fail("OpenAPI, Python, and dashboard versions must match");
}

if (!isObject(document.paths) || !isObject(document.components?.schemas)) {
  fail("paths and component schemas are required");
}

const requiredPaths = [
  "/health",
  "/dashboard",
  "/runs",
  "/network-campaigns",
  "/network-campaigns/{campaignId}/runs",
  "/storage-campaigns",
  "/storage-campaigns/{campaignId}",
  "/storage-campaigns/{campaignId}/runs",
];
for (const path of requiredPaths) {
  if (!isObject(document.paths[path])) fail(`missing required path ${path}`);
}

const operationNames = new Set(["get", "put", "post", "delete", "options", "head", "patch", "trace"]);
for (const [path, pathItem] of Object.entries(document.paths)) {
  if (!isObject(pathItem)) fail(`path item ${path} must be an object`);
  for (const [method, operation] of Object.entries(pathItem)) {
    if (!operationNames.has(method)) continue;
    if (!isObject(operation) || !isObject(operation.responses) || Object.keys(operation.responses).length === 0) {
      fail(`${method.toUpperCase()} ${path} must declare responses`);
    }
  }
}

function resolveLocalReference(reference) {
  if (!reference.startsWith("#/")) return true;
  let value = document;
  for (const encodedPart of reference.slice(2).split("/")) {
    const part = encodedPart.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!isObject(value) || !(part in value)) return false;
    value = value[part];
  }
  return true;
}

function validateReferences(value, location = "#") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => validateReferences(item, `${location}/${index}`));
    return;
  }
  if (!isObject(value)) return;
  if (typeof value.$ref === "string" && !resolveLocalReference(value.$ref)) {
    fail(`unresolved reference ${value.$ref} at ${location}`);
  }
  for (const [key, item] of Object.entries(value)) {
    validateReferences(item, `${location}/${key}`);
  }
}

validateReferences(document);
console.log(`Validated OpenAPI ${document.info.version}: ${Object.keys(document.paths).length} paths, all local references resolved.`);
