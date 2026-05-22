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

The workflow runs on a published GitHub Release or manual `workflow_dispatch`
with an explicit tag input.
It uses npm Trusted Publishing through GitHub OIDC, so no long-lived npm token
should be stored once the package is connected to npm.

Release guards:

- The tag must start with `v`.
- The tag must equal `v${packages/memdex/package.json.version}`.
- `packages/memdex/package.json.name` must be `memdex`.
- The same package version must not already exist on npm.
- The package is dry-run packed with `npm pack --dry-run --json` before publish.

Required npm setup:

1. Create or claim the `memdex` package on npm.
2. Add this GitHub repository as a trusted publisher for that package.
3. Set the trusted workflow filename to `publish.yml`.
4. Set the trusted environment to `npm-publish`.
5. Set allowed actions to `npm publish`.
6. Protect the GitHub `npm-publish` environment before the first public release.

## Versioning

Before creating a GitHub Release:

1. Update `packages/memdex/package.json`.
2. Run `bun install` so `bun.lock` records the same workspace version.
3. Run `bun run check`.
4. Commit the version bump.
5. Push to `main` and wait for CI to pass.
6. Create and publish a GitHub Release for that commit.

The GitHub Release tag must match the package version. For version `0.1.1`, use
tag `v0.1.1`.

The release workflow verifies the package again before running `npm publish`.

Manual publish rerun path:

```bash
gh workflow run publish.yml --repo yoyooyooo/memdex -f tag=v0.1.1
```
