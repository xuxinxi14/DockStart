from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_multiple_ligands_4dm3.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_multiple_ligands_4dm3_config_project",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


BOX = {
    "center": {"x": 27.8735, "y": 44.103, "z": 17.7445},
    "effective_size": {"x": 15.75, "y": 16.5, "z": 19.5},
}


def _config_payload(
    overrides: dict[str, str] | None = None,
    *,
    omit: str = "",
    extra_lines: list[str] | None = None,
) -> bytes:
    values = {
        "receptor": "runs/run_001/inputs/receptor.pdbqt",
        "scoring": "vina",
        "center_x": "27.873499999999999",
        "center_y": "44.103000000000002",
        "center_z": "17.744499999999999",
        "size_x": "15.75",
        "size_y": "16.5",
        "size_z": "19.5",
        "exhaustiveness": "32",
        "max_evals": "0",
        "num_modes": "20",
        "min_rmsd": "1",
        "energy_range": "5",
        "cpu": "1",
        "spacing": "0.375",
        "force_even_voxels": "true",
        "verbosity": "1",
        "seed": "12345",
    }
    values.update(overrides or {})
    lines = [
        f"{key} = {value}"
        for key, value in values.items()
        if key != omit
    ]
    lines.extend(extra_lines or [])
    return ("\n".join(lines) + "\n").encode("utf-8")


class StrictVinaConfigContractTests(unittest.TestCase):
    def test_exact_fixed_contract_is_accepted(self) -> None:
        evidence = VERIFY._audit_vina_config_contract(
            _config_payload(),
            run_id="run_001",
            box=BOX,
            seed=12345,
        )
        self.assertEqual(
            evidence["schema"],
            "dockstart_4dm3_strict_vina_config_v1",
        )
        self.assertEqual(
            evidence["values"]["receptor"],
            "runs/run_001/inputs/receptor.pdbqt",
        )
        self.assertEqual(evidence["values"]["center_x"], 27.8735)
        self.assertEqual(evidence["values"]["size_z"], 19.5)
        self.assertEqual(evidence["values"]["seed"], 12345)
        self.assertIs(evidence["values"]["force_even_voxels"], True)

    def test_duplicate_unknown_and_missing_keys_are_rejected(self) -> None:
        cases = {
            "duplicate": _config_payload(
                extra_lines=["seed = 12345"],
            ),
            "unknown": _config_payload(
                extra_lines=["ligand = prepared/ligand.pdbqt"],
            ),
            "missing": _config_payload(omit="verbosity"),
        }
        for label, payload in cases.items():
            with self.subTest(label=label), self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._audit_vina_config_contract(
                    payload,
                    run_id="run_001",
                    box=BOX,
                    seed=12345,
                )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            )

    def test_illegal_values_are_rejected_before_comparison(self) -> None:
        cases = {
            "integer": {"seed": "+12345"},
            "number": {"spacing": "nan"},
            "boolean": {"force_even_voxels": "TRUE"},
            "malformed_line": {},
        }
        for label, overrides in cases.items():
            payload = _config_payload(overrides)
            if label == "malformed_line":
                payload += b" cpu=1\n"
            with self.subTest(label=label), self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._audit_vina_config_contract(
                    payload,
                    run_id="run_001",
                    box=BOX,
                    seed=12345,
                )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_INVALID",
            )

    def test_every_frozen_protocol_field_is_compared(self) -> None:
        mismatches = {
            "receptor": "runs/run_999/inputs/receptor.pdbqt",
            "scoring": "vinardo",
            "center_x": "27.8",
            "center_y": "44.1",
            "center_z": "17.7",
            "size_x": "15",
            "size_y": "16",
            "size_z": "19",
            "exhaustiveness": "31",
            "max_evals": "1",
            "num_modes": "19",
            "min_rmsd": "2",
            "energy_range": "4",
            "cpu": "2",
            "spacing": "0.5",
            "force_even_voxels": "false",
            "verbosity": "0",
            "seed": "23456",
        }
        for key, value in mismatches.items():
            with self.subTest(key=key), self.assertRaises(
                VERIFY.MultipleLigand4dm3AcceptanceError
            ) as raised:
                VERIFY._audit_vina_config_contract(
                    _config_payload({key: value}),
                    run_id="run_001",
                    box=BOX,
                    seed=12345,
                )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_CONFIG_CONTRACT_MISMATCH",
            )
            self.assertIn(key, raised.exception.details["mismatches"])


class StrictProjectRootTests(unittest.TestCase):
    def _fake_create(self, name: str, base_dir: str) -> dict[str, object]:
        project_root = Path(base_dir) / name
        project_root.mkdir()
        return {"ok": True, "project_dir": str(project_root)}

    def test_plain_exact_project_root_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary).resolve()
            with (
                mock.patch.object(
                    VERIFY,
                    "create_project",
                    side_effect=self._fake_create,
                ) as create,
                mock.patch.object(
                    VERIFY,
                    "import_receptor_pdbqt",
                    return_value={"ok": True},
                ) as import_receptor,
                mock.patch.object(
                    VERIFY,
                    "update_box_params",
                    return_value={"ok": True},
                ) as update_box,
            ):
                project_root = VERIFY._configure_project(
                    work_root,
                    work_root / "receptor.pdbqt",
                    BOX,
                )
            self.assertEqual(
                project_root,
                work_root / "multiple_ligands_4dm3",
            )
            create.assert_called_once_with(
                "multiple_ligands_4dm3",
                str(work_root),
            )
            import_receptor.assert_called_once()
            update_box.assert_called_once()

    def test_preexisting_project_path_is_rejected_before_create(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary).resolve()
            (work_root / "multiple_ligands_4dm3").mkdir()
            with mock.patch.object(VERIFY, "create_project") as create:
                with self.assertRaises(
                    VERIFY.MultipleLigand4dm3AcceptanceError
                ) as raised:
                    VERIFY._configure_project(
                        work_root,
                        work_root / "receptor.pdbqt",
                        BOX,
                    )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_PROJECT_DIR_PREEXISTS",
            )
            create.assert_not_called()

    def test_create_project_escape_or_different_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary).resolve()
            escaped = work_root / "other_project"
            escaped.mkdir()
            with (
                mock.patch.object(
                    VERIFY,
                    "create_project",
                    return_value={
                        "ok": True,
                        "project_dir": str(escaped),
                    },
                ),
                mock.patch.object(
                    VERIFY,
                    "import_receptor_pdbqt",
                ) as import_receptor,
            ):
                with self.assertRaises(
                    VERIFY.MultipleLigand4dm3AcceptanceError
                ) as raised:
                    VERIFY._configure_project(
                        work_root,
                        work_root / "receptor.pdbqt",
                        BOX,
                    )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_MISMATCH",
            )
            import_receptor.assert_not_called()

    def test_created_reparse_point_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work_root = Path(temporary).resolve()
            with (
                mock.patch.object(
                    VERIFY,
                    "create_project",
                    side_effect=self._fake_create,
                ),
                mock.patch.object(
                    VERIFY,
                    "_stat_is_link_or_reparse",
                    side_effect=[False, True],
                ),
                mock.patch.object(
                    VERIFY,
                    "import_receptor_pdbqt",
                ) as import_receptor,
            ):
                with self.assertRaises(
                    VERIFY.MultipleLigand4dm3AcceptanceError
                ) as raised:
                    VERIFY._configure_project(
                        work_root,
                        work_root / "receptor.pdbqt",
                        BOX,
                    )
            self.assertEqual(
                raised.exception.code,
                "MULTIPLE_LIGAND_4DM3_PROJECT_ROOT_UNSAFE",
            )
            import_receptor.assert_not_called()

    def test_windows_reparse_attribute_is_detected(self) -> None:
        details = SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=0x400,
        )
        self.assertTrue(VERIFY._stat_is_link_or_reparse(details))


if __name__ == "__main__":
    unittest.main()
