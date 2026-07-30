from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.advanced_protocols import (  # noqa: E402
    ProtocolValidationError,
    build_meeko_macrocycle_plan,
    build_meeko_receptor_flex_plan,
    build_mk_export_plan,
    build_vina_flex_arguments,
    execute_meeko_macrocycle,
    execute_meeko_receptor_flex,
    execute_mk_export,
    extract_meeko_bad_residues,
    inspect_meeko_ligand_pdbqt,
    main,
    normalize_meeko_receptor_controls,
    parse_flexible_residue,
    validate_meeko_receptor_atom_partition,
    validate_flexible_residues,
    validate_macrocycle_options,
)


def _pdb_atom(
    serial: int,
    atom_name: str,
    residue_name: str,
    chain: str,
    residue_number: int,
    *,
    record: str = "ATOM",
    altloc: str = "",
    insertion_code: str = "",
    x: float = 1.0,
    y: float = 2.0,
    z: float = 3.0,
    element: str = "C",
) -> str:
    return (
        f"{record:<6}{serial:5d} {atom_name:^4}{altloc:1}{residue_name:>3} {chain:1}"
        f"{residue_number:4d}{insertion_code:1}   {x:8.3f}{y:8.3f}{z:8.3f}"
        f"  1.00 20.00          {element:>2}\n"
    )


def _receptor_pdb() -> str:
    return "".join(
        [
            _pdb_atom(1, "N", "ALA", "A", 42, element="N"),
            _pdb_atom(2, "CA", "ALA", "A", 42),
            _pdb_atom(3, "CB", "THR", "A", 43, altloc="A"),
            _pdb_atom(4, "CB", "THR", "A", 43, altloc="B", x=1.2),
            _pdb_atom(5, "O", "HOH", "A", 500, record="HETATM", element="O"),
            _pdb_atom(6, "C1", "LIG", "B", 1, record="HETATM"),
            _pdb_atom(7, "CA", "GLY", "A", 44, insertion_code="B"),
        ]
    )


def _complex_receptor_pdb() -> str:
    return "".join(
        [
            _pdb_atom(1, "CA", "GLY", "A", 27, altloc="A"),
            _pdb_atom(2, "CA", "GLY", "A", 27, altloc="B", x=1.2),
            _pdb_atom(3, "SG", "CYS", "A", 191, element="S"),
            _pdb_atom(4, "CA", "TRP", "A", 192, altloc="A"),
            _pdb_atom(5, "CA", "TRP", "A", 192, altloc="B", x=1.2),
            _pdb_atom(6, "SG", "CYS", "A", 220, element="S"),
            _pdb_atom(7, "CA", "TRP", "A", 221, insertion_code="A"),
            _pdb_atom(8, "C1", "BEN", "A", 250, record="HETATM"),
        ]
    )


def _complex_receptor_controls() -> dict[str, object]:
    return {
        "schema_version": 1,
        "alternate_locations": {
            "A:27": "A",
            "A:192": "B",
        },
        "template_assignments": {
            "A:191": "CYX",
            "A:220": "CYX",
        },
        "deleted_residues": [
            {
                "selector": "A:250",
                "expected_component_id": "BEN",
                "reason": "co_crystal_ligand",
            }
        ],
    }


def _write_flex_partition_triplet(basename: Path) -> None:
    rigid = _pdb_atom(1, "N", "ALA", "A", 42, element="N")
    flex = _pdb_atom(2, "CA", "ALA", "A", 42)
    receptor_json = {
        "monomers": {
            "A:42": {
                "molsetup": {
                    "atoms": [
                        {
                            "index": 0,
                            "pdbinfo": ["N", "ALA", 42, "", "A"],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                        {
                            "index": 1,
                            "pdbinfo": ["CA", "ALA", 42, "", "A"],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                    ]
                },
                "is_flexres_atom": [False, True],
            }
        }
    }
    Path(str(basename) + "_rigid.pdbqt").write_text(rigid, encoding="utf-8")
    Path(str(basename) + "_flex.pdbqt").write_text(flex, encoding="utf-8")
    Path(str(basename) + ".json").write_text(
        json.dumps(receptor_json) + "\n",
        encoding="utf-8",
    )


def _write_insertion_code_partition_triplet(
    basename: Path,
    *,
    flex_text: str | None = None,
) -> None:
    rigid = _pdb_atom(1, "N", "ALA", "A", 42, element="N")
    flex_atom_without_fixed_column_icode = _pdb_atom(
        2,
        "CA",
        "TRP",
        "A",
        221,
    )
    if flex_text is None:
        flex_text = (
            "BEGIN_RES TRP A 221A\n"
            f"{flex_atom_without_fixed_column_icode}"
            "END_RES TRP A 221A\n"
        )
    receptor_json = {
        "monomers": {
            "A:42": {
                "molsetup": {
                    "atoms": [
                        {
                            "index": 0,
                            "pdbinfo": ["N", "ALA", 42, "", "A"],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                    ]
                },
                "is_flexres_atom": [False],
            },
            "A:221A": {
                "molsetup": {
                    "atoms": [
                        {
                            "index": 0,
                            "pdbinfo": ["CA", "TRP", 221, "A", "A"],
                            "coord": [1.0, 2.0, 3.0],
                            "is_ignore": False,
                        },
                    ]
                },
                "is_flexres_atom": [True],
            },
        }
    }
    Path(str(basename) + "_rigid.pdbqt").write_text(rigid, encoding="utf-8")
    Path(str(basename) + "_flex.pdbqt").write_text(flex_text, encoding="utf-8")
    Path(str(basename) + ".json").write_text(
        json.dumps(receptor_json) + "\n",
        encoding="utf-8",
    )


def _macrocycle_pdbqt(*, topology: bool = True) -> str:
    remarks = "REMARK SMILES C1CCCCCCC1\nREMARK SMILES IDX 1 1 2 2 3 3 4 4\n" if topology else ""
    return remarks + """ROOT
ATOM      1  C1  LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 CG0
ATOM      2  C2  LIG A   1       1.400   0.000   0.000  1.00  0.00     0.000 CG0
BRANCH   1   3
ATOM      3  G   LIG A   1       1.400   0.000   0.000  1.00  0.00     0.000 G0
ENDBRANCH   1   3
BRANCH   2   4
ATOM      4  G   LIG A   1       0.000   0.000   0.000  1.00  0.00     0.000 G1
ENDBRANCH   2   4
ENDROOT
TORSDOF 3
"""


class FlexibleResidueParsingTests(unittest.TestCase):
    def test_normalizes_explicit_and_compact_insertion_codes(self) -> None:
        explicit = parse_flexible_residue("A:44:B")
        compact = parse_flexible_residue("A:44B")

        self.assertEqual(explicit, compact)
        self.assertEqual(explicit.canonical, "A:44:B")
        self.assertEqual(explicit.meeko_id, "A:44B")

    def test_rejects_shell_like_or_ambiguous_selector(self) -> None:
        for value in ("A:42;rm", "A 42", "A::42", "A:42:BB"):
            with self.subTest(value=value), self.assertRaises(ProtocolValidationError) as raised:
                parse_flexible_residue(value)
            self.assertEqual(raised.exception.code, "INVALID_FLEX_RESIDUE_ID")

    def test_extracts_unique_bad_residues_from_meeko_diagnostics(self) -> None:
        output = """No template matched for residue_key='A:226'
No template matched for residue_key='A:229'
- Template matching failed for: ['A:226', 'A:229'] Ignored due to allow_bad_res.
"""
        self.assertEqual(extract_meeko_bad_residues(output), ["A:226", "A:229"])


class FlexibleResidueValidationTests(unittest.TestCase):
    def test_validates_polymer_residue_from_original_pdb(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdb = Path(temp_dir) / "receptor.pdb"
            pdb.write_text(_receptor_pdb(), encoding="utf-8")

            result = validate_flexible_residues(pdb, ["A:42", "A:42"])

        self.assertEqual(result["source_format"], "pdb")
        self.assertEqual(result["meeko_flexres"], ["A:42"])
        self.assertEqual(result["residues"][0]["residue_name"], "ALA")
        self.assertEqual(result["residues"][0]["source"], "原始结构")

    def test_rejects_water_and_nonpolymer_components(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdb = Path(temp_dir) / "receptor.pdb"
            pdb.write_text(_receptor_pdb(), encoding="utf-8")

            with self.assertRaises(ProtocolValidationError) as water:
                validate_flexible_residues(pdb, ["A:500"])
            with self.assertRaises(ProtocolValidationError) as ligand:
                validate_flexible_residues(pdb, ["B:1"])

        self.assertEqual(water.exception.code, "FLEX_RESIDUE_IS_WATER")
        self.assertEqual(ligand.exception.code, "FLEX_RESIDUE_NOT_POLYMER")

    def test_requires_explicit_altloc_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdb = Path(temp_dir) / "receptor.pdb"
            pdb.write_text(_receptor_pdb(), encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as unresolved:
                validate_flexible_residues(pdb, ["A:43"])

            resolved = validate_flexible_residues(
                pdb,
                ["A:43"],
                resolved_altlocs={"A:43": "B"},
            )

        self.assertEqual(unresolved.exception.code, "UNRESOLVED_ALTERNATE_LOCATION")
        self.assertEqual(resolved["wanted_altlocs"], ["A:43=B"])
        self.assertEqual(resolved["residues"][0]["selected_altloc"], "B")

    def test_reads_atom_site_from_mmcif_without_guessing_hetatm(self) -> None:
        mmcif_text = """data_test
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.pdbx_PDB_ins_code
ATOM 1 CA . ALA AA 42 ?
ATOM 2 CB . ALA AA 42 ?
HETATM 3 O . HOH AA 500 ?
#
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            cif = Path(temp_dir) / "receptor.cif"
            cif.write_text(mmcif_text, encoding="utf-8")

            result = validate_flexible_residues(cif, ["AA:42"])
            with self.assertRaises(ProtocolValidationError) as water:
                validate_flexible_residues(cif, ["AA:500"])

        self.assertEqual(result["source_format"], "mmcif")
        self.assertEqual(result["residues"][0]["atom_count"], 2)
        self.assertEqual(water.exception.code, "FLEX_RESIDUE_IS_WATER")

    def test_refuses_pdbqt_as_flexible_residue_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdbqt = Path(temp_dir) / "receptor.pdbqt"
            pdbqt.write_text("ATOM      1  C   ALA A  42\n", encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as raised:
                validate_flexible_residues(pdbqt, ["A:42"])

        self.assertEqual(raised.exception.code, "UNSUPPORTED_RECEPTOR_FORMAT")


class FlexibleProtocolPlanTests(unittest.TestCase):
    def test_builds_meeko_rigid_flex_json_triplet_and_safe_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdb = root / "receptor.pdb"
            pdb.write_text(_receptor_pdb(), encoding="utf-8")

            result = build_meeko_receptor_flex_plan(
                sys.executable,
                pdb,
                root / "receptor_flexible",
                ["A:42", "A:44:B"],
            )

        self.assertIn("--read_pdb", result["argv"])
        self.assertIn("--write_pdbqt", result["argv"])
        self.assertIn("--write_json", result["argv"])
        self.assertEqual(result["argv"].count("--flexres"), 2)
        self.assertTrue(result["outputs"]["rigid_pdbqt"].endswith("_rigid.pdbqt"))
        self.assertTrue(result["outputs"]["flex_pdbqt"].endswith("_flex.pdbqt"))
        self.assertTrue(result["outputs"]["receptor_json"].endswith(".json"))
        self.assertNotIn("--allow_bad_res", result["argv"])
        self.assertNotIn("--read_with_prody", result["argv"])
        self.assertFalse(result["requires_prody"])

    def test_direct_mmcif_plan_is_blocked_until_project_bridge_is_verified(self) -> None:
        mmcif_text = """data_test
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_seq_id
_atom_site.pdbx_PDB_ins_code
ATOM 1 CA . ALA A 42 ?
#
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cif = root / "receptor.cif"
            cif.write_text(mmcif_text, encoding="utf-8")

            with self.assertRaises(ProtocolValidationError) as raised:
                build_meeko_receptor_flex_plan(
                    sys.executable,
                    cif,
                    root / "receptor_flexible",
                    ["A:42"],
                )

        self.assertEqual(
            raised.exception.code,
            "FLEX_MMCIF_VERIFIED_BRIDGE_REQUIRED",
        )

    def test_allow_bad_res_requires_and_records_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdb = root / "receptor.pdb"
            pdb.write_text(_receptor_pdb(), encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as missing:
                build_meeko_receptor_flex_plan(
                    sys.executable,
                    pdb,
                    root / "missing_ack",
                    ["A:42"],
                    allow_bad_res=True,
                )
            plan = build_meeko_receptor_flex_plan(
                sys.executable,
                pdb,
                root / "reviewed",
                ["A:42"],
                allow_bad_res=True,
                acknowledged_bad_residues=["A:226", "A:226"],
            )

        self.assertEqual(missing.exception.code, "FLEX_BAD_RESIDUE_ACKNOWLEDGEMENT_REQUIRED")
        self.assertIn("--allow_bad_res", plan["argv"])
        self.assertEqual(
            plan["scientific_review"]["acknowledged_bad_residues"],
            ["A:226"],
        )

    def test_builds_vina_flex_fragment_only_for_existing_pdbqt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            flex = Path(temp_dir) / "receptor_flex.pdbqt"
            flex.write_text("BEGIN_RES ALA A 42\nEND_RES ALA A 42\n", encoding="utf-8")
            arguments = build_vina_flex_arguments(flex)

        self.assertEqual(arguments, ["--flex", str(flex)])


class StructuredReceptorControlTests(unittest.TestCase):
    def test_builds_only_reviewed_meeko_controls_and_freezes_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "complex.pdb"
            receptor.write_text(_complex_receptor_pdb(), encoding="utf-8")
            plan = build_meeko_receptor_flex_plan(
                sys.executable,
                receptor,
                root / "prepared",
                ["A:192", "A:221:A"],
                receptor_controls=_complex_receptor_controls(),
            )

        argv = plan["argv"]
        self.assertNotIn("--allow_bad_res", argv)
        self.assertEqual(
            set(argv[argv.index("--wanted_altloc") + 1].split(",")),
            {"A:27=A", "A:192=B"},
        )
        self.assertEqual(
            set(argv[argv.index("--set_template") + 1].split(",")),
            {"A:191=CYX", "A:220=CYX"},
        )
        self.assertEqual(
            argv[argv.index("--delete_residues") + 1],
            "A:250",
        )
        self.assertEqual(plan["receptor_controls_mode"], "structured_contract")
        self.assertFalse(plan["receptor_controls"]["allow_bad_res"])
        self.assertRegex(plan["receptor_controls_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            plan["receptor_controls_fingerprint"]["sha256"],
            plan["receptor_controls_sha256"],
        )

    def test_fingerprint_is_stable_across_input_order_and_selector_spelling(self) -> None:
        first = _complex_receptor_controls()
        second = {
            "deleted_residues": [
                {
                    "reason": "CO_CRYSTAL_LIGAND",
                    "expected_component_id": "ben",
                    "selector": "A:250",
                }
            ],
            "template_assignments": {
                "A:220": "cyx",
                "A:191": "CYX",
            },
            "alternate_locations": {
                "A:192": "b",
                "A:27": "a",
            },
            "schema_version": 1,
        }
        normalized_first = normalize_meeko_receptor_controls(first)
        normalized_second = normalize_meeko_receptor_controls(second)

        self.assertEqual(normalized_first, normalized_second)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "complex.pdb"
            receptor.write_text(_complex_receptor_pdb(), encoding="utf-8")
            first_plan = build_meeko_receptor_flex_plan(
                sys.executable,
                receptor,
                root / "first",
                ["A:192", "A:221:A"],
                receptor_controls=first,
            )
            second_plan = build_meeko_receptor_flex_plan(
                sys.executable,
                receptor,
                root / "second",
                ["A:192", "A:221A"],
                receptor_controls=second,
            )
        self.assertEqual(
            first_plan["receptor_controls_sha256"],
            second_plan["receptor_controls_sha256"],
        )

    def test_rejects_free_form_unknown_fields_and_invalid_schema(self) -> None:
        invalid_contracts = (
            {**_complex_receptor_controls(), "argv": ["--allow_bad_res"]},
            {**_complex_receptor_controls(), "extra_args": "--allow_bad_res"},
            {**_complex_receptor_controls(), "schema_version": 2},
            {**_complex_receptor_controls(), "allow_bad_res": True},
        )
        expected_codes = (
            "UNKNOWN_MEEKO_RECEPTOR_CONTROL",
            "UNKNOWN_MEEKO_RECEPTOR_CONTROL",
            "INVALID_MEEKO_RECEPTOR_CONTROLS_SCHEMA",
            "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
        )
        for controls, expected_code in zip(
            invalid_contracts,
            expected_codes,
            strict=True,
        ):
            with self.subTest(controls=controls), self.assertRaises(
                ProtocolValidationError
            ) as raised:
                normalize_meeko_receptor_controls(controls)
            self.assertEqual(raised.exception.code, expected_code)

    def test_rejects_duplicate_or_illegal_normalized_selectors(self) -> None:
        duplicate_altloc = {
            "schema_version": 1,
            "alternate_locations": {
                "A:221:A": "A",
                "A:221A": "A",
            },
            "template_assignments": {},
            "deleted_residues": [],
        }
        duplicate_delete = {
            "schema_version": 1,
            "alternate_locations": {},
            "template_assignments": {},
            "deleted_residues": [
                {
                    "selector": "A:250",
                    "expected_component_id": "BEN",
                    "reason": "co_crystal_ligand",
                },
                {
                    "selector": "A:250",
                    "expected_component_id": "BEN",
                    "reason": "co_crystal_ligand",
                },
            ],
        }
        illegal = {
            "schema_version": 1,
            "alternate_locations": {"A:27;--allow_bad_res": "A"},
            "template_assignments": {},
            "deleted_residues": [],
        }
        for controls, code in (
            (duplicate_altloc, "DUPLICATE_MEEKO_RECEPTOR_CONTROL_SELECTOR"),
            (duplicate_delete, "DUPLICATE_MEEKO_RECEPTOR_CONTROL_SELECTOR"),
            (illegal, "INVALID_FLEX_RESIDUE_ID"),
        ):
            with self.subTest(code=code), self.assertRaises(
                ProtocolValidationError
            ) as raised:
                normalize_meeko_receptor_controls(controls)
            self.assertEqual(raised.exception.code, code)

    def test_rejects_delete_conflicts_unknown_selectors_and_component_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            receptor = Path(temp_dir) / "complex.pdb"
            receptor.write_text(_complex_receptor_pdb(), encoding="utf-8")

            conflict = _complex_receptor_controls()
            conflict["alternate_locations"] = {
                **conflict["alternate_locations"],  # type: ignore[dict-item]
                "A:250": "A",
            }
            with self.assertRaises(ProtocolValidationError) as conflict_error:
                normalize_meeko_receptor_controls(
                    conflict,
                    structure_path=receptor,
                    flexible_selections=["A:192"],
                )

            unknown = _complex_receptor_controls()
            unknown["template_assignments"] = {"A:999": "CYX"}
            with self.assertRaises(ProtocolValidationError) as unknown_error:
                normalize_meeko_receptor_controls(
                    unknown,
                    structure_path=receptor,
                    flexible_selections=["A:192"],
                )

            mismatch = _complex_receptor_controls()
            mismatch["deleted_residues"] = [
                {
                    "selector": "A:250",
                    "expected_component_id": "ATP",
                    "reason": "co_crystal_ligand",
                }
            ]
            with self.assertRaises(ProtocolValidationError) as mismatch_error:
                normalize_meeko_receptor_controls(
                    mismatch,
                    structure_path=receptor,
                    flexible_selections=["A:192"],
                )

        self.assertEqual(
            conflict_error.exception.code,
            "MEEKO_RECEPTOR_CONTROL_CONFLICT",
        )
        self.assertEqual(
            unknown_error.exception.code,
            "MEEKO_RECEPTOR_CONTROL_SELECTOR_NOT_FOUND",
        )
        self.assertEqual(
            mismatch_error.exception.code,
            "MEEKO_RECEPTOR_COMPONENT_MISMATCH",
        )

    def test_structured_controls_reject_legacy_altloc_or_bad_res_switches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "complex.pdb"
            receptor.write_text(_complex_receptor_pdb(), encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as altloc_conflict:
                build_meeko_receptor_flex_plan(
                    sys.executable,
                    receptor,
                    root / "altloc_conflict",
                    ["A:192"],
                    resolved_altlocs={"A:192": "B"},
                    receptor_controls=_complex_receptor_controls(),
                )
            with self.assertRaises(ProtocolValidationError) as bad_res:
                build_meeko_receptor_flex_plan(
                    sys.executable,
                    receptor,
                    root / "bad_res",
                    ["A:192", "A:221:A"],
                    receptor_controls=_complex_receptor_controls(),
                    allow_bad_res=True,
                    acknowledged_bad_residues=["A:999"],
                )

        self.assertEqual(
            altloc_conflict.exception.code,
            "MEEKO_RECEPTOR_ALTLOC_SOURCE_CONFLICT",
        )
        self.assertEqual(
            bad_res.exception.code,
            "MEEKO_RECEPTOR_ALLOW_BAD_RES_FORBIDDEN",
        )


class MacrocycleProtocolTests(unittest.TestCase):
    def test_validates_macrocycle_options_strictly(self) -> None:
        options = validate_macrocycle_options(
            {
                "mode": "auto",
                "min_ring_size": 8,
                "double_bond_penalty": 120,
                "allow_aromatic_breaks": True,
            }
        )
        self.assertEqual(options["min_ring_size"], 8)
        self.assertTrue(options["allow_aromatic_breaks"])
        for invalid in (
            {"mode": "guess"},
            {"min_ring_size": 2},
            {"double_bond_penalty": 50.5},
            {"allow_aromatic_breaks": "yes"},
            {"unreviewed_option": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ProtocolValidationError):
                validate_macrocycle_options(invalid)

    def test_builds_auto_and_rigid_meeko_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "ligand.sdf"
            ligand.write_text("mock sdf\n", encoding="utf-8")
            auto = build_meeko_macrocycle_plan(
                sys.executable,
                ligand,
                root / "auto.pdbqt",
                {"mode": "auto", "min_ring_size": 8, "allow_aromatic_breaks": True},
            )
            rigid = build_meeko_macrocycle_plan(
                sys.executable,
                ligand,
                root / "rigid.pdbqt",
                {"mode": "rigid"},
            )

        self.assertIn("--min_ring_size", auto["argv"])
        self.assertIn("--double_bond_penalty", auto["argv"])
        self.assertIn("--macrocycle_allow_A", auto["argv"])
        self.assertNotIn("--rigid_macrocycles", auto["argv"])
        self.assertIn("--rigid_macrocycles", rigid["argv"])
        self.assertNotIn("--min_ring_size", rigid["argv"])

    def test_inspects_glue_atoms_branches_torsdof_and_metadata_breaks(self) -> None:
        metadata = {"setup": {"ring_closure_info": {"bonds_removed": [[0, 7]]}}}
        with tempfile.TemporaryDirectory() as temp_dir:
            pdbqt = Path(temp_dir) / "macrocycle.pdbqt"
            pdbqt.write_text(_macrocycle_pdbqt(), encoding="utf-8")
            result = inspect_meeko_ligand_pdbqt(pdbqt, metadata)

        self.assertEqual(len(result["glue_pseudo_atoms"]), 2)
        self.assertEqual(len(result["closure_anchor_atoms"]), 2)
        self.assertEqual(result["branch_count"], 2)
        self.assertEqual(result["endbranch_count"], 2)
        self.assertEqual(result["torsdof"], 3)
        self.assertTrue(result["embedded_topology"])
        self.assertTrue(result["macrocycle_evidence"])
        self.assertEqual(result["metadata_ring_breaks"][0]["atom_indices"], [0, 7])

    def test_does_not_guess_exact_broken_bond_without_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdbqt = Path(temp_dir) / "macrocycle.pdbqt"
            pdbqt.write_text(_macrocycle_pdbqt(), encoding="utf-8")
            result = inspect_meeko_ligand_pdbqt(pdbqt)

        self.assertEqual(result["metadata_ring_breaks"], [])
        self.assertTrue(any("不得猜测具体键" in warning for warning in result["warnings"]))


class MeekoExportPlanTests(unittest.TestCase):
    def test_builds_mk_export_only_with_embedded_original_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_pdbqt = root / "out.pdbqt"
            result_pdbqt.write_text(_macrocycle_pdbqt(), encoding="utf-8")
            receptor_json = root / "receptor.json"
            receptor_json.write_text("{}", encoding="utf-8")

            plan = build_mk_export_plan(
                sys.executable,
                result_pdbqt,
                root / "poses.sdf",
                receptor_json=receptor_json,
                output_receptor_pdb=root / "receptor_poses.pdb",
                keep_flexres_sdf=True,
            )

        self.assertIn("--write_sdf", plan["argv"])
        self.assertIn("--read_json", plan["argv"])
        self.assertIn("--write_pdb", plan["argv"])
        self.assertIn("--keep_flexres_sdf", plan["argv"])
        self.assertEqual(plan["topology_evidence"]["source"], "对接结果 PDBQT 的 Meeko REMARK")

    def test_rejects_sdf_export_when_original_topology_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_pdbqt = root / "out.pdbqt"
            result_pdbqt.write_text(_macrocycle_pdbqt(topology=False), encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as raised:
                build_mk_export_plan(sys.executable, result_pdbqt, root / "poses.sdf")

        self.assertEqual(raised.exception.code, "MISSING_ORIGINAL_TOPOLOGY")
        self.assertIn("不会根据距离猜测键级", raised.exception.suggestion)


class AdvancedProtocolExecutionTests(unittest.TestCase):
    pdbqt_output = (
        "ROOT\n"
        "ATOM      1  C1  LIG A   1       0.000   0.000   0.000"
        "  1.00  0.00     0.000 C\n"
        "ENDROOT\nTORSDOF 0\n"
    )

    def test_flex_partition_proves_complete_disjoint_atom_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            basename = Path(temp_dir) / "receptor"
            _write_flex_partition_triplet(basename)

            evidence = validate_meeko_receptor_atom_partition(
                Path(str(basename) + "_rigid.pdbqt"),
                Path(str(basename) + "_flex.pdbqt"),
                Path(str(basename) + ".json"),
            )

        self.assertEqual(evidence["rigid_atom_count"], 1)
        self.assertEqual(evidence["flex_atom_count"], 1)
        self.assertEqual(evidence["total_atom_count"], 2)
        self.assertEqual(evidence["overlap_atom_count"], 0)
        self.assertRegex(evidence["partition_sha256"], r"^[0-9a-f]{64}$")

    def test_flex_partition_restores_insertion_code_from_residue_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            basename = Path(temp_dir) / "receptor"
            _write_insertion_code_partition_triplet(basename)

            evidence = validate_meeko_receptor_atom_partition(
                Path(str(basename) + "_rigid.pdbqt"),
                Path(str(basename) + "_flex.pdbqt"),
                Path(str(basename) + ".json"),
            )

        self.assertEqual(evidence["rigid_atom_count"], 1)
        self.assertEqual(evidence["flex_atom_count"], 1)
        self.assertEqual(evidence["total_atom_count"], 2)

    def test_flex_partition_rejects_nested_or_unterminated_boundaries(self) -> None:
        atom = _pdb_atom(2, "CA", "TRP", "A", 221)
        cases = (
            (
                "BEGIN_RES TRP A 221A\n"
                "BEGIN_RES TRP A 222A\n"
                f"{atom}"
                "END_RES TRP A 222A\n"
                "END_RES TRP A 221A\n",
                "FLEX_PARTITION_BOUNDARY_NESTED",
            ),
            (
                "BEGIN_RES TRP A 221A\n"
                f"{atom}",
                "FLEX_PARTITION_BOUNDARY_UNTERMINATED",
            ),
        )
        for flex_text, expected_code in cases:
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as temp_dir:
                basename = Path(temp_dir) / "receptor"
                _write_insertion_code_partition_triplet(
                    basename,
                    flex_text=flex_text,
                )
                with self.assertRaises(ProtocolValidationError) as raised:
                    validate_meeko_receptor_atom_partition(
                        Path(str(basename) + "_rigid.pdbqt"),
                        Path(str(basename) + "_flex.pdbqt"),
                        Path(str(basename) + ".json"),
                    )
            self.assertEqual(raised.exception.code, expected_code)

    def test_flex_partition_rejects_boundary_end_or_atom_identity_conflict(self) -> None:
        cases = (
            (
                "BEGIN_RES TRP A 221A\n"
                f"{_pdb_atom(2, 'CA', 'TRP', 'A', 221)}"
                "END_RES TRP A 221B\n",
                "FLEX_PARTITION_BOUNDARY_MISMATCH",
            ),
            (
                "BEGIN_RES TRP A 221A\n"
                f"{_pdb_atom(2, 'CA', 'TRP', 'B', 221)}"
                "END_RES TRP A 221A\n",
                "FLEX_PARTITION_BOUNDARY_ATOM_CONFLICT",
            ),
        )
        for flex_text, expected_code in cases:
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as temp_dir:
                basename = Path(temp_dir) / "receptor"
                _write_insertion_code_partition_triplet(
                    basename,
                    flex_text=flex_text,
                )
                with self.assertRaises(ProtocolValidationError) as raised:
                    validate_meeko_receptor_atom_partition(
                        Path(str(basename) + "_rigid.pdbqt"),
                        Path(str(basename) + "_flex.pdbqt"),
                        Path(str(basename) + ".json"),
                    )
            self.assertEqual(raised.exception.code, expected_code)

    def test_flex_partition_rejects_atom_present_in_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            basename = Path(temp_dir) / "receptor"
            _write_flex_partition_triplet(basename)
            rigid = Path(str(basename) + "_rigid.pdbqt")
            flex = Path(str(basename) + "_flex.pdbqt")
            flex.write_bytes(rigid.read_bytes())

            with self.assertRaises(ProtocolValidationError) as raised:
                validate_meeko_receptor_atom_partition(
                    rigid,
                    flex,
                    Path(str(basename) + ".json"),
                )

        self.assertEqual(raised.exception.code, "FLEX_PARTITION_OUTPUT_OVERLAP")

    def test_flex_success_publishes_complete_triplet_and_records_contract(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append((argv, kwargs))
            basename = Path(argv[argv.index("--output_basename") + 1])
            _write_flex_partition_triplet(basename)
            return SimpleNamespace(returncode=0, stdout="prepared\n", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            output_basename = root / "prepared" / "receptor_flexible"
            output_basename.parent.mkdir()
            record_dir = root / "records" / "flex_001"
            record_dir.parent.mkdir()

            result = execute_meeko_receptor_flex(
                sys.executable,
                receptor,
                output_basename,
                (value for value in ["A:42"]),
                record_dir=record_dir,
                runner=runner,
            )

            self.assertTrue(Path(str(output_basename) + "_rigid.pdbqt").is_file())
            self.assertTrue(Path(str(output_basename) + "_flex.pdbqt").is_file())
            self.assertTrue(Path(str(output_basename) + ".json").is_file())
            saved = json.loads((record_dir / "command_result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "success")
            self.assertEqual(saved["exit_code"], 0)
            self.assertEqual(saved["stdout"], "prepared\n")
            self.assertEqual((record_dir / "stdout.txt").read_text(encoding="utf-8"), "prepared\n")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["capture_output"])
        self.assertTrue(calls[0][1]["text"])
        self.assertFalse(calls[0][1]["shell"])
        self.assertTrue(calls[0][1]["cwd"])

    def test_flex_execution_freezes_structured_receptor_controls(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            basename = Path(argv[argv.index("--output_basename") + 1])
            _write_flex_partition_triplet(basename)
            return SimpleNamespace(returncode=0, stdout="prepared\n", stderr="")

        controls = {
            "schema_version": 1,
            "alternate_locations": {"A:43": "B"},
            "template_assignments": {"A:44:B": "CYX"},
            "deleted_residues": [
                {
                    "selector": "B:1",
                    "expected_component_id": "LIG",
                    "reason": "co_crystal_ligand",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            output_basename = root / "receptor_flexible"
            record_dir = root / "record"
            result = execute_meeko_receptor_flex(
                sys.executable,
                receptor,
                output_basename,
                ["A:42"],
                record_dir=record_dir,
                receptor_controls=controls,
                runner=runner,
            )
            saved = json.loads(
                (record_dir / "command_result.json").read_text(encoding="utf-8")
            )

        self.assertIn("--wanted_altloc", result["requested_command"])
        self.assertIn("--set_template", result["requested_command"])
        self.assertIn("--delete_residues", result["requested_command"])
        self.assertNotIn("--allow_bad_res", result["requested_command"])
        self.assertEqual(
            saved["receptor_controls"]["deleted_residues"][0]["selector"],
            "B:1",
        )
        self.assertRegex(saved["receptor_controls_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            saved["receptor_controls_mode"],
            "structured_contract",
        )
        self.assertEqual(
            saved["scientific_review"]["receptor_controls_mode"],
            "structured_contract",
        )
        self.assertEqual(
            saved["receptor_controls_fingerprint"]["sha256"],
            saved["receptor_controls_sha256"],
        )

    def test_nonzero_exit_records_failure_without_publishing_macrocycle(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=9, stdout="", stderr="meeko failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "ligand.sdf"
            ligand.write_text("mock sdf\n", encoding="utf-8")
            output = root / "ligand.pdbqt"
            record_dir = root / "macrocycle_001"

            with self.assertRaises(ProtocolValidationError) as raised:
                execute_meeko_macrocycle(
                    sys.executable,
                    ligand,
                    output,
                    {"mode": "auto"},
                    record_dir=record_dir,
                    runner=runner,
                )

            saved = json.loads((record_dir / "command_result.json").read_text(encoding="utf-8"))
            self.assertFalse(output.exists())
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["exit_code"], 9)
            self.assertEqual(saved["stderr"], "meeko failed")

        self.assertEqual(raised.exception.code, "PROTOCOL_COMMAND_FAILED")

    def test_flex_strict_failure_returns_reviewable_bad_residue_list(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="No template matched for residue_key='A:226'\n",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            record_dir = root / "strict_failure"
            with self.assertRaises(ProtocolValidationError) as raised:
                execute_meeko_receptor_flex(
                    sys.executable,
                    receptor,
                    root / "receptor_flexible",
                    ["A:42"],
                    record_dir=record_dir,
                    runner=runner,
                )
            saved = json.loads((record_dir / "command_result.json").read_text(encoding="utf-8"))

        self.assertEqual(raised.exception.code, "FLEX_BAD_RESIDUES_REVIEW_REQUIRED")
        self.assertEqual(json.loads(raised.exception.detail)["bad_residues"], ["A:226"])
        self.assertEqual(saved["scientific_review"]["detected_bad_residues"], ["A:226"])

    def test_flex_allow_bad_res_publishes_only_when_actual_list_matches_acknowledgement(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            basename = Path(argv[argv.index("--output_basename") + 1])
            _write_flex_partition_triplet(basename)
            return SimpleNamespace(
                returncode=0,
                stdout="prepared\n",
                stderr="No template matched for residue_key='A:226'\n",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            output_basename = root / "accepted"
            accepted = execute_meeko_receptor_flex(
                sys.executable,
                receptor,
                output_basename,
                ["A:42"],
                record_dir=root / "accepted_record",
                allow_bad_res=True,
                acknowledged_bad_residues=["A:226"],
                runner=runner,
            )

        self.assertIn("--allow_bad_res", accepted["requested_command"])
        self.assertTrue(accepted["scientific_review"]["allow_bad_res"])
        self.assertEqual(accepted["scientific_review"]["detected_bad_residues"], ["A:226"])

    def test_flex_allow_bad_res_rejects_changed_diagnostic_before_publish(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            basename = Path(argv[argv.index("--output_basename") + 1])
            Path(str(basename) + "_rigid.pdbqt").write_text(self.pdbqt_output, encoding="utf-8")
            Path(str(basename) + "_flex.pdbqt").write_text(self.pdbqt_output, encoding="utf-8")
            Path(str(basename) + ".json").write_text('{"ok": true}', encoding="utf-8")
            return SimpleNamespace(
                returncode=0,
                stdout="prepared\n",
                stderr="No template matched for residue_key='A:227'\n",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            output_basename = root / "rejected"
            with self.assertRaises(ProtocolValidationError) as raised:
                execute_meeko_receptor_flex(
                    sys.executable,
                    receptor,
                    output_basename,
                    ["A:42"],
                    record_dir=root / "rejected_record",
                    allow_bad_res=True,
                    acknowledged_bad_residues=["A:226"],
                    runner=runner,
                )

            self.assertFalse(Path(str(output_basename) + "_rigid.pdbqt").exists())
            self.assertFalse(Path(str(output_basename) + "_flex.pdbqt").exists())
            self.assertFalse(Path(str(output_basename) + ".json").exists())

        self.assertEqual(raised.exception.code, "FLEX_BAD_RESIDUES_CHANGED")

    def test_missing_one_flex_output_publishes_none(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            basename = Path(argv[argv.index("--output_basename") + 1])
            Path(str(basename) + "_rigid.pdbqt").write_text(self.pdbqt_output, encoding="utf-8")
            Path(str(basename) + ".json").write_text("{}", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="partial", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receptor = root / "receptor.pdb"
            receptor.write_text(_receptor_pdb(), encoding="utf-8")
            output_basename = root / "receptor_flexible"
            record_dir = root / "flex_missing"

            with self.assertRaises(ProtocolValidationError) as raised:
                execute_meeko_receptor_flex(
                    sys.executable,
                    receptor,
                    output_basename,
                    ["A:42"],
                    record_dir=record_dir,
                    runner=runner,
                )

            self.assertFalse(Path(str(output_basename) + "_rigid.pdbqt").exists())
            self.assertFalse(Path(str(output_basename) + "_flex.pdbqt").exists())
            self.assertFalse(Path(str(output_basename) + ".json").exists())
            self.assertEqual(list(root.glob(".dockstart-*")), [])
            saved = json.loads((record_dir / "command_result.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")

        self.assertEqual(raised.exception.code, "DECLARED_OUTPUT_MISSING")

    def test_macrocycle_success_validates_before_publish(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            output = Path(argv[argv.index("--out") + 1])
            output.write_text(self.pdbqt_output, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="warning")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "ligand.sdf"
            ligand.write_text("mock sdf\n", encoding="utf-8")
            output = root / "ligand.pdbqt"
            result = execute_meeko_macrocycle(
                sys.executable,
                ligand,
                output,
                {"mode": "rigid"},
                record_dir=root / "macrocycle_success",
                runner=runner,
            )

            self.assertTrue(output.is_file())
            self.assertEqual(result["published_outputs"]["ligand_pdbqt"], str(output.resolve()))

    def test_mk_export_publishes_sdf_and_receptor_together(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            sdf = Path(argv[argv.index("--write_sdf") + 1])
            receptor = Path(argv[argv.index("--write_pdb") + 1])
            sdf.write_text("pose\n$$$$\n", encoding="utf-8")
            receptor.write_text(
                "ATOM      1  C   ALA A   1       0.000   0.000   0.000\n",
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stdout="exported", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_pdbqt = root / "out.pdbqt"
            result_pdbqt.write_text(_macrocycle_pdbqt(), encoding="utf-8")
            receptor_json = root / "receptor.json"
            receptor_json.write_text("{}", encoding="utf-8")
            output_sdf = root / "poses.sdf"
            output_receptor = root / "receptor_poses.pdb"

            result = execute_mk_export(
                sys.executable,
                result_pdbqt,
                output_sdf,
                record_dir=root / "export_001",
                receptor_json=receptor_json,
                output_receptor_pdb=output_receptor,
                runner=runner,
            )

            self.assertTrue(output_sdf.is_file())
            self.assertTrue(output_receptor.is_file())
            self.assertEqual(set(result["published_outputs"]), {"ligand_sdf", "updated_receptor_pdb"})

    def test_mk_export_rejects_missing_topology_before_runner(self) -> None:
        called = False

        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            nonlocal called
            called = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_pdbqt = root / "out.pdbqt"
            result_pdbqt.write_text(_macrocycle_pdbqt(topology=False), encoding="utf-8")
            with self.assertRaises(ProtocolValidationError) as raised:
                execute_mk_export(
                    sys.executable,
                    result_pdbqt,
                    root / "poses.sdf",
                    record_dir=root / "export_rejected",
                    runner=runner,
                )

        self.assertEqual(raised.exception.code, "MISSING_ORIGINAL_TOPOLOGY")
        self.assertFalse(called)


class AdvancedProtocolCliTests(unittest.TestCase):
    def test_cli_writes_one_structured_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdbqt = Path(temp_dir) / "macrocycle.pdbqt"
            pdbqt.write_text(_macrocycle_pdbqt(), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(["inspect-ligand", "--pdbqt", str(pdbqt)])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["torsdof"], 3)

    def test_cli_validation_error_is_structured_json(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(["inspect-ligand", "--pdbqt", "missing.pdbqt"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INPUT_FILE_NOT_FOUND")

    def test_cli_execute_uses_transactional_runner_and_returns_record(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> SimpleNamespace:
            output = Path(argv[argv.index("--out") + 1])
            output.write_text(AdvancedProtocolExecutionTests.pdbqt_output, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="cli ok", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ligand = root / "ligand.sdf"
            ligand.write_text("mock sdf\n", encoding="utf-8")
            output = root / "ligand.pdbqt"
            record_dir = root / "cli_record"
            stdout = io.StringIO()
            with patch("dockstart_core.advanced_protocols.subprocess.run", side_effect=runner):
                with redirect_stdout(stdout):
                    exit_code = main(
                        [
                            "macrocycle-plan",
                            "--ligand",
                            str(ligand),
                            "--output-pdbqt",
                            str(output),
                            "--execute",
                            "--record-dir",
                            str(record_dir),
                        ]
                    )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["status"], "success")
            self.assertTrue(output.is_file())
            self.assertTrue((record_dir / "command_result.json").is_file())


if __name__ == "__main__":
    unittest.main()
