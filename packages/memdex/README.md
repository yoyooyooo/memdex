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
memdex ask --repo-worktree main "Where is retry/backfill documented?"
```

From the monorepo checkout:

```bash
bun run memdex -- --help
bun run memdex -- ask --repo /path/to/repo "Where is retry/backfill documented?"
bun run memdex -- ask --repo-worktree main "Where is retry/backfill documented?"
```

`--repo-worktree <branch>` is for queries launched from a lightweight Git
worktree. It resolves the checked-out worktree for that branch and reuses its
`.memdex` config/state without auto-refresh. Pass `--force-refresh` only when
you intentionally want to update that indexed branch worktree. If cwd is not
inside a Git worktree, or the branch is not checked out as a worktree, rerun
with `--repo /path/to/indexed-worktree`.

Release and CI details live in the repository-level
[release process](https://github.com/yoyooyooo/memdex/blob/main/docs/release.md).
