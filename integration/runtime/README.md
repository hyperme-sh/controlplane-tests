# Disposable cross-service qualification

From the repository root:

```bash
bash scripts/check-owner-integration.sh
```

Python 3.13, Docker, and read access to the immutable Git dependencies in
`pyproject.toml` are required. The script fails when Docker is missing. It
uses no deployed database, Keycloak, Azure, or payment-provider credential.

The collected cases cover:

- user creation producing an ACL policy, and a paid invoice upgrading its tenant
- a free listing purchase producing entitlement and metered inference
- Service Bus subscription resolution and the shared payment wire contract
- one marketplace setting read by the pinned consumers
- migrated PostgreSQL connectivity, the tenant GUC, Redis connectivity, a raw
  event-store round trip, and mocked HTTP

Provider interactions use deterministic doubles. PostgreSQL and the packaged
service code are real. PostgreSQL and Redis run in disposable digest-pinned
containers. The script requires every collected case to pass, with no skips.

`pyproject.toml` pins the revisions this bundle installs, including the
user-service closure. Substituting `main` for those revisions breaks imports.
The lockfile is the tested graph. The run qualifies that bundle and leaves
each service's production dependency graph unchanged.

Provenance of the retained cases is controlplane-tests revision
`943df6f85ac9650dc2944ab5ec9e6ab793f3003a`. Fixtures use installed migrations,
digest-pinned PostgreSQL
(`postgres:16-alpine@sha256:20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416`),
and imports from installed packages. The runtime conftest calls
`controlplane_db.migrations.upgrade` directly.
