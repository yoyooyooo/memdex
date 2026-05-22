import { existsSync, mkdirSync, readFileSync, readdirSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, relative } from "node:path";
import {
  JsonObject,
  bundleMode,
  defaultGroups,
  defaultShortSourceTitlePrefix,
  die,
  groupSpecs,
  includeSpecs,
  iso,
  neverUploadSpecs,
  parseSizeBytes,
  pathIsIgnored,
  pathIsIncluded,
  posixPath,
  removeFileQuiet,
  repomixCmd,
  runCommand,
  sha256File,
  sha256Text,
  slugify,
  yymmddhhmm,
} from "./common";
import { activeSources } from "./notebooklm";

export type Chunk = JsonObject;

export async function listGitFiles(repo: string): Promise<string[]> {
  const result = await runCommand(["git", "ls-files", "-co", "--exclude-standard"], repo);
  if (result.returncode === 0) return result.stdout.split("\n").map((line) => line.trim()).filter(Boolean).sort();
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      const rel = posixPath(relative(repo, full));
      if (rel === ".git" || rel.startsWith(".git/")) continue;
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) files.push(rel);
    }
  };
  walk(repo);
  return files.sort();
}

export async function collectBundleFiles(repo: string, config: JsonObject): Promise<string[]> {
  const includes = includeSpecs(config);
  const ignores = neverUploadSpecs(config);
  const files: string[] = [];
  for (const path of await listGitFiles(repo)) {
    if (!pathIsIncluded(path, includes) || pathIsIgnored(path, ignores)) continue;
    const full = join(repo, path);
    if (!existsSync(full)) continue;
    const stat = statSync(full);
    if (!stat.isFile() || stat.isSymbolicLink()) continue;
    files.push(path);
  }
  return [...new Set(files)].sort();
}

export function chunkFileSize(repo: string, path: string): number {
  return statSync(join(repo, path)).size + Buffer.byteLength(path, "utf8") + 64;
}

export function fileBucket(path: string): string {
  const parts = path.split("/");
  if (parts.length >= 3 && ["apps", "packages", "crates"].includes(parts[0])) return parts.slice(0, 3).join("/");
  if (parts.length >= 2) return parts.slice(0, 2).join("/");
  return parts[0];
}

export function sourceTitleForChunk(config: JsonObject, opts: { setId: string; group: string; index: number; chunkHash: string }): string {
  const configured = String(config.notebooklm?.source_title_prefix || "").trim();
  const prefix = !configured || configured.startsWith("codebase-retrieve-") ? defaultShortSourceTitlePrefix() : configured;
  const template = String(config.bundle?.source_title_template || "{prefix}--{set}--{group}--{chunk}--{hash}.md");
  return template
    .replaceAll("{prefix}", slugify(prefix))
    .replaceAll("{set}", opts.setId)
    .replaceAll("{set_id}", opts.setId)
    .replaceAll("{group}", slugify(opts.group))
    .replaceAll("{chunk}", String(opts.index).padStart(3, "0"))
    .replaceAll("{idx}", String(opts.index).padStart(3, "0"))
    .replaceAll("{hash}", opts.chunkHash.slice(0, 8));
}

export function chunkHashForFiles(repo: string, files: string[]): string {
  const digest = createHash("sha256");
  for (const file of files) {
    digest.update(file);
    digest.update("\0");
    const full = join(repo, file);
    if (existsSync(full) && statSync(full).isFile()) digest.update(readFileSync(full));
    digest.update("\0");
  }
  return digest.digest("hex");
}

export function assignFilesToGroups(files: string[], config: JsonObject): Array<[string, string]> {
  const bundle = config.bundle || {};
  const groups = "groups" in bundle ? bundle.groups || [] : defaultGroups();
  const assigned: Array<[string, string]> = [];
  const seen = new Set<string>();
  for (const group of groups) {
    const gid = slugify(String(group.id || "group"));
    const specs = groupSpecs(group);
    for (const path of files) {
      if (seen.has(path)) continue;
      if (specs.length && pathIsIncluded(path, specs)) {
        assigned.push([gid, path]);
        seen.add(path);
      }
    }
  }
  const defaultGroup = "default_group" in bundle ? bundle.default_group || {} : { enabled: true, id: "misc" };
  if (defaultGroup.enabled) {
    const gid = slugify(String(defaultGroup.id || "misc"));
    for (const path of files) if (!seen.has(path)) assigned.push([gid, path]);
  } else if (!groups.length) {
    for (const path of files) assigned.push(["repo", path]);
  }
  return assigned;
}

function flushChunk(chunks: Chunk[], repo: string, config: JsonObject, setId: string, group: string, index: number, files: string[], total: number): void {
  if (!files.length) return;
  const digest = chunkHashForFiles(repo, files);
  chunks.push({
    group,
    chunk: String(index).padStart(3, "0"),
    index,
    files: [...files],
    estimatedBytes: total,
    sha256: `sha256:${digest}`,
    title: sourceTitleForChunk(config, { setId, group, index, chunkHash: digest }),
  });
}

function activeChunkFileMembers(state: JsonObject | null | undefined, group: string): string[][] {
  if (!state) return [];
  return activeSources(state)
    .filter((source) => String(source.group || "") === group && Array.isArray(source.files) && source.files.length)
    .map((source) => [Number.parseInt(String(source.chunk || "0"), 10) || 0, source.files.map(String).filter(Boolean)] as [number, string[]])
    .sort((a, b) => a[0] - b[0])
    .map(([, files]) => files);
}

function appendGreedyChunks(chunks: Chunk[], repo: string, config: JsonObject, opts: { setId: string; group: string; startIndex: number; files: string[]; target: number; maxBytes: number }): void {
  let current: string[] = [];
  let currentSize = 0;
  let index = opts.startIndex;
  for (const path of opts.files) {
    const size = chunkFileSize(repo, path);
    if (size > opts.maxBytes) die(`file exceeds max chunk size (${opts.maxBytes} bytes): ${path} (${size} bytes)`);
    if (current.length && currentSize + size > opts.target) {
      flushChunk(chunks, repo, config, opts.setId, opts.group, index, current, currentSize);
      current = [];
      currentSize = 0;
      index += 1;
    }
    current.push(path);
    currentSize += size;
  }
  if (current.length) flushChunk(chunks, repo, config, opts.setId, opts.group, index, current, currentSize);
}

function planGroupChunks(chunks: Chunk[], repo: string, config: JsonObject, opts: { setId: string; group: string; files: string[]; target: number; maxBytes: number; state?: JsonObject | null }): void {
  const ordered = [...opts.files].sort((a, b) => `${fileBucket(a)}\0${a}`.localeCompare(`${fileBucket(b)}\0${b}`));
  const available = new Set(ordered);
  const kept: string[][] = [];
  for (const previousFiles of activeChunkFileMembers(opts.state, opts.group)) {
    const retained = previousFiles.filter((path) => available.has(path));
    if (!retained.length) continue;
    const sizes = retained.map((path) => [path, chunkFileSize(repo, path)] as [string, number]);
    for (const [path, size] of sizes) if (size > opts.maxBytes) die(`file exceeds max chunk size (${opts.maxBytes} bytes): ${path} (${size} bytes)`);
    const total = sizes.reduce((sum, [, size]) => sum + size, 0);
    if (total <= opts.target || retained.length === 1) {
      kept.push(retained);
      for (const path of retained) available.delete(path);
    }
  }
  let index = 1;
  for (const files of kept) {
    flushChunk(chunks, repo, config, opts.setId, opts.group, index, files, files.reduce((sum, path) => sum + chunkFileSize(repo, path), 0));
    index += 1;
  }
  appendGreedyChunks(chunks, repo, config, { ...opts, startIndex: index, files: ordered.filter((path) => available.has(path)) });
}

export async function planBundleChunks(repo: string, config: JsonObject, opts: { setId: string; state?: JsonObject | null }): Promise<Chunk[]> {
  const bundle = config.bundle || {};
  let target = parseSizeBytes(bundle.target_chunk_bytes, 524288);
  const maxBytes = parseSizeBytes(bundle.max_chunk_bytes, 900000);
  if (target > maxBytes) target = maxBytes;
  const assigned = assignFilesToGroups(await collectBundleFiles(repo, config), config);
  const byGroup = new Map<string, string[]>();
  for (const [group, path] of assigned) byGroup.set(group, [...(byGroup.get(group) || []), path]);
  const chunks: Chunk[] = [];
  for (const group of [...byGroup.keys()].sort()) {
    planGroupChunks(chunks, repo, config, { setId: opts.setId, group, files: byGroup.get(group) || [], target, maxBytes, state: opts.state });
  }
  return chunks;
}

export async function gitHead(repo: string): Promise<string> {
  const result = await runCommand(["git", "rev-parse", "HEAD"], repo);
  return result.returncode === 0 ? result.stdout.trim() : "no-git-head";
}

export async function gitStatusRecords(repo: string): Promise<Array<[string, string]>> {
  const result = await runCommand(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], repo);
  if (result.returncode !== 0) return [];
  const raw = result.stdout.split("\0").filter(Boolean);
  const records: Array<[string, string]> = [];
  let skip = false;
  for (const item of raw) {
    if (skip) {
      skip = false;
      continue;
    }
    const status = item.slice(0, 2);
    const path = item.slice(3);
    if (status.startsWith("R") || status.startsWith("C")) skip = true;
    records.push([status, path]);
  }
  return records;
}

export async function fastFingerprint(repo: string, config: JsonObject, configFile: string): Promise<[string, string[]]> {
  const includes = includeSpecs(config);
  const ignores = neverUploadSpecs(config);
  const parts = [`head=${await gitHead(repo)}`, `config=${sha256File(configFile)}`];
  const relevant: string[] = [];
  for (const [status, path] of await gitStatusRecords(repo)) {
    if (!pathIsIncluded(path, includes) || pathIsIgnored(path, ignores)) continue;
    relevant.push(path);
    const full = join(repo, path);
    let content = "missing";
    if (existsSync(full) && statSync(full).isFile()) content = sha256File(full);
    else if (existsSync(full)) content = "dir";
    parts.push(`${status} ${path} ${content}`);
  }
  return [sha256Text(parts.join("\n")), relevant];
}

export function expandBundlePath(repo: string, config: JsonObject): string {
  const prefix = config.notebooklm?.source_title_prefix || `${repo}-repo`;
  const timestamp = iso().replaceAll("-", "").replaceAll(":", "").replace("Z", "Z");
  const template = config.bundle?.output || `${CONFIG_DIR}/cache/{prefix}-{timestamp}.txt`;
  return join(repo, template.replaceAll("{prefix}", prefix).replaceAll("{timestamp}", timestamp));
}

export function expandChunkPath(repo: string, config: JsonObject, title: string): string {
  const template = config.bundle?.output || `${CONFIG_DIR}/cache/{title}`;
  if (template.includes("{title}")) {
    return join(repo, template.replaceAll("{title}", title).replaceAll("{prefix}", config.notebooklm?.source_title_prefix || defaultShortSourceTitlePrefix()).replaceAll("{timestamp}", yymmddhhmm()));
  }
  return join(dirname(join(repo, template)), title);
}

export function repomixBaseArgv(config: JsonObject): string[] {
  const argv = repomixCmd();
  const bundle = config.bundle || {};
  if (String(bundle.style || "").trim()) argv.push("--style", String(bundle.style).trim());
  if (bundle.compress) argv.push("--compress");
  const ignore = neverUploadSpecs(config).join(",");
  if (ignore) argv.push("--ignore", ignore);
  return argv;
}

export async function buildBundle(repo: string, config: JsonObject): Promise<string> {
  const out = expandBundlePath(repo, config);
  mkdirSync(dirname(out), { recursive: true });
  const result = await runCommand([...repomixBaseArgv(config), "--include", includeSpecs(config).join(","), "--output", out], repo, { timeout: 600 });
  if (result.returncode !== 0) die(`repomix failed:\n${result.stdout}\n${result.stderr}`);
  return out;
}

export async function buildBundleSet(repo: string, config: JsonObject, opts: { setId: string; state?: JsonObject | null }): Promise<Chunk[]> {
  const maxBytes = parseSizeBytes(config.bundle?.max_chunk_bytes, 900000);
  const chunks = await planBundleChunks(repo, config, opts);
  const bundles: Chunk[] = [];
  try {
    for (const chunk of chunks) {
      const title = String(chunk.title);
      const out = expandChunkPath(repo, config, title);
      mkdirSync(dirname(out), { recursive: true });
      const inputText = `${chunk.files.map(String).join("\n")}\n`;
      const result = await runCommand([...repomixBaseArgv(config), "--stdin", "--output", out], repo, { inputText, timeout: 600 });
      if (result.returncode !== 0) die(`repomix failed for chunk ${title}:\n${result.stdout}\n${result.stderr}`);
      const actualBytes = statSync(out).size;
      if (actualBytes > maxBytes) die(`rendered chunk exceeds max size (${maxBytes} bytes): ${title} (${actualBytes} bytes)`);
      bundles.push({ ...chunk, path: out, bundleSha256: sha256File(out), contentSha256: sha256File(out), fileListSha256: chunk.sha256, actualBytes, fileCount: chunk.files.length });
    }
  } catch (error) {
    for (const bundle of bundles) if (bundle.path) removeFileQuiet(String(bundle.path));
    throw error;
  }
  return bundles;
}

export { bundleMode };
