import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join } from "node:path";
import {
  CONFIG_DIR,
  PENDING_UPLOAD_JSON,
  JsonObject,
  defaultNotebookTitle,
  defaultShortSourceTitlePrefix,
  die,
  iso,
  loadState,
  notebooklmCmd,
  positiveInt,
  readJson,
  removeFileQuiet,
  repoLock,
  runCommand,
  sha256File,
  slugify,
  writeJson,
  yymmddhhmm,
} from "./common";

export function notebookId(config: JsonObject): string {
  const value = config.notebooklm?.notebook_id || "";
  if (!value) die("notebooklm.notebook_id missing in config");
  return String(value);
}

export function notebookTitle(config: JsonObject): string {
  const project = String(config.project?.name || "repo");
  const prefix = String(config.notebooklm?.notebook_title_prefix || "memdex");
  return String(config.notebooklm?.notebook_title || defaultNotebookTitle(project, prefix));
}

function parseNotebookJson(stdout: string, fallbackTitle: string): JsonObject | null {
  let data: any;
  try {
    data = JSON.parse(stdout);
  } catch {
    return null;
  }
  const candidates = [data, data?.notebook, data?.data, data?.result];
  for (const item of candidates) {
    if (!item || typeof item !== "object") continue;
    const id = item.id || item.notebook_id || item.notebookId;
    const title = item.title || item.name || fallbackTitle;
    if (id) return { id: String(id), title: String(title) };
  }
  return null;
}

export async function listNotebooks(repo: string): Promise<JsonObject[]> {
  const result = await runCommand([...notebooklmCmd(), "list", "--json"], repo, { timeout: 120 });
  if (result.returncode !== 0) die(`notebooklm list failed:\n${result.stdout}\n${result.stderr}`);
  try {
    const data = JSON.parse(result.stdout);
    const notebooks = data.notebooks || (Array.isArray(data) ? data : []);
    return notebooks.filter((item: any) => item && typeof item === "object");
  } catch (error: any) {
    die(`notebooklm list returned invalid JSON: ${error.message}`);
  }
}

export async function findNotebookByTitle(repo: string, title: string): Promise<JsonObject | null> {
  const matches = (await listNotebooks(repo)).filter((item) => String(item.title || "") === title);
  if (matches.length > 1) die(`multiple notebooks found with title ${JSON.stringify(title)}: ${matches.map((item) => item.id || "").join(", ")}`);
  if (!matches.length) return null;
  return { id: String(matches[0].id || ""), title: String(matches[0].title || title) };
}

export async function createNotebook(repo: string, title: string): Promise<JsonObject> {
  const result = await runCommand([...notebooklmCmd(), "create", title, "--json"], repo, { timeout: 180 });
  if (result.returncode !== 0) die(`notebooklm create failed:\n${result.stdout}\n${result.stderr}`);
  const parsed = parseNotebookJson(result.stdout, title);
  if (parsed) return parsed;
  const found = await findNotebookByTitle(repo, title);
  if (found) return found;
  die(`created notebook but could not resolve notebook id for title ${JSON.stringify(title)}`);
}

export async function listSources(repo: string, nbid: string): Promise<JsonObject[]> {
  const result = await runCommand([...notebooklmCmd(), "source", "list", "-n", nbid, "--json"], repo, { timeout: 120 });
  if (result.returncode !== 0) return [];
  try {
    const data = JSON.parse(result.stdout);
    const sources = data.sources || (Array.isArray(data) ? data : []);
    return sources.filter((item: any) => item && typeof item === "object");
  } catch {
    return [];
  }
}

export async function findSourceByTitle(repo: string, nbid: string, title: string): Promise<JsonObject | null> {
  for (const src of await listSources(repo, nbid)) {
    if (String(src.title || "") === title && src.id) return { id: String(src.id), title };
  }
  return null;
}

export function sourceFromAddJson(stdout: string, title: string): JsonObject | null {
  let data: any;
  try {
    data = JSON.parse(stdout);
  } catch {
    return null;
  }
  for (const item of [data, data?.source, data?.data, data?.result]) {
    if (!item || typeof item !== "object") continue;
    const id = item.id || item.source_id || item.sourceId;
    if (id) return { id: String(id), title: String(item.title || item.name || title) };
  }
  return null;
}

export async function uploadTextSourceFromFile(repo: string, config: JsonObject, path: string, title: string): Promise<JsonObject> {
  const nbid = notebookId(config);
  let content = "";
  try {
    content = readFileSync(path, "utf8");
  } catch (error: any) {
    die(`source is not valid UTF-8 text for ${title}: ${error.message}`);
  }
  const result = await runCommand([...notebooklmCmd(), "source", "add", "-", "-n", nbid, "--type", "text", "--title", title, "--json"], repo, { inputText: content, timeout: 600 });
  if (result.returncode !== 0) die(`notebooklm source add failed for ${title}:\n${result.stdout}\n${result.stderr}`);
  const source = sourceFromAddJson(result.stdout, title) || (await findSourceByTitle(repo, nbid, title));
  if (!source?.id) die(`uploaded source but could not resolve source id for ${title}`);
  return source;
}

export async function waitSourceReady(repo: string, nbid: string, sourceId: string): Promise<boolean> {
  const wait = await runCommand([...notebooklmCmd(), "source", "wait", sourceId, "-n", nbid], repo, { timeout: 600 });
  return wait.returncode === 0;
}

export function sourceContentSha(value: JsonObject): string {
  return String(value.contentSha256 || value.chunkSha256 || value.bundleSha256 || "");
}

export function sourceFileListSha(value: JsonObject): string {
  return String(value.fileListSha256 || value.sha256 || "");
}

export function chunkKey(value: JsonObject): string {
  return `${value.group}/${value.chunk}`;
}

export function sourceWithChunkMetadata(source: JsonObject, bundle: JsonObject, opts: { status: string; reused?: boolean }): JsonObject {
  const item = {
    ...source,
    group: bundle.group,
    chunk: bundle.chunk,
    chunkKey: chunkKey(bundle),
    chunkSha256: bundle.bundleSha256,
    contentSha256: bundle.contentSha256 || bundle.bundleSha256,
    fileListSha256: bundle.fileListSha256 || bundle.sha256,
    fileCount: bundle.fileCount,
    files: [...(bundle.files || [])],
    status: opts.status,
  };
  if (opts.reused) Object.assign(item, { reused: true, reusedAt: iso() });
  else Object.assign(item, { uploadedAt: iso() });
  return item;
}

export function activeSources(state: JsonObject): JsonObject[] {
  if (state.activeSourceSet && typeof state.activeSourceSet === "object" && Array.isArray(state.activeSourceSet.sources)) {
    return state.activeSourceSet.sources.filter((item: any) => item && typeof item === "object");
  }
  return Array.isArray(state.sources) ? state.sources.filter((item: any) => item && typeof item === "object") : [];
}

export function activeReadySourceIds(state: JsonObject): string[] {
  return activeSources(state).filter((src) => src.id && String(src.status || "ready") === "ready").map((src) => String(src.id));
}

export function cleanupPendingSourceIds(state: JsonObject): string[] {
  return Array.isArray(state.cleanupPendingSourceIds) ? [...new Set(state.cleanupPendingSourceIds.map(String).filter(Boolean))] : [];
}

export function queueCleanupSourceIds(state: JsonObject, sourceIds: string[]): string[] {
  const active = new Set(activeSources(state).map((src) => String(src.id || "")).filter(Boolean));
  const merged = [...new Set([...cleanupPendingSourceIds(state), ...sourceIds])].filter((sid) => sid && !active.has(sid));
  if (merged.length) state.cleanupPendingSourceIds = merged;
  else delete state.cleanupPendingSourceIds;
  return merged;
}

export function pendingUploadPath(repo: string): string {
  return join(repo, CONFIG_DIR, PENDING_UPLOAD_JSON);
}

export function clearPendingUpload(repo: string): void {
  removeFileQuiet(pendingUploadPath(repo));
}

export function readPendingUpload(repo: string): JsonObject | null {
  const path = pendingUploadPath(repo);
  if (!existsSync(path)) return null;
  try {
    const data = JSON.parse(readFileSync(path, "utf8"));
    return data && typeof data === "object" ? data : { sources: [] };
  } catch {
    return { sources: [] };
  }
}

export function writePendingUpload(repo: string, value: JsonObject): void {
  writeJson(pendingUploadPath(repo), value);
}

async function runPool<T, R>(items: T[], workers: number, fn: (item: T) => Promise<R>, progress?: (count: number, total: number) => void): Promise<R[]> {
  const results: R[] = [];
  let index = 0;
  let done = 0;
  const worker = async () => {
    while (index < items.length) {
      const current = items[index++];
      const result = await fn(current);
      results.push(result);
      done += 1;
      progress?.(done, items.length);
    }
  };
  await Promise.all(Array.from({ length: Math.min(workers, items.length) }, worker));
  return results;
}

export async function deleteSourceIdsParallel(repo: string, nbid: string, sourceIds: string[], opts: { parallelism: number }): Promise<string[]> {
  const ids = [...new Set(sourceIds.filter(Boolean))];
  if (!ids.length) return [];
  const deleted = await runPool(
    ids,
    Math.min(ids.length, Math.max(1, opts.parallelism)),
    async (sid) => {
      const result = await runCommand([...notebooklmCmd(), "source", "delete", sid, "-n", nbid, "--yes"], repo, { timeout: 120 });
      if (result.returncode !== 0) {
        console.error(`warning: failed to delete source ${sid}`);
        return "";
      }
      return sid;
    },
    (count, total) => console.error(`cleanup ${count}/${total}`),
  );
  return deleted.filter(Boolean);
}

export async function recoverPendingCleanup(repo: string, config: JsonObject, state: JsonObject, statePath: string): Promise<string[]> {
  const pending = cleanupPendingSourceIds(state);
  if (!pending.length) return [];
  const active = new Set(activeSources(state).map((src) => String(src.id || "")).filter(Boolean));
  const deleteIds = pending.filter((sid) => !active.has(sid));
  if (!deleteIds.length) {
    delete state.cleanupPendingSourceIds;
    writeJson(statePath, state);
    return [];
  }
  const deleted = await deleteSourceIdsParallel(repo, notebookId(config), deleteIds, { parallelism: positiveInt(config.notebooklm?.delete_parallelism, 4) });
  const deletedSet = new Set(deleted);
  const remaining = pending.filter((sid) => !deletedSet.has(sid) && !active.has(sid));
  if (remaining.length) state.cleanupPendingSourceIds = remaining;
  else delete state.cleanupPendingSourceIds;
  writeJson(statePath, state);
  return deleted;
}

export async function recoverPendingUpload(repo: string, config: JsonObject, state: JsonObject = {}): Promise<string[]> {
  const pending = readPendingUpload(repo);
  if (!pending) return [];
  const sources = Array.isArray(pending.sources) ? pending.sources : [];
  if (!Array.isArray(pending.sources)) {
    clearPendingUpload(repo);
    return [];
  }
  const active = new Set(activeSources(state).map((src) => String(src.id || "")).filter(Boolean));
  const ids = sources.filter((src: any) => src?.id).map((src: any) => String(src.id));
  if (ids.length && active.size && ids.every((sid) => active.has(sid))) {
    clearPendingUpload(repo);
    return [];
  }
  const deleted = await deleteSourceIdsParallel(repo, String(pending.notebookId || notebookId(config)), ids.filter((sid) => !active.has(sid)), { parallelism: positiveInt(config.notebooklm?.delete_parallelism, 4) });
  const deletedSet = new Set(deleted);
  const remaining = sources.filter((src: any) => src?.id && !deletedSet.has(String(src.id)));
  if (remaining.length) writePendingUpload(repo, { ...pending, sources: remaining });
  else clearPendingUpload(repo);
  return deleted;
}

function appendPendingSource(repo: string, journal: JsonObject, source: JsonObject): void {
  journal.sources = Array.isArray(journal.sources) ? journal.sources : [];
  journal.sources.push({ id: source.id, title: source.title });
  writePendingUpload(repo, journal);
}

function findReusableSource(bundle: JsonObject, previous: JsonObject[], used: Set<string>): JsonObject | null {
  const wanted = sourceContentSha(bundle);
  if (!wanted) return null;
  for (const source of previous) {
    const sid = String(source.id || "");
    if (!sid || used.has(sid) || String(source.status || "ready") !== "ready") continue;
    if (sourceContentSha(source) === wanted) {
      used.add(sid);
      return source;
    }
  }
  return null;
}

async function uploadOneChunk(repo: string, config: JsonObject, bundle: JsonObject): Promise<JsonObject> {
  const source = await uploadTextSourceFromFile(repo, config, String(bundle.path), String(bundle.title));
  return sourceWithChunkMetadata(source, bundle, { status: "uploaded" });
}

export async function uploadChunksParallel(repo: string, config: JsonObject, bundles: Array<[number, JsonObject]>, opts: { setId: string }): Promise<Array<[number, JsonObject]>> {
  if (!bundles.length) return [];
  const nbid = notebookId(config);
  const journal = { version: 1, setId: opts.setId, notebookId: nbid, startedAt: iso(), sources: [] as JsonObject[] };
  writePendingUpload(repo, journal);
  const uploaded: Array<[number, JsonObject]> = [];
  try {
    const results = await runPool(
      bundles,
      Math.min(bundles.length, positiveInt(config.notebooklm?.upload_parallelism, 4)),
      async ([index, bundle]) => {
        const source = await uploadOneChunk(repo, config, bundle);
        appendPendingSource(repo, journal, source);
        return [index, source] as [number, JsonObject];
      },
      (count, total) => console.error(`upload ${count}/${total}`),
    );
    uploaded.push(...results);
    return uploaded.sort((a, b) => a[0] - b[0]);
  } catch (error) {
    await deleteSourceIdsParallel(repo, nbid, uploaded.map(([, source]) => String(source.id || "")), { parallelism: positiveInt(config.notebooklm?.delete_parallelism, 4) });
    clearPendingUpload(repo);
    throw error;
  }
}

export async function waitUploadedSourcesParallel(repo: string, config: JsonObject, sources: Array<[number, JsonObject]>): Promise<Array<[number, JsonObject]>> {
  if (!sources.length || config.notebooklm?.wait_after_upload === false) return sources;
  const nbid = notebookId(config);
  const ready = await runPool(
    sources,
    Math.min(sources.length, positiveInt(config.notebooklm?.wait_parallelism, 8)),
    async ([index, source]) => {
      const sid = String(source.id || "");
      if (!sid) throw new Error(`missing source id for ${source.title}`);
      if (!(await waitSourceReady(repo, nbid, sid))) throw new Error(`source processing failed for chunk ${source.title}: ${sid}`);
      return [index, { ...source, status: "ready" }] as [number, JsonObject];
    },
    (count, total) => console.error(`wait ${count}/${total}`),
  );
  return ready.sort((a, b) => a[0] - b[0]);
}

export async function uploadBundleSet(repo: string, config: JsonObject, state: JsonObject, bundles: JsonObject[], opts: { setId: string }): Promise<JsonObject> {
  const nbid = notebookId(config);
  await recoverPendingUpload(repo, config, state);
  const previous = activeSources(state);
  const used = new Set<string>();
  const sourcesByIndex: Array<JsonObject | null> = Array(bundles.length).fill(null);
  const uploadPairs: Array<[number, JsonObject]> = [];
  bundles.forEach((bundle, index) => {
    const reusable = findReusableSource(bundle, previous, used);
    if (reusable) sourcesByIndex[index] = sourceWithChunkMetadata(reusable, bundle, { status: "ready", reused: true });
    else uploadPairs.push([index, bundle]);
  });
  const uploaded = await uploadChunksParallel(repo, config, uploadPairs, opts);
  let ready: Array<[number, JsonObject]>;
  try {
    ready = await waitUploadedSourcesParallel(repo, config, uploaded);
  } catch (error) {
    await deleteSourceIdsParallel(repo, nbid, uploaded.map(([, source]) => String(source.id || "")), { parallelism: positiveInt(config.notebooklm?.delete_parallelism, 4) });
    clearPendingUpload(repo);
    throw error;
  }
  for (const [index, source] of ready) sourcesByIndex[index] = source;
  const sources = sourcesByIndex.filter(Boolean) as JsonObject[];
  const activeIds = new Set(sources.map((src) => String(src.id || "")).filter(Boolean));
  const previousIds = previous.map((src) => String(src.id || "")).filter(Boolean);
  const keepPrevious = Number(config.refresh?.keep_previous_sources || 0);
  const keepIds = keepPrevious > 0 ? new Set(previousIds.slice(-keepPrevious)) : new Set<string>();
  const retiredIds = previousIds.filter((sid) => !activeIds.has(sid) && !keepIds.has(sid));
  const sourceSet: JsonObject = {
    id: opts.setId,
    prefix: String(config.notebooklm?.source_title_prefix || defaultShortSourceTitlePrefix()),
    bundleSetSha256: sourceSetHash(bundles),
    uploadedAt: iso(),
    sources,
  };
  if ((config.refresh?.mode || "replace") === "replace" && config.refresh?.delete_previous_after_success !== false) sourceSet._retiredSourceIds = retiredIds;
  return sourceSet;
}

export function sourceSetHash(bundles: JsonObject[]): string {
  return createHash("sha256")
    .update(bundles.map((bundle) => `${bundle.group} ${bundle.chunk} ${sourceContentSha(bundle)} ${sourceFileListSha(bundle)}`).join("\n"))
    .digest("hex")
    .replace(/^/, "sha256:");
}

export function tempSourcePrefix(config: JsonObject): string {
  const prefix = String(config.notebooklm?.temporary_source_title_prefix || "").trim();
  return prefix ? slugify(prefix) : `${String(config.notebooklm?.source_title_prefix || defaultShortSourceTitlePrefix()).trim()}tmp`;
}

export function tempSourceTitle(config: JsonObject, opts: { setId: string; kind: string; title: string; contentSha: string }): string {
  const digest = opts.contentSha.split(":", 2).at(-1) || opts.contentSha;
  return `${tempSourcePrefix(config)}--${opts.setId}--${slugify(opts.kind)}--${slugify(opts.title)}--${digest.slice(0, 8)}.md`;
}

export function stageTempSourceFile(repo: string, title: string, sourcePath: string): string {
  const staged = join(repo, CONFIG_DIR, "cache", title);
  mkdirSync(dirname(staged), { recursive: true });
  copyFileSync(sourcePath, staged);
  return staged;
}

export function tempSourceSets(state: JsonObject): JsonObject[] {
  return Array.isArray(state.temporarySourceSets) ? state.temporarySourceSets.filter((item: any) => item && typeof item === "object") : [];
}

export function tempSourceExpiresAt(ttlSeconds: number): string | null {
  return ttlSeconds > 0 ? iso(new Date(Date.now() + ttlSeconds * 1000)) : null;
}

export function sourceIsExpired(sourceSet: JsonObject): boolean {
  return sourceSet.expiresAt ? new Date(String(sourceSet.expiresAt)).getTime() <= Date.now() : false;
}

export async function withRepoLock<T>(repo: string, fn: () => Promise<T>): Promise<T> {
  return repoLock(repo, fn);
}

export { loadState, sha256File, writeJson, yymmddhhmm };
