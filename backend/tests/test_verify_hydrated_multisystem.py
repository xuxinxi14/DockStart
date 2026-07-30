from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "verify_hydrated_multisystem.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_hydrated_multisystem",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": str(path),
        "size_bytes": len(payload),
        "sha256": _sha(payload),
    }


class _ToolResult(SimpleNamespace):
    def to_dict(self) -> dict[str, object]:
        return dict(vars(self))


def _tool(
    path: Path,
    version: str,
    *,
    source: str = "configured",
    capabilities: dict[str, object] | None = None,
) -> _ToolResult:
    return _ToolResult(
        status="ok",
        path=str(path),
        version=version,
        source=source,
        is_bundled=False,
        capabilities=capabilities or {},
        message="",
        raw_error="",
    )


def _model(mode: int, water_count: int = 1, affinity: float = -5.0) -> list[str]:
    lines = [
        f"MODEL {mode}",
        f"REMARK VINA RESULT: {affinity - mode / 100:.3f} 0.000 0.000",
    ]
    for index in range(water_count):
        lines.append(
            "HETATM"
            f"{index + 1:5d}  WAT HOH     1"
            "       0.000   0.000   0.000  1.00  0.00     0.000 W"
        )
    lines.append("ENDMDL")
    return lines


def _coverage(
    center: dict[str, float] | None = None,
) -> dict[str, object]:
    selected_center = center or {
        "x": 29.356,
        "y": -27.247,
        "z": -28.98,
    }
    return {
        "covers_requested_box": True,
        "interval_semantics": "closed",
        "box_center_angstrom": selected_center,
        "requested_box_size_angstrom": {"x": 20.0, "y": 20.0, "z": 20.0},
        "effective_grid_spacing_angstrom": 0.375,
        "effective_grid_axis_intervals": {"x": 54, "y": 54, "z": 54},
        "effective_grid_size_angstrom": {"x": 20.25, "y": 20.25, "z": 20.25},
        "axis_coverage": {
            key: {
                "covers_requested_interval": True,
                "lower_margin_angstrom": 0.125,
                "upper_margin_angstrom": 0.125,
                "minimum_margin_angstrom": 0.125,
            }
            for key in ("x", "y", "z")
        },
    }


def _model_server_sdf(
    *,
    atom_x: str = "0.0000",
    bond_order: int = 1,
    properties: tuple[tuple[str, str], ...] = (),
    newline: str = "\n",
) -> bytes:
    lines = [
        "LOB",
        "  RCSB ModelServer",
        "",
        "  2  1  0  0  0  0  0  0  0  0999 V2000",
        f"   {atom_x}    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0",
        "    1.0000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0",
        f"  1  2  {bond_order}  0  0  0  0",
        "M  END",
    ]
    for title, value in properties:
        lines.extend((f"> <{title}>", value, ""))
    lines.append("$$$$")
    return (newline.join(lines) + newline).encode("utf-8")


class HydratedMultisystemVerifierTests(unittest.TestCase):
    def test_utf8_stdio_configuration_uses_reconfigure_when_available(
        self,
    ) -> None:
        class Reconfigurable:
            def __init__(self) -> None:
                self.calls: list[dict[str, str]] = []

            def reconfigure(self, **kwargs: str) -> None:
                self.calls.append(kwargs)

        stdout = Reconfigurable()
        stderr = Reconfigurable()
        with (
            mock.patch.object(VERIFY.sys, "stdout", stdout),
            mock.patch.object(VERIFY.sys, "stderr", stderr),
        ):
            evidence = VERIFY._configure_utf8_standard_streams()
        self.assertEqual(evidence, {"stdout": True, "stderr": True})
        self.assertEqual(
            stdout.calls,
            [{"encoding": "utf-8", "errors": "strict"}],
        )
        self.assertEqual(stderr.calls, stdout.calls)
        self.assertFalse(VERIFY._reconfigure_utf8_stream(object()))

    def test_manifest_is_exact_metadata_only_contract(self) -> None:
        manifest = VERIFY._load_manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(
            manifest["fixture_id"],
            "hydrated_multisystem_rcsb_external",
        )
        self.assertEqual(manifest["distribution"], "metadata_only")
        self.assertEqual(tuple(manifest["systems"]), ("2BYS", "2ZJU"))
        for flag in VERIFY.MANIFEST_CONTAINS_FLAGS:
            self.assertIs(manifest[flag], False)
        self.assertEqual(
            {path.name for path in VERIFY.MANIFEST_PATH.parent.iterdir()},
            {"README.md", "source_manifest.json"},
        )
        self.assertEqual(
            manifest["protocol"]["grid"]["grid_points"],
            {"x": 54, "y": 54, "z": 54},
        )
        self.assertEqual(
            manifest["protocol"]["vina"]["allowed_continuous_mode_ids"],
            list(range(1, 10)),
        )
        self.assertEqual(manifest["protocol"]["vina"]["verbosity"], 1)
        self.assertEqual(
            manifest["systems"]["2BYS"]["hydrated_ligand"]["water_count"],
            6,
        )
        self.assertEqual(
            manifest["systems"]["2ZJU"]["hydrated_ligand"]["water_count"],
            7,
        )
        self.assertIn(
            "Cl",
            manifest["systems"]["2ZJU"]["hydrated_ligand"]["atom_types"],
        )
        self.assertNotIn(
            "CL",
            manifest["systems"]["2ZJU"]["hydrated_ligand"]["atom_types"],
        )
        expected_receptor_sources = {
            "2BYS": (
                "https://files.rcsb.org/download/2BYS.pdb",
                1649484,
                "14721f05b346205c9f73c981c0ddd6bd382fc63a19e07736c846e82ae3b9ea08",
            ),
            "2ZJU": (
                "https://files.rcsb.org/download/2ZJU.pdb",
                768366,
                "f57daf824e3efb34bd2c4f6b91003200672109f2f4fadd3ceebe36bb340c37aa",
            ),
        }
        expected_ligand_sources = {
            "2BYS": (
                "https://models.rcsb.org/v1/2BYS/ligand?"
                "auth_asym_id=A&auth_seq_id=301&encoding=sdf",
                2412,
                "8c635031ce8d24534cf53f9e166886137bdf168cc7012e71b3835a485ab9d76c",
            ),
            "2ZJU": (
                    "https://models.rcsb.org/v1/2ZJU/ligand?"
                    "auth_asym_id=A&auth_seq_id=301&encoding=sdf",
                1654,
                "9f1301be36b2d68edd498dc46416797ce56f3f34151b9f6a470e85e053401e6f",
            ),
        }
        for system_id, expected in expected_receptor_sources.items():
            source = manifest["systems"][system_id]["receptor_source"]
            self.assertEqual(
                (source["url"], source["size_bytes"], source["sha256"]),
                expected,
            )
        for system_id, expected in expected_ligand_sources.items():
            source = manifest["systems"][system_id]["ligand_source"]
            self.assertEqual(
                (
                    source["url"],
                    source["canonical_size_bytes"],
                    source["canonical_sha256"],
                ),
                expected,
            )
            self.assertEqual(
                source["canonicalization"],
                "model_server_sdf_mol_block_lf_v1",
            )
            self.assertNotIn("size_bytes", source)
            self.assertNotIn("sha256", source)
        controls_2bys = manifest["systems"]["2BYS"]["receptor_preparation"]
        controls_2zju = manifest["systems"]["2ZJU"]["receptor_preparation"]
        self.assertEqual(
            len(controls_2bys["receptor_controls"]["alternate_locations"]),
            69,
        )
        self.assertEqual(
            controls_2zju["receptor_controls"]["alternate_locations"],
            {
                "A:120": "A",
                "C:12": "A",
                "D:120": "A",
                "E:120": "A",
            },
        )
        self.assertEqual(
            controls_2bys["receptor_controls_sha256"],
            "6d06874e1e585c0dffcd15741b5e8829d3d3f0445e948808e82a493912e522a8",
        )
        self.assertEqual(
            controls_2zju["receptor_controls_sha256"],
            "13494b49ce2a01a1680bc41445544d720bed32a9865583dc82384d1238b13ed9",
        )
        self.assertEqual(
            controls_2bys["receptor_controls"]["deleted_residues"],
            [
                {
                    "selector": f"{chain}:301",
                    "expected_component_id": "LOB",
                    "reason": "co_crystal_ligand",
                }
                for chain in "ABCDEFGHIJ"
            ],
        )
        self.assertTrue(
            {
                "A:74",
                "A:161",
                "A:193",
                "A:202",
                "B:7",
                "B:70",
                "C:74",
                "D:161",
                "F:74",
                "F:161",
                "F:193",
                "F:202",
                "G:7",
                "H:74",
                "I:161",
            }.issubset(
                controls_2bys["receptor_controls"]["alternate_locations"]
            )
        )
        self.assertEqual(
            controls_2zju["receptor_controls"]["deleted_residues"],
            [
                {
                    "selector": selector,
                    "expected_component_id": "IM4",
                    "reason": "co_crystal_ligand",
                }
                for selector in (
                    "A:301",
                    "C:301",
                    "D:301",
                    "D:302",
                    "E:301",
                )
            ],
        )

    def test_manifest_rejects_extra_fixture_bytes(self) -> None:
        source = VERIFY._load_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            path = fixture / "source_manifest.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            (fixture / "README.md").write_text("metadata", encoding="utf-8")
            (fixture / "2ZJU.pdb").write_bytes(b"forbidden")
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_manifest_contract(source, manifest_path=path)
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_MANIFEST_INVALID",
        )

    def test_download_source_accepts_exact_mocked_bytes(self) -> None:
        payload = b"exact rcsb bytes"
        record = {
            "url": "https://example.test/source.pdb",
            "size_bytes": len(payload),
            "sha256": _sha(payload),
        }
        calls: list[tuple[str, int, int]] = []

        def fetcher(url: str, maximum: int, timeout: int) -> object:
            calls.append((url, maximum, timeout))
            return {
                "payload": payload,
                "final_url": "https://cdn.example.test/source.pdb",
            }

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "source.pdb"
            evidence = VERIFY._download_source(
                record,
                destination,
                maximum_bytes=100,
                fetcher=fetcher,
                timeout=17,
            )
            self.assertEqual(destination.read_bytes(), payload)
        self.assertEqual(
            calls,
            [("https://example.test/source.pdb", 100, 17)],
        )
        self.assertEqual(evidence["sha256"], _sha(payload))
        self.assertTrue(evidence["final_url"].startswith("https://"))

    def test_model_server_sdf_identity_ignores_all_property_blocks(self) -> None:
        first = _model_server_sdf(
            properties=(
                ("model_server_result.job_id", "short"),
                ("model_server_result.datetime_utc", "2026-07-30T00:00:00Z"),
                ("model_server_stats.query_time_ms", "1"),
                ("model_server_result.entry_id", "2BYS"),
            ),
            newline="\r\n",
        )
        second = _model_server_sdf(
            properties=(
                ("model_server_result.job_id", "a-much-longer-dynamic-job-id"),
                ("model_server_result.datetime_utc", "2099-12-31T23:59:59Z"),
                ("model_server_stats.query_time_ms", "999999"),
                ("model_server_result.entry_id", "2bys"),
                ("new_non_scientific_property", "also ignored"),
            ),
        )
        first_canonical = VERIFY._canonicalize_model_server_sdf(first)
        second_canonical = VERIFY._canonicalize_model_server_sdf(second)
        self.assertEqual(first_canonical, second_canonical)
        self.assertTrue(first_canonical.endswith(b"M  END\n\n"))
        self.assertNotIn(b"$$$$", first_canonical)
        record = {
            "url": "https://example.test/ligand.sdf",
            "canonicalization": "model_server_sdf_mol_block_lf_v1",
            "canonical_size_bytes": len(first_canonical),
            "canonical_sha256": _sha(first_canonical),
        }
        evidences = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, payload in enumerate((first, second), start=1):
                evidences.append(
                    VERIFY._download_source(
                        record,
                        root / f"ligand_{index}.sdf",
                        maximum_bytes=10_000,
                        fetcher=lambda *_, payload=payload: (
                            payload,
                            "https://cdn.example.test/ligand.sdf",
                        ),
                        timeout=1,
                    )
                )
        self.assertNotEqual(evidences[0]["size_bytes"], evidences[1]["size_bytes"])
        self.assertNotEqual(evidences[0]["sha256"], evidences[1]["sha256"])
        self.assertEqual(
            evidences[0]["canonical_sha256"],
            evidences[1]["canonical_sha256"],
        )
        self.assertEqual(
            evidences[0]["identity_mode"],
            "canonical_scientific_content_v1",
        )

    def test_model_server_sdf_identity_rejects_mol_block_changes(self) -> None:
        original = _model_server_sdf(properties=(("audit", "first"),))
        canonical = VERIFY._canonicalize_model_server_sdf(original)
        record = {
            "url": "https://example.test/ligand.sdf",
            "canonicalization": "model_server_sdf_mol_block_lf_v1",
            "canonical_size_bytes": len(canonical),
            "canonical_sha256": _sha(canonical),
        }
        changed_payloads = (
            _model_server_sdf(
                atom_x="0.1000",
                properties=(("audit", "changed coordinate"),),
            ),
            _model_server_sdf(
                bond_order=2,
                properties=(("audit", "changed bond"),),
            ),
        )
        for payload in changed_payloads:
            with self.subTest(payload_sha256=_sha(payload)):
                with tempfile.TemporaryDirectory() as temporary:
                    with self.assertRaises(
                        VERIFY.HydratedMultisystemError
                    ) as raised:
                        VERIFY._download_source(
                            record,
                            Path(temporary) / "ligand.sdf",
                            maximum_bytes=10_000,
                            fetcher=lambda *_, payload=payload: (
                                payload,
                                "https://cdn.example.test/ligand.sdf",
                            ),
                            timeout=1,
                        )
                self.assertEqual(
                    raised.exception.code,
                    "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
                )

    def test_model_server_sdf_canonicalizer_requires_one_complete_record(
        self,
    ) -> None:
        valid = _model_server_sdf()
        cases = (
            valid.replace(b"M  END", b"M  END\nM  END", 1),
            valid + valid,
            valid.removesuffix(b"$$$$\n"),
            b"\xff" + valid,
        )
        for payload in cases:
            with self.subTest(payload_sha256=_sha(payload)):
                with self.assertRaises(
                    VERIFY.HydratedMultisystemError
                ) as raised:
                    VERIFY._canonicalize_model_server_sdf(payload)
                self.assertEqual(
                    raised.exception.code,
                    "HYDRATED_MULTISYSTEM_SDF_CANONICALIZATION_INVALID",
                )

    def test_raw_import_rejects_downloaded_source_changed_after_audit(
        self,
    ) -> None:
        audited_payload = b"ATOM exact receptor wire\n"
        changed_payload = b"ATOM other receptor wire\n"
        self.assertEqual(len(audited_payload), len(changed_payload))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "downloads" / "receptor.pdb"
            _write(source, audited_payload)
            source_evidence = {
                "path": str(source),
                "size_bytes": len(audited_payload),
                "sha256": _sha(audited_payload),
            }
            before_import = VERIFY._verify_downloaded_source(
                source,
                source_evidence,
                label="receptor before import",
            )
            source.write_bytes(changed_payload)
            project = root / "project"
            imported = project / "raw" / "receptor.pdb"
            _write(imported, changed_payload)
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._verify_raw_import(
                    project,
                    {"raw_file": "raw/receptor.pdb"},
                    source,
                    source_evidence,
                    before_import,
                    label="receptor raw import",
                )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_SOURCE_MUTATED",
        )

    def test_download_source_rejects_hash_or_insecure_redirect(self) -> None:
        payload = b"expected"
        record = {
            "url": "https://example.test/source.sdf",
            "size_bytes": len(payload),
            "sha256": _sha(payload),
        }
        cases = (
            (b"changed!", "https://example.test/source.sdf"),
            (payload, "http://example.test/source.sdf"),
        )
        for body, final_url in cases:
            with self.subTest(final_url=final_url, body=body):
                with tempfile.TemporaryDirectory() as temporary:
                    with self.assertRaises(
                        VERIFY.HydratedMultisystemError
                    ) as raised:
                        VERIFY._download_source(
                            record,
                            Path(temporary) / "source.sdf",
                            maximum_bytes=100,
                            fetcher=lambda *_: (body, final_url),
                            timeout=1,
                        )
                self.assertEqual(
                    raised.exception.code,
                    "HYDRATED_MULTISYSTEM_SOURCE_IDENTITY_MISMATCH",
                )

    def test_redirect_handler_rejects_intermediate_https_downgrade(
        self,
    ) -> None:
        handler = VERIFY._HttpsOnlyRedirectHandler()
        request = VERIFY.urllib.request.Request(
            "https://models.rcsb.org/source.sdf"
        )
        with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "http://temporary.example.test/source.sdf",
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_DOWNLOAD_REDIRECT_INVALID",
        )

    def test_toolchain_pins_versions_paths_and_hashes(self) -> None:
        manifest = VERIFY._load_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            autogrid = root / "autogrid4.exe"
            vina = root / "vina.exe"
            rdkit = root / "rdkit" / "__init__.py"
            meeko = root / "meeko" / "__init__.py"
            for path, data in (
                (python, b"python"),
                (autogrid, b"autogrid"),
                (vina, b"vina"),
                (rdkit, b"rdkit"),
                (meeko, b"meeko"),
            ):
                _write(path, data)
            module_probe = {
                "rdkit": {
                    **_write(rdkit, b"rdkit"),
                    "version": "2026.03.3",
                },
                "meeko": {
                    **_write(meeko, b"meeko"),
                    "version": "0.7.1",
                },
                "command": [str(python), "-c", "probe"],
            }
            with (
                mock.patch.object(
                    VERIFY.python_adapter,
                    "detect",
                    return_value=_tool(python, "Python 3.11.15"),
                ),
                mock.patch.object(
                    VERIFY.rdkit_adapter,
                    "detect",
                    return_value=_tool(python, "2026.03.3"),
                ),
                mock.patch.object(
                    VERIFY.meeko_adapter,
                    "detect",
                    return_value=_tool(python, "0.7.1"),
                ),
                mock.patch.object(
                    VERIFY.autogrid_adapter,
                    "detect",
                    return_value=_tool(autogrid, "4.2.7"),
                ),
                mock.patch.object(
                    VERIFY.vina_adapter,
                    "detect",
                    return_value=_tool(
                        vina,
                        "1.2.7",
                        capabilities={
                            "features": {
                                "maps": {
                                    "status": "supported",
                                    "supported": True,
                                }
                            }
                        },
                    ),
                ),
                mock.patch.object(
                    VERIFY,
                    "_module_probe",
                    return_value=module_probe,
                ),
            ):
                evidence = VERIFY._verify_toolchain(
                    python,
                    autogrid,
                    vina,
                    manifest,
                    isolated_root=root / "isolated",
                )
        self.assertEqual(evidence["python"]["sha256"], _sha(b"python"))
        self.assertEqual(evidence["autogrid"]["version"], "4.2.7")
        self.assertEqual(evidence["vina"]["version"], "1.2.7")
        self.assertEqual(evidence["rdkit"]["sha256"], _sha(b"rdkit"))
        self.assertEqual(evidence["meeko"]["sha256"], _sha(b"meeko"))

    def test_receptor_preparation_freezes_exact_reviewed_altloc_contract(
        self,
    ) -> None:
        system = VERIFY._load_manifest()["systems"]["2ZJU"]
        receptor_preparation = system["receptor_preparation"]
        controls = VERIFY.normalize_meeko_receptor_controls(
            receptor_preparation["receptor_controls"]
        )
        arguments = VERIFY.meeko_receptor_control_arguments(controls)
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            python = _write(project / "tools" / "python.exe", b"python")
            raw_relative = "raw/receptor.pdb"
            raw_identity = _write(project / raw_relative, b"ATOM raw receptor\n")
            raw_input_evidence = {
                "relative_path": raw_relative,
                "size_bytes": raw_identity["size_bytes"],
                "sha256": raw_identity["sha256"],
            }
            claimed_input = {
                "ok": True,
                "canonical_relative_path": raw_relative,
                "size_bytes": raw_identity["size_bytes"],
                "sha256": raw_identity["sha256"],
            }
            input_verification = {
                "matches": True,
                "reasons": [],
                "recorded_raw_file": raw_relative,
                "claimed": json.loads(json.dumps(claimed_input)),
                "current": json.loads(json.dumps(claimed_input)),
            }
            snapshot_relative = (
                "preparation/receptor_001/input_snapshot.json"
            )
            snapshot_path = project / snapshot_relative
            snapshot_path.parent.mkdir(parents=True)
            snapshot_path.write_text(
                json.dumps(
                    {
                        "target": "receptor",
                        "input_file": raw_relative,
                        "canonical_input_file": raw_relative,
                        "input_sha256": raw_identity["sha256"],
                        "claimed_input": json.loads(
                            json.dumps(claimed_input)
                        ),
                        "input": {
                            "path": raw_relative,
                            "exists": True,
                            "is_file": True,
                            "non_empty": True,
                            "size": raw_identity["size_bytes"],
                            "sha256": raw_identity["sha256"],
                        },
                        "claim_verification": json.loads(
                            json.dumps(input_verification)
                        ),
                    }
                ),
                encoding="utf-8",
            )
            output_path = project / "prepared" / "receptor.pdbqt"
            output = _write(output_path, b"ATOM receptor\n")
            metadata = {
                "status": "finished",
                "published": True,
                "exit_code": 0,
                "python_path": python["path"],
                "python_sha256": python["sha256"],
                "rdkit_version": "2026.03.3",
                "meeko_version": "0.7.1",
                "input_file": raw_relative,
                "input_snapshot_file": snapshot_relative,
                "claimed_input": json.loads(json.dumps(claimed_input)),
                "input_verification": json.loads(
                    json.dumps(input_verification)
                ),
                "method": "meeko_receptor_controls",
                "protocol": "meeko_receptor_controls",
                "protocol_mode": "reviewed",
                "options": {
                    "protocol": "meeko_receptor_controls",
                    "receptor_controls": controls,
                },
                "receptor_controls": controls,
                "receptor_controls_canonicalization": (
                    VERIFY.MEEKO_RECEPTOR_CONTROLS_CANONICALIZATION
                ),
                "receptor_controls_sha256": VERIFY._canonical_json_sha256(
                    controls
                ),
                "command": [
                    python["path"],
                    "-I",
                    "-B",
                    "-m",
                    "meeko.cli.mk_prepare_receptor",
                    "--read_pdb",
                    "raw/receptor.pdb",
                    "-o",
                    "candidate",
                    "-p",
                    *arguments,
                ],
                "output": {
                    "size_bytes": output["size_bytes"],
                    "sha256": output["sha256"],
                },
            }
            metadata_path = project / "preparation" / "receptor_001" / "metadata.json"
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            result = {
                "metadata_file": "preparation/receptor_001/metadata.json",
                "output_file": "prepared/receptor.pdbqt",
                "prep_id": "receptor_001",
            }
            toolchain = {
                "python": python,
                "rdkit": {"version": "2026.03.3"},
                "meeko": {"version": "0.7.1"},
            }
            evidence = VERIFY._validate_standard_preparation(
                project,
                result,
                "receptor",
                toolchain,
                raw_input_evidence=raw_input_evidence,
                receptor_preparation=receptor_preparation,
            )
            self.assertEqual(
                evidence["receptor_controls"]["command_arguments"],
                arguments,
            )
            self.assertIn("--wanted_altloc", arguments)
            self.assertIn("--delete_residues", arguments)
            delete_value = arguments[arguments.index("--delete_residues") + 1]
            self.assertEqual(
                delete_value,
                "A:301,C:301,D:301,D:302,E:301",
            )
            self.assertFalse(
                evidence["receptor_controls"]["default_altloc_used"]
            )
            self.assertTrue(
                evidence["input_binding"][
                    "final_input_verification_matches"
                ]
            )

            metadata["claimed_input"]["sha256"] = "f" * 64
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_standard_preparation(
                    project,
                    result,
                    "receptor",
                    toolchain,
                    raw_input_evidence=raw_input_evidence,
                    receptor_preparation=receptor_preparation,
                )
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_PREPARATION_INPUT_UNBOUND",
            )

            metadata["claimed_input"] = json.loads(json.dumps(claimed_input))
            metadata["receptor_controls_sha256"] = "0" * 64
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_standard_preparation(
                    project,
                    result,
                    "receptor",
                    toolchain,
                    raw_input_evidence=raw_input_evidence,
                    receptor_preparation=receptor_preparation,
                )
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_RECEPTOR_CONTROLS_INVALID",
            )

    def test_preparation_failure_reports_both_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            stdout = project / "preparation" / "receptor_001" / "stdout.txt"
            stderr = project / "preparation" / "receptor_001" / "stderr.txt"
            _write(stdout, b"alternate location remains unresolved\n")
            _write(stderr, b"deprecation warning\n")
            result = {
                "ok": False,
                "stdout_file": "preparation/receptor_001/stdout.txt",
                "stderr_file": "preparation/receptor_001/stderr.txt",
                "error": {"code": "RECEPTOR_PREPARATION_FAILED"},
            }
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._require_preparation_ok(
                    "prepare_receptor_pdbqt",
                    result,
                    project,
                )
        self.assertIn(
            "alternate location",
            raised.exception.details["stdout"]["text"],
        )
        self.assertIn(
            "deprecation warning",
            raised.exception.details["stderr"]["text"],
        )

    def test_mode_gate_accepts_short_continuous_output_and_rejects_gaps(
        self,
    ) -> None:
        accepted = VERIFY._validate_continuous_modes(
            [_model(index, 6) for index in range(1, 10)],
            list(range(1, 10)),
        )
        self.assertEqual(accepted["ids"], list(range(1, 10)))
        self.assertEqual(accepted["water_by_mode"], [6] * 9)

        for count in (1, 8):
            with self.subTest(count=count):
                accepted_prefix = VERIFY._validate_continuous_modes(
                    [_model(index) for index in range(1, count + 1)],
                    list(range(1, 10)),
                )
                self.assertEqual(
                    accepted_prefix["ids"],
                    list(range(1, count + 1)),
                )

        for models in (
            [],
            [_model(index) for index in (1, 2, 3, 4, 5, 6, 8, 9, 10)],
            [_model(index) for index in range(1, 11)],
        ):
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_continuous_modes(
                    models,
                    list(range(1, 10)),
                )
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_MODE_COUNT_INVALID",
            )

    def test_split_models_rejects_unclosed_output(self) -> None:
        text = "\n".join(_model(1)[:-1])
        with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
            VERIFY._split_models(text)
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_MODEL_FORMAT_INVALID",
        )

    def test_output_normalization_preserves_raw_and_is_control_free(self) -> None:
        normalized = (
            "\n".join(
                line
                for mode in range(1, 10)
                for line in _model(mode, water_count=1)
            )
            + "\n"
        ).encode()
        raw = normalized.replace(b"ENDMDL", b"\x00ENDMDL")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            raw_path = project / "runs" / "run_001" / "out.vina_raw.pdbqt"
            normalized_path = project / "runs" / "run_001" / "out.pdbqt"
            _write(raw_path, raw)
            _write(normalized_path, normalized)
            metadata = {
                "output_normalization": {
                    "status": "normalized",
                    "method": "torsdof_endmdl_nul_padding_v1",
                    "raw_output_file": "runs/run_001/out.vina_raw.pdbqt",
                    "normalized_file": "runs/run_001/out.pdbqt",
                    "changed": True,
                    "source_size_bytes": len(raw),
                    "source_sha256": _sha(raw),
                    "normalized_size_bytes": len(normalized),
                    "normalized_sha256": _sha(normalized),
                }
            }
            evidence = VERIFY._validate_output_normalization(project, metadata)
            self.assertEqual(evidence["raw"]["sha256"], _sha(raw))
            self.assertTrue(evidence["normalized"]["control_free"])

            bad = normalized + b"\x00"
            normalized_path.write_bytes(bad)
            metadata["output_normalization"]["normalized_size_bytes"] = len(bad)
            metadata["output_normalization"]["normalized_sha256"] = _sha(bad)
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_output_normalization(project, metadata)
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_OUTPUT_NORMALIZATION_INVALID",
            )

    def test_run_validator_accepts_public_nested_mode_shape(self) -> None:
        manifest = VERIFY._load_manifest()
        protocol = manifest["protocol"]
        system = manifest["systems"]["2ZJU"]
        affinities = [-5.0 - mode / 100 for mode in range(1, 10)]
        output_text = (
            "\n".join(
                line
                for mode in range(1, 10)
                for line in _model(mode, water_count=7, affinity=-5.0)
            )
            + "\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            vina = _write(project / "tools" / "vina.exe", b"vina")
            output_relative = "runs/run_001/out.pdbqt"
            raw_relative = "runs/run_001/out.vina_raw.pdbqt"
            output_path = project / output_relative
            raw_path = project / raw_relative
            _write(output_path, output_text.encode())
            _write(raw_path, output_text.encode())
            scores_path = project / "runs" / "run_001" / "scores.csv"
            project_scores_path = project / "results" / "hydrated_ad4_scores.csv"
            _write(scores_path, b"mode,affinity\n1,-5.01\n")
            _write(project_scores_path, scores_path.read_bytes())
            report_text = "\n".join(protocol["report"]["required_phrases"])
            report_path = project / "runs" / "run_001" / "report.md"
            project_report_path = project / "reports" / "report.md"
            _write(report_path, report_text.encode())
            _write(project_report_path, report_path.read_bytes())

            scores = [
                {
                    "mode": mode,
                    "affinity_kcal_mol": affinities[mode - 1],
                    "rmsd_lb": 0.0,
                    "rmsd_ub": 0.0,
                }
                for mode in range(1, 10)
            ]
            modes = [
                {
                    **score,
                    "raw_affinity_kcal_mol": score["affinity_kcal_mol"],
                    "water_summary": {
                        "candidate_water_count": 7,
                        "retained_water_count": 0,
                        "strong_water_count": 0,
                        "weak_water_count": 0,
                        "displaced_water_count": 7,
                    },
                    "waters": [],
                }
                for score in scores
            ]
            summary = {
                "pose_count": 9,
                "raw_water_count": 63,
                "candidate_water_count": 63,
                "retained_water_count": 0,
                "strong_water_count": 0,
                "weak_water_count": 0,
                "displaced_water_count": 63,
            }
            toolchain = {
                "vina": {**vina, "version": "1.2.7"},
            }
            command = [
                vina["path"],
                "--maps",
                "inputs/maps/receptor",
                "--scoring",
                "ad4",
                "--out",
                output_relative,
            ]
            metadata = {
                "status": "finished",
                "stage": "finished",
                "executed_command": command,
                "execution_vina": {**vina, "version": "1.2.7"},
                "vina_binary_integrity": {"match": True},
                "hydrated_postprocess": {"status": "finished"},
                "input_snapshot_integrity": {"ok": True},
                "output_normalization": {
                    "status": "not_required",
                    "method": "torsdof_endmdl_nul_padding_v1",
                    "raw_output_file": raw_relative,
                    "normalized_file": output_relative,
                    "changed": False,
                    "source_size_bytes": raw_path.stat().st_size,
                    "source_sha256": VERIFY._sha256(raw_path),
                    "normalized_size_bytes": output_path.stat().st_size,
                    "normalized_sha256": VERIFY._sha256(output_path),
                },
            }
            result = VERIFY._validate_run_and_results(
                project,
                {"run_id": "run_001"},
                {"run_id": "run_001", "metadata": metadata},
                {
                    "scores_file": "runs/run_001/scores.csv",
                    "project_scores_file": "results/hydrated_ad4_scores.csv",
                    "best_affinity": affinities[0],
                },
                {
                    "scores": scores,
                    "modes": modes,
                    "water_summary": summary,
                },
                {
                    "report_file": "runs/run_001/report.md",
                    "project_report_file": "reports/report.md",
                },
                system=system,
                protocol=protocol,
                toolchain=toolchain,
                maps_evidence={
                    "water_map": {"sha256": "a" * 64},
                    "manifest": {"sha256": "b" * 64},
                },
            )
        self.assertEqual(result["modes"]["count"], 9)
        self.assertTrue(result["water_conservation"]["conserved"])
        self.assertEqual(
            result["diagnostics"]["pose_rmsd"]["classification"],
            "known_pose_quality_non_positive",
        )

    def test_water_conservation_is_exact_but_reference_breakdown_is_not(self) -> None:
        modes = [
            {
                "candidate_water_count": 7,
                "retained_water_count": 2,
                "strong_water_count": 1,
                "weak_water_count": 1,
                "displaced_water_count": 5,
            }
            for _ in range(9)
        ]
        summary = {
            "pose_count": 9,
            "raw_water_count": 63,
            "candidate_water_count": 63,
            "retained_water_count": 18,
            "strong_water_count": 9,
            "weak_water_count": 9,
            "displaced_water_count": 45,
        }
        evidence = VERIFY._validate_water_conservation(
            summary,
            prepared_water_count=7,
            mode_count=9,
            modes=modes,
        )
        self.assertTrue(evidence["conserved"])

        changed = {**summary, "displaced_water_count": 44}
        with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
            VERIFY._validate_water_conservation(
                changed,
                prepared_water_count=7,
                mode_count=9,
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_WATER_CONSERVATION_FAILED",
        )

    def test_grid_coverage_requires_54_cubed_and_point_125_margin(self) -> None:
        manifest = VERIFY._load_manifest()
        grid = manifest["protocol"]["grid"]
        box = manifest["systems"]["2ZJU"]["box"]
        evidence = VERIFY._validate_grid_coverage(_coverage(), grid, box)
        self.assertEqual(evidence["grid_points"], {"x": 54, "y": 54, "z": 54})
        self.assertEqual(evidence["actual_size_angstrom"]["x"], 20.25)

        bad = _coverage()
        bad["effective_grid_axis_intervals"] = {"x": 53, "y": 54, "z": 54}
        with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
            VERIFY._validate_grid_coverage(bad, grid, box)
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_GRID_COVERAGE_INVALID",
        )

    def test_2zju_hydrated_ligand_requires_cl_and_seven_waters(self) -> None:
        system = VERIFY._load_manifest()["systems"]["2ZJU"]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            python = _write(project / "tools" / "python.exe", b"python")
            rdkit = _write(project / "tools" / "rdkit.py", b"rdkit")
            meeko = _write(project / "tools" / "meeko.py", b"meeko")
            lines = [
                "ATOM      1  C   LIG     1       0.000   0.000   0.000 C",
                "ATOM      2  C   LIG     1       0.000   0.000   0.000 A",
                "ATOM      3  C   LIG     1       0.000   0.000   0.000 Cl",
                "ATOM      4  C   LIG     1       0.000   0.000   0.000 HD",
                "ATOM      5  C   LIG     1       0.000   0.000   0.000 N",
                "ATOM      6  C   LIG     1       0.000   0.000   0.000 NA",
                "ATOM      7  C   LIG     1       0.000   0.000   0.000 OA",
                *[
                    f"HETATM{index:5d}  WAT HOH     1"
                    "       0.000   0.000   0.000 W"
                    for index in range(8, 15)
                ],
            ]
            hydrated_path = project / "protocols" / "ligand_hydrated.pdbqt"
            hydrated_record = _write(
                hydrated_path,
                ("\n".join(lines) + "\n").encode(),
            )
            hydrated_record.update(
                {
                    "path": "protocols/ligand_hydrated.pdbqt",
                    "water_count": 7,
                    "atom_types": ["A", "C", "Cl", "HD", "N", "NA", "OA", "W"],
                }
            )
            toolchain = {
                "python": python,
                "rdkit": {**rdkit, "version": "2026.03.3"},
                "meeko": {**meeko, "version": "0.7.1"},
            }
            ligand_source_evidence = {
                "requested_url": system["ligand_source"]["url"],
                "final_url": system["ligand_source"]["url"],
                "identity_mode": "canonical_scientific_content_v1",
                "size_bytes": 2307,
                "sha256": "d" * 64,
                "canonicalization": system["ligand_source"]["canonicalization"],
                "canonical_size_bytes": system["ligand_source"][
                    "canonical_size_bytes"
                ],
                "canonical_sha256": system["ligand_source"]["canonical_sha256"],
            }
            manifest = {
                "status": "finished",
                "protocol_id": "hydrated_ad4_experimental",
                "tools": {
                    "rdkit_version": "2026.03.3",
                    "meeko_version": "0.7.1",
                    "before": {
                        "python": python,
                        "rdkit": rdkit,
                        "meeko": meeko,
                    },
                    "probe": {
                        "rdkit_module_file": rdkit["path"],
                        "meeko_module_file": meeko["path"],
                    },
                    "probe_command": [python["path"], "probe"],
                },
                "source": {
                    "size_bytes": ligand_source_evidence["size_bytes"],
                    "sha256": ligand_source_evidence["sha256"],
                },
                "command": [python["path"], "prepare"],
                "outputs": {"hydrated_pdbqt": hydrated_record},
                "integrity": {
                    "source_unchanged": True,
                    "tools_unchanged": True,
                    "script_unchanged": True,
                    "tools_after": {
                        "python": python,
                        "rdkit": rdkit,
                        "meeko": meeko,
                    },
                },
            }
            manifest_path = project / "protocols" / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = {
                "ok": True,
                "protocol_id": "hydrated_ad4_experimental",
                "water_count": 7,
                "manifest": manifest,
                "manifest_file": "protocols/manifest.json",
                "manifest_sha256": VERIFY._sha256(manifest_path),
            }
            evidence = VERIFY._validate_hydrated_ligand(
                project,
                result,
                "2ZJU",
                system,
                toolchain,
                ligand_source_evidence,
            )
            self.assertEqual(evidence["water_count"], 7)
            self.assertIn("Cl", evidence["atom_types"])

            changed = json.loads(json.dumps(manifest))
            changed["outputs"]["hydrated_pdbqt"]["atom_types"] = [
                "A",
                "C",
                "CL",
                "HD",
                "N",
                "NA",
                "OA",
                "W",
            ]
            hydrated_path.write_text(
                hydrated_path.read_text(encoding="utf-8").replace(" Cl\n", " CL\n"),
                encoding="utf-8",
            )
            changed_record = changed["outputs"]["hydrated_pdbqt"]
            changed_record["size_bytes"] = hydrated_path.stat().st_size
            changed_record["sha256"] = VERIFY._sha256(hydrated_path)
            manifest_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_hydrated_ligand(
                    project,
                    {
                        **result,
                        "manifest": changed,
                        "manifest_sha256": VERIFY._sha256(manifest_path),
                    },
                    "2ZJU",
                    system,
                    toolchain,
                    ligand_source_evidence,
                )
            self.assertIn(
                raised.exception.code,
                {
                    "HYDRATED_MULTISYSTEM_HYDRATED_LIGAND_INVALID",
                    "HYDRATED_MULTISYSTEM_CHLORINE_NOT_CANONICAL",
                },
            )

    def test_2zju_maps_require_receptor_dot_cl_map(self) -> None:
        manifest = VERIFY._load_manifest()
        system = manifest["systems"]["2ZJU"]
        protocol = manifest["protocol"]
        names = [
            "receptor.maps.fld",
            "receptor.A.map",
            "receptor.C.map",
            "receptor.Cl.map",
            "receptor.HD.map",
            "receptor.N.map",
            "receptor.NA.map",
            "receptor.OA.map",
            "receptor.e.map",
            "receptor.d.map",
            "receptor.W.map",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            autogrid = _write(project / "tools" / "autogrid4.exe", b"ag")
            records: list[dict[str, object]] = []
            for name in names:
                relative = Path("maps") / "hydrated_001" / name
                identity = _write(project / relative, name.encode())
                records.append(
                    {
                        "name": name,
                        "relative_path": relative.as_posix(),
                        "size_bytes": identity["size_bytes"],
                        "sha256": identity["sha256"],
                    }
                )
            water = next(item for item in records if item["name"] == "receptor.W.map")
            coverage = _coverage()
            coverage_sha256 = VERIFY._canonical_json_sha256(coverage)
            maps_manifest = {
                "status": "ready",
                "autogrid": {
                    **autogrid,
                    "version": "4.2.7",
                    "command": [autogrid["path"], "-p", "receptor.gpf"],
                    "exit_code": 0,
                },
                "maps": {
                    "ligand_atom_types": ["A", "C", "Cl", "HD", "N", "NA", "OA", "W"],
                    "autogrid_ligand_atom_types": ["A", "C", "Cl", "HD", "N", "NA", "OA"],
                    "required_files": names,
                    "files": records,
                    "water_map": water,
                },
                "grid_coverage": coverage,
                "grid_coverage_sha256": coverage_sha256,
                "integrity": {
                    "sources_unchanged": True,
                    "autogrid_unchanged": True,
                    "autogrid_after": autogrid,
                },
            }
            maps_manifest_path = project / "maps" / "hydrated_001" / "manifest.json"
            maps_manifest_path.write_text(
                json.dumps(maps_manifest),
                encoding="utf-8",
            )
            evidence = VERIFY._validate_maps(
                project,
                {
                    "manifest": maps_manifest,
                    "manifest_file": "maps/hydrated_001/manifest.json",
                    "manifest_sha256": VERIFY._sha256(maps_manifest_path),
                    "map_set_id": "hydrated_001",
                    "grid_coverage": coverage,
                    "grid_coverage_sha256": coverage_sha256,
                },
                "2ZJU",
                system,
                protocol,
                {"autogrid": {**autogrid, "version": "4.2.7"}},
            )
            self.assertIn("receptor.Cl.map", [item["name"] for item in evidence["files"]])

            response_coverage = json.loads(json.dumps(coverage))
            response_coverage["box_center_angstrom"]["x"] = 0.0
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_maps(
                    project,
                    {
                        "manifest": maps_manifest,
                        "manifest_file": "maps/hydrated_001/manifest.json",
                        "manifest_sha256": VERIFY._sha256(maps_manifest_path),
                        "grid_coverage": response_coverage,
                        "grid_coverage_sha256": VERIFY._canonical_json_sha256(
                            response_coverage
                        ),
                    },
                    "2ZJU",
                    system,
                    protocol,
                    {"autogrid": {**autogrid, "version": "4.2.7"}},
                )
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_GRID_COVERAGE_INVALID",
            )

            changed = json.loads(json.dumps(maps_manifest))
            changed["maps"]["ligand_atom_types"][2] = "CL"
            changed["maps"]["autogrid_ligand_atom_types"][2] = "CL"
            maps_manifest_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_maps(
                    project,
                    {
                        "manifest": changed,
                        "manifest_file": "maps/hydrated_001/manifest.json",
                        "manifest_sha256": VERIFY._sha256(maps_manifest_path),
                        "grid_coverage": coverage,
                        "grid_coverage_sha256": coverage_sha256,
                    },
                    "2ZJU",
                    system,
                    protocol,
                    {"autogrid": {**autogrid, "version": "4.2.7"}},
                )
            self.assertIn(
                raised.exception.code,
                {
                    "HYDRATED_MULTISYSTEM_MAP_TYPES_INVALID",
                    "HYDRATED_MULTISYSTEM_CHLORINE_MAP_INVALID",
                },
            )

    def test_report_semantics_and_diagnostics_are_separate(self) -> None:
        manifest = VERIFY._load_manifest()
        report = "\n".join(manifest["protocol"]["report"]["required_phrases"])
        evidence = VERIFY._validate_report_semantics(
            report,
            manifest["protocol"]["report"],
        )
        self.assertTrue(evidence["all_present"])
        with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
            VERIFY._validate_report_semantics(
                "\n".join(manifest["protocol"]["report"]["required_phrases"][:-1]),
                manifest["protocol"]["report"],
            )
        self.assertEqual(
            raised.exception.code,
            "HYDRATED_MULTISYSTEM_REPORT_SEMANTICS_MISSING",
        )

        diagnostics = VERIFY._diagnostics(
            manifest["systems"]["2ZJU"],
            observed_best_affinity=-1.0,
            observed_water_map_sha256="f" * 64,
            observed_hydrated_manifest_sha256="e" * 64,
        )
        self.assertIs(diagnostics["best_affinity"]["gate"], False)
        self.assertIs(diagnostics["pose_rmsd"]["gate"], False)
        self.assertIs(
            diagnostics["pose_rmsd"]["scientific_success_oracle"],
            False,
        )
        self.assertEqual(
            diagnostics["pose_rmsd"]["reference_angstrom"],
            6.422,
        )

    def test_frozen_receptor_must_exist_and_match_public_preparation(self) -> None:
        output = {
            "relative_path": "prepared/receptor.pdbqt",
            "size_bytes": 123,
            "sha256": "a" * 64,
        }
        metadata = {
            "snapshots": {
                "inputs": {
                    "receptor": {
                        "source_relative_path": "prepared/receptor.pdbqt",
                        "size_bytes": 123,
                        "sha256": "a" * 64,
                    }
                }
            }
        }
        evidence = VERIFY._validate_frozen_receptor(metadata, output)
        self.assertTrue(evidence["matches_public_preparation"])

        for changed in (
            {},
            {
                "snapshots": {
                    "inputs": {
                        "receptor": {
                            "source_relative_path": "prepared/receptor.pdbqt",
                            "sha256": "b" * 64,
                        }
                    }
                }
            },
        ):
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY._validate_frozen_receptor(changed, output)
            self.assertEqual(
                raised.exception.code,
                "HYDRATED_MULTISYSTEM_RECEPTOR_FREEZE_MISMATCH",
            )

    def test_verifier_uses_typed_controls_without_prepared_fallback(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import_receptor_pdbqt", source)
        self.assertIn("options=receptor_options", source)
        self.assertIn('"--default_altloc" in command', source)
        self.assertIn('"--allow_bad_res" in command', source)
        self.assertIn('"fallback_import_used": False', source)

    def test_success_lifecycle_runs_only_two_systems_and_restores_environment(
        self,
    ) -> None:
        manifest = VERIFY._load_manifest()
        roots: list[Path] = []
        calls: list[str] = []

        def fake_download(
            system_id: str,
            _system: object,
            download_root: Path,
            **_kwargs: object,
        ) -> tuple[dict[str, Path], dict[str, object]]:
            roots.append(download_root.parent)
            return (
                {
                    "receptor": download_root / system_id / "receptor.pdb",
                    "ligand": download_root / system_id / "ligand.sdf",
                },
                {"downloaded": True},
            )

        def fake_workflow(
            system_id: str,
            *_args: object,
            **_kwargs: object,
        ) -> dict[str, object]:
            calls.append(system_id)
            return {"system_id": system_id, "ok": True}

        original = {
            VERIFY.SETTINGS_ENV_VAR: "old-settings",
            VERIFY.RESOURCE_DIR_ENV_VAR: "old-resources",
            VERIFY.PREPARATION_TOOLS_SNAPSHOT_ENV_VAR: "old-tools",
        }
        with (
            mock.patch.object(VERIFY, "_load_manifest", return_value=manifest),
            mock.patch.object(VERIFY, "save_settings"),
            mock.patch.object(
                VERIFY,
                "_verify_toolchain",
                return_value={"toolchain": "synthetic"},
            ),
            mock.patch.object(
                VERIFY,
                "_download_sources",
                side_effect=fake_download,
            ),
            mock.patch.object(
                VERIFY,
                "_run_system_workflow",
                side_effect=fake_workflow,
            ),
            mock.patch.dict(os.environ, original, clear=False),
        ):
            result = VERIFY.verify_hydrated_multisystem(
                python_executable=SCRIPT_PATH,
                autogrid_executable=SCRIPT_PATH,
                vina_executable=SCRIPT_PATH,
                fetcher=lambda *_: b"",
            )
            for key, value in original.items():
                self.assertEqual(os.environ[key], value)

        self.assertTrue(result["ok"])
        self.assertEqual(calls, ["2BYS", "2ZJU"])
        self.assertEqual(set(result["systems"]), {"2BYS", "2ZJU"})
        self.assertTrue(result["environment_restoration"]["restored"])
        self.assertTrue(result["temporary_cleanup"]["removed"])
        self.assertTrue(roots)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_failure_lifecycle_restores_all_environment_and_cleans_temp(
        self,
    ) -> None:
        manifest = VERIFY._load_manifest()
        failure = VERIFY.HydratedMultisystemError(
            "SYNTHETIC_PUBLIC_API_FAILURE",
            "synthetic",
        )
        captured: list[Path] = []

        def fail_workflow(
            _system_id: str,
            *_args: object,
            project_parent: Path,
            **_kwargs: object,
        ) -> object:
            captured.append(project_parent.parent)
            raise failure

        original = {
            VERIFY.SETTINGS_ENV_VAR: "old-settings",
            VERIFY.RESOURCE_DIR_ENV_VAR: "old-resources",
            VERIFY.PREPARATION_TOOLS_SNAPSHOT_ENV_VAR: "old-tools",
        }
        with (
            mock.patch.object(VERIFY, "_load_manifest", return_value=manifest),
            mock.patch.object(VERIFY, "save_settings"),
            mock.patch.object(
                VERIFY,
                "_verify_toolchain",
                return_value={"toolchain": "synthetic"},
            ),
            mock.patch.object(
                VERIFY,
                "_download_sources",
                return_value=(
                    {"receptor": SCRIPT_PATH, "ligand": SCRIPT_PATH},
                    {"downloaded": True},
                ),
            ),
            mock.patch.object(
                VERIFY,
                "_run_system_workflow",
                side_effect=fail_workflow,
            ),
            mock.patch.dict(os.environ, original, clear=False),
        ):
            with self.assertRaises(VERIFY.HydratedMultisystemError) as raised:
                VERIFY.verify_hydrated_multisystem(
                    python_executable=SCRIPT_PATH,
                    autogrid_executable=SCRIPT_PATH,
                    vina_executable=SCRIPT_PATH,
                )
            for key, value in original.items():
                self.assertEqual(os.environ[key], value)

        self.assertEqual(raised.exception.code, "SYNTHETIC_PUBLIC_API_FAILURE")
        self.assertTrue(
            raised.exception.details["environment_restoration"]["restored"]
        )
        self.assertTrue(
            raised.exception.details["temporary_cleanup"]["removed"]
        )
        self.assertTrue(captured)
        self.assertFalse(captured[0].exists())

    def test_keyboard_interrupt_still_restores_environment_and_temp(self) -> None:
        manifest = VERIFY._load_manifest()
        captured: list[Path] = []

        def interrupt(
            *_args: object,
            isolated_root: Path,
            **_kwargs: object,
        ) -> object:
            captured.append(isolated_root.parent)
            raise KeyboardInterrupt

        original = {
            VERIFY.SETTINGS_ENV_VAR: "old-settings",
            VERIFY.RESOURCE_DIR_ENV_VAR: "old-resources",
            VERIFY.PREPARATION_TOOLS_SNAPSHOT_ENV_VAR: "old-tools",
        }
        with (
            mock.patch.object(VERIFY, "_load_manifest", return_value=manifest),
            mock.patch.object(VERIFY, "save_settings"),
            mock.patch.object(
                VERIFY,
                "_verify_toolchain",
                side_effect=interrupt,
            ),
            mock.patch.dict(os.environ, original, clear=False),
        ):
            with self.assertRaises(KeyboardInterrupt):
                VERIFY.verify_hydrated_multisystem(
                    python_executable=SCRIPT_PATH,
                    autogrid_executable=SCRIPT_PATH,
                    vina_executable=SCRIPT_PATH,
                )
            for key, value in original.items():
                self.assertEqual(os.environ[key], value)

        self.assertTrue(captured)
        self.assertFalse(captured[0].exists())


if __name__ == "__main__":
    unittest.main()
