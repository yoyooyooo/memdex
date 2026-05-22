---
name: notebooklm
description: Automate NotebookLM notebooks, sources, chat, generated artifacts, and project semantic-database retrieval. Use when the user mentions NotebookLM, asks to create/search/update a NotebookLM project index, treats NotebookLM as a repo/database retrieval layer, uploads a source set for Q&A, asks where code/docs live, or wants semantic search followed by local file/line verification. Also use for NotebookLM podcasts, summaries, quizzes, flashcards, reports, videos, slide decks, mind maps, or downloads.
---

# NotebookLM Automation

Use NotebookLM for document and repository retrieval, source management, chat,
and generated artifacts. Keep this file as the routing surface; load references
only when the task needs them.

## Setup Gate

If `notebooklm` is missing, install it as a persistent uv tool instead of from a
temporary `/tmp` project:

```bash
uv tool install git+https://github.com/teng-lin/notebooklm-py.git
```

Before real work:

```bash
notebooklm status
notebooklm list --json
```

If auth fails:

```bash
notebooklm login
notebooklm auth check --test
```

For project/repo semantic retrieval, resolve the current project notebook before
asking any question:

1. Determine the project key from the git root or current working directory
   basename, unless the user gave an explicit notebook ID/title.
2. Prefer a notebook ID recorded in `AGENTS.md`, `CLAUDE.md`, `README.md`, or a
   local docs index.
3. Otherwise match `notebooklm list --json` by stable project titles such as
   `memdex:<project>`, `repo: <project>`, or
   `project-db: <project>`.
4. If the current `notebooklm status` context is a different project/corpus, do
   not query it. Switch first with `notebooklm use <notebook_id>`.
5. Even after switching, pass `-n <notebook_id>` / `--notebook <notebook_id>` on
   every command that supports it.
6. If no project notebook exists, create one with a stable title, report the ID,
   switch to it, and then check sources. Do not broad-upload local files unless
   the user requested/approved the upload.

For parallel agents, avoid shared implicit context. Prefer explicit notebook IDs
with `-n <notebook_id>` / `--notebook <notebook_id>`, or isolate with
`NOTEBOOKLM_PROFILE` / `NOTEBOOKLM_HOME`.

## Route

- Project/repo semantic retrieval: read
  [Project Semantic Database](references/project-semantic-database.md).
- CLI syntax, artifact generation, downloads, JSON formats, and errors: read
  [CLI Reference](references/cli-reference.md).

## Common Commands

```bash
notebooklm create "memdex:<project>"
notebooklm use <notebook_id>
notebooklm source add ./file.md -n <notebook_id>
notebooklm source list -n <notebook_id> --json
notebooklm ask "Where is this documented? Return likely files and keywords." -n <notebook_id> --json
notebooklm generate report --format briefing-doc -n <notebook_id> --json
notebooklm artifact list -n <notebook_id> --json
notebooklm download report ./report.md -n <notebook_id> -a <artifact_id>
```

## Autonomy

Run without asking:

- read/status/list commands;
- notebook creation requested by user, or required by the project notebook
  resolution gate for the current project;
- adding explicitly provided sources;
- chat queries without `--save-as-note`;
- source/artifact/research wait in a delegated or background context.

Ask first:

- deleting notebooks, sources, notes, or artifacts;
- broad local uploads, private docs, raw exports, credentials, or `.env*`;
- long-running generation in the main conversation;
- downloads that write files unless user already requested the artifact;
- chat/history operations that save notes.

## Authority Rule

NotebookLM is discovery, not authority. For project work, verify all claims
against local files, tests, command output, issue trackers, or the project’s
declared source of truth before implementing or reporting evidence.

For project retrieval, treat NotebookLM as a semantic locator. It can find
likely files, functions, tests, concepts, and docs, but exact paths and line
numbers must be verified locally with `rg -n`, `sed`, `nl -ba`, or the host
editor. If NotebookLM returns a missing file, stale source, or unsupported line
number claim, say so and fall back to local search.
