# Contributing

Thanks for helping improve `codebase-retrieve`.

## Development

Run the local test suite before sending changes:

```bash
bun install
bun run test
bun run check
```

The control-plane script should stay Python-stdlib-only. External tools may be
called through subprocess boundaries when needed.

## Safety Rules

- Do not add code paths that upload `.env*`, credentials, raw private logs,
  production exports, dependency folders, build output, generated caches, or
  unreviewed user data.
- Do not treat NotebookLM as authority for exact files, line numbers, test
  results, or implementation status.
- Do not delete NotebookLM sources unless they are source IDs recorded by this
  tool or the user explicitly selects them.

## Provider Boundary

NotebookLM integration depends on the community `notebooklm-py` CLI and
unofficial NotebookLM behavior. Keep failure messages explicit and prefer local
verification over provider claims.
