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
    frozen = (root / "docker-compose.yaml").read_text().replace(
        "      - uv\n      - run\n",
        "      - uv\n      - run\n      - --frozen\n",
    )
    (root / "docker-compose.yaml").write_text(frozen)
    assert checker.violations(root) == []
