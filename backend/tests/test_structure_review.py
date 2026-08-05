from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.models import ToolCheckResult  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    create_project,
    get_run_preflight,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
)
from dockstart_core.structure_review import (  # noqa: E402
    STRUCTURE_REVIEW_PATH_REDACTION,
    build_structure_review,
    format_structure_review_text,
)


def _pdb_line(
    record: str,
    serial: int,
    atom: str,
    residue: str,
    chain: str,
    number: int,
    element: str,
    *,
    altloc: str = "",
) -> str:
    return (
        f"{record:<6}{serial:5d} {atom:<4}{altloc:1}{residue:>3} {chain:1}{number:4d}    "
        f"{float(serial):8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{0.0:6.2f}          {element:>2}\n"
    )


def _ligand_sdf() -> str:
    return """Charged two-component ligand
DockStart

  3  1  0  0  0  0            999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.3000    0.0000    0.0000 N   0  0  1  0  0  0  0  0  0  0  0  0
    5.0000    0.0000    0.0000 Cl  0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  1  0  0  0
M  CHG  2   2   1   3  -1
M  END
> <PUBCHEM_TOTAL_CHARGE>
0

$$$$
"""


class StructureReviewTests(unittest.TestCase):
    def _create_legacy_review_project(self, root: Path) -> Path:
        (root / "raw").mkdir()
        (root / "raw" / "receptor.pdb").write_text(
            _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C")
            + _pdb_line("HETATM", 2, "O", "HOH", "A", 10, "O"),
            encoding="utf-8",
        )
        project_json = root / "project.json"
        project_json.write_text(
            json.dumps(
                {
                    "project_name": "legacy_review",
                    "created_at": "2026-08-05T00:00:00+00:00",
                    "updated_at": "2026-08-05T00:00:00+00:00",
                    "project_dir": "stale/machine/path",
                    "receptor": {"raw_file": "raw/receptor.pdb", "file": ""},
                    "ligand": {"raw_file": "", "file": ""},
                    "box": {},
                    "vina": {},
                    "runs": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return project_json

    def _run_structure_review_cli(
        self,
        project_dir: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "dockstart_core.preparation",
                "structure-review",
                str(project_dir),
                *arguments,
            ],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def _run_structure_review_cli_without_project(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "dockstart_core.preparation",
                "structure-review",
            ],
            cwd=BACKEND_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_existing_receptor_pdbqt_reports_observable_facts_without_guessing_chemistry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared"
            prepared.mkdir()
            receptor = prepared / "receptor.pdbqt"
            receptor.write_text(
                _pdb_line("ATOM", 1, "C1", "ALA", "A", 1, "C").rstrip("\n") + "  0.125 C\n"
                + _pdb_line("ATOM", 2, "N1", "ALA", "A", 1, "N").rstrip("\n") + " -0.250 NA\n"
                + _pdb_line("ATOM", 3, "H1", "ALA", "A", 1, "H").rstrip("\n") + "  0.125 HD\n",
                encoding="utf-8",
            )

            review = build_structure_review(root, receptor_file="prepared/receptor.pdbqt", ligand_file="")
            facts = review["receptor"]["pdbqt"]
            independent_heavy_count = sum(
                1
                for line in receptor.read_text(encoding="utf-8").splitlines()
                if line[:6].strip() in {"ATOM", "HETATM"} and line.split()[-1].upper() not in {"H", "HD", "HS"}
            )

            self.assertEqual(facts["atom_count"], 3)
            self.assertEqual(facts["coordinate_count"], 3)
            self.assertEqual(facts["heavy_atom_count"], independent_heavy_count)
            self.assertEqual(facts["hydrogen_atom_count"], 1)
            self.assertTrue(facts["has_3d_coordinates"])
            self.assertEqual(facts["coordinate_bounds"]["x"], [1.0, 3.0])
            self.assertEqual(facts["chains"], ["A"])
            self.assertEqual(facts["residue_count"], 1)
            self.assertEqual(facts["autodock_atom_types"], ["C", "HD", "NA"])
            self.assertEqual(facts["partial_charge_sum"], 0.0)
            self.assertEqual(facts["receptor_pdbqt_mode"], "rigid")
            self.assertFalse(facts["activity_torsion_applicable"])
            self.assertIsNone(facts["active_torsions"])
            self.assertNotIn("formal_charge", facts)
            self.assertNotIn("contains_salt", facts)
            self.assertIsNone(facts["ion_non_polymer_components"])
            self.assertIsNone(facts["bond_orders"])
            self.assertIsNone(facts["stereochemistry"])
            continuity = next(item for item in review["checks"] if item["key"] == "receptor_continuity")
            self.assertEqual(continuity["status"], "unknown")
            self.assertIn("需要原始 PDB/mmCIF", continuity["message"])

    def test_receptor_pdbqt_rejects_non_finite_partial_charge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared"
            prepared.mkdir()
            receptor = prepared / "receptor.pdbqt"
            receptor.write_text(
                _pdb_line("ATOM", 1, "C1", "ALA", "A", 1, "C").rstrip("\n") + " nan C\n",
                encoding="utf-8",
            )

            facts = build_structure_review(root, receptor_file="prepared/receptor.pdbqt", ligand_file="")["receptor"]["pdbqt"]
            self.assertEqual(facts["partial_charge_count"], 0)
            self.assertIsNone(facts["partial_charge_sum"])

    def test_receptor_pdbqt_with_unparseable_coordinate_is_not_marked_3d_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared"
            prepared.mkdir()
            valid = _pdb_line("ATOM", 1, "C1", "ALA", "A", 1, "C").rstrip("\n") + "  0.000 C\n"
            invalid = _pdb_line("ATOM", 2, "N1", "ALA", "A", 1, "N")
            invalid = invalid[:30] + "     nan" + invalid[38:]
            receptor = prepared / "receptor.pdbqt"
            receptor.write_text(valid + invalid.rstrip("\n") + "  0.000 N\n", encoding="utf-8")

            facts = build_structure_review(root, receptor_file="prepared/receptor.pdbqt", ligand_file="")["receptor"]["pdbqt"]
            self.assertEqual(facts["atom_count"], 2)
            self.assertEqual(facts["coordinate_count"], 1)
            self.assertFalse(facts["has_3d_coordinates"])
            self.assertIsNone(facts["coordinate_bounds"])

    def test_flexible_receptor_is_identified_from_flex_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prepared = root / "prepared"
            prepared.mkdir()
            receptor = prepared / "receptor_flex.pdbqt"
            receptor.write_text(
                "BEGIN_RES TYR A 42\n"
                + _pdb_line("ATOM", 1, "CB", "TYR", "A", 42, "C").rstrip("\n") + "  0.000 C\n"
                + _pdb_line("ATOM", 2, "CG", "TYR", "A", 42, "C").rstrip("\n") + "  0.000 A\n"
                + "BRANCH 1 2\nENDBRANCH 1 2\nEND_RES TYR A 42\n",
                encoding="utf-8",
            )

            facts = build_structure_review(root, receptor_file="prepared/receptor_flex.pdbqt", ligand_file="")["receptor"]["pdbqt"]
            self.assertEqual(facts["receptor_pdbqt_mode"], "flexible")
            self.assertTrue(facts["activity_torsion_applicable"])
            self.assertEqual(facts["active_torsions"], 1)

    def test_reports_receptor_records_and_ligand_chemistry_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            (root / "prepared").mkdir()
            (root / "preparation" / "ligand_001").mkdir(parents=True)
            receptor = root / "raw" / "receptor.pdb"
            receptor.write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C")
                + _pdb_line("ATOM", 2, "CA", "SER", "A", 2, "C", altloc="A")
                + _pdb_line("HETATM", 3, "O", "HOH", "A", 10, "O")
                + _pdb_line("HETATM", 4, "ZN", "ZN", "A", 11, "ZN")
                + _pdb_line("HETATM", 5, "C1", "FAD", "A", 12, "C")
                + _pdb_line("ATOM", 6, "CB", "ALA", "A", 1, "C"),
                encoding="utf-8",
            )
            ligand_raw = root / "raw" / "ligand.sdf"
            ligand_raw.write_text(_ligand_sdf(), encoding="utf-8")
            ligand_pdbqt = root / "prepared" / "ligand.pdbqt"
            ligand_pdbqt.write_text(
                "REMARK SMILES C[NH3+].[Cl-]\n"
                "REMARK SMILES IDX 1 1 2 2 3 3\n"
                + _pdb_line("HETATM", 1, "C1", "LIG", "B", 1, "C").rstrip("\n")
                + "  0.100 C\n"
                + _pdb_line("HETATM", 2, "N1", "LIG", "B", 1, "N").rstrip("\n")
                + " -0.100 N\n"
                + "ROOT\nTORSDOF 1\n",
                encoding="utf-8",
            )
            metadata = root / "preparation" / "ligand_001" / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "prep_id": "ligand_001",
                        "method": "rdkit_meeko",
                        "status": "finished",
                        "rdkit_version": "2026.03.3",
                        "meeko_version": "0.7.1",
                    },
                ),
                encoding="utf-8",
            )

            review = build_structure_review(
                root,
                receptor_file="",
                ligand_file="prepared/ligand.pdbqt",
                receptor_raw_file="raw/receptor.pdb",
                ligand_raw_file="raw/ligand.sdf",
                ligand_metadata_file="preparation/ligand_001/metadata.json",
            )

            self.assertFalse(review["scientific_validation"])
            self.assertEqual(review["receptor"]["chains"], ["A"])
            self.assertEqual(review["receptor"]["heavy_atom_count"], 6)
            self.assertTrue(review["receptor"]["has_3d_coordinates"])
            self.assertEqual(len(review["receptor"]["interrupted_residues"]), 1)
            self.assertEqual(review["receptor"]["water_residue_count"], 1)
            self.assertEqual(review["receptor"]["metals"][0]["element"], "ZN")
            self.assertEqual(review["receptor"]["nonstandard_residues"][0]["residue_name"], "FAD")
            self.assertEqual(review["receptor"]["alternate_locations"], ["A"])
            self.assertNotIn("formal_charge", review["receptor"]["raw"])
            self.assertNotIn("contains_salt", review["receptor"]["raw"])
            self.assertIsNone(review["receptor"]["raw"]["residue_template_anomalies"])
            self.assertEqual(review["ligand"]["raw"]["heavy_atom_count"], 3)
            self.assertEqual(review["ligand"]["raw"]["formal_charge"], 0)
            self.assertEqual(review["ligand"]["raw"]["fragment_count"], 2)
            self.assertTrue(review["ligand"]["raw"]["contains_salt"])
            self.assertFalse(review["ligand"]["raw"]["has_3d_coordinates"])
            self.assertEqual(review["ligand"]["pdbqt"]["torsdof"], 1)
            self.assertTrue(review["ligand"]["pdbqt"]["has_3d_coordinates"])
            self.assertEqual(review["ligand"]["pdbqt"]["formal_charge"], 0)
            self.assertEqual(review["provenance"]["ligand"]["meeko_version"], "0.7.1")
            checks = {item["key"]: item for item in review["checks"]}
            self.assertEqual(checks["receptor_continuity"]["status"], "warning")
            self.assertEqual(checks["ligand_fragments"]["status"], "warning")
            self.assertEqual(checks["ligand_tautomer"]["status"], "unknown")

    def test_run_preflight_exposes_non_blocking_structure_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_project("review_project", temp_dir)
            project_dir = Path(created["project_dir"])
            receptor_source = Path(temp_dir) / "receptor.pdbqt"
            ligand_source = Path(temp_dir) / "ligand.pdbqt"
            receptor_source.write_text(_pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"), encoding="utf-8")
            ligand_source.write_text(
                "REMARK SMILES CCO\n"
                + _pdb_line("HETATM", 1, "C1", "LIG", "B", 1, "C").rstrip("\n")
                + "  0.000 C\nROOT\nTORSDOF 0\n",
                encoding="utf-8",
            )
            self.assertTrue(import_receptor_pdbqt(str(project_dir), str(receptor_source))["ok"])
            self.assertTrue(import_ligand_pdbqt(str(project_dir), str(ligand_source))["ok"])
            vina = ToolCheckResult(
                key="vina",
                name="AutoDock Vina",
                status="ok",
                version="1.2.7",
                path="mock-vina",
                message="可用",
                source="auto",
            )

            with patch("dockstart_core.project.vina_adapter.detect", return_value=vina):
                response = get_run_preflight(str(project_dir))

            self.assertTrue(response["ok"])
            self.assertTrue(response["ready"])
            self.assertFalse(response["structure_review"]["scientific_validation"])
            keys = {item["key"] for item in response["checks"]}
            self.assertIn("receptor_structure_review", keys)
            self.assertIn("ligand_structure_review", keys)
            self.assertTrue(all(not item["blocking"] for item in response["checks"] if item["key"].endswith("structure_review")))

    def test_discovers_unique_related_representations_when_project_record_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            (root / "prepared").mkdir()
            (root / "raw" / "sample_receptor.pdb").write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"),
                encoding="utf-8",
            )
            prepared = root / "prepared" / "receptor.pdbqt"
            prepared.write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C").rstrip("\n") + "  0.000 C\n",
                encoding="utf-8",
            )

            review = build_structure_review(
                root,
                receptor_file=str(prepared),
                ligand_file="",
                receptor_raw_file="raw/missing.pdb",
            )

            self.assertEqual(review["receptor"]["pdbqt"]["atom_count"], 1)
            self.assertTrue(review["receptor"]["pdbqt"]["has_3d_coordinates"])
            self.assertEqual(review["receptor"]["raw"]["atom_count"], 1)
            self.assertEqual(
                {item["source"] for item in review["receptor"]["representations"]},
                {"项目记录", "项目目录唯一候选"},
            )

    def test_does_not_guess_between_ambiguous_raw_receptor_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            for name in ("alpha_receptor.pdb", "beta_receptor.pdb"):
                (root / "raw" / name).write_text(
                    _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"),
                    encoding="utf-8",
                )

            review = build_structure_review(root, receptor_file="", ligand_file="")

            self.assertEqual(review["receptor"]["raw"], {})
            self.assertEqual(review["receptor"]["representations"], [])

    def test_valid_raw_representation_is_used_when_recorded_pdbqt_has_no_atoms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            (root / "prepared").mkdir()
            (root / "raw" / "receptor.pdb").write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"),
                encoding="utf-8",
            )
            (root / "prepared" / "receptor.pdbqt").write_text(
                "REMARK malformed export without atom records\n",
                encoding="utf-8",
            )

            review = build_structure_review(
                root,
                receptor_file="prepared/receptor.pdbqt",
                receptor_raw_file="raw/receptor.pdb",
                ligand_file="",
            )

            self.assertEqual(review["receptor"]["pdbqt"], {})
            self.assertEqual(review["receptor"]["raw"]["atom_count"], 1)
            self.assertEqual(review["receptor"]["atom_count"], 1)
            self.assertEqual(
                {item["format"] for item in review["receptor"]["representations"]},
                {"pdb", "pdbqt"},
            )

    def test_inferred_directory_symlink_outside_project_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            outside_file = outside / "outside_receptor.pdb"
            outside_file.write_text(
                _pdb_line("ATOM", 1, "CA", "ALA", "A", 1, "C"),
                encoding="utf-8",
            )
            raw_link = root / "raw"
            try:
                raw_link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"当前平台无权创建目录符号链接：{exc}")

            review = build_structure_review(root, receptor_file="", ligand_file="")
            text_output = format_structure_review_text(
                {
                    "ok": True,
                    "project_dir": str(root),
                    "structure_review": review,
                    "error": None,
                },
            )

            self.assertEqual(review["receptor"]["raw"], {})
            self.assertEqual(review["receptor"]["representations"], [])
            path_checks = [
                item
                for item in review["checks"]
                if item.get("detail") == "STRUCTURE_REVIEW_PATH_OUTSIDE_PROJECT"
            ]
            self.assertTrue(path_checks, review["checks"])
            self.assertTrue(all(item["status"] == "warning" for item in path_checks))
            self.assertNotIn(str(outside), text_output)
            self.assertNotIn(outside_file.read_text(encoding="utf-8"), text_output)

    def test_inferred_directory_enumeration_error_is_structured_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured_stderr = io.StringIO()

            with redirect_stderr(captured_stderr), patch.object(
                Path,
                "iterdir",
                side_effect=OSError("mock directory read failure at /private/structure"),
            ):
                review = build_structure_review(root, receptor_file="", ligand_file="")

            path_checks = [
                item
                for item in review["checks"]
                if item.get("detail") == "STRUCTURE_REVIEW_PATH_READ_ERROR"
            ]
            self.assertTrue(path_checks, review["checks"])
            self.assertEqual(captured_stderr.getvalue(), "")
            self.assertTrue(all(item["blocking"] is False for item in path_checks))

    def test_inferred_directory_resolve_error_is_structured_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "raw").mkdir()
            captured_stderr = io.StringIO()
            original_resolve = Path.resolve

            def resolve_with_raw_failure(path: Path, strict: bool = False) -> Path:
                if path.name == "raw":
                    raise OSError(r"mock resolve failure at C:\private\raw")
                return original_resolve(path, strict=strict)

            with redirect_stderr(captured_stderr), patch.object(
                Path,
                "resolve",
                new=resolve_with_raw_failure,
            ):
                review = build_structure_review(root, receptor_file="", ligand_file="")

            path_checks = [
                item
                for item in review["checks"]
                if item.get("detail") == "STRUCTURE_REVIEW_PATH_READ_ERROR"
            ]
            self.assertTrue(path_checks, review["checks"])
            self.assertEqual(captured_stderr.getvalue(), "")
            self.assertTrue(all("C:\\private" not in item["message"] for item in path_checks))

    def test_text_formatter_redacts_absolute_paths_from_all_untrusted_fields(self) -> None:
        payload = {
            "ok": True,
            "project_dir": r"C:\work\visible-project",
            "structure_review": {
                "scientific_validation": False,
                "disclaimer": "内部记录位于 /opt/private/review.txt",
                "receptor": {
                    "representations": [
                        {
                            "file": r"C:\Users\secret\receptor.pdb",
                            "format": "PDB",
                            "source": r"来源 \\server\private\receptor",
                        },
                    ],
                },
                "ligand": {
                    "representations": [
                        {
                            "file": "raw/ligand.sdf",
                            "format": "SDF",
                            "source": "镜像 /srv/private/ligand.sdf",
                        },
                    ],
                },
                "checks": [
                    {
                        "role": "receptor",
                        "status": "warning",
                        "name": "检查 C:/private/name.txt",
                        "message": r"消息包含 D:\private\message.txt",
                        "evidence": r"\\server\share\evidence.json",
                    },
                    {
                        "role": "ligand",
                        "status": "unknown",
                        "name": "检查 /home/reviewer/name.txt",
                        "message": "消息包含 //server/share/message.txt",
                        "evidence": "raw/ligand.sdf",
                    },
                ],
            },
            "error": None,
        }
        error_payload = {
            "ok": False,
            "error": {
                "code": "STRUCTURE_REVIEW_READ_ERROR",
                "message": r"读取 C:\private\project.json 失败",
                "suggestion": "请检查 /var/private/project",
            },
        }

        text_output = format_structure_review_text(payload)
        error_output = format_structure_review_text(error_payload)

        for secret in (
            r"C:\Users\secret",
            r"\\server\private",
            "/srv/private",
            "C:/private",
            r"D:\private",
            r"\\server\share",
            "/home/reviewer",
            "//server/share",
            "/opt/private",
        ):
            self.assertNotIn(secret, text_output)
        self.assertNotIn(r"C:\private", error_output)
        self.assertNotIn("/var/private", error_output)
        self.assertGreaterEqual(text_output.count(STRUCTURE_REVIEW_PATH_REDACTION), 9)
        self.assertEqual(error_output.count(STRUCTURE_REVIEW_PATH_REDACTION), 2)
        self.assertIn("raw/ligand.sdf", text_output)

    def test_headless_text_cli_is_path_safe_and_does_not_persist_legacy_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_json = self._create_legacy_review_project(root)
            original_bytes = project_json.read_bytes()
            original_sha256 = hashlib.sha256(original_bytes).hexdigest()
            files_before = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )

            completed = self._run_structure_review_cli(root, "--format", "text")

            files_after = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )
            current_bytes = project_json.read_bytes()

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertIn("DockStart 结构审查摘要（只读）", completed.stdout)
            self.assertIn("raw/receptor.pdb", completed.stdout)
            self.assertIn("格式：PDB", completed.stdout)
            self.assertIn("可观察事实", completed.stdout)
            self.assertIn("[警告 / warning]", completed.stdout)
            self.assertIn("[未知 / unknown]", completed.stdout)
            self.assertIn("Docking score 仅供结构结合趋势参考，不能替代实验验证。", completed.stdout)
            self.assertNotIn(str(root), completed.stdout)
            self.assertEqual(current_bytes, original_bytes)
            self.assertEqual(hashlib.sha256(current_bytes).hexdigest(), original_sha256)
            self.assertEqual(files_after, files_before)
            self.assertEqual(list(root.glob("project.json.schema-v*.bak*")), [])
            self.assertFalse((root / ".project.lock").exists())

    def test_headless_structure_review_keeps_default_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_json = self._create_legacy_review_project(root)
            original_bytes = project_json.read_bytes()

            completed = self._run_structure_review_cli(root)
            payload = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                set(payload),
                {"ok", "project_dir", "structure_review", "error"},
            )
            self.assertTrue(payload["ok"], payload)
            self.assertFalse(payload["structure_review"]["scientific_validation"])
            self.assertEqual(project_json.read_bytes(), original_bytes)

    def test_headless_structure_review_rejects_invalid_format_with_chinese_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_json = self._create_legacy_review_project(root)
            original_bytes = project_json.read_bytes()

            completed = self._run_structure_review_cli(root, "--format", "yaml")
            payload = json.loads(completed.stdout)

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["error"]["code"],
                "STRUCTURE_REVIEW_FORMAT_INVALID",
            )
            self.assertIn("仅支持 json 或 text", payload["error"]["message"])
            self.assertEqual(project_json.read_bytes(), original_bytes)

    def test_headless_text_business_failure_is_nonzero_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_project = Path(temp_dir) / "missing-project"
            missing_project.mkdir()

            completed = self._run_structure_review_cli(
                missing_project,
                "--format",
                "text",
            )

            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, "")
            self.assertIn("DockStart 结构审查失败", completed.stdout)
            self.assertIn("PROJECT_JSON_NOT_FOUND", completed.stdout)
            self.assertNotIn(str(missing_project), completed.stdout)

    def test_headless_legacy_missing_project_argument_keeps_exit_zero_json_error(self) -> None:
        completed = self._run_structure_review_cli_without_project()
        payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "STRUCTURE_REVIEW_ARGS")
        self.assertEqual(payload["error"]["suggestion"], "")


if __name__ == "__main__":
    unittest.main()
