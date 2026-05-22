# memdex

CLI package for `memdex`.

It wraps a Python stdlib control-plane script with an npm `bin` entry named
`memdex`.

## Requirements

- Python 3.10+
- `git`
- `rg`
- `repomix` or `npx repomix`
- `notebooklm` CLI from `notebooklm-py`

## Usage

```bash
memdex init --repo /path/to/repo --create-notebook
memdex ask --repo /path/to/repo "Where is retry/backfill documented?"
memdex locate --repo /path/to/repo "invoice export retry command"
```

From the monorepo checkout:

```bash
bun run memdex -- --help
bun run memdex -- ask --repo /path/to/repo "Where is retry/backfill documented?"
```

Set `PYTHON` to choose a specific Python executable.

Release and CI details live in the repository-level
[release process](https://github.com/yoyooyooo/memdex/blob/main/docs/release.md).
