# Disposable cross-service qualification

Run `bash scripts/check-owner-integration.sh` from the repository root. Python
3.13, Docker and read access to the immutable Git dependencies are required.
No deployed database, Keycloak, Azure or payment-provider credential is used.

These tests activate the previously archived event-bus, marketplace/AI,
Service Bus subscription and shared marketplace tenant suites. User creation
must produce ACL policy and a paid invoice must upgrade its tenant. A free
listing purchase must produce entitlement and metered inference. Provider
interactions use deterministic doubles; PostgreSQL and packaged service code
are real. Docker absence fails the suite instead of skipping it.

The explicit integration overrides select user-service's declared shared-owner
pins, because other independently packaged services currently declare older,
conflicting commits. Some selected closure dependencies are pending owner PR
commits already used by user-service; substituting their main branches breaks
imports. The lockfile records the complete tested graph. This qualifies this
specific candidate bundle, not arbitrary combinations or stable release
admission. It changes no service's production dependency graph.

Provenance: retained `integration/core/e2e-tests/tests` at controlplane-tests
943df6f85ac9650dc2944ab5ec9e6ab793f3003a. Fixtures use installed owner migrations,
digest-pinned PostgreSQL and isolated package imports. The shared-tenant test
uses installed packages rather than searching neighboring monorepo sources.
The operational edge smoke and source-generator wire comparison remain to be
assigned to their owners; this suite does not claim their coverage.

The active suite also includes five infrastructure cases transferred unchanged
from the retained Panopticon suite: migrated PostgreSQL connectivity, tenant GUC,
Redis connectivity, raw event-store round trip and mocked HTTP. PostgreSQL and
Redis run in disposable digest-pinned containers. Business-flow tests retain
registered wire-contract validation; the low-level storage case deliberately
uses an unregistered synthetic event. All 11 cases must execute without skips.
