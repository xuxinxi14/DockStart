import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { basename, dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, "..");
const testsRoot = join(projectRoot, "tests");

function discover(directory) {
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const absolute = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...discover(absolute));
    else if (entry.isFile() && entry.name.endsWith(".test.ts")) files.push(absolute);
  }
  return files.sort((left, right) => left.localeCompare(right, "en"));
}

const discovered = discover(testsRoot);
if (discovered.length === 0) {
  console.error("No frontend *.test.ts files were discovered.");
  process.exit(1);
}

const requested = process.argv.slice(2);
let selected = discovered;
if (requested.length > 0) {
  const requestedNames = new Set(requested.map((value) => basename(value)));
  selected = discovered.filter((path) => requestedNames.has(basename(path)));
  const selectedNames = new Set(selected.map((path) => basename(path)));
  const missing = [...requestedNames].filter((name) => !selectedNames.has(name));
  if (missing.length > 0) {
    console.error(`Unknown frontend test file(s): ${missing.join(", ")}`);
    process.exit(1);
  }
}

const testFiles = selected.map((path) => relative(projectRoot, path));
const result = spawnSync(process.execPath, ["--test", ...testFiles], {
  cwd: projectRoot,
  env: process.env,
  stdio: "inherit",
  shell: false,
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
if (result.signal) {
  console.error(`Frontend test process ended by signal ${result.signal}.`);
  process.exit(1);
}
process.exit(result.status ?? 1);
