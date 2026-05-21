# Project Semantic Database

Use this workflow when the user wants to use NotebookLM as a repository,
workspace, or document-set retrieval database.

NotebookLM is a semantic retrieval database, not an authority layer. It is useful
for discovering relevant files, concepts, historical decisions, and cross-source
relationships. It does not replace local files, tests, command output, issue
trackers, or project documentation authority. Any implementation, completion,
status, evidence, or architecture claim must be verified against the project
working tree or the project's declared source of truth.

## When To Use

Use this workflow for:

- repository-wide semantic search;
- architecture and module relationship discovery;
- finding where a feature, concept, decision, or workflow is documented;
- locating likely implementation files, functions, tests, command routes, and
  docs for a codebase question;
- summarizing large local project documentation sets;
- building a project memory notebook that future agents can query quickly.

Do not use it as the sole source for:

- completion claims;
- security, legal, medical, or financial decisions;
- current git diff truth;
- exact API signatures, line numbers, test results, or generated evidence;
- existence of a file path, symbol, or function before local verification;
- private/raw data unless the user has explicitly approved upload.

## Notebook Registry

### Project Notebook Resolution Gate

Before asking NotebookLM about a repository or project, resolve the project
notebook first. Wrong-corpus answers are worse than no answers because they look
plausible and waste the local verification pass.

1. Determine the project key from the checked-out git root basename or current
   working directory basename. If the user gave a notebook ID, title, or corpus
   name, use that instead.
2. Run `notebooklm status` and `notebooklm list --json`.
3. Prefer an explicit notebook ID recorded in `AGENTS.md`, `CLAUDE.md`,
   `README.md`, or a local docs index.
4. Otherwise match existing notebooks by stable title, in this order:
   `codebase-retrieve:<project>`, `repo: <project>`,
   `project-db: <project>`, then exact project-name title.
5. If the status context is missing or points to another project/corpus, switch
   to the resolved notebook with `notebooklm use <notebook_id>`.
6. Still pass `-n <notebook_id>` / `--notebook <notebook_id>` on project
   commands. The explicit flag is the authority; `use` only protects the
   interactive/default context.
7. If no notebook exists, create one using `codebase-retrieve:<project>` for
   codebase retrieval. Report the created ID and switch to it.
8. Run `notebooklm source list -n <notebook_id> --json` to identify whether the
   notebook is empty, stale, or already sourced. Do not upload a broad local
   snapshot unless the user requested or approved that upload.

Use this resolved notebook ID in `queries_run` and mention any mismatch or
empty/stale source condition in the final claim boundary.

Create one notebook per durable project or corpus. For codebase retrieval, use
`codebase-retrieve:<project>` as the default stable title. If the project guide
already records another canonical title or notebook ID, follow that record
instead.

Report newly created notebook IDs back to the user. Suggest recording the ID in
the project's local agent guide if the user wants future sessions to reuse it.

Avoid relying on `notebooklm use` in shared or parallel workflows. Prefer
explicit notebook IDs.

## Source Scope

For source-code repositories, add a generated bundle or a small set of canonical
files. Prefer a reproducible source list:

```bash
npx repomix --include "src,crates,packages,apps,bins,docs,scripts,tests,xtask,AGENTS.md,CLAUDE.md,README.md,Cargo.toml,package.json,justfile" --output notebooklm-repo.txt
notebooklm source add notebooklm-repo.txt -n <notebook_id>
```

Adapt the include list to the host project. Exclude secrets, `.env*`,
credentials, private user data, raw production exports, `.git`, dependency
folders, build outputs, generated caches, and huge binary artifacts.

If a repo has an existing bundle command, use that instead of inventing a new
one. If `repomix` is unavailable, add the important docs and source files
directly, or ask the user before installing tooling.

### Codebase Bundle Checklist

When the notebook is meant to act as a codebase Q&A index, capture enough
structure for semantic search without uploading secrets or generated noise:

```text
include:
  source roots: src, crates, packages, apps, bins, cmd, internal, lib
  tests and fixtures when useful: tests, testdata, fixtures
  docs and agent guides: docs, AGENTS.md, CLAUDE.md, README.md
  repo command/config anchors: Cargo.toml, package.json, pnpm-workspace.yaml,
    bun.lock, justfile, Makefile, scripts, xtask

exclude:
  .git, node_modules, target, dist, build, coverage, caches
  .env*, credentials, keys, production exports, raw private logs
  huge binaries and unreviewed user data
```

If the project records a canonical include list in its agent guide or docs, use
that list. Report the source scope and whether it is a fresh snapshot or an
older upload.

## Refresh Policy

NotebookLM source uploads are snapshots. Treat answers as potentially stale.

Use one of these refresh modes:

- Append snapshot: add a new dated source when history matters or deletion is
  not approved.
- Replace snapshot: list sources, delete only the previous project bundle(s),
  then add the new bundle.

Deletion is destructive. Do not delete sources unless the user explicitly asked
for refresh/replace or confirmed deletion. Prefer exact title filters or source
IDs, and never bulk-delete unrelated sources.

Refresh template:

```bash
notebooklm source list -n <notebook_id> --json
npx repomix --include "<project-specific include list>" --output notebooklm-repo.txt
notebooklm source add notebooklm-repo.txt -n <notebook_id>
```

For replace refresh, do not pipe deletion blindly. First list sources, identify
only the previous project bundle(s), and delete by explicit ID or exact title
after the user has asked for refresh/replace or approved deletion.

## Retrieval Loop

For project retrieval, use this loop:

```text
0. Resolve the current project notebook and source freshness.
1. Ask NotebookLM broad semantic questions against the resolved notebook ID.
2. Use --json when references matter.
3. Extract referenced source titles / snippets.
4. Re-open local files with rg, sed, or the host editor.
5. Verify against the project's authority order.
6. Only then implement, summarize, or claim evidence.
```

## Code Location And Line-Number Loop

Use this stricter loop when the user asks "where is this implemented?", "find
the code", "give file and line", or similar:

```text
1. Ask NotebookLM for likely paths, functions, tests, command names, and rg keywords.
2. Treat returned paths and symbols as candidates, not facts.
3. Run local search with rg -n over the candidate terms and likely roots.
4. Open the matched files with sed or nl -ba to verify surrounding code.
5. Return clickable local file links with exact line numbers from local files.
6. Flag any NotebookLM misses, stale paths, hallucinated files, or line-number limits.
```

NotebookLM source chunks often do not preserve repository line numbers. It may
summarize nearby code correctly while lacking exact line data, and it can
occasionally name a stale or nonexistent file from a broad source bundle. Exact
line references must therefore come from the checked-out worktree.

Good code-location query:

```bash
notebooklm ask "Where is the invoice export retry command implemented? Return likely repo paths, function names, test names, and keywords I can pass to rg. If line numbers are unavailable, say so." -n <notebook_id> --json
```

Then verify locally:

```bash
rg -n "invoice export|retry|RetryPolicy|backoff" justfile xtask src crates packages apps bins docs
nl -ba packages/billing/src/retry.ts | sed -n '1,140p'
```

Good queries:

```bash
notebooklm ask "Where is the current authority for API error handling? Cite files and summarize boundaries." -n <notebook_id> --json
notebooklm ask "Which docs define release readiness checks? Return likely file paths and keywords to rg." -n <notebook_id> --json
notebooklm ask "Find modules related to websocket reconnect/backfill and list local symbols or tests to inspect." -n <notebook_id> --json
```

After retrieval, verify locally:

```bash
rg -n "API error|release readiness|reconnect|backfill" .
```

## Failure Handling

If NotebookLM returns:

- an answer from the wrong notebook/corpus: discard it, report the mismatch if
  relevant, resolve the project notebook, and rerun or fall back locally;
- candidate paths that do not exist locally: report them as stale or
  unverified, then use `rg --files` / `rg -n` to recover;
- broad prose without paths: ask a narrower follow-up for paths, function names,
  test names, command names, and grep keywords;
- no answer: fall back to local search and say NotebookLM did not add signal;
- line numbers: verify them locally before repeating them;
- answers based on old uploads: refresh the source only if the user wants an
  updated project snapshot and deletion/upload is safe.

## Output Contract

When using NotebookLM as a project semantic database, report:

```text
notebook_id:
source_scope:
freshness:
queries_run:
local_files_verified:
authority_status:
claim_boundary:
```

For code-location tasks, include:

```text
notebooklm_candidates:
local_line_refs:
notebooklm_misses_or_stale_paths:
```

Keep the user-facing summary short when the task is simple. Always state when a
claim is only NotebookLM-derived and still needs local verification.
