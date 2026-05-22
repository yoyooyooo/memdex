# Release Process

This repository publishes the `memdex` npm package from
`packages/memdex`.

## CI

Every pull request and every push to `main` runs:

- `bun install --frozen-lockfile`
- `bun run check`
- `bun run memdex -- --help`
- `npm pack --dry-run --json`

CI tests Python 3.10 through 3.14 on Ubuntu.

## npm Publishing

Publishing is handled by `.github/workflows/publish.yml`.

The workflow runs when a SemVer tag like `v0.1.2` is pushed. It uses npm
Trusted Publishing through GitHub OIDC, so no long-lived npm token should be
stored once the package is connected to npm.

Release guards:

- The tag must start with `v`.
- The tag must equal `v${packages/memdex/package.json.version}`.
- `packages/memdex/package.json.name` must be `memdex`.
- If the package version already exists on npm, the workflow skips `npm publish`
  and continues only to verify npm and create/update the GitHub Release.
- The package is dry-run packed with `npm pack --dry-run --json` before publish.

Required npm setup:

1. Create or claim the `memdex` package on npm.
2. Add this GitHub repository as a trusted publisher for that package.
3. Set the trusted workflow filename to `publish.yml`.
4. Set the trusted environment to `npm-publish`.
5. Set allowed actions to `npm publish`.
6. Protect the GitHub `npm-publish` environment before the first public release.

## Versioning

Local release is control-plane only:

1. Run `bun run release:check patch` to inspect the next version and tag.
2. Run `bun run release patch`.
3. The release script creates a temporary local branch from `main`.
4. It writes `packages/memdex/package.json`, refreshes `bun.lock`, commits
   `chore: release vX.Y.Z`, tags that commit, and pushes the tag only.
5. The script returns to `main` and deletes the temporary local branch.
6. `main` does not receive release-only version commits.

Use `minor`, `major`, or an explicit version when needed:

```bash
bun run release:check minor
bun run release 0.2.0
```

If the latest git tag is not present on npm, the next bump command treats it as
a failed release and reuses/replaces that tag. Once a version exists on npm, it
is never reused.

The release workflow checks the tag/package version match, runs `bun run check`,
smokes the CLI, dry run packs the package, runs `npm publish` when needed,
verifies npm contains the published version, then creates or updates the GitHub
Release.

If npm publish succeeds but GitHub Release creation fails, rerun `publish.yml`
manually with the same tag. The workflow will skip npm publish, verify the npm
version, and create/update the GitHub Release:

```bash
gh workflow run publish.yml --repo yoyooyooo/memdex -f tag=v0.1.2
```

## Changelog

GitHub Release notes are generated in CI after npm publish is verified.

The changelog range is the previous published npm version tag to the current
tag, for example `v0.1.1..v0.1.2`. The generated notes use Conventional Commit
subjects and group changes into Breaking Changes, Features, Fixes,
Performance, Refactors, Docs, Tests, Maintenance, and Other.

`CHANGELOG.md` is not updated by default. Release notes are tag artifacts, not
`main` commits.
