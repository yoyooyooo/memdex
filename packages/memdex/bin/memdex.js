#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const scriptPath = resolve(packageRoot, "scripts", "memdex.py");
const python = process.env.PYTHON || "python3";

const result = spawnSync(python, [scriptPath, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: {
    ...process.env,
    MEMDEX_CMD: process.env.MEMDEX_CMD || process.env.CODEBASE_RETRIEVE_CMD || "memdex"
  }
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
