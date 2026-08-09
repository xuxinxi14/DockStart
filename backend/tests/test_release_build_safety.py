from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_build_safety.py"
SPEC = importlib.util.spec_from_file_location("check_release_build_safety", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class _FakeRegistryKey:
    def __init__(self, hive: str, view: int, path: str, payload: dict[str, object]) -> None:
        self.hive = hive
        self.view = view
        self.path = path
        self.payload = payload

    def __enter__(self) -> _FakeRegistryKey:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 0x1
    KEY_WOW64_64KEY = 0x100
    KEY_WOW64_32KEY = 0x200

    def __init__(self) -> None:
        self.keys: dict[tuple[str, int, str], dict[str, object]] = {}
        self.opened_uninstall_views: set[tuple[str, int]] = set()

    @staticmethod
    def _view(access: int) -> int:
        if access & _FakeWinreg.KEY_WOW64_64KEY:
            return _FakeWinreg.KEY_WOW64_64KEY
        if access & _FakeWinreg.KEY_WOW64_32KEY:
            return _FakeWinreg.KEY_WOW64_32KEY
        return 0

    def add_key(
        self,
        hive: str,
        view: int,
        path: str,
        *,
        values: dict[object, object] | None = None,
        children: list[str] | None = None,
    ) -> None:
        self.keys[(hive, view, path)] = {
            "values": dict(values or {}),
            "children": list(children or []),
        }

    def OpenKey(
        self,
        root: str | _FakeRegistryKey,
        sub_key: str,
        _reserved: int = 0,
        access: int = 0,
    ) -> _FakeRegistryKey:
        view = self._view(access)
        if isinstance(root, _FakeRegistryKey):
            hive = root.hive
            if view == 0:
                view = root.view
            path = f"{root.path}\\{sub_key}"
        else:
            hive = root
            path = sub_key
        if path == MODULE.UNINSTALL_REGISTRY_PATH:
            self.opened_uninstall_views.add((hive, view))
        payload = self.keys.get((hive, view, path))
        if payload is None:
            raise FileNotFoundError(path)
        return _FakeRegistryKey(hive, view, path, payload)

    @staticmethod
    def EnumKey(key: _FakeRegistryKey, index: int) -> str:
        children = key.payload["children"]
        assert isinstance(children, list)
        if index >= len(children):
            error = OSError("no more registry items")
            error.winerror = 259  # type: ignore[attr-defined]
            raise error
        return str(children[index])

    @staticmethod
    def QueryValueEx(key: _FakeRegistryKey, name: str) -> tuple[object, int]:
        values = key.payload["values"]
        assert isinstance(values, dict)
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    @staticmethod
    def QueryValue(key: _FakeRegistryKey, _name: None) -> object:
        values = key.payload["values"]
        assert isinstance(values, dict)
        if None not in values:
            raise FileNotFoundError("default")
        return values[None]


class ReleaseBuildSafetyTests(unittest.TestCase):
    def _temporary_repo(self, root: Path) -> Path:
        repo = root / "DockStart source"
        config = repo / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            '{"bundle":{"publisher":"XinXi Xu"}}\n',
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _uninstall_record(
        *,
        install_location: str | None = None,
        uninstall_string: str | None = None,
    ) -> object:
        return MODULE.RegistryInstallRecord(
            kind="uninstall",
            hive="HKCU",
            view="64-bit",
            key=rf"{MODULE.UNINSTALL_REGISTRY_PATH}\DockStart",
            install_location=install_location,
            uninstall_string=uninstall_string,
        )

    def test_cleanup_roots_must_be_strict_children_of_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            safe = repo / ".release" / "basic"
            result = MODULE.validate_release_build_safety(repo, [safe], [])
            self.assertEqual(result["cleanup_roots"], [str(safe.resolve())])

            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [repo / ".release"], [])
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [repo / "outside"], [])
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(
                    repo,
                    [repo / ".release" / "basic" / ".." / ".." / "outside"],
                    [],
                )

    def test_cleanup_root_must_be_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [Path(".release") / "basic"], [])

    def test_existing_cleanup_root_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            cleanup = repo / ".release" / "basic"
            cleanup.parent.mkdir()
            cleanup.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [cleanup], [])

    def test_nested_link_cannot_escape_release_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            release = repo / ".release"
            release.mkdir()
            outside = root / "outside"
            outside.mkdir()
            link = release / "escaped"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink/junction creation is unavailable: {exc}")

            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [link / "build"], [])

    def test_installation_and_cleanup_overlap_is_rejected_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            cleanup = repo / ".release" / "gate"
            cases = (
                cleanup,
                cleanup / "installed",
                repo,
            )
            for install_root in cases:
                with self.subTest(install_root=install_root):
                    record = self._uninstall_record(install_location=str(install_root))
                    with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                        MODULE.validate_release_build_safety(repo, [cleanup], [record])

    def test_unrelated_existing_install_is_allowed_without_require_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            installed = root / "Programs" / "DockStart"
            record = self._uninstall_record(install_location=f'  "{installed}"  ')
            result = MODULE.validate_release_build_safety(
                repo,
                [repo / ".release" / "basic"],
                [record],
            )
            self.assertEqual(
                result["existing_installations"][0]["install_root"],
                str(installed.resolve()),
            )

    def test_apostrophe_in_absolute_install_path_is_not_treated_as_quote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            installed = root / "O'Brien" / "DockStart"
            record = self._uninstall_record(install_location=str(installed))
            result = MODULE.validate_release_build_safety(
                repo,
                [repo / ".release" / "basic"],
                [record],
            )
            self.assertEqual(
                result["existing_installations"][0]["install_root"],
                str(installed.resolve()),
            )

    def test_require_no_existing_install_rejects_unrelated_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            record = self._uninstall_record(
                install_location=str(root / "Programs" / "DockStart"),
            )
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(
                    repo,
                    [repo / ".release" / "install-gate"],
                    [record],
                    require_no_existing_install=True,
                )

    def test_require_no_existing_install_rejects_nonempty_default_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            local_app_data = root / "LocalAppData"
            stale_install = local_app_data / "DockStart"
            stale_install.mkdir(parents=True)
            (stale_install / "uninstall.exe").write_bytes(b"stale")

            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(
                    repo,
                    [repo / ".release" / "install-gate"],
                    [],
                    require_no_existing_install=True,
                    environ={"LOCALAPPDATA": str(local_app_data)},
                )

    def test_empty_default_install_directory_is_reported_but_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            local_app_data = root / "LocalAppData"
            (local_app_data / "DockStart").mkdir(parents=True)

            result = MODULE.validate_release_build_safety(
                repo,
                [repo / ".release" / "install-gate"],
                [],
                require_no_existing_install=True,
                environ={"LOCALAPPDATA": str(local_app_data)},
            )
            self.assertEqual(result["nonempty_default_install_directories"], [])

    def test_uninstall_string_fallback_expands_environment_and_uses_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            installed = root / "Programs with spaces" / "DockStart"
            record = self._uninstall_record(
                uninstall_string='"%DOCKSTART_TEST_INSTALL%{}uninstall.exe" /S'.format(os.sep),
            )
            result = MODULE.validate_release_build_safety(
                repo,
                [repo / ".release" / "basic"],
                [record],
                environ={"dockstart_test_install": str(installed)},
            )
            existing = result["existing_installations"][0]
            self.assertEqual(existing["install_root"], str(installed.resolve()))
            self.assertEqual(existing["path_source"], "uninstall_string")

    def test_unquoted_uninstall_path_with_spaces_is_extracted(self) -> None:
        executable = MODULE.extract_uninstaller_executable(
            r"C:\Program Files\DockStart\uninstall.exe /S",
        )
        self.assertEqual(executable, r"C:\Program Files\DockStart\uninstall.exe")

    def test_missing_or_invalid_registry_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            cleanup = repo / ".release" / "basic"
            records = (
                self._uninstall_record(),
                self._uninstall_record(install_location="relative/DockStart"),
                self._uninstall_record(install_location="%MISSING_VARIABLE%/DockStart"),
                MODULE.RegistryInstallRecord(
                    kind="publisher",
                    hive="HKCU",
                    view="default",
                    key=r"Software\XinXi Xu\DockStart",
                    install_location="",
                ),
            )
            for record in records:
                with self.subTest(record=record):
                    with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                        MODULE.validate_release_build_safety(repo, [cleanup], [record], environ={})

    def test_existing_install_link_is_resolved_before_overlap_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            repo = self._temporary_repo(root)
            cleanup = repo / ".release" / "basic"
            cleanup.mkdir(parents=True)
            alias = root / "installed-alias"
            try:
                alias.symlink_to(cleanup, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink/junction creation is unavailable: {exc}")
            record = self._uninstall_record(install_location=str(alias))
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.validate_release_build_safety(repo, [cleanup], [record])

    def test_registry_reader_enumerates_both_hives_and_views_and_publisher_key(self) -> None:
        fake = _FakeWinreg()
        install_a = Path(tempfile.gettempdir()) / "DockStart A"
        install_b = Path(tempfile.gettempdir()) / "DockStart B"
        uninstall = MODULE.UNINSTALL_REGISTRY_PATH

        for hive in (fake.HKEY_CURRENT_USER, fake.HKEY_LOCAL_MACHINE):
            for view in (fake.KEY_WOW64_64KEY, fake.KEY_WOW64_32KEY):
                fake.add_key(hive, view, uninstall)
        fake.keys[(fake.HKEY_CURRENT_USER, fake.KEY_WOW64_64KEY, uninstall)]["children"] = [
            "DockStart-A",
            "Other-App",
        ]
        fake.add_key(
            fake.HKEY_CURRENT_USER,
            fake.KEY_WOW64_64KEY,
            rf"{uninstall}\DockStart-A",
            values={"DisplayName": "DockStart", "InstallLocation": str(install_a)},
        )
        fake.add_key(
            fake.HKEY_CURRENT_USER,
            fake.KEY_WOW64_64KEY,
            rf"{uninstall}\Other-App",
            values={"DisplayName": "Other"},
        )
        fake.keys[(fake.HKEY_LOCAL_MACHINE, fake.KEY_WOW64_32KEY, uninstall)]["children"] = [
            "DockStart-B",
        ]
        fake.add_key(
            fake.HKEY_LOCAL_MACHINE,
            fake.KEY_WOW64_32KEY,
            rf"{uninstall}\DockStart-B",
            values={
                "DisplayName": "DockStart",
                "UninstallString": f'"{install_b / "uninstall.exe"}" /S',
            },
        )
        fake.add_key(
            fake.HKEY_CURRENT_USER,
            0,
            r"Software\XinXi Xu\DockStart",
            values={None: str(install_a)},
        )

        records = MODULE.collect_windows_registry_records("XinXi Xu", winreg_module=fake)

        self.assertEqual(len(records), 3)
        self.assertEqual(
            fake.opened_uninstall_views,
            {
                ("HKCU", fake.KEY_WOW64_64KEY),
                ("HKCU", fake.KEY_WOW64_32KEY),
                ("HKLM", fake.KEY_WOW64_64KEY),
                ("HKLM", fake.KEY_WOW64_32KEY),
            },
        )
        self.assertEqual([record.kind for record in records], ["uninstall", "uninstall", "publisher"])

    def test_registry_reader_preserves_empty_publisher_default_for_fail_closed_validation(self) -> None:
        fake = _FakeWinreg()
        for hive in (fake.HKEY_CURRENT_USER, fake.HKEY_LOCAL_MACHINE):
            for view in (fake.KEY_WOW64_64KEY, fake.KEY_WOW64_32KEY):
                fake.add_key(hive, view, MODULE.UNINSTALL_REGISTRY_PATH)
        fake.add_key(
            fake.HKEY_CURRENT_USER,
            0,
            r"Software\XinXi Xu\DockStart",
            values={},
        )
        records = MODULE.collect_windows_registry_records("XinXi Xu", winreg_module=fake)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].kind, "publisher")
        self.assertIsNone(records[0].install_location)

    def test_non_windows_cli_refuses_before_registry_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            stderr = io.StringIO()
            with patch.object(MODULE.sys, "platform", "linux"), contextlib.redirect_stderr(stderr):
                exit_code = MODULE.main(
                    [
                        "--repo-root",
                        str(repo),
                        "--cleanup-root",
                        str(repo / ".release" / "basic"),
                    ],
                )
            self.assertEqual(exit_code, 2)
            self.assertIn("仅支持 Windows", stderr.getvalue())

    def test_read_tauri_publisher_rejects_unsafe_registry_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            repo = self._temporary_repo(Path(temporary_dir))
            config = repo / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
            config.write_text('{"bundle":{"publisher":"unsafe\\\\publisher"}}', encoding="utf-8")
            with self.assertRaises(MODULE.ReleaseBuildSafetyError):
                MODULE.read_tauri_publisher(repo)


if __name__ == "__main__":
    unittest.main()
