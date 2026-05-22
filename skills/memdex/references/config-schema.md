# Memdex Config Schema

The project config lives in `.memdex/config.json` by default. JSON is
the default because it works with Python stdlib. YAML may be read only when
PyYAML is installed.

## `config.json`

```json
{
  "version": 1,
  "project": {
    "name": "repo-name"
  },
  "provider": "notebooklm",
  "notebooklm": {
    "notebook_id": "",
    "notebook_title_prefix": "memdex",
    "notebook_title": "memdex:repo-name",
    "source_title_prefix": "memdex",
    "temporary_source_title_prefix": "memdextmp",
    "wait_after_upload": true,
    "upload_parallelism": 4,
    "wait_parallelism": 8,
    "delete_parallelism": 4
  },
  "bundle": {
    "tool": "repomix",
    "mode": "chunked",
    "include": [
      "src",
      "crates",
      "packages",
      "apps",
      "bins",
      "docs",
      "scripts",
      "tests",
      "xtask",
      "AGENTS.md",
      "CLAUDE.md",
      "README.md",
      "Cargo.toml",
      "package.json",
      "justfile"
    ],
    "output": ".memdex/cache/{prefix}-{timestamp}.txt",
    "style": "",
    "compress": false,
    "target_chunk_bytes": 524288,
    "max_chunk_bytes": 900000,
    "source_title_template": "{prefix}--{set}--{group}--{chunk}--{hash}.md",
    "groups": [
      { "id": "docs", "include": ["AGENTS.md", "CLAUDE.md", "README.md", "docs/**"] },
      { "id": "apps", "include": ["apps/**"] },
      { "id": "packages", "include": ["packages/**"] },
      { "id": "src", "include": ["src/**", "crates/**", "bins/**", "xtask/**"] },
      { "id": "tests", "include": ["tests/**", "testdata/**"] },
      { "id": "scripts", "include": ["scripts/**"] }
    ],
    "default_group": {
      "enabled": true,
      "id": "misc"
    }
  },
  "refresh": {
    "auto": true,
    "mode": "replace",
    "check_ttl_seconds": 300,
    "min_upload_interval_seconds": 900,
    "max_staleness_seconds": 86400,
    "keep_previous_sources": 0,
    "delete_previous_after_success": true
  },
  "safety": {
    "require_user_approval_first_upload": true,
    "never_upload": [
      ".env*",
      "**/.env*",
      ".git/**",
      "**/.git/**",
      "node_modules/**",
      "**/node_modules/**",
      "target/**",
      "**/target/**",
      "dist/**",
      "**/dist/**",
      "build/**",
      "**/build/**",
      "coverage/**",
      "**/coverage/**",
      ".generated/**",
      "**/.generated/**",
      "public/**",
      "**/public/**",
      "*.png",
      "**/*.png",
      "*.jpg",
      "**/*.jpg",
      "*.jpeg",
      "**/*.jpeg",
      "*.gif",
      "**/*.gif",
      "*.webp",
      "**/*.webp",
      "*.svg",
      "**/*.svg",
      "*.ico",
      "**/*.ico",
      "*.otf",
      "**/*.otf",
      "*.ttf",
      "**/*.ttf",
      "*.woff",
      "**/*.woff",
      "*.woff2",
      "**/*.woff2",
      "*.mp4",
      "**/*.mp4",
      "*.mov",
      "**/*.mov",
      "*.zip",
      "**/*.zip",
      "*.tar",
      "**/*.tar",
      "*.gz",
      "**/*.gz"
    ]
  },
  "retrieval": {
    "line_numbers_require_local_verify": true,
    "max_local_matches": 80
  }
}
```

## State

`.memdex/state.local.json` is local-only and should not be committed.

```json
{
  "lastCheckedAt": "2026-05-20T08:40:00Z",
  "lastUploadedAt": "2026-05-20T08:36:00Z",
  "lastConfigSha256": "sha256:...",
  "lastCheckedFastFingerprint": "sha256:...",
  "lastUploadedFastFingerprint": "sha256:...",
  "lastFastFingerprint": "sha256:...",
  "lastBundleSetSha256": "sha256:...",
  "lastBundleSha256": "sha256:...",
  "lastBundlePath": null,
  "activeSourceSet": {
    "id": "2605200912",
    "prefix": "memdex",
    "bundleSetSha256": "sha256:...",
    "uploadedAt": "2026-05-20T08:36:00Z",
    "sources": [
      {
        "id": "source-id",
        "title": "memdex--2605200912--docs--001--a1b2c3d4.md",
        "group": "docs",
        "chunk": "001",
        "chunkKey": "docs/001",
        "chunkSha256": "sha256:...",
        "contentSha256": "sha256:...",
        "fileListSha256": "sha256:...",
        "fileCount": 42,
        "files": ["docs/a.md", "docs/b.md"],
        "status": "ready",
        "uploadedAt": "2026-05-20T08:36:00Z"
      }
    ]
  },
  "sources": [
    {
      "id": "source-id",
      "title": "repo-20260520T083600Z.txt",
      "bundleSha256": "sha256:...",
      "uploadedAt": "2026-05-20T08:36:00Z"
    }
  ],
  "temporarySourceSets": [
    {
      "id": "2605201310",
      "kind": "flashcard",
      "purpose": "retry design",
      "createdAt": "2026-05-20T13:10:00Z",
      "expiresAt": "2026-05-20T14:10:00Z",
      "sources": [
        {
          "id": "temp-source-id",
          "title": "memdextmp--2605201310--notes--retry-design--a1b2c3d4.md",
          "contentSha256": "sha256:...",
          "uploadedAt": "2026-05-20T13:10:00Z",
          "status": "ready",
          "origin": {
            "activeSourceSetId": "2605200912",
            "chunkKeys": ["packages/001"],
            "filePaths": ["packages/billing/src/retry.ts"]
          }
        }
      ]
    }
  ]
}
```

## Folder Policy

Commit:

```text
.memdex/config.json
.memdex/.gitignore
```

Do not commit:

```text
.memdex/state.local.json
.memdex/pending-upload.local.json
.memdex/cache/**
```

Repomix bundles are upload intermediates. They are written under
`.memdex/cache/` and deleted after bundle-set hash comparison or
provider source upload. State should keep `lastBundleSetSha256` and
`activeSourceSet`, not depend on retained chunk files.

`pending-upload.local.json` is a transaction journal for interrupted chunked
uploads. It records newly uploaded source IDs before `activeSourceSet` is
committed. On the next locked run, sources recorded there are deleted unless
they already appear in the active source set.

`state.local.json` may include `cleanupPendingSourceIds`. These are retired
NotebookLM source IDs from a successfully committed previous active set. The
next locked run retries deletion, removes successfully deleted IDs from state,
and keeps failures for a later retry.

Chunked refresh is incremental. Each source records rendered chunk
`contentSha256` plus `fileListSha256` and `files`. The next plan keeps previous
whole-file chunk membership when it still fits under `target_chunk_bytes`; a
single file may still occupy a larger chunk up to `max_chunk_bytes`. Chunks with
identical `contentSha256` reuse the previous ready NotebookLM source ID. Only
changed or new chunks are uploaded.

Temporary source sets are derived NotebookLM materials, for example flashcard
seeds created from selected chunks/files. They are independent from
`activeSourceSet` and are not used by `ask` or `locate` unless a future command
explicitly opts in. Cleanup uses recorded source IDs as authority. Titles that
match the temporary prefix but are absent from `temporarySourceSets` are
reported as untracked prefix matches and are not deleted by default.

## Provider Boundary

The first provider is NotebookLM. The config keeps `provider` explicit so a
future local embedding index, Sourcegraph, DeepWiki, or other retrieval backend
can reuse the project-level freshness and line-verification workflow.

## Notebook Naming

Default NotebookLM title:

```text
memdex:<project.name>
```

The title is a human-facing binding between a repository and its NotebookLM
notebook. The notebook ID remains the execution authority. If the config is
lost or copied to another checkout, `init --reuse-existing-notebook` can recover
the ID by exact title match.

Source title prefix:

```text
memdex
memdex--<YYMMDDHHmm>--<group>--<chunk>--<hash>.md
memdextmp--<YYMMDDHHmm>--<kind>--<slug>--<hash>.md
```

The NotebookLM notebook title already identifies the project. Source titles stay
short and filterable by tool prefix, upload set, group, chunk, kind, and hash.
