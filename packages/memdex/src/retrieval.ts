import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { JsonObject, commandLine, includeSpecs, loadConfig, loadState, output, runCommand, which } from "./common";
import { ensureIndex } from "./ensure";
import { activeReadySourceIds, activeSources, notebookId } from "./notebooklm";

const PATH_RE = /(?:(?:[\w.-]+\/)+[\w.@+-]+\.(?:rs|ts|tsx|js|jsx|py|go|java|kt|md|toml|yaml|yml|json|sh|sql|css|scss|html))/g;
const TERM_RE = /[A-Za-z_][A-Za-z0-9_]{3,}|[A-Za-z0-9][A-Za-z0-9_-]{4,}/g;
const STOP_TERMS = new Set([
  "agent", "authority", "btreemap", "bundle", "codex", "command", "docs", "fixture", "gate", "justfile", "keywords",
  "local", "names", "paths", "postgres", "postgresql", "real", "refs", "repo", "shell", "test", "trigger",
  "where", "which", "what", "when", "implemented", "implementation", "function", "tests", "files", "return",
  "likely", "line", "numbers", "source", "notebooklm",
]);

export async function askProvider(repo: string, question: string): Promise<JsonObject> {
  const [config, cfgPath] = loadConfig(repo, "ask");
  const [state] = loadState(cfgPath);
  const argv = ["notebooklm", "ask", question, "-n", notebookId(config)];
  const { notebooklmCmd } = await import("./common");
  argv.splice(0, 1, ...notebooklmCmd());
  for (const sourceId of activeReadySourceIds(state)) argv.push("-s", sourceId);
  argv.push("--json");
  const result = await runCommand(argv, repo, { timeout: 180 });
  if (result.returncode !== 0) return { error: true, stdout: result.stdout, stderr: result.stderr };
  try {
    return JSON.parse(result.stdout);
  } catch {
    return { answer: result.stdout };
  }
}

export function answerText(data: JsonObject): string {
  return typeof data.answer === "string" ? data.answer : JSON.stringify(data);
}

function activeSourcesById(repo: string): Map<string, JsonObject> {
  const [, cfg] = loadConfig(repo, "ask");
  const [state] = loadState(cfg);
  const byId = new Map<string, JsonObject>();
  for (const source of activeSources(state)) if (source.id) byId.set(String(source.id), source);
  return byId;
}

function referencePathCandidates(repo: string, source: JsonObject, text: string): Array<[string, number | null]> {
  const files = Array.isArray(source.files) ? source.files.map(String).filter(Boolean) : [];
  const fileSet = new Set(files);
  const matches: Array<[string, number | null]> = [];
  for (const raw of text.matchAll(PATH_RE)) {
    const path = raw[0].replace(/[`'".,;:()[\]{}<>]+$/g, "");
    if (fileSet.has(path) && existsSync(join(repo, path))) matches.push([path, null]);
  }
  if (matches.length) return [...new Map(matches.map((item) => [item.join(":"), item])).values()].slice(0, 5);
  const snippet = text.split(/\s+/).join(" ");
  if (snippet.length < 4 || snippet.length > 240 || text.includes("<directory_structure>")) return [];
  for (const path of files) {
    const full = join(repo, path);
    if (!existsSync(full) || statSync(full).size > 2_000_000) continue;
    const content = readFileSync(full, "utf8");
    const index = content.indexOf(text);
    if (index >= 0) matches.push([path, content.slice(0, index).split("\n").length]);
    else if (content.split(/\s+/).join(" ").includes(snippet)) matches.push([path, null]);
    if (matches.length >= 5) break;
  }
  return matches;
}

function formatReferencePaths(paths: Array<[string, number | null]>): string {
  const rendered = paths.slice(0, 3).map(([path, line]) => (line ? `${path}:${line}` : path));
  return `${rendered.join(", ")}${paths.length <= 3 ? "" : `, ...(+${paths.length - 3})`}`;
}

export function printCompactReferences(repo: string, answer: JsonObject): void {
  if (!Array.isArray(answer.references) || !answer.references.length) return;
  const sources = activeSourcesById(repo);
  const rows: string[] = [];
  const seen = new Set<string>();
  for (const ref of answer.references) {
    if (!ref || typeof ref !== "object") continue;
    const num = String(ref.citation_number || "").trim();
    if (!num || seen.has(num)) continue;
    seen.add(num);
    const source = sources.get(String(ref.source_id || ""));
    const paths = source ? referencePathCandidates(repo, source, String(ref.cited_text || "")) : [];
    if (paths.length) rows.push(`[${num}] ${formatReferencePaths(paths)}`);
  }
  if (rows.length) console.log(`\nreferences:\n${rows.join("\n")}`);
}

export function extractCandidates(text: string, query: string): [string[], string[]] {
  const paths = [...new Set([...text.matchAll(PATH_RE)].map((match) => match[0]))].sort();
  const terms = new Set<string>();
  for (const raw of `${text}\n${query}`.matchAll(TERM_RE)) {
    const term = raw[0].replace(/^[`'"]|[`'"]$/g, "");
    if (term.length < 4 || STOP_TERMS.has(term.toLowerCase()) || term.includes("/") || term.includes(".")) continue;
    terms.add(term);
  }
  return [paths, [...terms].sort().slice(0, 24)];
}

function highSignalTerms(terms: string[]): string[] {
  const selected = terms.filter((term) => !STOP_TERMS.has(term.toLowerCase()) && (term.includes("_") || term.includes("-") || /[A-Z]/.test(term.slice(1)) || term.length >= 14));
  return selected.length ? selected : terms.filter((term) => !STOP_TERMS.has(term.toLowerCase())).slice(0, 8);
}

function rgRoots(repo: string, config: JsonObject, candidates: string[]): string[][] {
  const candidateRoots = candidates.filter((path) => existsSync(join(repo, path)));
  const roots = includeSpecs(config).filter((spec) => existsSync(join(repo, spec)));
  return [...(candidateRoots.length ? [candidateRoots] : []), roots.length ? roots : ["."]];
}

function parseRgMatches(stdout: string, seen: Set<string>, remaining: number): JsonObject[] {
  const matches: JsonObject[] = [];
  for (const line of stdout.split("\n")) {
    if (matches.length >= remaining) break;
    const parts = line.split(":", 3);
    if (parts.length !== 3) continue;
    const [path, lineNo, text] = parts;
    const key = `${path}\0${lineNo}\0${text.trim()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    matches.push({ path, line: /^\d+$/.test(lineNo) ? Number(lineNo) : lineNo, text: text.trim() });
  }
  return matches;
}

export async function localRg(repo: string, config: JsonObject, terms: string[], candidatePaths: string[] = []): Promise<JsonObject[]> {
  if (!terms.length || !which("rg")) return [];
  const signal = highSignalTerms(terms);
  const pattern = signal.slice(0, 16).map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const maxMatches = Number(config.retrieval?.max_local_matches || 80);
  const matches: JsonObject[] = [];
  const seen = new Set<string>();
  for (const roots of rgRoots(repo, config, candidatePaths)) {
    const remaining = maxMatches - matches.length;
    if (remaining <= 0) break;
    const result = await runCommand(["rg", "-n", "-S", "-e", pattern, "--", ...roots], repo, { timeout: 120 });
    if (![0, 1].includes(result.returncode)) return [{ error: result.stderr.trim() }];
    matches.push(...parseRgMatches(result.stdout, seen, remaining));
  }
  return matches;
}

export function freshnessWarning(freshness: JsonObject): string | null {
  const status = String(freshness.status || "");
  if (status === "stale-throttled") {
    const changed = Array.isArray(freshness.relevant_changed_paths) ? freshness.relevant_changed_paths : [];
    const preview = changed.length ? `; changed=${changed.slice(0, 5).join(", ")}${changed.length <= 5 ? "" : `, ...(+${changed.length - 5})`}` : "";
    const age = freshness.uploaded_age_seconds !== undefined ? `; uploaded_age_seconds=${freshness.uploaded_age_seconds}` : "";
    return `warning: index is stale-throttled${age}${preview}; provider answer may lag local changes. Use --force-refresh or refresh --force if needed.`;
  }
  if (status === "needs-first-upload-approval") return "warning: first broad upload requires approval; rerun with --yes or run refresh explicitly.";
  if (status === "auto-refresh-disabled") return "warning: auto refresh is disabled; provider answer may lag local changes.";
  return null;
}

export function providerBlockMessage(freshness: JsonObject): string | null {
  if (freshness.status === "not-initialized") return "skipped; project is not initialized for project retrieval.";
  if (freshness.status === "needs-first-upload-approval") return "skipped; first broad upload requires approval. Rerun ask/locate with --yes or run refresh explicitly.";
  return null;
}

export function firstUploadNext(repo: string, command: string, query: string): JsonObject {
  return {
    [`${command}WithFirstUploadApproval`]: commandLine(repo, command, "--yes", query),
    refresh: commandLine(repo, "refresh", "--force"),
  };
}

export function providerBlockPayload(freshness: JsonObject, nextSteps?: JsonObject): JsonObject {
  const next = freshness.next || nextSteps;
  return { error: true, message: providerBlockMessage(freshness) || "skipped", ...(next ? { next } : {}) };
}

export async function locate(repo: string, query: string, opts: { forceRefresh?: boolean; yes?: boolean; json?: boolean; includeProviderAnswer?: boolean }): Promise<JsonObject> {
  const freshness = await ensureIndex(repo, { force: opts.forceRefresh, yes: opts.yes, jsonOutput: opts.json, command: "locate", returnUninitialized: true });
  const blocked = providerBlockMessage(freshness);
  if (blocked) {
    const next = freshness.next || (freshness.status === "needs-first-upload-approval" ? firstUploadNext(repo, "locate", query) : undefined);
    return {
      freshness,
      notebooklm_candidates: { paths: [], existing_paths: [], terms: [] },
      local_line_refs: [],
      provider_misses_or_stale_paths: [],
      provider_answer: `(${blocked})`,
      claim_boundary: "Semantic provider was not called because retrieval preflight is blocked.",
      ...(next ? { next } : {}),
    };
  }
  const prompt = `Find the code location for this repository question. Return likely repo paths, function names, test names, command names, and keywords for rg. If exact line numbers are unavailable, say so. Question: ${query}`;
  const provider = await askProvider(repo, prompt);
  const [paths, terms] = extractCandidates(answerText(provider), query);
  const [config] = loadConfig(repo, "locate");
  const existing = paths.filter((path) => existsSync(join(repo, path)));
  return {
    freshness,
    notebooklm_candidates: { paths, existing_paths: existing, terms },
    local_line_refs: await localRg(repo, config, terms, existing),
    provider_misses_or_stale_paths: paths.filter((path) => !existsSync(join(repo, path))),
    provider_answer: opts.includeProviderAnswer ? provider : "(hidden; pass --include-provider-answer)",
    claim_boundary: "Line refs come from local rg results, not NotebookLM.",
  };
}
