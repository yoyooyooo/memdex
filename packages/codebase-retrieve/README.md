# @yoyooyooo/codebase-retrieve

CLI package for `codebase-retrieve`.

It wraps a Python stdlib control-plane script with an npm `bin` entry named
`codebase-retrieve`.

## Requirements

- Python 3.10+
- `git`
- `rg`
- `repomix` or `npx repomix`
- `notebooklm` CLI from `notebooklm-py`

## Usage

```bash
codebase-retrieve init --repo /path/to/repo --create-notebook
codebase-retrieve ask --repo /path/to/repo "Where is retry/backfill documented?"
codebase-retrieve locate --repo /path/to/repo "invoice export retry command"
```

From the monorepo checkout:

```bash
bun run cbr -- --help
bun run cbr -- ask --repo /path/to/repo "Where is retry/backfill documented?"
```

Set `PYTHON` to choose a specific Python executable.

Release and CI details live in the repository-level
[release process](https://github.com/yoyooyooo/codebase-retrieve/blob/main/docs/release.md).
