# Codebase Retrieve Workflow

## Mental Model

```text
repo checkout
  -> project config
  -> project-bound NotebookLM title
  -> deterministic whole-file chunk plan
  -> repomix renders each chunk with --stdin
  -> provider source set upload
  -> semantic Q&A candidates
  -> local rg/sed/nl verification
  -> answer with exact local file refs
```

The provider is a semantic locator, not an authority layer. The local checkout
owns exact files, symbols, line numbers, current diff state, and runnable
evidence.

## Notebook Identity

Default NotebookLM title:

```text
codebase-retrieve:<project_name>
```

Default source title prefix:

```text
cbr
cbr--<YYMMDDHHmm>--<group>--<chunk>--<hash>.md
```

The title is for human routing and recovery. The notebook ID in
`.codebase-retrieve/config.json` is still the authority for CLI calls.

The `codebase-retrieve` CLI is distributed from the npm package under
`packages/codebase-retrieve`. From this monorepo, use `bun run cbr -- <args>`.
When installed as a package, use `codebase-retrieve <args>`.

If `notebooklm` is missing, install it persistently before init:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
```

Init options:

```bash
codebase-retrieve init --repo . --create-notebook
codebase-retrieve init --repo . --reuse-existing-notebook
codebase-retrieve init --repo . --notebook-id <id>
```

`--create-notebook` first reuses an exact title match, then creates a new
NotebookLM notebook if no match exists. `--reuse-existing-notebook` only reuses
an exact title match and does not create cloud state.

Commands that need project config should guide uninitialized repos toward
`init --create-notebook`, `init --reuse-existing-notebook`, or `init --notebook-id`
instead of returning only a missing file path.

In `--json` mode, uninitialized repositories should return a structured
`freshness.status=not-initialized` payload and skip provider calls. Plain output
should show copyable init / ask / locate commands. `ask` and `locate` are the
default agent-facing entries; `status` and `ensure` are maintenance/debug
commands, not mandatory preflight calls.

## Freshness Algorithm

`ask`, `locate`, `ensure`, and `refresh` share the same freshness preflight.
The standalone `ensure` command is useful for prewarming or maintenance, but
agents should not run it before every Q&A turn. The preflight uses a cheap
fingerprint before running repomix:

```text
config sha
git HEAD
relevant dirty / staged / untracked paths
sha256 of relevant dirty / untracked file content
```

If the fingerprint and TTL are still valid, it skips upload. If a relevant file
changed, it builds a deterministic whole-file chunk plan, renders each chunk
with repomix, and hashes the bundle set. If the bundle-set hash is unchanged, it
updates local state without uploading. If the bundle-set hash changed and
refresh policy allows upload, it builds the next provider source set from reused
unchanged sources plus newly uploaded changed chunks.

The bundle-set hash is the final upload dedupe check. Git state is only a fast
"could have changed" signal.

Chunk files are temporary upload intermediates. They are written under
`.codebase-retrieve/cache/` and deleted after hash comparison or upload. Local
state retains `lastBundleSetSha256` plus `activeSourceSet`; it should not rely on
long-lived bundle paths.

`ensure`, `refresh`, `ask`, and `locate` take `.codebase-retrieve/.lock` before
reading / writing state or uploading sources. Concurrent requests wait on the
same repo-local lock instead of racing into duplicate NotebookLM uploads.

## Upload And Prune

Default mode is `replace`:

1. Build a whole-file chunk plan from `include`, `safety.never_upload`, and
   `bundle.groups`.
2. Prefer previous active chunk membership when the same files still exist and
   the chunk remains under `max_chunk_bytes`; this limits boundary churn.
3. Render each chunk with `repomix --stdin` and compute `contentSha256`.
4. Reuse previous ready NotebookLM sources with identical `contentSha256`.
5. Upload changed/new chunks with bounded `upload_parallelism`.
6. Wait for uploaded sources with bounded `wait_parallelism` when
   `wait_after_upload` is true.
7. Commit `activeSourceSet` only after all required chunks upload and process
   successfully.
8. Record retired old source IDs in `cleanupPendingSourceIds` after the new
   active set is written. Cleanup is retried from state on the next locked run
   with bounded `delete_parallelism`; cleanup failure leaves the remaining IDs
   in state and does not roll back the ready index.

Never delete unrelated NotebookLM sources by title glob alone.

During upload, `.codebase-retrieve/pending-upload.local.json` records newly
created source IDs. If the process is interrupted before state commit, the next
locked run deletes those partial sources before planning a new upload. If the
journal points at sources already present in `activeSourceSet`, it is discarded.

After a successful active-set commit, `state.local.json` may contain
`cleanupPendingSourceIds`. Those IDs are owned retired sources. Each locked run
attempts to delete them before freshness checks; successful deletes are removed
from the list, failed deletes remain for the next run.

`pack --dry-run` prints the planned group/chunk/source-title map without
building bundle files or uploading. Add `--include-files` when the exact file
membership is needed.

## Temporary Sources

Use `temp-source` for derived materials such as flashcard seeds that should live
in the same NotebookLM notebook but not become part of the repo index:

```bash
codebase-retrieve temp-source upload --repo . \
  --kind notes --title retry-design --file /tmp/retry-design.md \
  --origin-chunk packages/001 --origin-file packages/billing/src/retry.ts

codebase-retrieve temp-source list --repo .
codebase-retrieve temp-source cleanup --repo . --kind notes --yes
```

Temporary source titles use:

```text
cbrtmp--<YYMMDDHHmm>--<kind>--<slug>--<hash>.md
```

`temporarySourceSets` in local state owns deletion. Cleanup deletes only source
IDs recorded there by default. NotebookLM sources that merely match the
temporary prefix are reported as `untrackedPrefixMatches`; they are likely
manual uploads or state drift and must not be deleted unless the user explicitly
passes `--include-untracked-prefix --yes`.

## Ask Mode

Use `ask` for broad repo questions:

```bash
codebase-retrieve ask --repo . \
  "Which docs define retry and backoff behavior?"
```

Plain output hides full freshness by default and prints only stale / blocked
refresh warnings. `--json` always includes full `freshness`; `--verbose` prints
freshness in plain output. For implementation claims, open local files before
finalizing.

`ask` runs freshness preflight internally. If preflight returns
`not-initialized` or `needs-first-upload-approval`, `ask` prints short guidance
and skips the provider call. If the first broad upload is already approved,
rerun `ask --yes ...`.

When an active source set exists, provider calls pass only its ready source IDs
with `-s`. This prevents stale or failed NotebookLM sources in the same notebook
from contaminating retrieval.

## Locate Mode

Use `locate` when the user asks for code files, functions, tests, or line refs:

```bash
codebase-retrieve locate --repo . \
  "where is invoice export retry implemented?"
```

Loop:

```text
1. Run freshness preflight.
2. Stop with guidance if setup or first-upload approval is blocked.
3. Ask provider for likely paths, function names, test names, and rg keywords.
4. Extract candidate paths and terms.
5. Run local rg -n over candidate terms.
6. Verify file existence and line context locally.
7. Return local file:line refs.
8. Flag provider stale paths or hallucinated files.
```

NotebookLM often cannot supply exact repository line numbers from source
chunks. Do not repeat line numbers unless local search verified them.

## Failure Handling

Provider stale or hallucinated path:

```text
Report it as stale/unverified.
Run local rg --files / rg -n to recover.
```

Provider returns prose only:

```text
Ask a narrower follow-up for paths, function names, tests, command names, and rg keywords.
```

Provider times out:

```text
Fall back to local rg.
Say semantic provider did not add signal.
```

First upload blocked:

```text
ask / locate skip provider calls. Ask for explicit approval, or rerun with --yes
when the user already authorized broad upload.
```

Upload too soon:

```text
Respect min_upload_interval_seconds unless --force is explicit or max_staleness_seconds is exceeded.
```

## Output Contract

Machine output for semantic Q&A:

```text
freshness:
provider_query:
provider_answer:
local_verification:
claim_boundary:
```

Human-oriented plain output should not print the full `freshness` object unless
`--verbose` is set. It may print a one-line freshness warning when the provider
answer may lag local changes.

For code location:

```text
freshness:
notebooklm_candidates:
local_line_refs:
provider_misses_or_stale_paths:
claim_boundary:
```
