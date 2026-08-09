from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepare_assisted_release_resources.py"
if str(SCRIPT_PATH.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("prepare_assisted_release_resources", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AssistedReleaseProfileTests(unittest.TestCase):
    def _require_local_release_resources(self, *required: Path) -> None:
        if not required:
            required = (
                REPO_ROOT / "resources" / "python" / "python.exe",
                REPO_ROOT / "resources" / "vina" / "vina.exe",
            )
        missing = [str(path) for path in required if not path.is_file()]
        if not missing:
            return
        message = (
            "本测试需要未提交到 Git 的本地发布 runtime：" + ", ".join(missing)
        )
        if os.environ.get("DOCKSTART_REQUIRE_RELEASE_RESOURCES") == "1":
            self.fail(message)
        self.skipTest(message)

    def test_source_toolchain_manifest_does_not_claim_committed_runtime(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "resources" / "toolchain_manifest.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(manifest["status"], "source_template")
        self.assertEqual(manifest["maturity"], "local_candidate")
        self.assertFalse(manifest["resources_committed"])
        self.assertFalse(manifest["vina"]["exists"])
        self.assertFalse(manifest["bundled_vina"]["bundled"])
        self.assertFalse(manifest["bundled_python"]["bundled"])

    def test_source_manifest_pins_complete_windows_runtime(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "resources" / "assisted" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["profile"], "assisted_stable")
        self.assertEqual(manifest["platform"], "windows-x86_64")
        packages = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(
            set(packages),
            {"meeko", "rdkit", "numpy", "scipy", "gemmi", "pillow", "tqdm", "tomli", "colorama"},
        )
        self.assertEqual(manifest["python"]["required_series"], "3.11")
        base_runtime = manifest["python"]["expected_base_runtime"]
        self.assertEqual(len(base_runtime["sha256"]), 64)
        self.assertGreater(base_runtime["file_count"], 0)
        self.assertGreater(base_runtime["size_bytes"], 0)
        self.assertEqual(len(manifest["python"]["expected_executable_sha256"]), 64)
        self.assertEqual(len(manifest["vina"]["expected_executable_sha256"]), 64)
        self.assertEqual(manifest["vina"]["license"], "Apache-2.0")
        self.assertEqual(packages["tqdm"]["license"], "MPL-2.0 AND MIT")
        self.assertEqual(packages["colorama"]["version"], "0.4.6")
        for item in packages.values():
            self.assertEqual(len(item["sha256"]), 64)
            self.assertTrue(all(character in "0123456789abcdef" for character in item["sha256"]))
            self.assertTrue(item["url"].startswith("https://files.pythonhosted.org/"))
            self.assertEqual(item["url"].rsplit("/", 1)[-1], item["filename"])
            self.assertTrue(item["filename"].startswith(f'{item["name"]}-{item["version"]}-'))
            self.assertTrue(item["license"].strip())
            self.assertTrue(item["license_members"])

        sources = {item["name"]: item for item in manifest["source_archives"]}
        self.assertEqual(set(sources), {"meeko", "gemmi", "tqdm"})
        self.assertEqual(sources["meeko"]["version"], packages["meeko"]["version"])
        self.assertEqual(sources["gemmi"]["version"], packages["gemmi"]["version"])
        for item in sources.values():
            self.assertEqual(len(item["sha256"]), 64)
            self.assertTrue(all(character in "0123456789abcdef" for character in item["sha256"]))
            self.assertTrue(item["url"].startswith("https://files.pythonhosted.org/"))
            self.assertEqual(item["url"].rsplit("/", 1)[-1], item["filename"])
            self.assertTrue(item["reason"].strip())

    def test_local_release_executables_match_source_manifest(self) -> None:
        self._require_local_release_resources()
        manifest = json.loads(
            (REPO_ROOT / "resources" / "assisted" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"),
        )
        toolchain_manifest = json.loads(
            (REPO_ROOT / "resources" / "toolchain_manifest.json").read_text(encoding="utf-8"),
        )
        python_sha = hashlib.sha256(
            (REPO_ROOT / "resources" / "python" / "python.exe").read_bytes(),
        ).hexdigest()
        vina_sha = hashlib.sha256(
            (REPO_ROOT / "resources" / "vina" / "vina.exe").read_bytes(),
        ).hexdigest()
        self.assertEqual(manifest["python"]["expected_executable_sha256"], python_sha)
        self.assertEqual(manifest["vina"]["expected_executable_sha256"], vina_sha)
        self.assertEqual(toolchain_manifest["bundled_python"]["sha256"], python_sha)
        self.assertEqual(toolchain_manifest["bundled_vina"]["sha256"], vina_sha)

    def test_requirements_lock_matches_source_manifest_wheels(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "resources" / "assisted" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"),
        )
        manifest_packages = {item["name"].lower(): item for item in manifest["packages"]}
        locked: dict[str, tuple[str, str]] = {}
        lock_path = REPO_ROOT / "resources" / "assisted" / "requirements.lock"
        for line_number, raw_line in enumerate(lock_path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            self.assertEqual(len(parts), 2, f"requirements.lock 第 {line_number} 行格式无效")
            requirement, hash_option = parts
            self.assertIn("==", requirement, f"requirements.lock 第 {line_number} 行未固定版本")
            name, version = requirement.split("==", 1)
            normalized_name = name.lower().replace("_", "-")
            self.assertNotIn(normalized_name, locked, f"requirements.lock 重复固定 {name}")
            prefix = "--hash=sha256:"
            self.assertTrue(hash_option.startswith(prefix), f"requirements.lock 第 {line_number} 行未固定 SHA256")
            sha256 = hash_option[len(prefix):]
            self.assertEqual(len(sha256), 64)
            self.assertTrue(all(character in "0123456789abcdef" for character in sha256))
            locked[normalized_name] = (version, sha256)

        self.assertEqual(set(locked), set(manifest_packages))
        for name, item in manifest_packages.items():
            self.assertEqual(locked[name], (item["version"], item["sha256"]))
            self.assertEqual(item["url"].rsplit("/", 1)[-1], item["filename"])
            self.assertTrue(item["filename"].startswith(f'{item["name"]}-{item["version"]}-'))

    def test_meeko_and_gemmi_license_texts_are_tracked(self) -> None:
        dockstart = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        meeko = (REPO_ROOT / "resources" / "licenses" / "Meeko-LGPL-2.1.txt").read_text(encoding="utf-8")
        gemmi = (REPO_ROOT / "resources" / "licenses" / "Gemmi-MPL-2.0.txt").read_text(encoding="utf-8")
        self.assertIn("Apache License", dockstart)
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", meeko)
        self.assertIn("Mozilla Public License Version 2.0", gemmi)

        for filename in (
            "3Dmol_LICENSE.txt",
            "React_LICENSE.txt",
            "React-DOM_LICENSE.txt",
            "Phosphor-Icons_LICENSE.txt",
            "Tauri_LICENSE_APACHE-2.0.txt",
            "Tauri_LICENSE_MIT.txt",
            "Tauri-plugin-dialog_LICENSE.spdx",
            "Serde_LICENSE-MIT.txt",
        ):
            self.assertGreater((REPO_ROOT / "resources" / "licenses" / filename).stat().st_size, 0)

    def test_wheel_extraction_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            wheel = root / "unsafe.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("../escaped.py", "raise SystemExit")
            with self.assertRaises(MODULE.AssistedReleasePreparationError):
                MODULE._safe_extract_wheel(wheel, root / "target")

    def test_base_runtime_fingerprint_matches_pinned_filtered_tree(self) -> None:
        self._require_local_release_resources(REPO_ROOT / "resources" / "python" / "python.exe")
        manifest = json.loads(
            (REPO_ROOT / "resources" / "assisted" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"),
        )
        expected = {
            key: manifest["python"]["expected_base_runtime"][key]
            for key in ("sha256", "file_count", "size_bytes")
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            runtime = Path(temporary_dir) / "python"
            MODULE._copy_minimal_python(REPO_ROOT / "resources" / "python", runtime)
            assisted_readme = REPO_ROOT / "resources" / "assisted" / "ASSISTED_RUNTIME.md"
            shutil.copy2(assisted_readme, runtime / "README.md")
            shutil.copy2(assisted_readme, runtime / "ASSISTED_RUNTIME.md")
            self.assertEqual(MODULE._ordered_tree_fingerprint(runtime), expected)
            (runtime / "README.md").write_text("tampered", encoding="utf-8")
            self.assertNotEqual(MODULE._ordered_tree_fingerprint(runtime), expected)


if __name__ == "__main__":
    unittest.main()
