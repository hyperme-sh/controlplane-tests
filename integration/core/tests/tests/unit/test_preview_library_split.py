"""Real Git-history checks for the private extraction preview."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "preview", Path(__file__).resolve().parents[4] / "scripts/preview_library_split.py"
)
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Preview test")
        self.git("config", "user.email", "test@example.invalid")
        registry = {"libraries": {}}
        for name in sorted(preview.LIBRARIES):
            path = f"core/lib/{name}"
            self.write(
                path + "/pyproject.toml", f'[project]\nname="{name}"\nversion="1.2.3"\n'
            )
            self.write(path + "/src/module.py", "original = True\n")
            registry["libraries"][name] = {
                "source_roots": [path + "/src"],
                "version": "2.0.0",
                "public_modules": [name.replace("-", "_")],
            }
        self.write("scripts/ownership.json", json.dumps(registry))
        self.write("scripts/import_boundaries.json", "{}")
        self.write("scripts/api_snapshots/a.json", '{"original":true}\n')
        self.write("products/example/private.py", "excluded = True\n")
        self.commit("original")
        self.first = self.git("rev-parse", "HEAD")
        self.write("core/lib/controlplane-api/src/module.py", "second = True\n")
        self.commit("second")
        self.head = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return preview.git(self.root, *args)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    @unittest.skipUnless(
        shutil.which("git-filter-repo"), "git-filter-repo required for history export"
    )
    def test_export_preserves_selected_history_and_source(self):
        report = preview.plan(self.root, self.head)
        self.write("core/lib/controlplane-api/src/module.py", "dirty = True\n")
        refs_before = self.git("show-ref")
        destination = Path(self.tmp.name) / "preview"
        result = preview.export_preview(self.root, report, destination)
        self.assertEqual(self.git("show-ref"), refs_before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(preview.git(destination, "rev-list", "--count", "HEAD"), "2")
        self.assertEqual(
            (destination / "core/lib/controlplane-api/src/module.py").read_text(),
            "second = True\n",
        )
        self.assertFalse((destination / "products").exists())
        self.assertEqual(preview.git(destination, "remote"), "")
        self.assertEqual(preview.git(destination, "tag"), "")
        self.assertFalse(result["external_cutover_ready"])
        self.assertFalse(result["rename_history_followed"])
        self.assertEqual(report["packages"][0]["contract_version"], "2.0.0")
        self.assertEqual(report["packages"][0]["package_version"], "1.2.3")

    @unittest.skipUnless(
        shutil.which("git-filter-repo"), "git-filter-repo required for history export"
    )
    def test_pin_ignores_later_commits(self):
        report = preview.plan(self.root, self.first)
        destination = Path(self.tmp.name) / "old-preview"
        preview.export_preview(self.root, report, destination)
        self.assertEqual(
            (destination / "core/lib/controlplane-api/src/module.py").read_text(),
            "original = True\n",
        )

    def test_destination_refusals(self):
        report = preview.plan(self.root, self.head)
        for destination in [self.root, self.root / "inside", Path(self.tmp.name)]:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                preview.export_preview(self.root, report, destination)
        link = Path(self.tmp.name) / "link"
        link.symlink_to(self.root)
        with self.assertRaises(ValueError):
            preview.export_preview(self.root, report, link)

    def test_runtime_service_dependency_refused(self):
        self.write(
            "core/service/pyproject.toml",
            '[project]\nname="service"\nversion="1.0.0"\n',
        )
        self.write(
            "core/lib/controlplane-api/pyproject.toml",
            '[project]\nname="controlplane-api"\nversion="1.0.0"\ndependencies=["service"]\n[tool.uv.sources]\nservice={workspace=true}\n',
        )
        self.commit("service dependency")
        with self.assertRaisesRegex(ValueError, "service/product"):
            preview.plan(self.root, "HEAD")

    def test_missing_workspace_dependency_refused(self):
        self.write(
            "core/lib/controlplane-api/pyproject.toml",
            '[project]\nname="controlplane-api"\nversion="1.0.0"\ndependencies=["missing"]\n[tool.uv.sources]\nmissing={workspace=true}\n',
        )
        self.commit("missing dependency")
        with self.assertRaisesRegex(ValueError, "missing"):
            preview.plan(self.root, "HEAD")

    def test_bad_paths_refused(self):
        for path in ["../secrets", "/absolute", "a/../b", "a//b", "."]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                preview.safe_path(path)


if __name__ == "__main__":
    unittest.main()
