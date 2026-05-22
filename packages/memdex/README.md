# memdex

CLI package for `memdex`.

It is implemented in TypeScript, uses Commander for the command surface, and is
bundled with Bun into an npm `bin` entry named `memdex`.

## Requirements

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

Release and CI details live in the repository-level
[release process](https://github.com/yoyooyooo/memdex/blob/main/docs/release.md).
