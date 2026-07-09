# Image Publishing Requirements

How and when the `api` and `ingestion` Docker images are published to a
container registry. This documents the rules for the future publish workflow;
see `docs/CI_REQUIREMENTS.md` for the general CI policy and the `images` job
in `.github/workflows/ci.yml` for the PR-time build validation that already
exists.

## Core rule: publish on merge only, never on PRs

- Pull request runs **build and smoke-test images only**. They never log in to
  a registry and never push. This is a security boundary: PR jobs (including
  from forks) must have zero registry credentials available to them.
- Publishing runs only in a workflow triggered by a push to a protected
  branch (`develop` or `main`), which by definition contains only merged,
  reviewed code.
- The publish workflow lives in a separate file
  (`.github/workflows/publish.yml`) so its permissions and secrets are never
  shared with PR jobs.

## Registry: GitHub Container Registry (GHCR)

Chosen because it requires no external account and no stored secrets.

Images:

- `ghcr.io/<owner>/northstar-api`
- `ghcr.io/<owner>/northstar-ingestion`

## Required CI secrets

**None.** GHCR authentication uses the workflow's built-in `GITHUB_TOKEN`.
The publish job must declare:

```yaml
permissions:
  contents: read
  packages: write
```

and log in with:

```yaml
- uses: docker/login-action@v3
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}
```

If the registry ever changes (Docker Hub, AWS ECR), the required secrets must
be documented here before the workflow is changed, and they must be scoped to
the publish workflow only — never available to PR-triggered jobs (see
`docs/CI_REQUIREMENTS.md` "Secrets").

## Branch and tag strategy

| Event | Tags pushed | Consumed by |
|---|---|---|
| Merge to `develop` | `develop`, `sha-<short-commit>` | staging environment |
| Merge/promotion to `main` | `latest`, `sha-<short-commit>`, `vX.Y.Z` when a version tag exists | production (future) |
| Pull request | nothing published | — |

Rules:

- Every published image also carries an immutable `sha-<short-commit>` tag so
  any environment can pin an exact build and roll back deterministically.
- `develop` and `latest` are moving tags for convenience; deployments should
  reference the `sha-` tag they were tested with.
- Version tags (`vX.Y.Z`) are created manually on `main` when a release is
  cut; pushing the tag publishes the images rebuilt from that exact commit,
  alongside its immutable `sha-` tag.

## Main-only vs develop publishing

The integration branch for this repo is `develop`; `main` is the promotion
target. Publishing from `main` only would leave staging without fresh images,
so:

- **Staging images are published from `develop`.**
- **Production-facing tags (`latest`, versions) are published from `main`
  only.**

No other branch may publish. Feature branches and PRs always stop at
build-validation.
