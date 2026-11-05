# Cross-service suites

`integration/core/tests` and `integration/core/e2e-tests` keep tests that
originally ran beside the control-plane services. They are not configured as
independent projects. Their presence in the tree is not executed coverage.
Reconcile service dependencies, migrations, conftest initialization,
source-relative paths, Docker, and service fixtures before running these
trees. Default root checks collect only `tests/`.

`integration/runtime` runs the retained cross-service suites against an
explicit locked bundle of installed service packages. See
[runtime/README.md](runtime/README.md) for the pin set and the cases that
execute.
