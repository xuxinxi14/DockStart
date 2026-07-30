from __future__ import annotations

import copy
import hashlib
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

from adapters.vina_adapter import ManagedRunResult  # noqa: E402
from dockstart_core import ad4zn as ad4zn_module  # noqa: E402
from dockstart_core.ad4zn import validate_parameter_file  # noqa: E402
from dockstart_core.autogrid import (  # noqa: E402
    _canonical_json_sha256,
    compute_ad4zn_box_coverage,
    generate_maps,
)
from dockstart_core.project import (  # noqa: E402
    _ad4zn_frozen_box_coverage,
    _ad4zn_parameter_reference_issue,
    _ad4zn_prepared_receptor_issue,
    _validate_execute_prerequisites,
    analyze_vina_run_results,
    build_markdown_report,
    execute_prepared_vina_run,
    generate_vina_config,
    prepare_vina_run,
)
from tests import test_ad4zn_project_integration as support  # noqa: E402


VINA_LOG = """mode |   affinity | dist from best mode
     | (kcal/mol) | rmsd l.b.| rmsd u.b.
-----+------------+----------+----------
   1       -7.4          0          0
"""


def _marker_line(
    serial: int,
    atom_type: str,
    x: float,
    y: float,
    z: float,
) -> str:
    return support._atom_line(
        serial,
        atom_type,
        atom_type,
        "A",
        500 + serial,
        x,
        y,
        z,
        0.0,
        atom_type,
        record_type="HETATM",
    )


class AD4ZnFrozenContractTests(unittest.TestCase):
    def setUp(self) -> None:
        reference_sha256 = hashlib.sha256(
            support.PARAMETER_TEXT.encode("utf-8")
        ).hexdigest()
        patcher = patch.object(
            ad4zn_module,
            "SUPPORTED_PARAMETER_REFERENCE_SHA256",
            reference_sha256,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.support = support.AD4ZnProjectIntegrationTests(
            methodName="runTest"
        )

    @staticmethod
    def _marker_receptor(path: Path) -> None:
        path.write_text(
            _marker_line(1, "ZN", 0.0, 0.0, 0.0)
            + _marker_line(2, "TZ", -1.155, -1.155, -1.155),
            encoding="utf-8",
        )

    def _coverage_manifest(
        self,
        receptor: Path,
        *,
        spacing: float = 0.333333333,
    ) -> dict[str, object]:
        points = [60, 60, 60]
        box = {
            "center_x": 0.0,
            "center_y": 0.0,
            "center_z": 0.0,
            "size_x": 20.0,
            "size_y": 20.0,
            "size_z": 20.0,
        }
        result = compute_ad4zn_box_coverage(
            receptor,
            box,
            points,
            spacing,
        )
        self.assertTrue(result["ok"], result)
        return {
            "grid": {
                "requested_box": {
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "size": {"x": 20.0, "y": 20.0, "z": 20.0},
                },
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "grid_points": {
                    axis: points[index]
                    for index, axis in enumerate(("x", "y", "z"))
                },
                "spacing": spacing,
                "actual_size": {
                    axis: round(points[index] * spacing, 6)
                    for index, axis in enumerate(("x", "y", "z"))
                },
            },
            "ad4zn": {
                "box_coverage": copy.deepcopy(result["box_coverage"]),
                "box_coverage_sha256": result[
                    "box_coverage_sha256"
                ],
            },
        }

    def _ready_run(
        self,
        root: str,
    ) -> tuple[Path, dict[str, object]]:
        project_dir, _parameter = self.support._prepared_project(root)
        autogrid = Path(root) / "autogrid4.exe"
        autogrid.write_bytes(b"mock AutoGrid 4.2.7 executable")
        with patch(
            "dockstart_core.autogrid.autogrid_adapter.detect",
            return_value=self.support._tool(
                "autogrid4",
                "AutoGrid4",
                "4.2.7.x.2019-07-11",
                autogrid,
            ),
        ):
            generated = generate_maps(
                str(project_dir),
                runner=self.support._fake_autogrid,
            )
        self.assertTrue(generated["ok"], generated)
        configured = generate_vina_config(str(project_dir))
        self.assertTrue(configured["ok"], configured)
        vina = Path(root) / "vina.exe"
        vina.write_bytes(b"mock Vina 1.2.7 executable")
        with patch(
            "dockstart_core.project.vina_adapter.detect",
            return_value=self.support._tool(
                "vina",
                "AutoDock Vina",
                "1.2.7",
                vina,
            ),
        ):
            prepared = prepare_vina_run(str(project_dir))
        self.assertTrue(prepared["ok"], prepared)
        return project_dir, prepared

    @staticmethod
    def _successful_vina(
        command: list[str],
        cwd: str | Path,
        stdout_path: str | Path,
        stderr_path: str | Path,
        log_path: str | Path,
        **_kwargs: object,
    ) -> ManagedRunResult:
        Path(stdout_path).write_text(VINA_LOG, encoding="utf-8")
        Path(stderr_path).write_text("", encoding="utf-8")
        Path(log_path).write_text(VINA_LOG, encoding="utf-8")
        output = Path(cwd) / command[command.index("--out") + 1]
        output.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
        return ManagedRunResult(pid=None, exit_code=0)

    @staticmethod
    def _rewrite_frozen_manifest(
        project_dir: Path,
        prepared: dict[str, object],
        mutator,
    ) -> dict[str, object]:
        metadata = copy.deepcopy(prepared["metadata"])
        manifest_record = metadata["snapshots"]["ad4_maps"]["manifest"]
        manifest_path = project_dir / manifest_record["relative_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutator(manifest)
        payload = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        manifest_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        manifest_record["sha256"] = digest
        manifest_record["size_bytes"] = len(payload)
        input_hashes = metadata.get("input_sha256")
        if isinstance(input_hashes, dict):
            input_hashes["maps_manifest"] = digest
        return metadata

    def test_six_decimal_actual_size_accepts_high_precision_spacing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            receptor = Path(root) / "receptor_tz.pdbqt"
            self._marker_receptor(receptor)
            manifest = self._coverage_manifest(receptor)

            coverage, issue = _ad4zn_frozen_box_coverage(
                receptor,
                manifest,
            )

            self.assertEqual(issue, "")
            self.assertIsNotNone(coverage)
            self.assertEqual(
                manifest["grid"]["actual_size"]["x"],
                20.0,
            )
            self.assertNotEqual(
                60 * manifest["grid"]["spacing"],
                manifest["grid"]["actual_size"]["x"],
            )

            outside_tolerance = copy.deepcopy(manifest)
            outside_tolerance["grid"]["actual_size"]["x"] += 0.000002
            _coverage, issue = _ad4zn_frozen_box_coverage(
                receptor,
                outside_tolerance,
            )
            self.assertIn("实际网格尺寸", issue)

    def test_missing_or_self_consistently_tampered_coverage_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            receptor = Path(root) / "receptor_tz.pdbqt"
            self._marker_receptor(receptor)
            manifest = self._coverage_manifest(receptor)

            missing = copy.deepcopy(manifest)
            missing["ad4zn"].pop("box_coverage")
            _coverage, issue = _ad4zn_frozen_box_coverage(
                receptor,
                missing,
            )
            self.assertIn("缺少现代 ZN/TZ Box 覆盖记录", issue)

            tampered = copy.deepcopy(manifest)
            tampered_coverage = tampered["ad4zn"]["box_coverage"]
            tampered_coverage["markers"]["ZN"]["coordinate_angstrom"][
                "x"
            ] = 0.25
            tampered["ad4zn"]["box_coverage_sha256"] = (
                _canonical_json_sha256(tampered_coverage)
            )
            _coverage, issue = _ad4zn_frozen_box_coverage(
                receptor,
                tampered,
            )
            self.assertIn("覆盖记录与冻结输入重算结果不一致", issue)

    def test_execute_gate_rejects_deleted_or_tampered_frozen_coverage(
        self,
    ) -> None:
        mutations = {
            "deleted": lambda manifest: (
                manifest["ad4zn"].pop("box_coverage"),
                manifest["ad4zn"].pop("box_coverage_sha256"),
            ),
            "tampered": self._tamper_coverage_with_matching_digest,
        }
        for label, mutation in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as root,
            ):
                project_dir, prepared = self._ready_run(root)
                metadata = self._rewrite_frozen_manifest(
                    project_dir,
                    prepared,
                    mutation,
                )

                rejected = _validate_execute_prerequisites(
                    str(project_dir),
                    str(prepared["run_id"]),
                    metadata,
                )

                self.assertFalse(rejected["ok"], rejected)
                self.assertEqual(
                    rejected["error"]["code"],
                    "RUN_AD4ZN_PROTOCOL_BINDING_MISMATCH",
                )
                if label == "deleted":
                    self.assertIn(
                        "缺少现代 ZN/TZ Box 覆盖记录",
                        rejected["error"]["raw_error"],
                    )
                else:
                    self.assertIn(
                        "覆盖记录与冻结输入重算结果不一致",
                        rejected["error"]["raw_error"],
                    )

    @staticmethod
    def _tamper_coverage_with_matching_digest(
        manifest: dict[str, object],
    ) -> None:
        coverage = manifest["ad4zn"]["box_coverage"]
        coverage["markers"]["TZ"]["coordinate_angstrom"]["z"] += 0.25
        manifest["ad4zn"]["box_coverage_sha256"] = (
            _canonical_json_sha256(coverage)
        )

    def test_semantically_valid_nonofficial_parameter_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            official = Path(root) / "official.dat"
            official.write_text(support.PARAMETER_TEXT, encoding="utf-8")
            self.assertEqual(
                _ad4zn_parameter_reference_issue(official),
                "",
            )

            rewritten = Path(root) / "rewritten.dat"
            rewritten.write_text(
                support.PARAMETER_TEXT
                + "# harmless local rewrite keeps the parameter table valid\n",
                encoding="utf-8",
            )
            validation = validate_parameter_file(rewritten)
            self.assertTrue(validation["ok"], validation)
            self.assertEqual(
                validation["sha256"],
                hashlib.sha256(rewritten.read_bytes()).hexdigest(),
            )
            self.assertFalse(validation["matches_reference_sha256"])

            issue = _ad4zn_parameter_reference_issue(rewritten)

            self.assertIn("canonical LF SHA256", issue)
            self.assertIn("v1.2.7", issue)

    def test_frozen_receptor_requires_exactly_one_zn_and_one_tz(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            receptor = Path(root) / "receptor_tz.pdbqt"
            base = (
                _marker_line(1, "ZN", 0.0, 0.0, 0.0)
                + _marker_line(2, "TZ", -1.155, -1.155, -1.155)
            )
            receptor.write_text(base, encoding="utf-8")
            self.assertEqual(_ad4zn_prepared_receptor_issue(receptor), "")

            cases = {
                "double_zn": (
                    base + _marker_line(3, "ZN", 4.0, 0.0, 0.0),
                    "恰好包含一个 ZN；当前为 2",
                ),
                "double_tz": (
                    base + _marker_line(3, "TZ", 4.0, 0.0, 0.0),
                    "恰好包含一个 TZ；当前为 2",
                ),
                "double_zn_and_tz": (
                    base
                    + _marker_line(3, "ZN", 4.0, 0.0, 0.0)
                    + _marker_line(4, "TZ", 4.0, 1.0, 0.0),
                    "恰好包含一个 ZN；当前为 2",
                ),
            }
            for label, (payload, expected) in cases.items():
                with self.subTest(label=label):
                    receptor.write_text(payload, encoding="utf-8")
                    self.assertIn(
                        expected,
                        _ad4zn_prepared_receptor_issue(receptor),
                    )

    def test_report_uses_verified_marker_coordinates_and_box_bounds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir, prepared = self._ready_run(root)
            run_id = str(prepared["run_id"])
            vina = Path(root) / "vina.exe"
            process_identity = {
                "pid": os.getpid(),
                "executable_path": sys.executable,
                "creation_token": "ad4zn-frozen-contract",
            }
            with (
                patch(
                    "dockstart_core.project.vina_adapter.detect",
                    return_value=self.support._tool(
                        "vina",
                        "AutoDock Vina",
                        "1.2.7",
                        vina,
                    ),
                ),
                patch(
                    "dockstart_core.project.vina_adapter.run_managed",
                    side_effect=self._successful_vina,
                ),
                patch(
                    "dockstart_core.project.vina_adapter.get_process_identity",
                    return_value=process_identity,
                ),
            ):
                executed = execute_prepared_vina_run(
                    str(project_dir),
                    run_id,
                )
            self.assertTrue(executed["ok"], executed)
            analyzed = analyze_vina_run_results(
                str(project_dir),
                run_id,
            )
            self.assertTrue(analyzed["ok"], analyzed)

            report = build_markdown_report(str(project_dir), run_id)

            self.assertTrue(report["ok"], report)
            report_text = report["report_text"]
            manifest_path = (
                project_dir
                / prepared["metadata"]["snapshots"]["ad4_maps"][
                    "manifest"
                ]["relative_path"]
            )
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            coverage = manifest["ad4zn"]["box_coverage"]
            for marker_type in ("ZN", "TZ"):
                self.assertIn(
                    json.dumps(
                        coverage["markers"][marker_type][
                            "coordinate_angstrom"
                        ],
                        ensure_ascii=False,
                    ),
                    report_text,
                )
            for key in (
                "requested_box_bounds_angstrom",
                "effective_grid_bounds_angstrom",
            ):
                self.assertIn(
                    json.dumps(
                        coverage[key],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    report_text,
                )
            self.assertIn("| ZN/TZ 位于请求 Box | 通过 |", report_text)
            self.assertIn("| ZN/TZ 位于实际网格 | 通过 |", report_text)


if __name__ == "__main__":
    unittest.main()
