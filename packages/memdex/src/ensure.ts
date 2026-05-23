import { existsSync } from "node:fs";
import { join } from "node:path";
import {
  JsonObject,
  bundleMode,
  configPath,
  die,
  iso,
  loadConfig,
  loadState,
  output,
  readJson,
  removeFileQuiet,
  repoLock,
  secondsSince,
  sha256File,
  uninitializedStatus,
  missingConfigMessage,
  writeJson,
  yymmddhhmm,
} from "./common";
import { buildBundle, buildBundleSet, fastFingerprint } from "./chunking";
import {
  activeSources,
  clearPendingUpload,
  notebookId,
  queueCleanupSourceIds,
  recoverPendingCleanup,
  recoverPendingUpload,
  sourceSetHash,
  uploadBundleSet,
  uploadTextSourceFromFile,
  waitSourceReady,
} from "./notebooklm";

export function stateUploadedFingerprint(state: JsonObject): string | null {
  return state.lastUploadedFastFingerprint || null;
}

export async function ensureIndex(repo: string, opts: { force?: boolean; yes?: boolean; jsonOutput?: boolean; command?: string; returnUninitialized?: boolean; reuseOnly?: boolean } = {}): Promise<JsonObject> {
  const cfg = configPath(repo);
  if (!existsSync(cfg)) {
    if (opts.jsonOutput || opts.returnUninitialized) return uninitializedStatus(repo, cfg);
    die(missingConfigMessage(repo, cfg, opts.command || "ensure"));
  }
  return repoLock(repo, () => ensureIndexLocked(repo, opts));
}

export async function ensureIndexLocked(repo: string, opts: { force?: boolean; yes?: boolean; jsonOutput?: boolean; command?: string; reuseOnly?: boolean } = {}): Promise<JsonObject> {
  const [config, cfgPath] = loadConfig(repo, opts.command || "ensure");
  const [state, statePath] = loadState(cfgPath);
  await recoverPendingUpload(repo, config, state);
  await recoverPendingCleanup(repo, config, state, statePath);
  const [fastHash, relevantPaths] = await fastFingerprint(repo, config, cfgPath);
  const refresh = config.refresh || {};
  const checkedAge = secondsSince(state.lastCheckedAt);
  const uploadedAge = secondsSince(state.lastUploadedAt);
  const uploadedFingerprint = stateUploadedFingerprint(state);
  const result: JsonObject = {
    status: "unknown",
    config: cfgPath,
    state: statePath,
    relevant_changed_paths: relevantPaths,
    fast_fingerprint: fastHash,
  };

  if (!opts.force && checkedAge !== null && checkedAge < Number(refresh.check_ttl_seconds ?? 300) && uploadedFingerprint === fastHash) {
    Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
    writeJson(statePath, state);
    return { ...result, status: "fresh-ttl", checked_age_seconds: checkedAge };
  }

  if (!opts.force && uploadedFingerprint === fastHash && state.lastUploadedAt) {
    Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
    writeJson(statePath, state);
    return { ...result, status: "fresh-fingerprint" };
  }

  const firstUpload = activeSources(state).length === 0;
  if (firstUpload && config.safety?.require_user_approval_first_upload !== false && !opts.yes && !opts.force) {
    return { ...result, status: "needs-first-upload-approval" };
  }

  if (!opts.force && opts.reuseOnly && !firstUpload) {
    Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
    writeJson(statePath, state);
    return { ...result, status: "reuse-index-stale", uploaded_age_seconds: uploadedAge };
  }

  const minInterval = Number(refresh.min_upload_interval_seconds ?? 900);
  const maxStaleness = Number(refresh.max_staleness_seconds ?? 86400);
  if (!opts.force && uploadedAge !== null && uploadedAge < minInterval && uploadedAge < maxStaleness) {
    Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
    writeJson(statePath, state);
    return { ...result, status: "stale-throttled", uploaded_age_seconds: uploadedAge };
  }

  if (!opts.force && refresh.auto === false) {
    Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
    writeJson(statePath, state);
    return { ...result, status: "auto-refresh-disabled" };
  }

  if (bundleMode(config) === "chunked") {
    const setId = yymmddhhmm();
    const bundles = await buildBundleSet(repo, config, { setId, state });
    try {
      const bundleSetSha = sourceSetHash(bundles);
      if (!opts.force && state.lastBundleSetSha256 === bundleSetSha) {
        Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
        writeJson(statePath, state);
        return { ...result, status: "fresh-bundle-hash", bundleSetSha256: bundleSetSha, bundleDeleted: true };
      }
      const sourceSet = await uploadBundleSet(repo, config, state, bundles, { setId });
      const retiredIds = (sourceSet._retiredSourceIds || []).map(String).filter(Boolean);
      delete sourceSet._retiredSourceIds;
      Object.assign(state, {
        lastCheckedAt: iso(),
        lastUploadedAt: iso(),
        lastConfigSha256: sha256File(cfgPath),
        lastCheckedFastFingerprint: fastHash,
        lastUploadedFastFingerprint: fastHash,
        lastFastFingerprint: fastHash,
        lastBundleSetSha256: bundleSetSha,
        lastBundleSha256: bundleSetSha,
        lastBundlePath: null,
        activeSourceSet: sourceSet,
        sources: (sourceSet.sources || []).filter((src: any) => src && typeof src === "object"),
      });
      const cleanupPendingSourceIds = queueCleanupSourceIds(state, retiredIds);
      writeJson(statePath, state);
      clearPendingUpload(repo);
      return { ...result, status: "uploaded", bundleSetSha256: bundleSetSha, bundleDeleted: true, sourceSet, cleanupPendingSourceIds };
    } finally {
      for (const bundle of bundles) if (bundle.path) removeFileQuiet(String(bundle.path));
    }
  }

  const bundle = await buildBundle(repo, config);
  try {
    const bundleSha = sha256File(bundle);
    if (!opts.force && state.lastBundleSha256 === bundleSha) {
      Object.assign(state, { lastCheckedAt: iso(), lastCheckedFastFingerprint: fastHash, lastBundlePath: null });
      writeJson(statePath, state);
      return { ...result, status: "fresh-bundle-hash", bundleSha256: bundleSha, bundleDeleted: true };
    }
    const source = await uploadTextSourceFromFile(repo, config, bundle, bundle.split("/").at(-1) || "bundle.txt");
    source.bundleSha256 = bundleSha;
    source.uploadedAt = iso();
    if (config.notebooklm?.wait_after_upload && source.id) {
      if (!(await waitSourceReady(repo, notebookId(config), String(source.id)))) console.error(`warning: source wait failed for ${source.id}`);
    }
    state.sources = [...(state.sources || []), source];
    Object.assign(state, {
      lastCheckedAt: iso(),
      lastUploadedAt: iso(),
      lastConfigSha256: sha256File(cfgPath),
      lastCheckedFastFingerprint: fastHash,
      lastUploadedFastFingerprint: fastHash,
      lastFastFingerprint: fastHash,
      lastBundleSha256: bundleSha,
      lastBundlePath: null,
    });
    writeJson(statePath, state);
    return { ...result, status: "uploaded", bundleSha256: bundleSha, bundleDeleted: true, source };
  } finally {
    removeFileQuiet(bundle);
  }
}
