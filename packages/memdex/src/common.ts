import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

export type JsonObject = Record<string, any>;

export type RunResult = {
  argv: string[];
  returncode: number;
  stdout: string;
  stderr: string;
};

export class MemdexError extends Error {
  code: number;

  constructor(message: string, code = 2) {
    super(message);
    this.name = "MemdexError";
    this.code = code;
  }
}

export const CONFIG_DIR = ".memdex";
export const CONFIG_JSON = "config.json";
export const STATE_JSON = "state.local.json";
export const PENDING_UPLOAD_JSON = "pending-upload.local.json";
export const DEFAULT_NOTEBOOK_TITLE_PREFIX = "memdex";
export const SCRIPT_CMD_ENV = "MEMDEX_CMD";
export const LEGACY_SCRIPT_CMD_ENV = "CODEBASE_RETRIEVE_CMD";
export const NOTEBOOKLM_PACKAGE = "git+https://github.com/teng-lin/notebooklm-py.git";
export const NOTEBOOKLM_BIN_ENV = "NOTEBOOKLM_BIN";

export const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

type Hooks = {
  run?: (argv: string[], cwd: string, opts?: { inputText?: string; timeout?: number }) => Promise<RunResult>;
  notebooklmCmd?: () => string[];
  repomixCmd?: () => string[];
};

const hooks: Hooks = {};

export function setTestHooks(next: Hooks): void {
  hooks.run = next.run;
  hooks.notebooklmCmd = next.notebooklmCmd;
  hooks.repomixCmd = next.repomixCmd;
}

export function resetTestHooks(): void {
  hooks.run = undefined;
  hooks.notebooklmCmd = undefined;
  hooks.repomixCmd = undefined;
}

export function nowUtc(): Date {
  return new Date();
}

export function iso(ts = nowUtc()): string {
  return ts.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function parseIso(value?: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function yymmddhhmm(ts = nowUtc()): string {
  const yy = String(ts.getUTCFullYear()).slice(-2);
  const mm = String(ts.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(ts.getUTCDate()).padStart(2, "0");
  const hh = String(ts.getUTCHours()).padStart(2, "0");
  const mi = String(ts.getUTCMinutes()).padStart(2, "0");
  return `${yy}${mm}${dd}${hh}${mi}`;
}

export function die(message: string, code = 2): never {
  throw new MemdexError(message, code);
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

export function shellSplit(value: string): string[] {
  const parts: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(value))) parts.push(match[1] ?? match[2] ?? match[3]);
  return parts;
}

export function shellQuote(value: string): string {
  if (/^[A-Za-z0-9_./:=+-]+$/.test(value)) return value;
  return `'${value.replaceAll("'", "'\"'\"'")}'`;
}

export function scriptCmd(): string[] {
  const override = (process.env[SCRIPT_CMD_ENV] || process.env[LEGACY_SCRIPT_CMD_ENV] || "").trim();
  return override ? shellSplit(override) : ["memdex"];
}

export function commandLine(repo: string, command: string, ...parts: string[]): string {
  return [...scriptCmd(), command, "--repo", repo, ...parts].map(shellQuote).join(" ");
}

export function missingConfigMessage(repo: string, configFile: string, command = ""): string {
  const lines = [
    `project is not initialized for project retrieval: ${configFile}`,
    "",
    "Initialize this repo first:",
    `  ${commandLine(repo, "init", "--create-notebook")}`,
    "",
    "Or reuse an existing NotebookLM notebook with the expected title:",
    `  ${commandLine(repo, "init", "--reuse-existing-notebook")}`,
    "",
    "Then ask or locate directly; both commands run freshness preflight:",
    `  ${commandLine(repo, "ask", "your question")}`,
    `  ${commandLine(repo, "locate", "thing to find")}`,
    "",
    "If this is the first broad upload and you already approve it:",
    `  ${commandLine(repo, "ask", "--yes", "your question")}`,
  ];
  if (command) lines.splice(1, 0, `Command \`${command}\` needs \`.memdex/config.json\` before it can run.`);
  return lines.join("\n");
}

export function uninitializedStatus(repo: string, configFile: string): JsonObject {
  return {
    status: "not-initialized",
    initialized: false,
    config: configFile,
    message: "project is not initialized for project retrieval",
    next: {
      createNotebook: commandLine(repo, "init", "--create-notebook"),
      reuseExistingNotebook: commandLine(repo, "init", "--reuse-existing-notebook"),
      ask: commandLine(repo, "ask", "your question"),
      locate: commandLine(repo, "locate", "thing to find"),
      askWithFirstUploadApproval: commandLine(repo, "ask", "--yes", "your question"),
    },
  };
}

type LockStat = ReturnType<typeof statSync>;

function parseLockPid(content: string): number | null {
  const match = content.match(/^pid=(\d+)$/m);
  if (!match) return null;
  const pid = Number(match[1]);
  return Number.isSafeInteger(pid) && pid > 0 ? pid : null;
}

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error: any) {
    return error?.code !== "ESRCH";
  }
}

function sameLockFile(left: LockStat, right: LockStat): boolean {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeMs === right.mtimeMs;
}

function removeStaleRepoLock(lockPath: string, timeoutSeconds: number): string | null {
  let before: LockStat;
  let content: string;
  try {
    before = statSync(lockPath);
    content = readFileSync(lockPath, "utf8");
  } catch (error: any) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }

  const pid = parseLockPid(content);
  let reason: string | null = null;
  if (pid) {
    if (!processIsAlive(pid)) reason = `stale lock pid ${pid} is not running`;
  } else if ((Date.now() - before.mtimeMs) / 1000 > timeoutSeconds) {
    reason = "stale lock has no valid pid";
  }
  if (!reason) return null;

  try {
    const current = statSync(lockPath);
    if (!sameLockFile(before, current)) return null;
    unlinkSync(lockPath);
    return reason;
  } catch (error: any) {
    if (error?.code === "ENOENT") return reason;
    throw error;
  }
}

export async function repoLock<T>(repo: string, fn: () => Promise<T>, timeoutSeconds = 300): Promise<T> {
  const lockPath = join(repo, CONFIG_DIR, ".lock");
  mkdirSync(dirname(lockPath), { recursive: true });
  const started = Date.now();
  let fd: number | undefined;
  while (fd === undefined) {
    try {
      fd = openSync(lockPath, "wx");
      writeFileSync(fd, `pid=${process.pid}\ncreatedAt=${iso()}\n`);
    } catch (error: any) {
      if (error?.code !== "EEXIST") throw error;
      if (removeStaleRepoLock(lockPath, timeoutSeconds)) continue;
      if ((Date.now() - started) / 1000 > timeoutSeconds) die(`timed out waiting for lock: ${lockPath}`);
      await sleep(200);
    }
  }
  try {
    return await fn();
  } finally {
    try {
      if (fd !== undefined) closeSync(fd);
    } catch {
      // fd cleanup is best-effort; unlink below releases the lock for callers.
    }
    removeFileQuiet(lockPath);
  }
}

export async function runCommand(
  argv: string[],
  cwd: string,
  opts: { inputText?: string; timeout?: number } = {},
): Promise<RunResult> {
  if (hooks.run) return hooks.run(argv, cwd, opts);
  return new Promise((resolveRun) => {
    const child = spawn(argv[0], argv.slice(1), { cwd, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let finished = false;
    const timer = opts.timeout
      ? setTimeout(() => {
          if (!finished) child.kill("SIGTERM");
        }, opts.timeout * 1000)
      : undefined;
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (code) => {
      finished = true;
      if (timer) clearTimeout(timer);
      resolveRun({ argv, returncode: code ?? 1, stdout, stderr });
    });
    child.on("error", (error) => {
      finished = true;
      if (timer) clearTimeout(timer);
      resolveRun({ argv, returncode: 1, stdout, stderr: `${stderr}${error.message}` });
    });
    if (opts.inputText !== undefined) child.stdin.end(opts.inputText);
    else child.stdin.end();
  });
}

export function which(name: string): string | null {
  const paths = (process.env.PATH || "").split(":");
  const exts = process.platform === "win32" ? ["", ".exe", ".cmd", ".bat"] : [""];
  for (const dir of paths) {
    for (const ext of exts) {
      const candidate = join(dir, name + ext);
      if (existsSync(candidate)) return candidate;
    }
  }
  return null;
}

export function notebooklmCmd(): string[] {
  if (hooks.notebooklmCmd) return hooks.notebooklmCmd();
  const override = (process.env[NOTEBOOKLM_BIN_ENV] || "").trim();
  if (override) return shellSplit(override);
  const found = which("notebooklm");
  if (found) return [found];
  die(
    "required tool not found on PATH: notebooklm\n" +
      `Install persistently: uv tool install ${NOTEBOOKLM_PACKAGE}\n` +
      `Or set ${NOTEBOOKLM_BIN_ENV}='uvx --from ${NOTEBOOKLM_PACKAGE} notebooklm'`,
  );
}

export function repomixCmd(): string[] {
  if (hooks.repomixCmd) return hooks.repomixCmd();
  const found = which("repomix");
  if (found) return [found];
  if (which("npx")) return ["npx", "repomix"];
  die("required tool not found on PATH: repomix or npx");
}

export function sha256Bytes(data: Buffer | string): string {
  return `sha256:${createHash("sha256").update(data).digest("hex")}`;
}

export function sha256Text(data: string): string {
  return sha256Bytes(Buffer.from(data, "utf8"));
}

export function sha256File(path: string): string {
  return sha256Bytes(readFileSync(path));
}

export function removeFileQuiet(path: string): void {
  try {
    unlinkSync(path);
  } catch (error: any) {
    if (error?.code !== "ENOENT") throw error;
  }
}

export function writeJson(path: string, value: any): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`);
}

export function readJson(path: string): any {
  return JSON.parse(readFileSync(path, "utf8"));
}

export function defaultInclude(): string[] {
  return ["src", "crates", "packages", "apps", "bins", "docs", "scripts", "tests", "xtask", "AGENTS.md", "CLAUDE.md", "README.md", "Cargo.toml", "package.json", "justfile"];
}

export function defaultGroups(): JsonObject[] {
  return [
    { id: "docs", include: ["AGENTS.md", "CLAUDE.md", "README.md", "docs/**"] },
    { id: "apps", include: ["apps/**"] },
    { id: "packages", include: ["packages/**"] },
    { id: "src", include: ["src/**", "crates/**", "bins/**", "xtask/**"] },
    { id: "tests", include: ["tests/**", "testdata/**"] },
    { id: "scripts", include: ["scripts/**"] },
  ];
}

export function slugify(value: string): string {
  const slug = value.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  return slug || "repo";
}

export function defaultNotebookTitle(projectName: string, titlePrefix = DEFAULT_NOTEBOOK_TITLE_PREFIX): string {
  return `${titlePrefix}:${projectName}`;
}

export function defaultShortSourceTitlePrefix(): string {
  return "memdex";
}

export function defaultConfig(repo: string, notebookId = "", opts: { projectName?: string; notebookTitlePrefix?: string; notebookTitle?: string } = {}): JsonObject {
  const project = opts.projectName || basename(repo);
  const prefix = opts.notebookTitlePrefix || DEFAULT_NOTEBOOK_TITLE_PREFIX;
  return {
    version: 1,
    project: { name: project },
    provider: "notebooklm",
    notebooklm: {
      notebook_id: notebookId,
      notebook_title_prefix: prefix,
      notebook_title: opts.notebookTitle || defaultNotebookTitle(project, prefix),
      source_title_prefix: defaultShortSourceTitlePrefix(),
      wait_after_upload: true,
      upload_parallelism: 4,
      wait_parallelism: 8,
      delete_parallelism: 4,
    },
    bundle: {
      tool: "repomix",
      mode: "chunked",
      include: defaultInclude(),
      output: `${CONFIG_DIR}/cache/{prefix}-{timestamp}.txt`,
      style: "",
      compress: false,
      target_chunk_bytes: 524288,
      max_chunk_bytes: 900000,
      source_title_template: "{prefix}--{set}--{group}--{chunk}--{hash}.md",
      groups: defaultGroups(),
      default_group: { enabled: true, id: "misc" },
    },
    refresh: {
      auto: true,
      mode: "replace",
      check_ttl_seconds: 300,
      min_upload_interval_seconds: 900,
      max_staleness_seconds: 86400,
      keep_previous_sources: 0,
      delete_previous_after_success: true,
    },
    safety: {
      require_user_approval_first_upload: true,
      never_upload: [
        ".env*", "**/.env*", ".git/**", "**/.git/**", "node_modules/**", "**/node_modules/**", "target/**", "**/target/**",
        "dist/**", "**/dist/**", "build/**", "**/build/**", "coverage/**", "**/coverage/**", ".next/**", "**/.next/**",
        ".generated/**", "**/.generated/**", "public/**", "**/public/**", "*.png", "**/*.png", "*.jpg", "**/*.jpg",
        "*.jpeg", "**/*.jpeg", "*.gif", "**/*.gif", "*.webp", "**/*.webp", "*.svg", "**/*.svg", "*.ico", "**/*.ico",
        "*.otf", "**/*.otf", "*.ttf", "**/*.ttf", "*.woff", "**/*.woff", "*.woff2", "**/*.woff2", "*.mp4", "**/*.mp4",
        "*.mov", "**/*.mov", "*.zip", "**/*.zip", "*.tar", "**/*.tar", "*.gz", "**/*.gz",
      ],
    },
    retrieval: { line_numbers_require_local_verify: true, max_local_matches: 80 },
  };
}

export function configPath(repo: string): string {
  const candidates = [
    join(repo, CONFIG_DIR, CONFIG_JSON),
    join(repo, CONFIG_DIR, "config.yaml"),
    join(repo, CONFIG_DIR, "config.yml"),
    join(repo, ".notebooklm", CONFIG_JSON),
    join(repo, ".notebooklm", "config.yaml"),
    join(repo, ".notebooklm", "config.yml"),
  ];
  return candidates.find((path) => existsSync(path)) || join(repo, CONFIG_DIR, CONFIG_JSON);
}

export function loadConfig(repo: string, command = ""): [JsonObject, string] {
  const path = configPath(repo);
  if (!existsSync(path)) die(missingConfigMessage(repo, path, command));
  const text = readFileSync(path, "utf8");
  const data = path.endsWith(".json") ? JSON.parse(text) : YAML.parse(text);
  return [data || {}, path];
}

export function loadState(configFile: string): [JsonObject, string] {
  const statePath = join(dirname(configFile), STATE_JSON);
  if (existsSync(statePath)) return [readJson(statePath), statePath];
  return [{ sources: [] }, statePath];
}

export function includeSpecs(config: JsonObject): string[] {
  const include = config.bundle?.include || defaultInclude();
  return include.map((item: any) => String(item).trim().replace(/^\/|\/$/g, "")).filter(Boolean);
}

export function groupSpecs(group: JsonObject): string[] {
  return (group.include || []).map((item: any) => String(item).trim().replace(/^\/|\/$/g, "")).filter(Boolean);
}

export function neverUploadSpecs(config: JsonObject): string[] {
  const builtIn = defaultConfig(process.cwd()).safety.never_upload;
  return [...builtIn, ...(config.safety?.never_upload || [])].map((item) => String(item).trim()).filter(Boolean);
}

function globRegex(spec: string): RegExp {
  let pattern = spec.trim().replace(/^\.\//, "");
  let out = "";
  for (let i = 0; i < pattern.length; i += 1) {
    const ch = pattern[i];
    const next = pattern[i + 1];
    if (ch === "*" && next === "*") {
      out += ".*";
      i += 1;
    } else if (ch === "*") {
      out += "[^/]*";
    } else if (ch === "?") {
      out += "[^/]";
    } else {
      out += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp(`^${out}$`);
}

export function pathMatchesSpec(path: string, spec: string): boolean {
  const clean = path.trim().replace(/^\.\//, "");
  const pattern = spec.trim().replace(/^\.\//, "");
  if (!pattern) return false;
  if (pattern === "." || pattern === "*") return true;
  if (clean === pattern || clean.startsWith(`${pattern.replace(/\/$/, "")}/`)) return true;
  return globRegex(pattern).test(clean) || globRegex(pattern).test(`./${clean}`);
}

export function pathIsIncluded(path: string, includes: string[]): boolean {
  return includes.some((spec) => pathMatchesSpec(path, spec));
}

export function pathIsIgnored(path: string, ignores: string[]): boolean {
  return ignores.some((spec) => pathMatchesSpec(path, spec));
}

export function bundleMode(config: JsonObject): string {
  return String(config.bundle?.mode || "chunked");
}

export function parseSizeBytes(value: any, fallback: number): number {
  if (Number.isInteger(value)) return value;
  const match = String(value || "").trim().toLowerCase().match(/^(\d+)(?:\s*(b|kb|kib|mb|mib))?$/);
  if (!match) return fallback;
  const amount = Number(match[1]);
  const unit = match[2] || "b";
  if (unit === "kb" || unit === "kib") return amount * 1024;
  if (unit === "mb" || unit === "mib") return amount * 1024 * 1024;
  return amount;
}

export function positiveInt(value: any, fallback: number, minimum = 1, maximum = 32): number {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  const valueOrFallback = Number.isFinite(parsed) ? parsed : fallback;
  return Math.max(minimum, Math.min(maximum, valueOrFallback));
}

export function secondsSince(value?: string | null): number | null {
  const parsed = parseIso(value);
  return parsed ? (Date.now() - parsed.getTime()) / 1000 : null;
}

export function posixPath(path: string): string {
  return sep === "/" ? path : path.split(sep).join("/");
}

export function output(data: any, asJson?: boolean): void {
  if (asJson) {
    console.log(JSON.stringify(data, null, 2));
    return;
  }
  if (data && typeof data === "object" && !Array.isArray(data)) {
    for (const [key, value] of Object.entries(data)) {
      console.log(`${key}: ${typeof value === "object" ? JSON.stringify(value) : value}`);
    }
    return;
  }
  console.log(data);
}

export async function fileExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

export function fileSize(path: string): number {
  return statSync(path).size;
}
