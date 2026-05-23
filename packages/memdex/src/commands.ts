import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import {
  CONFIG_DIR,
  CONFIG_JSON,
  JsonObject,
  commandLine,
  configPath,
  defaultConfig,
  defaultNotebookTitle,
  die,
  iso,
  loadConfig,
  loadState,
  output,
  removeFileQuiet,
  sha256File,
  slugify,
  uninitializedStatus,
  writeJson,
  yymmddhhmm,
} from "./common";
import { buildBundleSet, fastFingerprint, planBundleChunks } from "./chunking";
import { ensureIndex, stateUploadedFingerprint } from "./ensure";
import {
  createNotebook,
  deleteSourceIdsParallel,
  findNotebookByTitle,
  listSources,
  notebookId,
  notebookTitle,
  sourceIsExpired,
  stageTempSourceFile,
  tempSourceExpiresAt,
  tempSourcePrefix,
  tempSourceSets,
  tempSourceTitle,
  uploadTextSourceFromFile,
  waitSourceReady,
  withRepoLock,
} from "./notebooklm";
import { answerText, askProvider, firstUploadNext, freshnessWarning, locate, printCompactReferences, providerBlockMessage, providerBlockPayload } from "./retrieval";
import { resolveRepoOption } from "./worktree";

export type CommandOptions = JsonObject;

export async function cmdInit(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const cfgDir = join(repo, CONFIG_DIR);
  const cfg = join(cfgDir, CONFIG_JSON);
  if (existsSync(cfg) && !opts.force) die(`config already exists: ${cfg}`);
  const projectName = opts.projectName || basename(repo);
  const titlePrefix = opts.notebookTitlePrefix || "memdex";
  const title = opts.notebookTitle || defaultNotebookTitle(projectName, titlePrefix);
  let notebookIdValue = opts.notebookId || "";
  let resolvedNotebook: JsonObject | null = null;
  if (!notebookIdValue && (opts.reuseExistingNotebook || opts.createNotebook)) {
    resolvedNotebook = await findNotebookByTitle(repo, title);
    if (!resolvedNotebook && opts.createNotebook) resolvedNotebook = await createNotebook(repo, title);
    if (!resolvedNotebook) die(`no NotebookLM notebook found with title ${JSON.stringify(title)}; pass --create-notebook or --notebook-id`);
    notebookIdValue = String(resolvedNotebook.id || "");
  }
  const config = defaultConfig(repo, notebookIdValue, { projectName, notebookTitlePrefix: titlePrefix, notebookTitle: title });
  if (opts.include) config.bundle.include = String(opts.include).split(",").map((part) => part.trim()).filter(Boolean);
  if (opts.sourceTitlePrefix) config.notebooklm.source_title_prefix = opts.sourceTitlePrefix;
  writeJson(cfg, config);
  writeFileSync(join(cfgDir, ".gitignore"), "state.local.json\nstate.local.*.json\npending-upload.local.json\ncache/\n*.lock\n");
  console.log(`created: ${cfg}`);
  console.log(`created: ${join(cfgDir, ".gitignore")}`);
  console.log(`notebook_title: ${title}`);
  if (resolvedNotebook) console.log(`notebook_id: ${notebookIdValue}`);
  console.log("next:");
  if (notebookIdValue) {
    console.log(`  ${commandLine(repo, "ensure", "--yes")}`);
    console.log(`  ${commandLine(repo, "ask", "your question")}`);
  } else {
    console.log("  set notebooklm.notebook_id in the config, or rerun init with --create-notebook / --reuse-existing-notebook / --notebook-id");
  }
}

export async function cmdStatus(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const cfgCandidate = configPath(repo);
  if (!existsSync(cfgCandidate)) {
    output(uninitializedStatus(repo, cfgCandidate), opts.json);
    return;
  }
  const [config, cfgPath] = loadConfig(repo, "status");
  const [state, statePath] = loadState(cfgPath);
  const [fastHash, changed] = await fastFingerprint(repo, config, cfgPath);
  output(
    {
      initialized: true,
      config: cfgPath,
      state: statePath,
      provider: config.provider,
      projectName: config.project?.name,
      notebook_id: config.notebooklm?.notebook_id,
      notebookTitle: notebookTitle(config),
      sourceTitlePrefix: config.notebooklm?.source_title_prefix,
      lastCheckedAt: state.lastCheckedAt,
      lastUploadedAt: state.lastUploadedAt,
      lastBundleSha256: state.lastBundleSha256,
      fastFingerprint: fastHash,
      stateCheckedFastFingerprint: state.lastCheckedFastFingerprint,
      stateUploadedFastFingerprint: stateUploadedFingerprint(state),
      stateFastFingerprint: state.lastFastFingerprint,
      relevantChangedPaths: changed,
      sources: state.sources || [],
    },
    opts.json,
  );
}

export async function cmdPack(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const [config, cfgPath] = loadConfig(repo, "pack");
  const [state] = loadState(cfgPath);
  const setId = opts.setId || yymmddhhmm();
  const chunks = await planBundleChunks(repo, config, { setId, state });
  if (opts.dryRun) {
    output(
      {
        setId,
        mode: "chunked",
        chunkCount: chunks.length,
        chunks: chunks.map((chunk) => ({
          group: chunk.group,
          chunk: chunk.chunk,
          title: chunk.title,
          estimatedBytes: chunk.estimatedBytes,
          fileCount: (chunk.files || []).length,
          ...(opts.includeFiles ? { files: chunk.files || [] } : {}),
        })),
      },
      opts.json,
    );
    return;
  }
  const bundles = await buildBundleSet(repo, config, { setId, state });
  output(
    {
      setId,
      bundleCount: bundles.length,
      bundles: bundles.map((bundle) => ({
        group: bundle.group,
        chunk: bundle.chunk,
        title: bundle.title,
        path: bundle.path,
        fileCount: bundle.fileCount,
        bundleSha256: bundle.bundleSha256,
        contentSha256: bundle.contentSha256,
      })),
    },
    opts.json,
  );
}

export async function cmdEnsure(opts: CommandOptions): Promise<void> {
  output(await ensureIndex(resolve(String(opts.repo || ".")), { force: opts.force, yes: opts.yes, jsonOutput: opts.json, command: "ensure" }), opts.json);
}

export async function cmdRefresh(opts: CommandOptions): Promise<void> {
  output(await ensureIndex(resolve(String(opts.repo || ".")), { force: true, yes: true, jsonOutput: opts.json, command: "refresh" }), opts.json);
}

export function printAskResult(repo: string, freshness: JsonObject, answer: JsonObject, opts: CommandOptions): void {
  if (opts.json) {
    output({ freshness, provider_answer: answer }, true);
    return;
  }
  const warning = freshnessWarning(freshness);
  if (warning) console.log(warning);
  if (opts.verbose) {
    console.log(`freshness: ${JSON.stringify(freshness)}`);
    const metadata: JsonObject = {};
    for (const key of ["conversation_id", "turn_number", "is_follow_up"]) if (key in answer) metadata[key] = answer[key];
    if (Array.isArray(answer.references)) metadata.references_count = answer.references.length;
    if (Object.keys(metadata).length) console.log(`provider: ${JSON.stringify(metadata)}`);
  }
  console.log(answerText(answer));
  printCompactReferences(repo, answer);
}

export async function cmdAsk(question: string, opts: CommandOptions): Promise<void> {
  const repo = await resolveRepoOption(opts, "ask", question);
  const freshness = await ensureIndex(repo, { force: opts.forceRefresh, yes: opts.yes, jsonOutput: opts.json, command: "ask", returnUninitialized: true, reuseOnly: Boolean(opts.repoWorktree) && !opts.forceRefresh });
  const blocked = providerBlockMessage(freshness);
  if (blocked) {
    const next = freshness.status === "needs-first-upload-approval" ? firstUploadNext(repo, "ask", question) : undefined;
    printAskResult(repo, freshness, providerBlockPayload(freshness, next), opts);
    return;
  }
  printAskResult(repo, freshness, await askProvider(repo, question), opts);
}

export function printLocateResult(result: JsonObject, opts: CommandOptions): void {
  if (opts.json) {
    output(result, true);
    return;
  }
  const warning = freshnessWarning(result.freshness || {});
  if (warning) console.log(warning);
  if (opts.verbose) console.log(`freshness: ${JSON.stringify(result.freshness || {})}`);
  const visible = { ...result };
  delete visible.freshness;
  output(visible, false);
}

export async function cmdLocate(query: string, opts: CommandOptions): Promise<void> {
  printLocateResult(await locate(await resolveRepoOption(opts, "locate", query), query, { forceRefresh: opts.forceRefresh, yes: opts.yes, json: opts.json, includeProviderAnswer: opts.includeProviderAnswer, reuseOnly: Boolean(opts.repoWorktree) && !opts.forceRefresh }), opts);
}

export async function cmdTempSourceUpload(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const [config, cfgPath] = loadConfig(repo, "temp-source upload");
  let sourcePath = String(opts.file || "");
  sourcePath = isAbsolute(sourcePath) ? sourcePath : resolve(repo, sourcePath);
  if (!existsSync(sourcePath)) die(`temp source file not found: ${sourcePath}`);
  const setId = yymmddhhmm();
  const contentSha = sha256File(sourcePath);
  const title = tempSourceTitle(config, { setId, kind: opts.kind, title: opts.title, contentSha });
  const staged = stageTempSourceFile(repo, title, sourcePath);
  let sourceSet: JsonObject = {};
  let item: JsonObject = {};
  await withRepoLock(repo, async () => {
    try {
      const [state, statePath] = loadState(cfgPath);
      const source = await uploadTextSourceFromFile(repo, config, staged, title);
      let status = "uploaded";
      if (config.notebooklm?.wait_after_upload !== false && source.id) {
        status = (await waitSourceReady(repo, notebookId(config), String(source.id))) ? "ready" : "error";
        if (status !== "ready") {
          await deleteSourceIdsParallel(repo, notebookId(config), [String(source.id || "")], { parallelism: Number(config.notebooklm?.delete_parallelism || 4) });
          die(`source processing failed for temp source ${title}: ${source.id}`);
        }
      }
      const active = state.activeSourceSet && typeof state.activeSourceSet === "object" ? state.activeSourceSet : {};
      item = {
        id: source.id,
        title: source.title || title,
        contentSha256: contentSha,
        uploadedAt: iso(),
        status,
        origin: { activeSourceSetId: active.id, chunkKeys: opts.originChunk || [], filePaths: opts.originFile || [] },
      };
      sourceSet = { id: setId, kind: slugify(opts.kind), purpose: opts.title, createdAt: iso(), expiresAt: tempSourceExpiresAt(Number(opts.ttlSeconds || 0)), sources: [item] };
      state.temporarySourceSets = [...tempSourceSets(state), sourceSet];
      writeJson(statePath, state);
    } finally {
      removeFileQuiet(staged);
    }
  });
  output({ sourceSet, source: item }, opts.json);
}

export async function cmdTempSourceList(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const [config, cfgPath] = loadConfig(repo, "temp-source list");
  const [state] = loadState(cfgPath);
  let sets = tempSourceSets(state);
  if (opts.kind) sets = sets.filter((item) => String(item.kind || "") === slugify(opts.kind));
  const prefix = tempSourcePrefix(config);
  const provider = (await listSources(repo, notebookId(config))).filter((src) => String(src.title || "").startsWith(`${prefix}--`));
  const tracked = new Set(tempSourceSets(state).flatMap((set) => (set.sources || []).map((src: any) => String(src.id || "")).filter(Boolean)));
  output({ temporarySourceSets: sets, untrackedPrefixMatches: provider.filter((src) => !tracked.has(String(src.id || ""))) }, opts.json);
}

export async function cmdTempSourceCleanup(opts: CommandOptions): Promise<void> {
  const repo = resolve(String(opts.repo || "."));
  const [config, cfgPath] = loadConfig(repo, "temp-source cleanup");
  let deleted: string[] = [];
  let untracked: JsonObject[] = [];
  await withRepoLock(repo, async () => {
    const [state, statePath] = loadState(cfgPath);
    const wantedKind = opts.kind ? slugify(opts.kind) : "";
    const selected: JsonObject[] = [];
    const kept: JsonObject[] = [];
    for (const sourceSet of tempSourceSets(state)) {
      let matches = true;
      if (opts.setId && String(sourceSet.id || "") !== String(opts.setId)) matches = false;
      if (wantedKind && String(sourceSet.kind || "") !== wantedKind) matches = false;
      if (opts.expired && !sourceIsExpired(sourceSet)) matches = false;
      (matches ? selected : kept).push(sourceSet);
    }
    if (!opts.yes) die("cleanup requires --yes");
    const sourceIds = selected.flatMap((set) => (set.sources || []).map((src: any) => String(src.id || "")).filter(Boolean));
    deleted = await deleteSourceIdsParallel(repo, notebookId(config), sourceIds, { parallelism: Number(config.notebooklm?.delete_parallelism || 4) });
    const deletedSet = new Set(deleted);
    const remainingSelected = selected
      .map((set) => ({ ...set, sources: (set.sources || []).filter((src: any) => !deletedSet.has(String(src.id || ""))) }))
      .filter((set) => set.sources.length);
    state.temporarySourceSets = [...kept, ...remainingSelected];
    writeJson(statePath, state);
    const prefix = tempSourcePrefix(config);
    const providerMatches = (await listSources(repo, notebookId(config))).filter((src) => String(src.title || "").startsWith(`${prefix}--`));
    const tracked = new Set(tempSourceSets(state).flatMap((set) => (set.sources || []).map((src: any) => String(src.id || "")).filter(Boolean)));
    untracked = providerMatches.filter((src) => !tracked.has(String(src.id || "")) && !deletedSet.has(String(src.id || "")));
    if (opts.includeUntrackedPrefix) {
      const extra = await deleteSourceIdsParallel(repo, notebookId(config), untracked.map((src) => String(src.id || "")).filter(Boolean), { parallelism: Number(config.notebooklm?.delete_parallelism || 4) });
      deleted.push(...extra);
      const extraSet = new Set(extra);
      untracked = untracked.filter((src) => !extraSet.has(String(src.id || "")));
    }
  });
  output({ deletedSourceIds: deleted, untrackedPrefixMatches: untracked }, opts.json);
}
