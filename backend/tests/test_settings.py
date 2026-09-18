"""Tests for the persistent settings store and its docking-default integration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.project import (  # noqa: E402
    VinaSettings,
    create_project,
    load_project,
    update_vina_params,
    validate_vina_params,
)
from dockstart_core.settings import (  # noqa: E402
    DOCKING_DEFAULT_SCORING_FUNCTIONS,
    SETTINGS_ENV_VAR,
    DockStartSettings,
    DockingDefaults,
    SettingsFileError,
    diagnose_settings,
    docking_default_overrides,
    get_settings_path,
    load_settings,
    save_settings,
    update_tool_path,
)


class SettingsStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.root = Path(self._temp_dir.name)
        self.settings_path = self.root / "dockstart_settings.json"
        self._env_patch = patch.dict(os.environ, {SETTINGS_ENV_VAR: str(self.settings_path)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def write_raw_settings(self, payload: object) -> None:
        self.settings_path.write_text(json.dumps(payload), encoding="utf-8")


class DockingDefaultsTests(SettingsStoreTestCase):
    def test_dataclass_defaults_mirror_project_vina_settings(self) -> None:
        defaults = DockingDefaults()
        project_defaults = VinaSettings()

        self.assertEqual(defaults.scoring, project_defaults.scoring)
        self.assertEqual(defaults.exhaustiveness, project_defaults.exhaustiveness)
        self.assertEqual(defaults.num_modes, project_defaults.num_modes)
        self.assertEqual(defaults.energy_range, project_defaults.energy_range)
        self.assertEqual(defaults.cpu, project_defaults.cpu)
        self.assertEqual(defaults.seed, project_defaults.seed)

    def test_overrides_are_accepted_by_vina_settings(self) -> None:
        overrides = DockingDefaults(
            scoring="vinardo",
            exhaustiveness=16,
            num_modes=20,
            energy_range=3.0,
            cpu=4,
            seed=12345,
        ).vina_overrides()

        applied = VinaSettings(**overrides)

        self.assertEqual(applied.scoring, "vinardo")
        self.assertEqual(applied.exhaustiveness, 16)
        self.assertEqual(applied.num_modes, 20)
        self.assertEqual(applied.energy_range, 3.0)
        self.assertEqual(applied.cpu, 4)
        self.assertEqual(applied.seed, 12345)

    def test_missing_settings_file_falls_back_to_documented_defaults(self) -> None:
        self.assertFalse(self.settings_path.exists())
        self.assertEqual(docking_default_overrides(), DockingDefaults().vina_overrides())


class SettingsPersistenceTests(SettingsStoreTestCase):
    def test_round_trip_persists_docking_defaults(self) -> None:
        save_settings(
            DockStartSettings(docking_defaults=DockingDefaults(exhaustiveness=16, num_modes=20, cpu=4))
        )

        reloaded = load_settings()

        self.assertEqual(reloaded.docking_defaults.exhaustiveness, 16)
        self.assertEqual(reloaded.docking_defaults.num_modes, 20)
        self.assertEqual(reloaded.docking_defaults.cpu, 4)
        self.assertEqual(reloaded.docking_defaults.scoring, "vina")

        on_disk = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["docking_defaults"]["exhaustiveness"], 16)

    def test_legacy_file_without_docking_defaults_still_loads(self) -> None:
        self.write_raw_settings(
            {
                "tool_paths": {"vina": "C:/tools/vina.exe", "python": "", "autogrid4": ""},
                "project": {"default_project_dir": "D:/projects"},
            }
        )

        loaded = load_settings()

        self.assertEqual(loaded.tool_paths.vina, "C:/tools/vina.exe")
        self.assertEqual(loaded.project.default_project_dir, "D:/projects")
        self.assertEqual(loaded.docking_defaults, DockingDefaults())

    def test_unusable_values_fall_back_and_out_of_range_values_are_clamped(self) -> None:
        self.write_raw_settings(
            {
                "docking_defaults": {
                    "scoring": "VINARDO",
                    "exhaustiveness": "9999",
                    "num_modes": "-4",
                    "energy_range": "not-a-number",
                    "cpu": 3.7,
                    "seed": "",
                }
            }
        )

        defaults = load_settings().docking_defaults

        self.assertEqual(defaults.scoring, "vinardo")
        self.assertEqual(defaults.exhaustiveness, 128)
        self.assertEqual(defaults.num_modes, 1)
        self.assertEqual(defaults.energy_range, DockingDefaults().energy_range)
        # 3.7 is not an integer: fall back instead of silently rounding a
        # docking parameter.
        self.assertEqual(defaults.cpu, DockingDefaults().cpu)
        self.assertIsNone(defaults.seed)

    def test_integral_floats_are_accepted_and_seed_is_clamped(self) -> None:
        self.write_raw_settings(
            {
                "docking_defaults": {
                    "cpu": "4.0",
                    "seed": "9999999999999",
                    "exhaustiveness": 8.0,
                }
            }
        )

        defaults = load_settings().docking_defaults

        self.assertEqual(defaults.cpu, 4)
        self.assertEqual(defaults.exhaustiveness, 8)
        self.assertEqual(defaults.seed, 2_147_483_647)

    def test_unknown_scoring_and_wrong_types_degrade_without_raising(self) -> None:
        self.write_raw_settings(
            {
                "docking_defaults": {
                    "scoring": "mmgbsa",
                    "exhaustiveness": {"nested": True},
                    "num_modes": None,
                    "energy_range": [4],
                    "cpu": "四",
                    "seed": "abc",
                }
            }
        )

        defaults = load_settings().docking_defaults

        self.assertEqual(defaults, DockingDefaults())
        self.assertNotIn(defaults.scoring, ())
        self.assertIn(defaults.scoring, DOCKING_DEFAULT_SCORING_FUNCTIONS)

    def test_tool_path_update_preserves_docking_defaults(self) -> None:
        save_settings(DockStartSettings(docking_defaults=DockingDefaults(exhaustiveness=24)))

        updated = update_tool_path("python", " C:/Python313/python.exe ")

        self.assertEqual(updated.tool_paths.python, "C:/Python313/python.exe")
        self.assertEqual(updated.docking_defaults.exhaustiveness, 24)
        self.assertEqual(load_settings().docking_defaults.exhaustiveness, 24)

    def test_damaged_json_is_rejected_and_never_overwritten(self) -> None:
        self.settings_path.write_text("{not-json", encoding="utf-8")

        with self.assertRaises(SettingsFileError):
            load_settings()

        with self.assertRaises(SettingsFileError):
            save_settings(DockStartSettings())

        self.assertEqual(self.settings_path.read_text(encoding="utf-8"), "{not-json")


class DockingDefaultsContractTests(SettingsStoreTestCase):
    """A global default must be a value the single-project workflow accepts.

    The defaults seed a brand new project's ``VinaSettings``, which is guarded by
    ``project.validate_vina_params``.  A value accepted here but rejected there
    would let a user create projects that can never be edited or run, so the
    contract is asserted explicitly rather than assumed.
    """

    def test_scoring_whitelist_matches_the_project_level_validator(self) -> None:
        self.assertEqual(sorted(DOCKING_DEFAULT_SCORING_FUNCTIONS), ["vina", "vinardo"])

        # AutoDock4 needs pre-computed affinity maps, so it is only reachable
        # through the batch screening workflow's `ad4_maps` protocol.
        self.assertNotIn("ad4", DOCKING_DEFAULT_SCORING_FUNCTIONS)

        rejected = validate_vina_params({"scoring": "ad4"})
        self.assertFalse(rejected.get("ok"))
        self.assertEqual(rejected["error"]["code"], "VINA_SCORING_INVALID")

    def test_every_global_default_is_accepted_by_validate_vina_params(self) -> None:
        for scoring in (None, *DOCKING_DEFAULT_SCORING_FUNCTIONS):
            defaults = DockingDefaults() if scoring is None else DockingDefaults(scoring=scoring)
            with self.subTest(scoring=defaults.scoring):
                result = validate_vina_params(defaults.vina_overrides())
                self.assertTrue(result.get("ok"), result)

    def test_out_of_range_stored_defaults_are_clamped_into_the_accepted_range(self) -> None:
        self.write_raw_settings(
            {
                "docking_defaults": {
                    "exhaustiveness": 9999,
                    "num_modes": 9999,
                    "energy_range": 9999,
                    "cpu": 9999,
                    "seed": -5,
                }
            }
        )

        overrides = docking_default_overrides()

        self.assertTrue(validate_vina_params(overrides).get("ok"), overrides)
        self.assertLessEqual(overrides["exhaustiveness"], 128)
        self.assertLessEqual(overrides["num_modes"], 50)
        self.assertLessEqual(overrides["cpu"], 64)
        self.assertLessEqual(overrides["energy_range"], 20)
        self.assertEqual(overrides["seed"], 0)

    def test_created_projects_can_always_be_edited_and_kept(self) -> None:
        """End-to-end: create from defaults, then re-validate through update-vina."""

        save_settings(DockStartSettings(docking_defaults=DockingDefaults(scoring="vinardo")))

        created = create_project("契约项目", str(self.root / "projects"))
        self.assertTrue(created.get("ok"), created)
        project_dir = created["project_dir"]

        kept = update_vina_params(project_dir, {"scoring": "vinardo"})
        self.assertTrue(kept.get("ok"), kept)
        self.assertEqual(load_project(project_dir)["project"]["vina"]["scoring"], "vinardo")


class SettingsDiagnosticsTests(SettingsStoreTestCase):
    def test_diagnose_reports_a_writable_store_and_real_probe_file_is_removed(self) -> None:
        save_settings(DockStartSettings(docking_defaults=DockingDefaults(num_modes=11)))

        payload = diagnose_settings()
        diagnostics = payload["diagnostics"]

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["settings_path"], str(self.settings_path))
        self.assertTrue(diagnostics["dir_exists"])
        self.assertTrue(diagnostics["file_exists"])
        self.assertTrue(diagnostics["readable"])
        self.assertTrue(diagnostics["writable"])
        self.assertGreater(diagnostics["file_size_bytes"], 0)
        self.assertTrue(diagnostics["file_modified_at"])
        self.assertEqual(diagnostics["current_settings"]["docking_defaults"]["num_modes"], 11)
        self.assertEqual(
            [path.name for path in self.root.glob(".dockstart-settings-probe-*")],
            [],
        )

    def test_diagnose_reports_a_damaged_file_as_unreadable(self) -> None:
        self.settings_path.write_text("[]", encoding="utf-8")

        diagnostics = diagnose_settings()["diagnostics"]

        self.assertTrue(diagnostics["file_exists"])
        self.assertFalse(diagnostics["readable"])
        self.assertFalse(diagnostics["load"]["ok"])
        self.assertTrue(diagnostics["load"]["suggestion"])
        self.assertIsNone(diagnostics["current_settings"])

    def test_diagnose_reports_a_missing_directory_as_not_writable(self) -> None:
        missing = self.root / "missing-dir" / "dockstart_settings.json"
        with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(missing)}):
            payload = diagnose_settings()

        diagnostics = payload["diagnostics"]

        self.assertFalse(diagnostics["dir_exists"])
        self.assertFalse(diagnostics["file_exists"])
        self.assertFalse(diagnostics["writable"])
        self.assertTrue(diagnostics["write_probe"]["suggestion"])

    def test_settings_path_honours_the_environment_override(self) -> None:
        self.assertEqual(get_settings_path(), self.settings_path)

        custom = self.root / "custom.json"
        with patch.dict(os.environ, {SETTINGS_ENV_VAR: str(custom)}):
            self.assertEqual(get_settings_path(), custom)


class SettingsWorkflowIntegrationTests(SettingsStoreTestCase):
    def test_new_project_uses_the_global_docking_defaults(self) -> None:
        save_settings(
            DockStartSettings(
                docking_defaults=DockingDefaults(
                    scoring="vinardo",
                    exhaustiveness=16,
                    num_modes=20,
                    energy_range=3.0,
                    cpu=4,
                    seed=42,
                )
            )
        )

        created = create_project("global-defaults", str(self.root / "projects"))
        self.assertTrue(created["ok"], created)

        project = created["project"]["vina"]
        self.assertEqual(project["scoring"], "vinardo")
        self.assertEqual(project["exhaustiveness"], 16)
        self.assertEqual(project["num_modes"], 20)
        self.assertEqual(project["energy_range"], 3.0)
        self.assertEqual(project["cpu"], 4)
        self.assertEqual(project["seed"], 42)

    def test_existing_project_keeps_its_own_values_when_global_defaults_change(self) -> None:
        save_settings(DockStartSettings(docking_defaults=DockingDefaults(exhaustiveness=16)))
        created = create_project("keeps-own-values", str(self.root / "projects"))
        project_dir = created["project_dir"]

        acknowledged = update_vina_params(project_dir, {"exhaustiveness": 32, "num_modes": 12})
        self.assertTrue(acknowledged["ok"], acknowledged)

        save_settings(DockStartSettings(docking_defaults=DockingDefaults(exhaustiveness=64)))

        reloaded = load_project(project_dir)
        self.assertTrue(reloaded["ok"], reloaded)
        self.assertEqual(reloaded["project"]["vina"]["exhaustiveness"], 32)
        self.assertEqual(reloaded["project"]["vina"]["num_modes"], 12)

    def test_project_creation_survives_a_damaged_settings_file(self) -> None:
        self.settings_path.write_text("{broken", encoding="utf-8")

        created = create_project("damaged-settings", str(self.root / "projects"))

        self.assertTrue(created["ok"], created)
        defaults = VinaSettings()
        self.assertEqual(created["project"]["vina"]["exhaustiveness"], defaults.exhaustiveness)
        self.assertEqual(created["project"]["vina"]["num_modes"], defaults.num_modes)

    def test_project_creation_survives_an_unknown_override_key(self) -> None:
        with patch(
            "dockstart_core.project.docking_default_overrides",
            return_value={"exhaustiveness": 16, "unexpected_key": "ignored"},
        ):
            created = create_project("unknown-key", str(self.root / "projects"))

        self.assertTrue(created["ok"], created)
        self.assertEqual(created["project"]["vina"]["exhaustiveness"], 16)
        self.assertEqual(created["project"]["vina"]["num_modes"], VinaSettings().num_modes)


if __name__ == "__main__":
    unittest.main()
