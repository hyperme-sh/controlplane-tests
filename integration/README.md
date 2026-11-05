# Owner integration suites

The core/tests and core/e2e-tests trees preserve tests from their owner workspace.
They are not configured as independent projects. Do not count their source
presence as executed coverage. Reconcile owner dependencies, migrations,
conftest initialization, source-relative paths, Docker and service fixtures
before enabling these suites. Default root checks collect only tests/.

The runtime/ project now executes four retained cross-service suites against an
explicit locked bundle of installed owner packages. See runtime/README.md for
provenance, dependency overrides and remaining operational/source-generator gaps.
