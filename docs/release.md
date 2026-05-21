# Release Process

This repository publishes the `@yoyooyooo/codebase-retrieve` npm package from
`packages/codebase-retrieve`.

## CI

Every pull request and every push to `main` runs:

- `bun install --frozen-lockfile`
- `bun run check`
- `bun run cbr -- --help`
- `bun pm pack --dry-run`

CI tests Python 3.10 through 3.14 on Ubuntu.

## npm Publishing

Publishing is handled by `.github/workflows/publish.yml`.

The workflow runs on a published GitHub Release or manual `workflow_dispatch`.
It uses npm Trusted Publishing through GitHub OIDC, so no long-lived npm token
should be stored once the package is connected to npm.

Required npm setup:

1. Create or claim the `@yoyooyooo/codebase-retrieve` package on npm.
2. Add this GitHub repository as a trusted publisher for that package.
3. Set the trusted workflow filename to `publish.yml`.
4. Set the trusted environment to `npm-publish`.
5. Set allowed actions to `npm publish`.
6. Protect the GitHub `npm-publish` environment before the first public release.

## Versioning

Before creating a GitHub Release:

1. Update `packages/codebase-retrieve/package.json`.
2. Run `bun install` so `bun.lock` records the same workspace version.
3. Run `bun run check`.
4. Commit the version bump.
5. Create and publish a GitHub Release for that commit.

The release workflow verifies the package again before running `npm publish`.
