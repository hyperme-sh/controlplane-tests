"""A service must declare every workspace package it imports.

hypermesh-service imported ``panopticon_models`` without declaring it. Tests
passed because they run from one venv with every member installed. The image
runs ``uv sync --package hypermesh-service``, which never installed it, so the
new container failed at import and the test API returned 503.
"""
import importlib.util
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location(
    "check_workspace_dependencies", ROOT / "scripts/check_workspace_dependencies.py"
)
checker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)

DOCKERFILE = """\
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/models ./libs/models
COPY svc ./svc
RUN uv sync --frozen --no-dev --package svc-service
ENV PYTHONPATH=/app/svc/src
RUN /app/.venv/bin/python -c "import svc_service.app"
CMD ["/app/.venv/bin/python", "-m", "svc_service.main"]
"""


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def package(name, dependencies=()):
    items = ", ".join(f'"{item}"' for item in dependencies)
    return f'[project]\nname = "{name}"\nversion = "0"\ndependencies = [{items}]\n'


def workspace(tmp_path, *, svc_dependencies=("models-lib",), svc_source="", dockerfile=DOCKERFILE):
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.uv.workspace]\nmembers = ["libs/models", "libs/extra", "svc"]\n',
    )
    write(tmp_path, "libs/models/pyproject.toml", package("models-lib"))
    write(tmp_path, "libs/models/src/models_pkg/__init__.py", "")
    write(tmp_path, "libs/extra/pyproject.toml", package("extra-lib", ["models-lib"]))
    write(tmp_path, "libs/extra/src/extra_pkg/__init__.py", "from models_pkg import x\n")
    write(tmp_path, "svc/pyproject.toml", package("svc-service", svc_dependencies))
    write(tmp_path, "svc/src/svc_service/__init__.py", "")
    write(tmp_path, "svc/src/svc_service/app.py", svc_source)
    write(tmp_path, "svc/Dockerfile", dockerfile)
    return tmp_path


def test_repository_declares_every_workspace_import():
    assert checker.violations(ROOT) == []


def test_hypermesh_declares_panopticon_models():
    project = tomllib.loads((ROOT / "products/hypermesh/pyproject.toml").read_text())
    assert "panopticon-models" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["panopticon-models"] == {"workspace": True}
    assert "COPY core/models ./core/models" in (ROOT / "products/hypermesh/Dockerfile").read_text()


def test_every_service_image_imports_its_app_after_the_sync():
    names = [path.relative_to(ROOT).as_posix() for path in checker.dockerfiles(ROOT)]
    assert len(names) >= 16
    assert "products/hypermesh/Dockerfile" in names


def test_declared_dependency_passes(tmp_path):
    root = workspace(tmp_path, svc_source="from models_pkg import x\n")
    assert checker.violations(root) == []


def test_undeclared_workspace_import_fails(tmp_path):
    root = workspace(
        tmp_path,
        svc_dependencies=(),
        svc_source="from models_pkg.payment import MIN\n",
    )
    errors = checker.violations(root)
    assert any("svc/src/svc_service/app.py:1" in e and "models-lib" in e for e in errors)


def test_function_level_import_is_checked(tmp_path):
    root = workspace(
        tmp_path,
        svc_dependencies=(),
        svc_source="def handler():\n    import models_pkg\n",
    )
    assert any("models_pkg" in e for e in checker.violations(root))


def test_dependency_through_another_declared_package_passes(tmp_path):
    root = workspace(
        tmp_path,
        svc_dependencies=("extra-lib",),
        svc_source="import models_pkg\n",
        dockerfile=DOCKERFILE.replace(
            "COPY libs/models ./libs/models\n",
            "COPY libs/models ./libs/models\nCOPY libs/extra ./libs/extra\n",
        ),
    )
    assert checker.violations(root) == []


def test_type_checking_and_guarded_imports_are_optional(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import models_pkg\n"
        "try:\n"
        "    import models_pkg.optional\n"
        "except ImportError:\n"
        "    optional = None\n"
    )
    root = workspace(tmp_path, svc_dependencies=(), svc_source=source)
    assert checker.violations(root) == []


def test_import_in_except_handler_is_not_optional(tmp_path):
    source = "try:\n    pass\nexcept ImportError:\n    import models_pkg\n"
    root = workspace(tmp_path, svc_dependencies=(), svc_source=source)
    assert any("models_pkg" in e for e in checker.violations(root))


def test_image_must_copy_every_package_in_the_closure(tmp_path):
    root = workspace(
        tmp_path,
        svc_source="import models_pkg\n",
        dockerfile=DOCKERFILE.replace("COPY libs/models ./libs/models\n", ""),
    )
    errors = checker.violations(root)
    assert any("does not COPY libs/models" in e for e in errors)


def test_image_must_import_the_app_after_the_sync(tmp_path):
    before_sync = DOCKERFILE.replace(
        'RUN /app/.venv/bin/python -c "import svc_service.app"\n', ""
    ).replace(
        "RUN uv sync", 'RUN /app/.venv/bin/python -c "import svc_service.app"\nRUN uv sync'
    )
    for text in (before_sync, DOCKERFILE.replace("import svc_service.app", "import svc_service")):
        root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=text)
        assert checker.violations(root), text


def test_broad_except_does_not_make_an_import_optional(tmp_path):
    for handler in ("except Exception:", "except:"):
        source = f"try:\n    import models_pkg\n{handler}\n    models_pkg = None\n"
        root = workspace(tmp_path, svc_dependencies=(), svc_source=source)
        assert any("models_pkg" in e for e in checker.violations(root)), handler


def test_import_error_in_a_tuple_is_optional(tmp_path):
    source = "try:\n    import models_pkg\nexcept (ValueError, ModuleNotFoundError):\n    pass\n"
    root = workspace(tmp_path, svc_dependencies=(), svc_source=source)
    assert checker.violations(root) == []


def test_multiline_uv_sync_is_still_checked(tmp_path):
    split = DOCKERFILE.replace(
        "RUN uv sync --frozen --no-dev --package svc-service",
        "RUN uv sync --frozen \\\n    --no-dev \\\n    --package svc-service",
    )
    assert checker.violations(workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=split)) == []
    missing_copy = split.replace("COPY libs/models ./libs/models\n", "")
    root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=missing_copy)
    assert any("does not COPY libs/models" in e for e in checker.violations(root))


def test_uv_sync_without_a_package_is_an_error(tmp_path):
    text = DOCKERFILE.replace(" --package svc-service", "")
    root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=text)
    assert any("without --package" in e for e in checker.violations(root))


def test_copy_after_the_sync_does_not_count(tmp_path):
    text = DOCKERFILE.replace("COPY libs/models ./libs/models\n", "").replace(
        "ENV PYTHONPATH", "COPY libs/models ./libs/models\nENV PYTHONPATH"
    )
    root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=text)
    assert any("does not COPY libs/models" in e for e in checker.violations(root))


def test_entrypoint_image_must_import_the_app_module(tmp_path):
    entrypoint = (
        DOCKERFILE.replace('CMD ["/app/.venv/bin/python", "-m", "svc_service.main"]\n', "")
        + 'ENTRYPOINT ["/docker-entrypoint.sh"]\n'
    )
    root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=entrypoint)
    assert checker.violations(root) == []
    wrong = entrypoint.replace("import svc_service.app", "import svc_service.main")
    root = workspace(tmp_path, svc_source="import models_pkg\n", dockerfile=wrong)
    assert any("import svc_service.app" in e for e in checker.violations(root))


def test_edge_image_imports_the_api_app():
    text = (ROOT / "controlplane-edge/Dockerfile").read_text()
    assert 'RUN /app/.venv/bin/python -c "import api"' in text


def test_operator_ban_store_is_a_workspace_member():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "core/user-service/contracts" in root["tool"]["uv"]["workspace"]["members"]
    project = tomllib.loads((ROOT / "core/user-service/contracts/pyproject.toml").read_text())
    assert project["project"]["name"] == "operator-ban-store"


def test_migrate_image_imports_alembic_after_the_sync():
    text = (ROOT / "core/payment-service/Dockerfile").read_text()
    assert 'RUN /app/.venv/bin/python -c "import controlplane_db"' in text
    assert 'RUN /app/.venv/bin/python -c "import alembic"' in text
    assert 'RUN /app/.venv/bin/python -c "import controlplane_tenancy.startup"' in text


def test_unlocked_uv_run_requires_sources_of_copied_members(tmp_path):
    """Copying a parent pyproject makes `uv run` build that member.

    payment-service installs host-binding-store, the contract under
    products/hypermesh. The parent pyproject declares hypermesh-ticket as
    ``{workspace=true}``. An unlocked ``uv run`` builds the parent and fails
    when the ticket package was not copied.
    """

    root = workspace(tmp_path, svc_source="import models_pkg\n")
    write(
        root,
        "pyproject.toml",
        "\n".join(
            [
                "[tool.uv.workspace]",
                "members = [",
                '    "libs/models",',
                '    "libs/extra",',
                '    "svc",',
                '    "product",',
                '    "product/ticket",',
                "]",
                "",
            ]
        ),
    )
    write(
        root,
        "product/pyproject.toml",
        "\n".join(
            [
                "[project]",
                'name = "product-service"',
                'version = "0"',
                'dependencies = ["ticket-lib"]',
                "",
                "[tool.uv.sources]",
                "ticket-lib = { workspace = true }",
                "",
            ]
        ),
    )
    write(root, "product/ticket/pyproject.toml", package("ticket-lib"))
    write(root, "product/ticket/src/ticket_pkg/__init__.py", "")
    dockerfile = DOCKERFILE.replace(
        "COPY libs/models ./libs/models\n",
        "COPY libs/models ./libs/models\n"
        "COPY product/pyproject.toml ./product/pyproject.toml\n",
    )
    write(root, "svc/Dockerfile", dockerfile)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  migrate:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                "    command:",
                "      - uv",
                "      - run",
                "      - python",
                "      - -m",
                "      - svc_service.app",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("ticket-lib" in error and "product/ticket" in error for error in errors)
    # `--no-sync` after the child command is the child's argument. uv still syncs.
    child_flag = (root / "docker-compose.yaml").read_text().replace(
        "      - svc_service.app\n",
        "      - svc_service.app\n      - --no-sync\n",
    )
    (root / "docker-compose.yaml").write_text(child_flag)
    assert any("ticket-lib" in error for error in checker.violations(root))
    frozen = child_flag.replace(
        "      - uv\n      - run\n",
        "      - uv\n      - run\n      - --frozen\n",
    )
    (root / "docker-compose.yaml").write_text(frozen)
    assert checker.violations(root) == []


def _product_with_dev_group(tmp_path):
    root = workspace(tmp_path, svc_source="import models_pkg\n")
    write(
        root,
        "pyproject.toml",
        "\n".join(
            [
                "[tool.uv.workspace]",
                "members = [",
                '    "libs/models",',
                '    "libs/extra",',
                '    "svc",',
                '    "product",',
                '    "product/ticket",',
                '    "product/devlib",',
                "]",
                "",
            ]
        ),
    )
    write(
        root,
        "product/pyproject.toml",
        "\n".join(
            [
                "[project]",
                'name = "product-service"',
                'version = "0"',
                'dependencies = ["ticket-lib"]',
                "",
                "[dependency-groups]",
                'dev = ["dev-lib"]',
                "",
                "[tool.uv.sources]",
                "ticket-lib = { workspace = true }",
                "dev-lib = { workspace = true }",
                "",
            ]
        ),
    )
    write(root, "product/ticket/pyproject.toml", package("ticket-lib"))
    write(root, "product/ticket/src/ticket_pkg/__init__.py", "")
    write(root, "product/devlib/pyproject.toml", package("dev-lib"))
    write(root, "product/devlib/src/dev_pkg/__init__.py", "")
    dockerfile = DOCKERFILE.replace(
        "COPY libs/models ./libs/models\n",
        "COPY libs/models ./libs/models\n"
        "COPY product/pyproject.toml ./product/pyproject.toml\n",
    )
    write(root, "svc/Dockerfile", dockerfile)
    return root


def test_unlocked_uv_run_includes_dev_group_sources(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                "    command:",
                "      - uv",
                "      - run",
                "      - python",
                "      - -m",
                "      - svc_service.app",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("dev-lib" in error and "product/devlib" in error for error in errors)
    assert any("ticket-lib" in error for error in errors)
    no_dev = (root / "docker-compose.yaml").read_text().replace(
        "      - uv\n      - run\n",
        "      - uv\n      - run\n      - --no-dev\n",
    )
    (root / "docker-compose.yaml").write_text(no_dev)
    errors = checker.violations(root)
    assert any("ticket-lib" in error for error in errors)
    assert not any("dev-lib" in error for error in errors)


def test_overlay_image_without_a_dockerfile_is_still_checked(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  svc-service:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                "    command:",
                "      - /app/.venv/bin/python",
                "      - -m",
                "      - svc_service.app",
                "",
            ]
        ),
    )
    write(
        root,
        "docker-compose.vm.yaml",
        "\n".join(
            [
                "services:",
                "  enroll:",
                "    image: ghcr.io/fyberlabs/svc-service:${PANOPTICON_IMAGE_TAG:-test}",
                '    entrypoint: ["uv", "run", "--project", "/app/svc", "python", "-m", "svc_service.app"]',
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)
    locked = (root / "docker-compose.vm.yaml").read_text().replace(
        '["uv", "run", "--project", "/app/svc", "python"',
        '["uv", "run", "--no-sync", "--project", "/app/svc", "python"',
    )
    (root / "docker-compose.vm.yaml").write_text(locked)
    assert checker.violations(root) == []


def test_known_host_enroll_uses_the_image_venv():
    text = (ROOT / "docker-compose.vm.yaml").read_text()
    enroll = text.split("hypermesh-known-host-enroll:", 1)[1].split("\n  hypermesh-gateway:", 1)[0]
    assert "/app/.venv/bin/python" in enroll
    assert "hypermesh_service.lease_harness" in enroll
    assert "uv" not in enroll


def _product_with_extras(tmp_path):
    root = _product_with_dev_group(tmp_path)
    members = (root / "pyproject.toml").read_text().replace(
        '    "product/devlib",',
        '    "product/devlib",\n    "product/feature",\n    "product/other",',
    )
    (root / "pyproject.toml").write_text(members)
    project = (root / "product/pyproject.toml").read_text()
    project = project.replace(
        'dependencies = ["ticket-lib"]\n',
        "\n".join(
            [
                'dependencies = ["ticket-lib"]',
                "",
                "[project.optional-dependencies]",
                'feature = ["feature-lib"]',
                'other = ["other-lib"]',
                "",
            ]
        ),
    )
    project = project.replace(
        "dev-lib = { workspace = true }\n",
        "\n".join(
            [
                "dev-lib = { workspace = true }",
                "feature-lib = { workspace = true }",
                "other-lib = { workspace = true }",
                "",
            ]
        ),
    )
    (root / "product/pyproject.toml").write_text(project)
    write(root, "product/feature/pyproject.toml", package("feature-lib"))
    write(root, "product/feature/src/feature_pkg/__init__.py", "")
    write(root, "product/other/pyproject.toml", package("other-lib"))
    write(root, "product/other/src/other_pkg/__init__.py", "")
    return root


def _compose_uv(root, command_lines):
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                "    command:",
                *command_lines,
                "",
            ]
        ),
    )


def test_unlocked_uv_run_includes_selected_extras(tmp_path):
    root = _product_with_extras(tmp_path)
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --extra",
            "      - feature",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    errors = checker.violations(root)
    assert any("feature-lib" in error and "product/feature" in error for error in errors)
    assert not any("other-lib" in error for error in errors)
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --all-extras",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    errors = checker.violations(root)
    assert any("feature-lib" in error for error in errors)
    assert any("other-lib" in error for error in errors)
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --all-extras",
            "      - --no-extra",
            "      - feature",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    errors = checker.violations(root)
    assert not any("feature-lib" in error for error in errors)
    assert any("other-lib" in error for error in errors)


def test_global_uv_options_still_detect_an_unlocked_run(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                '    entrypoint: ["uv", "--directory", "/app", "run", "python", "-m", "svc_service.app"]',
                "",
            ]
        ),
    )
    assert any("dev-lib" in error for error in checker.violations(root))
    (root / "docker-compose.yaml").write_text(
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                '    entrypoint: ["uv", "-q", "run", "--frozen", "python", "-m", "svc_service.app"]',
                "",
            ]
        )
    )
    assert checker.violations(root) == []


def test_dockerfile_path_is_resolved_from_the_build_context(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      context: svc",
                "      dockerfile: Dockerfile",
                "    command:",
                "      - uv",
                "      - run",
                "      - python",
                "      - -m",
                "      - svc_service.app",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("svc/Dockerfile" in error and "dev-lib" in error for error in errors)


def test_nested_compose_context_is_relative_to_the_compose_file(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "deploy/docker-compose.yml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      context: ..",
                "      dockerfile: svc/Dockerfile",
                "    command:",
                "      - uv",
                "      - run",
                "      - python",
                "      - -m",
                "      - svc_service.app",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("svc/Dockerfile" in error and "dev-lib" in error for error in errors)


def test_configured_default_groups_replace_dev(tmp_path):
    root = _product_with_dev_group(tmp_path)
    members = (root / "pyproject.toml").read_text().replace(
        '    "product/devlib",',
        '    "product/devlib",\n    "product/qalib",',
    )
    (root / "pyproject.toml").write_text(members)
    project = (root / "product/pyproject.toml").read_text().replace(
        'dev = ["dev-lib"]\n',
        'dev = ["dev-lib"]\nqa = ["qa-lib"]\n',
    )
    project = project.replace(
        "[tool.uv.sources]\n",
        '[tool.uv]\ndefault-groups = ["qa"]\n\n[tool.uv.sources]\n',
    )
    project = project.replace(
        "dev-lib = { workspace = true }\n",
        "dev-lib = { workspace = true }\nqa-lib = { workspace = true }\n",
    )
    (root / "product/pyproject.toml").write_text(project)
    write(root, "product/qalib/pyproject.toml", package("qa-lib"))
    write(root, "product/qalib/src/qa_pkg/__init__.py", "")
    _compose_uv(
        root,
        ["      - uv", "      - run", "      - python", "      - -m", "      - svc_service.app"],
    )
    errors = checker.violations(root)
    assert any("qa-lib" in error for error in errors)
    assert not any("dev-lib" in error for error in errors)
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --no-default-groups",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    errors = checker.violations(root)
    assert not any("qa-lib" in error for error in errors)
    assert any("ticket-lib" in error for error in errors)


def test_include_group_is_part_of_the_closure(tmp_path):
    root = _product_with_dev_group(tmp_path)
    members = (root / "pyproject.toml").read_text().replace(
        '    "product/devlib",',
        '    "product/devlib",\n    "product/qalib",',
    )
    (root / "pyproject.toml").write_text(members)
    project = (root / "product/pyproject.toml").read_text()
    project = project.replace(
        'dev = ["dev-lib"]\n',
        'dev = [{include-group = "qa"}]\nqa = ["qa-lib", {include-group = "dev"}]\n',
    )
    project = project.replace(
        "dev-lib = { workspace = true }\n",
        "qa-lib = { workspace = true }\n",
    )
    (root / "product/pyproject.toml").write_text(project)
    write(root, "product/qalib/pyproject.toml", package("qa-lib"))
    write(root, "product/qalib/src/qa_pkg/__init__.py", "")
    _compose_uv(
        root,
        ["      - uv", "      - run", "      - python", "      - -m", "      - svc_service.app"],
    )
    errors = checker.violations(root)
    assert any("qa-lib" in error and "product/qalib" in error for error in errors)


def test_no_project_does_not_expand_the_workspace(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --no-project",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    assert checker.violations(root) == []


def test_package_option_requires_that_member_even_when_not_copied(tmp_path):
    root = workspace(tmp_path, svc_source="import models_pkg\n")
    text = (root / "pyproject.toml").read_text().replace(
        'members = ["libs/models", "libs/extra", "svc"]',
        'members = ["libs/models", "libs/extra", "svc", "product"]',
    )
    (root / "pyproject.toml").write_text(text)
    write(root, "product/pyproject.toml", package("product-service"))
    write(root, "product/src/product_pkg/__init__.py", "")
    _compose_uv(
        root,
        [
            "      - uv",
            "      - run",
            "      - --package",
            "      - product-service",
            "      - python",
            "      - -m",
            "      - svc_service.app",
        ],
    )
    assert any("product-service" in error and "COPY product" in error for error in checker.violations(root))


def test_service_name_comes_from_a_multiline_block():
    block = "  worker:\n    image: ghcr.io/fyberlabs/svc-service:test\n    command: [x]"
    assert checker._service_name(block) == "worker"


def test_repository_compose_services_have_names():
    for path in checker.compose_files(ROOT):
        for block in checker.compose_service_blocks(path.read_text()):
            assert checker._service_name(block), (path, block.splitlines()[0])


def test_image_only_overlay_maps_to_its_own_build_service(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(root, "other/pyproject.toml", "")
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  svc-service:",
                "    build:",
                "      dockerfile: svc/Dockerfile",
                "  zz-last:",
                "    build:",
                "      dockerfile: other/Dockerfile",
                "",
            ]
        ),
    )
    write(
        root,
        "docker-compose.vm.yaml",
        "\n".join(
            [
                "services:",
                "  svc-service:",
                "    command: [uv, run, python, -m, svc_service.app]",
                "",
            ]
        ),
    )
    groups, errors = checker.unlocked_compose_targets(root)
    assert errors == []
    assert set(groups) == {"svc/Dockerfile"}


def test_alternate_compose_dockerfile_is_checked(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(root, "svc/Worker.Dockerfile", (root / "svc/Dockerfile").read_text())
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Worker.Dockerfile",
                "    command: [uv, run, python, -m, svc_service.app]",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("svc/Worker.Dockerfile" in e and "dev-lib" in e for e in errors)


def test_missing_compose_dockerfile_is_reported(tmp_path):
    root = _product_with_dev_group(tmp_path)
    write(
        root,
        "docker-compose.yaml",
        "\n".join(
            [
                "services:",
                "  worker:",
                "    build:",
                "      dockerfile: svc/Gone.Dockerfile",
                "    command: [uv, run, python, -m, svc_service.app]",
                "",
            ]
        ),
    )
    errors = checker.violations(root)
    assert any("svc/Gone.Dockerfile" in e and "not in the repository" in e for e in errors)


def _worker_compose(root, lines):
    write(
        root,
        "docker-compose.yaml",
        "\n".join(["services:", "  worker:", "    build:", "      dockerfile: svc/Dockerfile", *lines, ""]),
    )


def test_inline_entrypoint_and_command_form_one_uv_run(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(root, ['    entrypoint: ["uv"]', '    command: ["run", "python", "-m", "svc_service.app"]'])
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)
    _worker_compose(root, ['    entrypoint: ["uv"]', '    command: ["run", "--no-sync", "python", "-m", "svc_service.app"]'])
    assert checker.violations(root) == []


def test_block_entrypoint_and_string_command_form_one_uv_run(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(root, ["    entrypoint:", "      - uv", "    command: run python -m svc_service.app"])
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)


def test_member_copied_after_the_sync_is_built_by_an_unlocked_run(tmp_path):
    root = _product_with_dev_group(tmp_path)
    dockerfile = (root / "svc/Dockerfile").read_text().replace(
        "COPY product/pyproject.toml ./product/pyproject.toml\n", ""
    )
    write(root, "svc/Dockerfile", dockerfile)
    _worker_compose(root, ["    command: [uv, run, --no-dev, python, -m, svc_service.app]"])
    assert checker.violations(root) == []
    late = dockerfile.replace(
        'RUN /app/.venv/bin/python -c "import svc_service.app"\n',
        'RUN /app/.venv/bin/python -c "import svc_service.app"\n'
        "COPY product/pyproject.toml ./product/pyproject.toml\n",
    )
    write(root, "svc/Dockerfile", late)
    errors = checker.violations(root)
    assert any("ticket-lib" in error and "product/ticket" in error for error in errors)
    fixed = late.replace(
        "COPY product/pyproject.toml ./product/pyproject.toml\n",
        "COPY product/pyproject.toml ./product/pyproject.toml\n"
        "COPY product/ticket ./product/ticket\n",
    )
    write(root, "svc/Dockerfile", fixed)
    errors = checker.violations(root)
    assert not any("ticket-lib" in error for error in errors)


def test_overlay_command_runs_with_the_base_entrypoint(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(root, ['    entrypoint: ["uv"]', '    command: ["/app/.venv/bin/python", "-m", "svc_service.app"]'])
    assert checker.violations(root) == []
    write(
        root,
        "docker-compose.vm.yaml",
        'services:\n  worker:\n    command: ["run", "python", "-m", "svc_service.app"]\n',
    )
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)


def test_inline_comments_in_a_command_list_are_ignored(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(
        root,
        ["    command:", "      - uv # project runner", "      - run", "      - python", "      - '#not-a-comment'"],
    )
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)
    assert checker._strip_comment("'a # b' # c") == "'a # b'"


def test_only_executable_compose_fields_count(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(
        root,
        [
            "    environment:",
            '      HELP_TEXT: "uv run python"',
            "    labels:",
            '      note: "uv run python -m svc_service.app"',
            '    command: ["/app/.venv/bin/python", "-m", "svc_service.app"]',
        ],
    )
    assert checker.violations(root) == []


def test_shell_form_command_with_uv_run_is_checked(tmp_path):
    root = _product_with_dev_group(tmp_path)
    _worker_compose(root, ['    command: ["sh", "-c", "uv run python -m svc_service.app"]'])
    errors = checker.violations(root)
    assert any("dev-lib" in error and "svc/Dockerfile" in error for error in errors)


def test_runtime_members_come_from_the_final_stage():
    text = "\n".join(
        [
            "FROM base AS builder",
            "COPY lib ./lib",
            "COPY svc ./svc",
            "FROM base",
            "COPY svc ./svc",
            "",
        ]
    )
    assert checker.final_stage_copies(text) == {"svc"}
    carried = text + "COPY --from=builder /app /app\n"
    assert checker.final_stage_copies(carried) == {"lib", "svc"}
