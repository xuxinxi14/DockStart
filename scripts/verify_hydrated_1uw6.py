"""Run the project-level AutoDock Vina 1UW6 hydrated-docking gate.

No upstream scientific data or AutoGrid executable is distributed by this
fixture.  The caller supplies a pinned Vina v1.2.7 checkout and an external
AutoGrid 4.2.6+ executable.  The verifier exercises DockStart's public project
API, requires top-3 same-frame crystal-pose recovery, and removes every
generated artifact with its temporary project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from adapters import autogrid_adapter, meeko_adapter, vina_adapter  # noqa: E402
from dockstart_core.autogrid import (  # noqa: E402
    HYDRATED_PROTOCOL_ID,
    autogrid_version_supported,
    minimum_autogrid_version,
)
from dockstart_core.hydrated import (  # noqa: E402
    generate_hydrated_maps,
    get_status as get_hydrated_status,
    prepare_hydrated_ligand,
)
from dockstart_core.hydrated_maps import (  # noqa: E402
    AutoGridMap,
    generate_best_water_map,
    parse_autogrid_map,
)
from dockstart_core.hydrated_run import (  # noqa: E402
    get_hydrated_run_preflight,
    load_hydrated_results,
    prepare_hydrated_run,
)
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    import_receptor_pdbqt,
    update_box_params,
    update_vina_params,
)
from dockstart_core.settings import (  # noqa: E402
    SETTINGS_ENV_VAR,
    DockStartSettings,
    ToolPaths,
    save_settings,
)
from dockstart_core.structure_fetch import import_ligand_raw_file  # noqa: E402
from dockstart_core.toolchain_paths import RESOURCE_DIR_ENV_VAR  # noqa: E402

MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "scientific"
    / "hydrated_1uw6"
    / "source_manifest.json"
)
DEFAULT_PYTHON = REPOSITORY_ROOT / "resources" / "python" / "python.exe"
DEFAULT_VINA = REPOSITORY_ROOT / "resources" / "vina" / "vina.exe"
PORTABLE_IDENTITY_ALGORITHM = "normalize_crlf_and_cr_to_lf_then_sha256"
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_BLOB_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MANIFEST_REQUIRED_FILES = frozenset(
    {
        "crystal_reference_ligand",
        "hydrated_ligand_input",
        "official_hydrated_ligand",
        "receptor",
        "gpf",
        "map_A",
        "map_C",
        "map_HD",
        "map_N",
        "map_NA",
        "map_OA",
        "map_W",
        "map_d",
        "map_e",
        "official_raw_output",
    }
)
PINNED_POSE_RECOVERY_CONTRACT: dict[str, Any] = {
    "reference_file_key": "crystal_reference_ligand",
    "reference_format": "SDF V2000",
    "reference_atom_count": 12,
    "reference_elements": [
        "N",
        "C",
        "C",
        "C",
        "C",
        "C",
        "N",
        "C",
        "C",
        "C",
        "C",
        "C",
    ],
    "reference_bonds_one_based": [
        [1, 2, 2],
        [1, 6, 1],
        [2, 3, 1],
        [3, 4, 2],
        [3, 8, 1],
        [4, 5, 1],
        [5, 6, 2],
        [7, 8, 1],
        [7, 11, 1],
        [7, 12, 1],
        [8, 9, 1],
        [9, 10, 1],
        [10, 11, 1],
    ],
    "reference_coordinate_fingerprint": {
        "algorithm": "ordered_sdf_coordinates_fixed_4_decimal_csv_sha256",
        "sha256": "bc13c24450da63a03c3d8a04aa83a7defd999da3dd372da6ee663858e3923c81",
    },
    "pdbqt_smiles": "C[N@@H+]1CCC[C@H]1c1cccnc1",
    "pdbqt_smiles_idx_pairs_one_based": [
        6,
        1,
        2,
        2,
        3,
        3,
        1,
        4,
        5,
        5,
        4,
        6,
        7,
        7,
        12,
        8,
        8,
        9,
        11,
        10,
        9,
        11,
        10,
        12,
    ],
    "smiles_to_reference_sdf_atom_zero_based": [
        11,
        6,
        10,
        9,
        8,
        7,
        2,
        3,
        4,
        5,
        0,
        1,
    ],
    "rmsd_semantics": (
        "same_receptor_coordinate_frame_no_alignment_heavy_atoms_"
        "explicit_mapping"
    ),
    "alignment_applied": False,
    "heavy_atoms_only": True,
    "top_n_modes": 3,
    "maximum_rmsd_angstrom": 2.0,
}
MANIFEST_CONTAINS_FLAGS = (
    "contains_upstream_structure_files",
    "contains_parameter_files",
    "contains_maps_or_outputs",
    "contains_executables",
)


class HydratedAcceptanceError(RuntimeError):
    """One stable, JSON-serializable acceptance failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.steps: dict[str, Any] = {}


def _fail(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise HydratedAcceptanceError(code, message, details=details)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_lf_bytes(payload: bytes) -> bytes:
    """Normalize only line endings; preserve every other upstream byte."""

    return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _line_ending_profile(payload: bytes) -> dict[str, Any]:
    crlf = payload.count(b"\r\n")
    without_crlf = payload.replace(b"\r\n", b"")
    lf = without_crlf.count(b"\n")
    cr = without_crlf.count(b"\r")
    styles = [
        name
        for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr))
        if count
    ]
    return {
        "style": styles[0] if len(styles) == 1 else "mixed" if styles else "none",
        "crlf_count": crlf,
        "lf_count": lf,
        "cr_count": cr,
    }


def _portable_text_snapshot(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    canonical = _canonical_lf_bytes(payload)
    return {
        "size_bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "line_endings": _line_ending_profile(payload),
        "portable_identity": {
            "algorithm": PORTABLE_IDENTITY_ALGORITHM,
            "size_bytes": len(canonical),
            "sha256": _sha256_bytes(canonical),
        },
    }


def _validate_pose_recovery_contract(
    value: object,
) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or dict(value) != PINNED_POSE_RECOVERY_CONTRACT
        or type(value.get("top_n_modes")) is not int
        or type(value.get("maximum_rmsd_angstrom")) is not float
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_POSE_CONTRACT_INVALID",
            (
                "Hydrated 1UW6 pose recovery must use the pinned crystal "
                "reference, explicit atom mapping, same-frame RMSD, and "
                "top-3 threshold."
            ),
            details={
                "expected": PINNED_POSE_RECOVERY_CONTRACT,
                "actual": dict(value) if isinstance(value, Mapping) else value,
            },
        )
    return value


def _validate_manifest_contract(
    payload: Mapping[str, Any],
    *,
    manifest_path: Path,
) -> None:
    if (
        payload.get("schema_version") != 2
        or payload.get("fixture_id") != "hydrated_1uw6_external"
        or payload.get("distribution") != "metadata_only"
        or any(payload.get(key) is not False for key in MANIFEST_CONTAINS_FLAGS)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            (
                "Hydrated 1UW6 source manifest must be the pinned "
                "metadata-only schema v2 contract."
            ),
            details={
                "schema_version": payload.get("schema_version"),
                "fixture_id": payload.get("fixture_id"),
                "distribution": payload.get("distribution"),
                "contains_flags": {
                    key: payload.get(key) for key in MANIFEST_CONTAINS_FLAGS
                },
            },
        )

    upstream = payload.get("upstream")
    required_files = payload.get("required_files")
    if not isinstance(upstream, Mapping) or (
        not isinstance(required_files, Mapping)
        or set(required_files) != MANIFEST_REQUIRED_FILES
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 required_files does not match the pinned file set.",
            details={
                "expected": sorted(MANIFEST_REQUIRED_FILES),
                "actual": (
                    sorted(str(key) for key in required_files)
                    if isinstance(required_files, Mapping)
                    else None
                ),
            },
        )

    checkout_profile = upstream.get("checkout_profile")
    accepted_line_endings = (
        {
            str(value).strip().lower()
            for value in checkout_profile.get(
                "accepted_worktree_line_endings"
            )
            or []
        }
        if isinstance(checkout_profile, Mapping)
        else set()
    )
    if (
        not isinstance(checkout_profile, Mapping)
        or checkout_profile.get("portable_identity_algorithm")
        != PORTABLE_IDENTITY_ALGORITHM
        or str(
            checkout_profile.get("known_reference_line_endings") or ""
        ).lower()
        != "crlf"
        or accepted_line_endings != {"lf", "crlf"}
        or not GIT_BLOB_SHA1_PATTERN.fullmatch(
            str(upstream.get("commit") or "").lower()
        )
        or str(upstream.get("tag") or "") != "v1.2.7"
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 checkout identity policy is incomplete.",
        )

    seen_paths: set[str] = set()
    for key in sorted(MANIFEST_REQUIRED_FILES):
        record = required_files.get(key)
        if not isinstance(record, Mapping):
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} is invalid.",
            )
        relative_text = str(record.get("path") or "")
        relative = Path(relative_text)
        raw_size = record.get("size_bytes")
        raw_sha = str(record.get("sha256") or "").lower()
        git_blob_sha = str(record.get("git_blob_sha1") or "").lower()
        portable = record.get("portable_text_identity")
        if (
            not relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in seen_paths
            or isinstance(raw_size, bool)
            or not isinstance(raw_size, int)
            or raw_size <= 0
            or not SHA256_PATTERN.fullmatch(raw_sha)
            or not GIT_BLOB_SHA1_PATTERN.fullmatch(git_blob_sha)
            or not isinstance(portable, Mapping)
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} violates the pinned identity contract.",
                details={"record": dict(record)},
            )
        seen_paths.add(relative.as_posix())
        portable_size = portable.get("size_bytes")
        portable_sha = str(portable.get("sha256") or "").lower()
        if (
            portable.get("algorithm") != PORTABLE_IDENTITY_ALGORITHM
            or isinstance(portable_size, bool)
            or not isinstance(portable_size, int)
            or portable_size <= 0
            or not SHA256_PATTERN.fullmatch(portable_sha)
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} has an invalid LF identity.",
                details={"portable_text_identity": dict(portable)},
            )

    expected = payload.get("expected")
    vina_expected = (
        expected.get("vina") if isinstance(expected, Mapping) else None
    )
    if not isinstance(vina_expected, Mapping) or (
        vina_expected.get("accepted_output_mode_counts")
        != [1, 2, 3, 4, 5, 6, 7, 8, 9]
        or vina_expected.get("minimum_output_modes") != 1
        or vina_expected.get("maximum_output_modes") != 9
        or vina_expected.get("num_modes") != 9
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 must treat num_modes=9 as an output maximum.",
            details={
                "vina": (
                    dict(vina_expected)
                    if isinstance(vina_expected, Mapping)
                    else None
                )
            },
        )

    pose_recovery = (
        expected.get("pose_recovery")
        if isinstance(expected, Mapping)
        else None
    )
    _validate_pose_recovery_contract(pose_recovery)

    try:
        fixture_entries = {item.name for item in manifest_path.parent.iterdir()}
    except OSError as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 fixture directory cannot be inspected.",
            details={"path": str(manifest_path.parent), "error": str(exc)},
        )
    expected_entries = {"README.md", "source_manifest.json"}
    if fixture_entries != expected_entries:
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_DISTRIBUTION_INVALID",
            "The metadata-only hydrated fixture contains an unexpected artifact.",
            details={
                "expected": sorted(expected_entries),
                "actual": sorted(fixture_entries),
            },
        )


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_MISSING",
            "Hydrated 1UW6 source manifest is missing.",
            details={"path": str(MANIFEST_PATH)},
        )
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 source manifest cannot be parsed.",
            details={"path": str(MANIFEST_PATH), "error": str(exc)},
        )
    if not isinstance(payload, dict):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated 1UW6 source manifest must contain a JSON object.",
        )
    _validate_manifest_contract(payload, manifest_path=MANIFEST_PATH)
    return payload


def _regular_file(path: Path, label: str) -> Path:
    try:
        valid = (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size > 0
            and path.resolve(strict=True) == path.absolute()
        )
    except OSError:
        valid = False
    if not valid:
        _fail(
            "HYDRATED_ACCEPTANCE_FILE_INVALID",
            f"{label} is missing, empty, symbolic, or a reparse path.",
            details={"path": str(path)},
        )
    return path


def _normalized_path(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        candidate = candidate.resolve(strict=False)
    return os.path.normcase(str(candidate))


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_COMMAND_FAILED",
            "Unable to execute an acceptance command.",
            details={"command": [str(item) for item in command], "error": str(exc)},
        )
    raise AssertionError("unreachable")


def _verify_upstream(
    upstream_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Path], dict[str, Any]]:
    try:
        root = upstream_root.expanduser().resolve(strict=True)
    except OSError as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_UPSTREAM_INVALID",
            "--upstream-root cannot be resolved.",
            details={"path": str(upstream_root), "error": str(exc)},
        )
    if upstream_root.is_symlink() or not root.is_dir():
        _fail(
            "HYDRATED_ACCEPTANCE_UPSTREAM_INVALID",
            "--upstream-root must be a plain AutoDock Vina source directory.",
            details={"path": str(root)},
        )

    upstream = manifest.get("upstream")
    required_files = manifest.get("required_files")
    if not isinstance(upstream, Mapping) or not isinstance(required_files, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest upstream or required_files section is invalid.",
        )
    checkout_profile = upstream.get("checkout_profile")
    if not isinstance(checkout_profile, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest upstream checkout profile is missing.",
        )
    accepted_line_endings = {
        str(value).strip().lower()
        for value in checkout_profile.get("accepted_worktree_line_endings") or []
    }
    if (
        checkout_profile.get("portable_identity_algorithm")
        != PORTABLE_IDENTITY_ALGORITHM
        or accepted_line_endings != {"lf", "crlf"}
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest portable checkout policy must accept exactly LF and CRLF.",
        )

    expected_commit = str(upstream.get("commit") or "").lower()
    expected_tag = str(upstream.get("tag") or "")
    detected_commit = ""
    detected_tag = ""
    git_executable = ""
    git_marker = root / ".git"
    if git_marker.exists():
        if git_marker.is_symlink():
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_GIT_ROOT_INVALID",
                "The supplied checkout has a symbolic .git marker.",
                details={"path": str(git_marker)},
            )
        git_executable = str(shutil.which("git") or "")
        if not git_executable:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_GIT_UNAVAILABLE",
                "Git metadata exists but Git is unavailable for verification.",
            )

        def git_probe(arguments: Sequence[str], label: str) -> str:
            completed = _run(
                [git_executable, "-C", str(root), *arguments],
                cwd=root,
                timeout=20,
            )
            if completed.returncode != 0:
                _fail(
                    "HYDRATED_ACCEPTANCE_UPSTREAM_GIT_PROBE_FAILED",
                    f"Git could not verify the supplied checkout {label}.",
                    details={
                        "arguments": list(arguments),
                        "exit_code": completed.returncode,
                        "stdout": completed.stdout.strip(),
                        "stderr": completed.stderr.strip(),
                    },
                )
            return completed.stdout.strip()

        detected_root = Path(
            git_probe(["rev-parse", "--show-toplevel"], "root")
        ).resolve(strict=True)
        if _normalized_path(detected_root) != _normalized_path(root):
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_GIT_ROOT_INVALID",
                "--upstream-root must be the exact Git worktree root.",
                details={
                    "supplied": str(root),
                    "detected": str(detected_root),
                },
            )
        detected_commit = git_probe(
            ["rev-parse", "HEAD"],
            "commit",
        ).lower()
        if detected_commit != expected_commit:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_COMMIT_MISMATCH",
                "The supplied AutoDock Vina checkout is not the pinned commit.",
                details={
                    "expected": expected_commit,
                    "actual": detected_commit,
                },
            )
        detected_tag = git_probe(
            ["describe", "--tags", "--exact-match"],
            "tag",
        )
        if detected_tag != expected_tag:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_TAG_MISMATCH",
                "The supplied checkout tag does not match the manifest.",
                details={"expected": expected_tag, "actual": detected_tag},
            )

    resolved: dict[str, Path] = {}
    verified: dict[str, Any] = {}
    for key, record_value in required_files.items():
        if not isinstance(record_value, Mapping):
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} is invalid.",
            )
        relative_text = str(record_value.get("path") or "")
        relative = Path(relative_text)
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} has an unsafe path.",
                details={"path": relative_text},
            )
        path = _regular_file(root / relative, str(key))
        try:
            path = path.resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            _fail(
                "HYDRATED_ACCEPTANCE_FILE_OUTSIDE_UPSTREAM",
                f"Manifest file {key} resolves outside --upstream-root.",
                details={"path": str(path)},
            )

        actual = _portable_text_snapshot(path)
        actual_size = int(actual["size_bytes"])
        actual_sha = str(actual["sha256"])
        expected_size = int(record_value.get("size_bytes") or 0)
        expected_sha = str(record_value.get("sha256") or "").lower()
        portable_expected = record_value.get("portable_text_identity")
        if not isinstance(portable_expected, Mapping):
            _fail(
                "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
                f"Manifest file record {key} lacks portable text identity.",
            )
        expected_algorithm = str(portable_expected.get("algorithm") or "")
        expected_portable_size = int(portable_expected.get("size_bytes") or 0)
        expected_portable_sha = str(
            portable_expected.get("sha256") or ""
        ).lower()
        actual_portable = actual["portable_identity"]
        actual_line_ending_style = str(actual["line_endings"]["style"])
        if actual_line_ending_style not in accepted_line_endings:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_LINE_ENDINGS_UNSUPPORTED",
                (
                    f"Upstream file {key} must use consistent LF or CRLF "
                    "line endings."
                ),
                details={
                    "path": relative.as_posix(),
                    "accepted": sorted(accepted_line_endings),
                    "actual": actual["line_endings"],
                },
            )
        if (
            expected_algorithm != PORTABLE_IDENTITY_ALGORITHM
            or int(actual_portable["size_bytes"]) != expected_portable_size
            or str(actual_portable["sha256"]) != expected_portable_sha
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_PORTABLE_IDENTITY_MISMATCH",
                (
                    f"Upstream file {key} does not match the pinned content "
                    "after line-ending normalization."
                ),
                details={
                    "path": relative.as_posix(),
                    "expected": dict(portable_expected),
                    "actual": actual_portable,
                    "line_endings": actual["line_endings"],
                },
            )
        expected_variant = (
            {
                "size_bytes": expected_portable_size,
                "sha256": expected_portable_sha,
            }
            if actual_line_ending_style == "lf"
            else {
                "size_bytes": expected_size,
                "sha256": expected_sha,
            }
        )
        if (
            actual_size != int(expected_variant["size_bytes"])
            or actual_sha != str(expected_variant["sha256"])
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_CHECKOUT_HASH_MISMATCH",
                (
                    f"Upstream file {key} is not the exact pinned "
                    f"{actual_line_ending_style.upper()} checkout form."
                ),
                details={
                    "path": relative.as_posix(),
                    "variant": actual_line_ending_style,
                    "expected": expected_variant,
                    "actual": {
                        "size_bytes": actual_size,
                        "sha256": actual_sha,
                    },
                },
            )

        detected_blob = ""
        expected_blob = str(record_value.get("git_blob_sha1") or "").lower()
        if detected_commit:
            completed = _run(
                [
                    git_executable,
                    "-C",
                    str(root),
                    "rev-parse",
                    f"HEAD:{relative.as_posix()}",
                ],
                cwd=root,
                timeout=20,
            )
            if completed.returncode != 0:
                _fail(
                    "HYDRATED_ACCEPTANCE_UPSTREAM_BLOB_UNAVAILABLE",
                    f"Git could not resolve the pinned blob for {key}.",
                    details={
                        "path": relative.as_posix(),
                        "stderr": completed.stderr.strip(),
                    },
                )
            detected_blob = completed.stdout.strip().lower()
            if detected_blob != expected_blob:
                _fail(
                    "HYDRATED_ACCEPTANCE_UPSTREAM_BLOB_MISMATCH",
                    f"The repository blob for {key} is not pinned.",
                    details={
                        "path": relative.as_posix(),
                        "expected": expected_blob,
                        "actual": detected_blob,
                    },
                )

        resolved[str(key)] = path
        verified[str(key)] = {
            "path": relative.as_posix(),
            "checkout_variant": actual_line_ending_style,
            "size_bytes": actual_size,
            "sha256": actual_sha,
            "line_endings": actual["line_endings"],
            "portable_identity": actual_portable,
            "git_blob_sha1": (
                detected_blob or "not_available_hashes_verified"
            ),
            "matches_pinned_windows_crlf_bytes": (
                actual_line_ending_style == "crlf"
            ),
        }

    expected_w_sha = str(
        (
            ((manifest.get("expected") or {}).get("water_map") or {}).get(
                "portable_lf_sha256"
            )
            or ""
        )
    ).lower()
    actual_w_sha = str(
        (
            verified.get("map_W", {}).get("portable_identity")
            or {}
        ).get("sha256")
        or ""
    )
    if actual_w_sha != expected_w_sha:
        _fail(
            "HYDRATED_ACCEPTANCE_OFFICIAL_W_PORTABLE_HASH_MISMATCH",
            "The official W map portable content hash is not the pinned value.",
            details={"expected": expected_w_sha, "actual": actual_w_sha},
        )

    return resolved, {
        "tag": str(upstream.get("tag") or ""),
        "expected_commit": expected_commit,
        "detected_commit": detected_commit or "not_available_hashes_verified",
        "detected_tag": detected_tag or "not_available_hashes_verified",
        "git_root_verified": bool(detected_commit),
        "identity_policy": {
            "algorithm": PORTABLE_IDENTITY_ALGORITHM,
            "accepted_line_endings": sorted(accepted_line_endings),
            "known_windows_crlf_bytes_are_advisory": True,
        },
        "files": verified,
    }


def _scientific_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def _execute_step(
    steps: dict[str, Any],
    name: str,
    action: Callable[[], StepResult],
) -> StepResult:
    """Record one successful or failed scientific gate in JSON-ready form."""

    try:
        result = action()
    except HydratedAcceptanceError as exc:
        steps[name] = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
        exc.steps = dict(steps)
        raise
    except Exception as exc:  # noqa: BLE001 - normalize a step failure.
        wrapped = HydratedAcceptanceError(
            "HYDRATED_ACCEPTANCE_STEP_UNEXPECTED_ERROR",
            f"Acceptance step {name} failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
        steps[name] = {
            "ok": False,
            "error": {
                "code": wrapped.code,
                "message": wrapped.message,
                "details": wrapped.details,
            },
        }
        wrapped.steps = dict(steps)
        raise wrapped from exc

    steps[name] = {"ok": True, **dict(result)}
    return result


def _count_w_atoms(lines: Sequence[str]) -> int:
    count = 0
    for line in lines:
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        fields = line.split()
        if fields and fields[-1] == "W":
            count += 1
    return count


def _split_models(path: Path) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    models: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("MODEL"):
            if current is not None:
                _fail(
                    "HYDRATED_ACCEPTANCE_PDBQT_INVALID",
                    "Nested MODEL records were found in a PDBQT output.",
                )
            current = [line]
        elif current is not None:
            current.append(line)
            if line.startswith("ENDMDL"):
                models.append(current)
                current = None
    if current is not None:
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_INVALID",
            "A PDBQT MODEL is missing ENDMDL.",
        )
    if not models:
        models = [lines]
    return models


def _validate_output_mode_contract(
    models: Sequence[Sequence[str]],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    accepted_raw = expected.get("accepted_output_mode_counts")
    minimum = expected.get("minimum_output_modes")
    maximum = expected.get("maximum_output_modes")
    requested = expected.get("num_modes")
    if (
        accepted_raw != [1, 2, 3, 4, 5, 6, 7, 8, 9]
        or minimum != 1
        or maximum != 9
        or requested != 9
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "The hydrated Vina mode contract must accept one through nine modes.",
            details={
                "accepted_output_mode_counts": accepted_raw,
                "minimum_output_modes": minimum,
                "maximum_output_modes": maximum,
                "num_modes": requested,
            },
        )

    mode_count = len(models)
    if mode_count not in set(range(1, 10)):
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_MODE_COUNT",
            "Vina must emit between one and nine hydrated modes.",
            details={
                "accepted_output_mode_counts": [1, 2, 3, 4, 5, 6, 7, 8, 9],
                "minimum": 1,
                "maximum": 9,
                "actual": mode_count,
            },
        )

    model_numbers: list[int] = []
    for model in models:
        first = str(model[0] if model else "")
        fields = first.split()
        if (
            len(fields) != 2
            or fields[0] != "MODEL"
            or not fields[1].isdigit()
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_VINA_MODE_SEQUENCE",
                "Each hydrated pose must begin with an integer MODEL record.",
                details={"record": first},
            )
        model_numbers.append(int(fields[1]))
    expected_numbers = list(range(1, mode_count + 1))
    if model_numbers != expected_numbers:
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_MODE_SEQUENCE",
            "Hydrated Vina MODEL records must be continuous from 1.",
            details={
                "expected": expected_numbers,
                "actual": model_numbers,
            },
        )
    return {
        "accepted_output_mode_counts": [1, 2, 3, 4, 5, 6, 7, 8, 9],
        "minimum": 1,
        "maximum": 9,
        "actual": mode_count,
        "model_numbers": model_numbers,
        "continuous": True,
    }


def _model_affinity(model: Sequence[str]) -> float:
    for line in model:
        if line.startswith("REMARK VINA RESULT:"):
            fields = line.split()
            try:
                value = float(fields[3])
            except (IndexError, ValueError) as exc:
                _fail(
                    "HYDRATED_ACCEPTANCE_SCORE_INVALID",
                    "A Vina result affinity could not be parsed.",
                    details={"line": line, "error": str(exc)},
                )
            if not math.isfinite(value):
                _fail(
                    "HYDRATED_ACCEPTANCE_SCORE_INVALID",
                    "A Vina result affinity is non-finite.",
                    details={"line": line},
                )
            return value
    _fail(
        "HYDRATED_ACCEPTANCE_SCORE_MISSING",
        "A Vina output MODEL has no REMARK VINA RESULT affinity.",
    )
    raise AssertionError("unreachable")


def _reference_coordinate_fingerprint(
    coordinates: Sequence[Sequence[float]],
) -> str:
    payload = "\n".join(
        ",".join(f"{float(value):.4f}" for value in coordinate)
        for coordinate in coordinates
    ).encode("ascii")
    return _sha256_bytes(payload)


def _parse_crystal_reference_sdf(
    path: Path,
    pose_expected: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _validate_pose_recovery_contract(pose_expected)
    reference_path = _regular_file(path, "crystal reference ligand")
    try:
        lines = reference_path.read_text(
            encoding="utf-8",
            errors="strict",
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
            "The pinned 1UW6 crystal-reference SDF cannot be read.",
            details={"path": str(reference_path), "error": str(exc)},
        )
    if len(lines) < 4:
        _fail(
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
            "The pinned 1UW6 crystal-reference SDF is truncated.",
            details={"path": str(reference_path), "line_count": len(lines)},
        )
    counts = lines[3]
    try:
        atom_count = int(counts[0:3])
        bond_count = int(counts[3:6])
    except (ValueError, IndexError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
            "The pinned 1UW6 crystal-reference SDF has no V2000 counts line.",
            details={"line": counts, "error": str(exc)},
        )
    expected_atom_count = int(contract["reference_atom_count"])
    expected_bonds = [
        tuple(int(value) for value in bond)
        for bond in contract["reference_bonds_one_based"]
    ]
    if (
        atom_count != expected_atom_count
        or bond_count != len(expected_bonds)
        or len(lines) < 4 + atom_count + bond_count + 2
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_TOPOLOGY_MISMATCH",
            "The crystal-reference SDF atom or bond count is not pinned.",
            details={
                "expected_atom_count": expected_atom_count,
                "actual_atom_count": atom_count,
                "expected_bond_count": len(expected_bonds),
                "actual_bond_count": bond_count,
            },
        )

    coordinates: list[tuple[float, float, float]] = []
    elements: list[str] = []
    for atom_index in range(atom_count):
        line = lines[4 + atom_index]
        try:
            coordinate = (
                float(line[0:10].strip()),
                float(line[10:20].strip()),
                float(line[20:30].strip()),
            )
            element = line[31:34].strip()
        except (ValueError, IndexError) as exc:
            _fail(
                "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
                "A crystal-reference SDF atom record cannot be parsed.",
                details={
                    "atom_zero_based": atom_index,
                    "line": line,
                    "error": str(exc),
                },
            )
        if (
            not element
            or not all(math.isfinite(value) for value in coordinate)
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
                "A crystal-reference SDF atom is missing or non-finite.",
                details={
                    "atom_zero_based": atom_index,
                    "element": element,
                    "coordinate": coordinate,
                },
            )
        coordinates.append(coordinate)
        elements.append(element)

    bonds: list[tuple[int, int, int]] = []
    for bond_index in range(bond_count):
        line = lines[4 + atom_count + bond_index]
        try:
            bond = (
                int(line[0:3]),
                int(line[3:6]),
                int(line[6:9]),
            )
        except (ValueError, IndexError) as exc:
            _fail(
                "HYDRATED_ACCEPTANCE_REFERENCE_SDF_INVALID",
                "A crystal-reference SDF bond record cannot be parsed.",
                details={
                    "bond_zero_based": bond_index,
                    "line": line,
                    "error": str(exc),
                },
            )
        bonds.append(bond)

    expected_elements = [str(value) for value in contract["reference_elements"]]
    fingerprint_record = contract["reference_coordinate_fingerprint"]
    actual_fingerprint = _reference_coordinate_fingerprint(coordinates)
    if (
        elements != expected_elements
        or bonds != expected_bonds
        or lines[4 + atom_count + bond_count].strip() != "M  END"
        or lines.count("$$$$") != 1
        or actual_fingerprint != str(fingerprint_record["sha256"])
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_REFERENCE_SDF_TOPOLOGY_MISMATCH",
            (
                "The crystal-reference SDF elements, bonds, or frozen "
                "coordinates do not match 1UW6."
            ),
            details={
                "expected_elements": expected_elements,
                "actual_elements": elements,
                "expected_bonds": expected_bonds,
                "actual_bonds": bonds,
                "expected_coordinate_sha256": fingerprint_record["sha256"],
                "actual_coordinate_sha256": actual_fingerprint,
            },
        )

    mapping = [
        int(value)
        for value in contract[
            "smiles_to_reference_sdf_atom_zero_based"
        ]
    ]
    if sorted(mapping) != list(range(atom_count)):
        _fail(
            "HYDRATED_ACCEPTANCE_POSE_MAPPING_INVALID",
            "The frozen SMILES-to-SDF atom mapping is not a permutation.",
            details={"mapping": mapping, "atom_count": atom_count},
        )
    return {
        "coordinates": tuple(coordinates),
        "elements": tuple(elements),
        "evidence": {
            "reference_file_key": contract["reference_file_key"],
            "format": contract["reference_format"],
            "atom_count": atom_count,
            "bond_count": bond_count,
            "elements_verified": True,
            "bonds_verified": True,
            "coordinate_fingerprint": {
                **dict(fingerprint_record),
                "verified": True,
            },
        },
    }


def _pdbqt_heavy_atoms_for_pose_recovery(
    model: Sequence[str],
    reference: Mapping[str, Any],
    pose_expected: Mapping[str, Any],
) -> dict[int, tuple[tuple[float, float, float], str]]:
    contract = _validate_pose_recovery_contract(pose_expected)
    smiles_lines = [
        line.removeprefix("REMARK SMILES ")
        for line in model
        if line.startswith("REMARK SMILES ")
        and not line.startswith("REMARK SMILES IDX ")
    ]
    if smiles_lines != [str(contract["pdbqt_smiles"])]:
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_SMILES_MISMATCH",
            "A project Vina pose does not preserve the pinned ligand SMILES.",
            details={
                "expected": contract["pdbqt_smiles"],
                "actual": smiles_lines,
            },
        )

    idx_lines = [
        line.removeprefix("REMARK SMILES IDX ")
        for line in model
        if line.startswith("REMARK SMILES IDX ")
    ]
    if len(idx_lines) != 1:
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
            "A project Vina pose must contain one REMARK SMILES IDX record.",
            details={"records": idx_lines},
        )
    try:
        actual_pairs = [int(value) for value in idx_lines[0].split()]
    except ValueError as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
            "A project Vina pose has a non-integer SMILES IDX mapping.",
            details={"record": idx_lines[0], "error": str(exc)},
        )
    expected_pairs = [
        int(value)
        for value in contract["pdbqt_smiles_idx_pairs_one_based"]
    ]
    if actual_pairs != expected_pairs:
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
            "A project Vina pose does not preserve the pinned SMILES IDX mapping.",
            details={"expected": expected_pairs, "actual": actual_pairs},
        )

    serial_to_smiles: dict[int, int] = {}
    for offset in range(0, len(actual_pairs), 2):
        smiles_one_based = actual_pairs[offset]
        pdbqt_serial = actual_pairs[offset + 1]
        if pdbqt_serial in serial_to_smiles:
            _fail(
                "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
                "The PDBQT SMILES IDX mapping repeats an atom serial.",
                details={"pdbqt_serial": pdbqt_serial},
            )
        serial_to_smiles[pdbqt_serial] = smiles_one_based - 1
    atom_count = int(contract["reference_atom_count"])
    if (
        sorted(serial_to_smiles) != list(range(1, atom_count + 1))
        or sorted(serial_to_smiles.values()) != list(range(atom_count))
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PDBQT_MAPPING_MISMATCH",
            "The PDBQT SMILES IDX record is not a complete atom bijection.",
            details={"mapping": serial_to_smiles},
        )

    all_atoms: dict[int, tuple[tuple[float, float, float], str]] = {}
    for line in model:
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        try:
            serial = int(line[6:11].strip())
            coordinate = (
                float(line[30:38].strip()),
                float(line[38:46].strip()),
                float(line[46:54].strip()),
            )
            atom_type = line.split()[-1].upper()
        except (ValueError, IndexError) as exc:
            _fail(
                "HYDRATED_ACCEPTANCE_PDBQT_POSE_INVALID",
                "A project Vina PDBQT atom record cannot be parsed.",
                details={"line": line, "error": str(exc)},
            )
        if serial in all_atoms or not all(
            math.isfinite(value) for value in coordinate
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_PDBQT_POSE_INVALID",
                "A project Vina PDBQT atom serial is repeated or non-finite.",
                details={"serial": serial, "coordinate": coordinate},
            )
        all_atoms[serial] = (coordinate, atom_type)

    reference_elements = tuple(str(value) for value in reference["elements"])
    smiles_to_sdf = [
        int(value)
        for value in contract[
            "smiles_to_reference_sdf_atom_zero_based"
        ]
    ]
    selected: dict[int, tuple[tuple[float, float, float], str]] = {}
    for serial, smiles_index in serial_to_smiles.items():
        if serial not in all_atoms:
            _fail(
                "HYDRATED_ACCEPTANCE_PDBQT_POSE_INVALID",
                "A mapped ligand heavy atom is missing from a project pose.",
                details={"pdbqt_serial": serial},
            )
        coordinate, atom_type = all_atoms[serial]
        pdbqt_element = (
            "C"
            if atom_type in {"A", "C"}
            else "N"
            if atom_type in {"N", "NA"}
            else ""
        )
        reference_index = smiles_to_sdf[smiles_index]
        expected_element = reference_elements[reference_index]
        if not pdbqt_element or pdbqt_element != expected_element:
            _fail(
                "HYDRATED_ACCEPTANCE_POSE_ELEMENT_MISMATCH",
                "The explicit PDBQT-to-reference mapping changes atom element.",
                details={
                    "pdbqt_serial": serial,
                    "smiles_atom_zero_based": smiles_index,
                    "reference_sdf_atom_zero_based": reference_index,
                    "pdbqt_atom_type": atom_type,
                    "pdbqt_element": pdbqt_element,
                    "reference_element": expected_element,
                },
            )
        selected[serial] = (coordinate, pdbqt_element)
    return selected


def _evaluate_pose_recovery(
    models: Sequence[Sequence[str]],
    reference: Mapping[str, Any],
    pose_expected: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _validate_pose_recovery_contract(pose_expected)
    top_n = int(contract["top_n_modes"])
    threshold = float(contract["maximum_rmsd_angstrom"])
    if len(models) < top_n:
        _fail(
            "HYDRATED_ACCEPTANCE_POSE_MODE_COUNT",
            "The project Vina output has fewer modes than the frozen top-N gate.",
            details={"required": top_n, "actual": len(models)},
        )
    reference_coordinates = tuple(reference["coordinates"])
    smiles_to_sdf = [
        int(value)
        for value in contract[
            "smiles_to_reference_sdf_atom_zero_based"
        ]
    ]
    expected_pairs = [
        int(value)
        for value in contract["pdbqt_smiles_idx_pairs_one_based"]
    ]
    serial_to_smiles = {
        expected_pairs[offset + 1]: expected_pairs[offset] - 1
        for offset in range(0, len(expected_pairs), 2)
    }
    mode_rmsd: list[dict[str, Any]] = []
    mode_rmsd_raw: list[tuple[int, float]] = []
    for model_index, model in enumerate(models, start=1):
        first = str(model[0] if model else "")
        fields = first.split()
        if (
            len(fields) != 2
            or fields[0] != "MODEL"
            or not fields[1].isdigit()
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_PDBQT_POSE_INVALID",
                "A pose-recovery MODEL record is missing or invalid.",
                details={"record": first, "model_index": model_index},
            )
        mode = int(fields[1])
        atoms = _pdbqt_heavy_atoms_for_pose_recovery(
            model,
            reference,
            contract,
        )
        squared_distance = 0.0
        for serial, smiles_index in serial_to_smiles.items():
            reference_index = smiles_to_sdf[smiles_index]
            coordinate = atoms[serial][0]
            expected_coordinate = reference_coordinates[reference_index]
            squared_distance += sum(
                (actual - expected_value) ** 2
                for actual, expected_value in zip(
                    coordinate,
                    expected_coordinate,
                    strict=True,
                )
            )
        rmsd = math.sqrt(squared_distance / len(serial_to_smiles))
        if not math.isfinite(rmsd):
            _fail(
                "HYDRATED_ACCEPTANCE_POSE_RMSD_INVALID",
                "A same-frame heavy-atom RMSD is non-finite.",
                details={"mode": mode, "rmsd_angstrom": rmsd},
            )
        mode_rmsd.append(
            {
                "mode": mode,
                "rmsd_angstrom": round(rmsd, 6),
            }
        )
        mode_rmsd_raw.append((mode, rmsd))

    best_mode, best_rmsd_raw = min(
        mode_rmsd_raw[:top_n],
        key=lambda record: record[1],
    )
    best_rmsd_reported = round(best_rmsd_raw, 6)
    passed = best_rmsd_raw <= threshold
    evidence = {
        "reference": dict(reference.get("evidence") or {}),
        "rmsd_semantics": contract["rmsd_semantics"],
        "alignment_applied": False,
        "heavy_atoms_only": True,
        "atom_count": len(serial_to_smiles),
        "pdbqt_smiles": contract["pdbqt_smiles"],
        "pdbqt_smiles_idx_pairs_one_based": expected_pairs,
        "smiles_to_reference_sdf_atom_zero_based": smiles_to_sdf,
        "mode_rmsd_angstrom": mode_rmsd,
        "top_n_modes": top_n,
        "best_mode_within_top_n": best_mode,
        "best_rmsd_angstrom_within_top_n": best_rmsd_reported,
        "maximum_rmsd_angstrom": threshold,
        "passed": passed,
        "current_project_vina_output": True,
    }
    if not passed:
        _fail(
            "HYDRATED_ACCEPTANCE_POSE_RECOVERY_FAILED",
            (
                "The current DockStart 1UW6 run did not recover a crystal-like "
                "pose within the pinned top-3 same-frame RMSD threshold."
            ),
            details=evidence,
        )
    return evidence


def _model_water_coordinates(
    model: Sequence[str],
) -> list[tuple[float, float, float]]:
    coordinates: list[tuple[float, float, float]] = []
    for line in model:
        if line[:6].strip().upper() not in {"ATOM", "HETATM"}:
            continue
        fields = line.split()
        if not fields or fields[-1].upper() != "W":
            continue
        try:
            coordinate = (
                float(line[30:38].strip()),
                float(line[38:46].strip()),
                float(line[46:54].strip()),
            )
        except ValueError as exc:
            _fail(
                "HYDRATED_ACCEPTANCE_WATER_COORDINATE_INVALID",
                "A final-type W atom has invalid PDBQT coordinates.",
                details={"line": line, "error": str(exc)},
            )
        if not all(math.isfinite(value) for value in coordinate):
            _fail(
                "HYDRATED_ACCEPTANCE_WATER_COORDINATE_INVALID",
                "A final-type W atom has non-finite PDBQT coordinates.",
                details={"line": line},
            )
        coordinates.append(coordinate)
    return coordinates


def _legacy_dry_map_minimum(
    water_map: AutoGridMap,
    coordinate: tuple[float, float, float],
) -> float:
    """Reproduce only the v1.2.7 dry.py map-index convention.

    This is a compatibility oracle, not DockStart's runtime lookup rule.
    """

    spacing = water_map.geometry.spacing
    shape = water_map.geometry.shape
    center = water_map.geometry.center
    legacy_origin = tuple(
        axis_center - (axis_points * spacing / 2.0)
        for axis_center, axis_points in zip(center, shape, strict=True)
    )
    legacy_maximum = tuple(
        axis_center + (axis_points * spacing / 2.0)
        for axis_center, axis_points in zip(center, shape, strict=True)
    )
    if not all(
        minimum < value < maximum
        for value, minimum, maximum in zip(
            coordinate,
            legacy_origin,
            legacy_maximum,
            strict=True,
        )
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_LEGACY_WATER_OUTSIDE_MAP",
            "An official 1UW6 water falls outside the legacy dry.py map bounds.",
            details={"coordinate": coordinate},
        )

    center_index = tuple(
        int(round((value - minimum) / spacing))
        for value, minimum in zip(coordinate, legacy_origin, strict=True)
    )
    radius_steps = max(1, int(round(1.0 / spacing)))
    best = math.inf
    for x_offset in range(-radius_steps, radius_steps + 1):
        x_index = center_index[0] + x_offset
        if not 0 <= x_index < shape[0]:
            break
        for y_offset in range(-radius_steps, radius_steps + 1):
            y_index = center_index[1] + y_offset
            if not 0 <= y_index < shape[1]:
                break
            for z_offset in range(-radius_steps, radius_steps + 1):
                z_index = center_index[2] + z_offset
                if not 0 <= z_index < shape[2]:
                    break
                best = min(
                    best,
                    water_map.value_at_index(x_index, y_index, z_index),
                )
    if not math.isfinite(best):
        _fail(
            "HYDRATED_ACCEPTANCE_LEGACY_MAP_SAMPLE_EMPTY",
            "The legacy dry.py compatibility lookup sampled no map values.",
            details={"coordinate": coordinate},
        )
    return best


def _legacy_dry_reference(
    official_raw_output: Path,
    water_map_path: Path,
) -> dict[str, Any]:
    """Calculate the documented v1.2.7 dry.py totals without executing it."""

    water_map = parse_autogrid_map(water_map_path)
    models = _split_models(official_raw_output)
    retained_by_mode: list[int] = []
    map_values_by_mode: list[list[float]] = []
    strong_count = 0
    weak_count = 0
    displaced_count = 0
    for model in models:
        mode_values: list[float] = []
        mode_retained = 0
        for coordinate in _model_water_coordinates(model):
            value = _legacy_dry_map_minimum(water_map, coordinate)
            mode_values.append(value)
            if value < -0.5:
                strong_count += 1
                mode_retained += 1
            elif value < -0.3:
                weak_count += 1
                mode_retained += 1
            else:
                displaced_count += 1
        retained_by_mode.append(mode_retained)
        map_values_by_mode.append(mode_values)
    raw_water_count = strong_count + weak_count + displaced_count
    return {
        "semantics": "upstream_v1_2_7_dry_py_map_lookup_reference_only",
        "runtime_rule": False,
        "raw_water_count": raw_water_count,
        "strong_water_count": strong_count,
        "weak_water_count": weak_count,
        "displaced_water_count": displaced_count,
        "retained_by_mode": retained_by_mode,
        "map_values_by_mode": map_values_by_mode,
    }


def _verify_meeko(
    python_executable: Path,
    input_sdf: Path,
    output_pdbqt: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    python_path = _regular_file(python_executable, "Python executable")
    version_probe = _run(
        [
            str(python_path),
            "-I",
            "-B",
            "-c",
            "import meeko; print(getattr(meeko, '__version__', ''))",
        ],
        cwd=output_pdbqt.parent,
        timeout=60,
        env=_scientific_environment(),
    )
    if version_probe.returncode != 0:
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_UNAVAILABLE",
            "The selected Python cannot import Meeko.",
            details={
                "stderr": version_probe.stderr.strip(),
                "stdout": version_probe.stdout.strip(),
            },
        )
    version = version_probe.stdout.strip()
    expected_version = str(expected.get("validated_version") or "")
    if version != expected_version:
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_VERSION_MISMATCH",
            "The selected Meeko version is not the pinned acceptance version.",
            details={"expected": expected_version, "actual": version},
        )

    command = [
        str(python_path),
        "-I",
        "-B",
        "-m",
        "meeko.cli.mk_prepare_ligand",
        "-i",
        str(input_sdf),
        "-o",
        str(output_pdbqt),
        "-w",
    ]
    completed = _run(
        command,
        cwd=output_pdbqt.parent,
        timeout=180,
        env=_scientific_environment(),
    )
    if completed.returncode != 0:
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_FAILED",
            "Meeko hydrated ligand preparation failed.",
            details={
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            },
        )
    _regular_file(output_pdbqt, "generated hydrated ligand")
    actual_sha = _sha256(output_pdbqt)
    expected_sha = str(expected.get("hydrated_ligand_sha256") or "").lower()
    lines = output_pdbqt.read_text(encoding="utf-8", errors="strict").splitlines()
    water_count = _count_w_atoms(lines)
    expected_waters = int(expected.get("water_atom_count") or 0)
    if actual_sha != expected_sha or water_count != expected_waters:
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_OUTPUT_MISMATCH",
            "Meeko did not reproduce the pinned hydrated ligand.",
            details={
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "expected_water_count": expected_waters,
                "actual_water_count": water_count,
            },
        )
    return {
        "version": version,
        "command": command,
        "sha256": actual_sha,
        "size_bytes": output_pdbqt.stat().st_size,
        "water_atom_count": water_count,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _verify_water_map(
    oa_map: Path,
    hd_map: Path,
    official_w_map: Path,
    generated_w_map: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    generated = generate_best_water_map(oa_map, hd_map, generated_w_map)
    generated_parsed = parse_autogrid_map(generated_w_map)
    official_parsed = parse_autogrid_map(official_w_map)

    if (
        generated_parsed.header_lines != official_parsed.header_lines
        or generated_parsed.geometry != official_parsed.geometry
        or generated_parsed.values != official_parsed.values
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_W_MAP_MISMATCH",
            "The clean-room BEST W map is not semantically identical to the official map.",
            details={
                "generated_sha256": generated_parsed.sha256,
                "official_sha256": official_parsed.sha256,
            },
        )
    expected_points = int(expected.get("value_count") or 0)
    if len(generated_parsed.values) != expected_points:
        _fail(
            "HYDRATED_ACCEPTANCE_W_MAP_POINT_COUNT",
            "The generated W map has an unexpected point count.",
            details={
                "expected": expected_points,
                "actual": len(generated_parsed.values),
            },
        )
    return {
        "method": generated.get("method"),
        "parameters": generated.get("parameters"),
        "statistics": generated.get("statistics"),
        "generated_sha256": generated_parsed.sha256,
        "generated_size_bytes": generated_parsed.size_bytes,
        "official_sha256": official_parsed.sha256,
        "semantic_identity": True,
        "value_count": len(generated_parsed.values),
    }


def _stage_vina_maps(
    files: Mapping[str, Path],
    generated_w_map: Path,
    destination: Path,
) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    map_keys = ("map_A", "map_C", "map_HD", "map_N", "map_NA", "map_d", "map_e")
    for key in map_keys:
        source = files[key]
        shutil.copyfile(source, destination / source.name)
    shutil.copyfile(generated_w_map, destination / "1uw6_receptor.W.map")
    return destination / "1uw6_receptor"


def _vina_version(vina_executable: Path, cwd: Path) -> str:
    completed = _run([str(vina_executable), "--version"], cwd=cwd, timeout=30)
    output = "\n".join((completed.stdout, completed.stderr)).strip()
    for token in output.replace("v", " ").split():
        if token.count(".") >= 2 and all(
            part.isdigit() for part in token.strip().split(".")[:3]
        ):
            return token.strip()
    return output


def _verify_vina(
    vina_executable: Path,
    hydrated_ligand: Path,
    maps_prefix: Path,
    output_pdbqt: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    vina_path = _regular_file(vina_executable, "AutoDock Vina executable")
    version = _vina_version(vina_path, output_pdbqt.parent)
    expected_version = str(expected.get("validated_version") or "")
    if version != expected_version:
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_VERSION_MISMATCH",
            "The selected Vina version is not the pinned acceptance version.",
            details={"expected": expected_version, "actual": version},
        )

    command = [
        str(vina_path),
        "--ligand",
        str(hydrated_ligand),
        "--maps",
        str(maps_prefix),
        "--scoring",
        str(expected.get("scoring") or "ad4"),
        "--exhaustiveness",
        str(int(expected.get("exhaustiveness") or 32)),
        "--num_modes",
        str(int(expected.get("num_modes") or 9)),
        "--energy_range",
        str(int(expected.get("energy_range") or 3)),
        "--cpu",
        str(int(expected.get("cpu") or 1)),
        "--seed",
        str(int(expected.get("seed") or 0)),
        "--out",
        str(output_pdbqt),
    ]
    completed = _run(command, cwd=output_pdbqt.parent, timeout=300)
    if completed.returncode != 0:
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_FAILED",
            "Vina hydrated AD4 docking failed.",
            details={
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            },
        )
    _regular_file(output_pdbqt, "Vina hydrated raw output")
    models = _split_models(output_pdbqt)
    mode_contract = _validate_output_mode_contract(models, expected)
    affinities = [_model_affinity(model) for model in models]
    water_counts = [_count_w_atoms(model) for model in models]
    requested_modes = int(expected.get("num_modes") or 0)
    expected_waters = int(expected.get("water_atoms_per_raw_pose") or 0)
    expected_best = float(expected.get("best_affinity"))
    tolerance = float(expected.get("affinity_tolerance") or 0.0)
    if any(count != expected_waters for count in water_counts):
        _fail(
            "HYDRATED_ACCEPTANCE_RAW_WATER_LOST",
            "At least one raw Vina mode did not retain every hydrated W atom.",
            details={"expected_per_mode": expected_waters, "actual": water_counts},
        )
    if abs(affinities[0] - expected_best) > tolerance:
        _fail(
            "HYDRATED_ACCEPTANCE_BEST_AFFINITY_MISMATCH",
            "The fixed-seed best affinity is outside the official neighborhood.",
            details={
                "expected": expected_best,
                "tolerance": tolerance,
                "actual": affinities[0],
            },
        )
    return {
        "version": version,
        "command": command,
        "sha256": _sha256(output_pdbqt),
        "requested_maximum_modes": requested_modes,
        "mode_count": len(models),
        "mode_contract": mode_contract,
        "affinities": affinities,
        "water_atoms_by_mode": water_counts,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _clean_room_postprocess_counts(
    summary: Mapping[str, Any],
    modes: Sequence[Any],
) -> dict[str, Any]:
    return {
        "raw_water_count": int(summary.get("raw_water_count") or 0),
        "strong_water_count": int(summary.get("strong_water_count") or 0),
        "weak_water_count": int(summary.get("weak_water_count") or 0),
        "displaced_water_count": int(
            summary.get("displaced_water_count") or 0
        ),
        "retained_by_mode": [
            int(
                (mode.get("strong_water_count") or 0)
                + (mode.get("weak_water_count") or 0)
            )
            for mode in modes
            if isinstance(mode, Mapping)
        ],
    }


def _validate_clean_room_postprocess_oracle(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    wanted = {
        key: expected.get(key)
        for key in (
            "raw_water_count",
            "strong_water_count",
            "weak_water_count",
            "displaced_water_count",
            "retained_by_mode",
        )
    }
    if dict(actual) != wanted:
        _fail(
            "HYDRATED_ACCEPTANCE_POSTPROCESS_MISMATCH",
            "The corrected 1UW6 retained-water classification changed.",
            details={"expected": wanted, "actual": dict(actual)},
        )
    if (
        wanted["raw_water_count"] != 18
        or wanted["strong_water_count"] != 8
        or wanted["weak_water_count"] != 1
        or wanted["displaced_water_count"] != 9
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "The 1UW6 clean-room water oracle must remain 8 strong / 1 weak / 9 displaced.",
            details={"oracle": wanted},
        )
    return dict(actual)


def _verify_postprocess(
    official_raw_output: Path,
    receptor: Path,
    water_map: Path,
    output_dir: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Call the clean-room hydrated postprocessor and assert 1UW6 semantics."""

    try:
        from dockstart_core.hydrated_postprocess import postprocess_hydrated_output
    except (ImportError, AttributeError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_POSTPROCESS_UNAVAILABLE",
            "The clean-room hydrated postprocessor is not available.",
            details={"error": str(exc)},
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    annotated_output = output_dir / "official_raw_retained_waters.pdbqt"
    water_free_output = output_dir / "official_raw_water_free.pdbqt"
    result = postprocess_hydrated_output(
        official_raw_output,
        receptor,
        water_map,
        annotated_output,
        water_free_output,
    )
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        _fail(
            "HYDRATED_ACCEPTANCE_POSTPROCESS_FAILED",
            "The clean-room hydrated postprocessor reported failure.",
            details={"result": dict(result) if isinstance(result, Mapping) else str(result)},
        )

    summary = result.get("summary")
    modes = result.get("modes")
    if not isinstance(summary, Mapping) or not isinstance(modes, list):
        _fail(
            "HYDRATED_ACCEPTANCE_POSTPROCESS_RESULT_INVALID",
            "The hydrated postprocessor returned an incomplete result.",
            details={"result": dict(result)},
        )

    actual_clean_room = _clean_room_postprocess_counts(summary, modes)
    clean_room_expected = expected.get("clean_room")
    legacy_expected = expected.get("upstream_legacy_reference")
    if not isinstance(clean_room_expected, Mapping) or not isinstance(
        legacy_expected,
        Mapping,
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Postprocess expectations must contain clean-room and legacy sections.",
        )
    _validate_clean_room_postprocess_oracle(
        actual_clean_room,
        clean_room_expected,
    )

    legacy_reference = _legacy_dry_reference(
        official_raw_output,
        water_map,
    )
    wanted_legacy = {
        key: legacy_expected.get(key)
        for key in (
            "raw_water_count",
            "strong_water_count",
            "weak_water_count",
            "displaced_water_count",
            "retained_by_mode",
        )
    }
    actual_legacy = {
        key: legacy_reference.get(key)
        for key in wanted_legacy
    }
    if actual_legacy != wanted_legacy:
        _fail(
            "HYDRATED_ACCEPTANCE_LEGACY_REFERENCE_MISMATCH",
            "The documented v1.2.7 dry.py 1UW6 reference changed.",
            details={"expected": wanted_legacy, "actual": actual_legacy},
        )

    raw_affinities = [
        _model_affinity(model) for model in _split_models(official_raw_output)
    ]
    annotated_affinities = [
        _model_affinity(model) for model in _split_models(annotated_output)
    ]
    if raw_affinities != annotated_affinities:
        _fail(
            "HYDRATED_ACCEPTANCE_AFFINITY_REWRITTEN",
            "Retained-water postprocessing changed the raw Vina affinities.",
            details={"raw": raw_affinities, "annotated": annotated_affinities},
        )
    if any(_count_w_atoms(model) for model in _split_models(water_free_output)):
        _fail(
            "HYDRATED_ACCEPTANCE_WATER_FREE_OUTPUT_INVALID",
            "The water-free pose output still contains W atoms.",
        )
    return {
        "clean_room": actual_clean_room,
        "upstream_legacy_reference": legacy_reference,
        "raw_affinities": raw_affinities,
        "affinities_unchanged": True,
        "annotated_output_sha256": _sha256(annotated_output),
        "water_free_output_sha256": _sha256(water_free_output),
    }


def _require_api_success(
    operation: str,
    result: Any,
) -> Mapping[str, Any]:
    if isinstance(result, Mapping) and result.get("ok") is True:
        return result
    error = result.get("error") if isinstance(result, Mapping) else None
    _fail(
        "HYDRATED_ACCEPTANCE_PROJECT_API_FAILED",
        f"DockStart project API operation {operation} failed.",
        details={
            "operation": operation,
            "message": (
                str(result.get("message") or "")
                if isinstance(result, Mapping)
                else ""
            ),
            "error": dict(error) if isinstance(error, Mapping) else error,
            "result_type": type(result).__name__,
        },
    )
    raise AssertionError("unreachable")


def _project_artifact(
    project_root: Path,
    relative_path: str,
    label: str,
) -> Path:
    relative = Path(str(relative_path or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_ARTIFACT_PATH_INVALID",
            f"{label} has an unsafe project-relative path.",
            details={"relative_path": str(relative_path or "")},
        )
    path = (project_root / relative).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_ARTIFACT_OUTSIDE_PROJECT",
            f"{label} resolves outside the temporary DockStart project.",
            details={"relative_path": relative.as_posix(), "path": str(path)},
        )
    return _regular_file(path, label)


def _require_command_executable(
    command: Any,
    expected_executable: Path,
    *,
    code: str,
    label: str,
) -> list[str]:
    normalized = (
        [str(item) for item in command]
        if isinstance(command, list)
        else []
    )
    detected = normalized[0] if normalized else ""
    if (
        not detected
        or _normalized_path(detected)
        != _normalized_path(expected_executable)
    ):
        _fail(
            code,
            f"{label} did not execute the caller-supplied tool.",
            details={
                "supplied": str(expected_executable),
                "detected": detected,
                "command": normalized,
            },
        )
    return normalized


def _verify_configured_tools(
    python_executable: Path,
    vina_executable: Path,
    autogrid_executable: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    python_path = _regular_file(python_executable.resolve(), "Python executable")
    vina_path = _regular_file(vina_executable.resolve(), "AutoDock Vina executable")
    autogrid_path = _regular_file(
        autogrid_executable.resolve(),
        "external AutoGrid4 executable",
    )

    meeko_expected = expected.get("meeko")
    meeko_expected = (
        meeko_expected if isinstance(meeko_expected, Mapping) else {}
    )
    meeko_detection = meeko_adapter.detect(
        str(python_path),
        source="configured",
    )
    if (
        not meeko_detection.path
        or _normalized_path(meeko_detection.path)
        != _normalized_path(python_path)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_PYTHON_PATH_MISMATCH",
            "Meeko detection did not preserve the caller-supplied Python executable.",
            details={
                "supplied": str(python_path),
                "detected": str(meeko_detection.path or ""),
            },
        )
    if (
        meeko_detection.status != "ok"
        or meeko_detection.version
        != str(meeko_expected.get("validated_version") or "")
        or meeko_detection.source != "configured"
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MEEKO_INVALID",
            "The selected Python does not provide the pinned Meeko toolchain.",
            details=meeko_detection.to_dict(),
        )

    autogrid_detection = autogrid_adapter.detect(str(autogrid_path))
    if (
        not autogrid_detection.path
        or _normalized_path(autogrid_detection.path)
        != _normalized_path(autogrid_path)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_AUTOGRID_PATH_MISMATCH",
            "AutoGrid detection did not preserve the caller-supplied executable.",
            details={
                "supplied": str(autogrid_path),
                "detected": str(autogrid_detection.path or ""),
            },
        )
    if (
        autogrid_detection.status != "ok"
        or autogrid_detection.is_bundled
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_AUTOGRID_INVALID",
            "The user-supplied AutoGrid4 executable was not detected as an external tool.",
            details=autogrid_detection.to_dict(),
        )
    if not autogrid_version_supported(
        autogrid_detection.version,
        HYDRATED_PROTOCOL_ID,
    ):
        minimum = ".".join(
            str(part)
            for part in minimum_autogrid_version(HYDRATED_PROTOCOL_ID)
        )
        _fail(
            "HYDRATED_ACCEPTANCE_AUTOGRID_VERSION_UNSUPPORTED",
            f"Hydrated AD4 project verification requires AutoGrid {minimum}+.",
            details={
                "detected": autogrid_detection.version or "unknown",
                "required": f">={minimum}",
            },
        )

    disabled_bundled_candidate = vina_path.with_name(
        f".{vina_path.name}.dockstart_acceptance_disabled_bundled"
    )
    vina_detection = vina_adapter.detect(
        str(vina_path),
        bundled_path=str(disabled_bundled_candidate),
    )
    if (
        not vina_detection.path
        or _normalized_path(vina_detection.path)
        != _normalized_path(vina_path)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_PATH_MISMATCH",
            "Vina detection did not preserve the caller-supplied executable.",
            details={
                "supplied": str(vina_path),
                "detected": str(vina_detection.path or ""),
            },
        )
    maps_feature = (
        ((vina_detection.capabilities or {}).get("features") or {}).get("maps")
        if isinstance(vina_detection.capabilities, Mapping)
        else {}
    )
    expected_vina = expected.get("vina")
    expected_vina = expected_vina if isinstance(expected_vina, Mapping) else {}
    if (
        vina_detection.status != "ok"
        or vina_detection.version
        != str(expected_vina.get("validated_version") or "")
        or not isinstance(maps_feature, Mapping)
        or maps_feature.get("status") != "supported"
        or maps_feature.get("supported") is not True
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_INVALID",
            "The selected Vina does not satisfy the pinned version and --maps gate.",
            details=vina_detection.to_dict(),
        )

    return {
        "python": {
            "path": str(python_path),
            "size_bytes": python_path.stat().st_size,
            "sha256": _sha256(python_path),
            "source": meeko_detection.source,
            "meeko_version": meeko_detection.version,
            "caller_path_preserved": True,
        },
        "autogrid": {
            "path": autogrid_detection.path,
            "version": autogrid_detection.version,
            "source": autogrid_detection.source,
            "is_bundled": autogrid_detection.is_bundled,
            "size_bytes": autogrid_path.stat().st_size,
            "sha256": _sha256(autogrid_path),
            "caller_path_preserved": True,
        },
        "vina": {
            "path": vina_detection.path,
            "version": vina_detection.version,
            "source": vina_detection.source,
            "is_bundled": vina_detection.is_bundled,
            "size_bytes": vina_path.stat().st_size,
            "sha256": _sha256(vina_path),
            "maps_capability": dict(maps_feature),
            "caller_path_preserved": True,
        },
    }


def _project_expectations(expected: Mapping[str, Any]) -> Mapping[str, Any]:
    project = expected.get("project_api")
    if not isinstance(project, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.project_api section is missing.",
        )
    return project


def _create_and_configure_project(
    work_root: Path,
    files: Mapping[str, Path],
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    project_expected = _project_expectations(expected)
    box = project_expected.get("box")
    if not isinstance(box, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest project_api.box section is invalid.",
        )
    vina_expected = expected.get("vina")
    if not isinstance(vina_expected, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected.vina section is invalid.",
        )

    created = _require_api_success(
        "create_project",
        create_project("hydrated_1uw6_acceptance", str(work_root)),
    )
    project_root = Path(str(created.get("project_dir") or "")).resolve()
    if project_root.parent != work_root.resolve():
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_PATH_INVALID",
            "The project API created the project outside the temporary workspace.",
            details={"project_dir": str(project_root), "workspace": str(work_root)},
        )

    ligand_import = _require_api_success(
        "import_ligand_raw_file",
        import_ligand_raw_file(
            str(project_root),
            str(files["hydrated_ligand_input"]),
        ),
    )
    receptor_import = _require_api_success(
        "import_receptor_pdbqt",
        import_receptor_pdbqt(
            str(project_root),
            str(files["receptor"]),
        ),
    )
    box_update = _require_api_success(
        "update_box_params",
        update_box_params(str(project_root), dict(box)),
    )
    vina_update = _require_api_success(
        "update_vina_params",
        update_vina_params(
            str(project_root),
            {
                "exhaustiveness": int(vina_expected.get("exhaustiveness") or 32),
                "num_modes": int(vina_expected.get("num_modes") or 9),
                "energy_range": float(vina_expected.get("energy_range") or 3),
                "cpu": int(vina_expected.get("cpu") or 1),
                "seed": int(vina_expected.get("seed") or 0),
            },
        ),
    )

    receptor_relative = str(
        ((receptor_import.get("project") or {}).get("receptor") or {}).get(
            "file"
        )
        or "prepared/receptor.pdbqt"
    )
    receptor_path = _project_artifact(
        project_root,
        receptor_relative,
        "project receptor PDBQT",
    )
    raw_relative = str(ligand_import.get("raw_file") or "")
    raw_path = _project_artifact(
        project_root,
        raw_relative,
        "project raw hydrated-ligand SDF",
    )
    source_ligand = _portable_text_snapshot(files["hydrated_ligand_input"])
    imported_ligand = _portable_text_snapshot(raw_path)
    if (
        imported_ligand["portable_identity"]
        != source_ligand["portable_identity"]
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_RAW_IMPORT_CHANGED",
            "The local raw-ligand import changed scientific file content.",
            details={
                "source": source_ligand,
                "imported": imported_ligand,
            },
        )
    if _sha256(receptor_path) != _sha256(files["receptor"]):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_RECEPTOR_IMPORT_CHANGED",
            "The PDBQT receptor import changed upstream bytes.",
        )

    return {
        "project_dir": str(project_root),
        "raw_ligand": {
            "relative_path": raw_relative,
            **imported_ligand,
        },
        "receptor": {
            "relative_path": receptor_relative,
            "size_bytes": receptor_path.stat().st_size,
            "sha256": _sha256(receptor_path),
        },
        "box": dict((box_update.get("project") or {}).get("box") or box),
        "vina": dict((vina_update.get("project") or {}).get("vina") or {}),
    }


def _verify_project_hydrated_ligand(
    project_root: Path,
    official_hydrated_ligand: Path,
    python_executable: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    prepared = _require_api_success(
        "prepare_hydrated_ligand",
        prepare_hydrated_ligand(str(project_root)),
    )
    manifest = prepared.get("manifest")
    manifest = manifest if isinstance(manifest, Mapping) else {}
    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, Mapping) else {}
    hydrated_record = outputs.get("hydrated_pdbqt")
    hydrated_record = (
        hydrated_record if isinstance(hydrated_record, Mapping) else {}
    )
    hydrated_relative = str(
        hydrated_record.get("path")
        or hydrated_record.get("relative_path")
        or ""
    )
    hydrated_path = _project_artifact(
        project_root,
        hydrated_relative,
        "project hydrated ligand PDBQT",
    )
    actual = _portable_text_snapshot(hydrated_path)
    official = _portable_text_snapshot(official_hydrated_ligand)
    meeko_expected = expected.get("meeko")
    meeko_expected = (
        meeko_expected if isinstance(meeko_expected, Mapping) else {}
    )
    expected_portable = str(
        meeko_expected.get("portable_lf_sha256") or ""
    ).lower()
    actual_portable = str(actual["portable_identity"]["sha256"])
    if (
        actual["portable_identity"] != official["portable_identity"]
        or actual_portable != expected_portable
        or int(hydrated_record.get("water_count") or 0)
        != int(meeko_expected.get("water_atom_count") or 0)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_MEEKO_OUTPUT_MISMATCH",
            "The project API did not reproduce the pinned hydrated ligand.",
            details={
                "expected_portable_sha256": expected_portable,
                "generated": actual,
                "official": official,
                "water_count": hydrated_record.get("water_count"),
            },
        )
    tools = manifest.get("tools")
    tools = tools if isinstance(tools, Mapping) else {}
    probe_command = _require_command_executable(
        tools.get("probe_command"),
        python_executable,
        code="HYDRATED_ACCEPTANCE_PROJECT_MEEKO_PYTHON_PATH_MISMATCH",
        label="Meeko capability probe",
    )
    prepare_command = _require_command_executable(
        manifest.get("command"),
        python_executable,
        code="HYDRATED_ACCEPTANCE_PROJECT_MEEKO_PYTHON_PATH_MISMATCH",
        label="Hydrated ligand preparation",
    )
    meeko_version = str(tools.get("meeko_version") or "")
    if meeko_version != str(meeko_expected.get("validated_version") or ""):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_MEEKO_VERSION_MISMATCH",
            "The project preparation manifest recorded an unexpected Meeko version.",
            details={
                "expected": meeko_expected.get("validated_version"),
                "actual": meeko_version,
            },
        )
    return {
        "preparation_id": str(prepared.get("preparation_id") or ""),
        "manifest_file": str(prepared.get("manifest_file") or ""),
        "manifest_sha256": str(prepared.get("manifest_sha256") or ""),
        "meeko_version": meeko_version,
        "python_executable": str(python_executable),
        "probe_command": probe_command,
        "prepare_command": prepare_command,
        "caller_python_path_preserved": True,
        "hydrated_ligand": {
            "relative_path": hydrated_relative,
            **actual,
            "water_atom_count": int(hydrated_record.get("water_count") or 0),
            "byte_identical_to_supplied_checkout": (
                actual["sha256"] == official["sha256"]
            ),
            "portable_identity_matches_official": True,
        },
    }


def _verify_project_maps(
    project_root: Path,
    official_w_map: Path,
    autogrid_executable: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    project_expected = _project_expectations(expected)
    grid = project_expected.get("grid")
    grid = grid if isinstance(grid, Mapping) else {}
    generated = _require_api_success(
        "generate_hydrated_maps",
        generate_hydrated_maps(
            str(project_root),
            {
                "spacing": float(grid.get("spacing") or 0.375),
                "grid_points": dict(grid.get("grid_points") or {}),
            },
        ),
    )
    manifest = generated.get("manifest")
    manifest = manifest if isinstance(manifest, Mapping) else {}
    maps = manifest.get("maps")
    maps = maps if isinstance(maps, Mapping) else {}
    water_record = maps.get("water_map")
    water_record = (
        water_record if isinstance(water_record, Mapping) else {}
    )
    water_path = _project_artifact(
        project_root,
        str(water_record.get("relative_path") or ""),
        "project W map",
    )
    generated_w = parse_autogrid_map(water_path)
    official_w = parse_autogrid_map(official_w_map)
    if (
        generated_w.geometry != official_w.geometry
        or generated_w.values != official_w.values
    ):
        differences = [
            abs(left - right)
            for left, right in zip(
                generated_w.values,
                official_w.values,
                strict=False,
            )
        ]
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_W_MAP_MISMATCH",
            "The project-generated W map is not numerically identical to the official map.",
            details={
                "generated_points": len(generated_w.values),
                "official_points": len(official_w.values),
                "maximum_absolute_difference": (
                    max(differences) if differences else None
                ),
            },
        )

    base_record = manifest.get("base_maps_manifest")
    base_record = base_record if isinstance(base_record, Mapping) else {}
    base_path = _project_artifact(
        project_root,
        str(base_record.get("relative_path") or ""),
        "project base maps manifest",
    )
    try:
        base_manifest = json.loads(base_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_BASE_MANIFEST_INVALID",
            "The project base maps manifest cannot be parsed.",
            details={"error": str(exc), "path": str(base_path)},
        )
    if not isinstance(base_manifest, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_BASE_MANIFEST_INVALID",
            "The project base maps manifest is not an object.",
        )
    gpf_record = base_manifest.get("gpf")
    gpf_record = gpf_record if isinstance(gpf_record, Mapping) else {}
    gpf_path = _project_artifact(
        project_root,
        str(gpf_record.get("relative_path") or ""),
        "project AutoGrid GPF",
    )
    gpf_text = gpf_path.read_text(encoding="utf-8", errors="strict")
    required_gpf_lines = [
        str(line)
        for line in project_expected.get("gpf_required_lines") or []
    ]
    missing_gpf_lines = [
        line for line in required_gpf_lines if line not in gpf_text.splitlines()
    ]
    if missing_gpf_lines:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_GPF_MISMATCH",
            "The generated GPF lacks pinned 1UW6 grid directives.",
            details={"missing_lines": missing_gpf_lines},
        )

    tool = base_manifest.get("autogrid")
    tool = tool if isinstance(tool, Mapping) else {}
    autogrid_command = _require_command_executable(
        tool.get("command"),
        autogrid_executable,
        code="HYDRATED_ACCEPTANCE_PROJECT_AUTOGRID_PATH_MISMATCH",
        label="Project AutoGrid map generation",
    )
    if (
        base_manifest.get("status") != "ready"
        or base_manifest.get("protocol_id") != HYDRATED_PROTOCOL_ID
        or not autogrid_version_supported(
            str(tool.get("version") or ""),
            HYDRATED_PROTOCOL_ID,
        )
        or tool.get("exit_code") is None
        or int(tool.get("exit_code")) != 0
        or not tool.get("path")
        or _normalized_path(str(tool.get("path")))
        != _normalized_path(autogrid_executable)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_AUTOGRID_RECORD_INVALID",
            "The base maps manifest lacks a successful supported AutoGrid record.",
            details={
                "status": base_manifest.get("status"),
                "protocol_id": base_manifest.get("protocol_id"),
                "autogrid": dict(tool),
            },
        )
    status = _require_api_success(
        "get_hydrated_status_after_maps",
        get_hydrated_status(str(project_root)),
    )
    if status.get("maps_ready") is not True:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_MAPS_NOT_ACTIVE",
            "Project-generated hydrated maps did not remain active and valid.",
            details={"issues": status.get("maps_issues")},
        )

    official_snapshot = _portable_text_snapshot(official_w_map)
    generated_snapshot = _portable_text_snapshot(water_path)
    return {
        "map_set_id": str(generated.get("map_set_id") or ""),
        "manifest_file": str(generated.get("manifest_file") or ""),
        "manifest_sha256": str(generated.get("manifest_sha256") or ""),
        "gpf": {
            "relative_path": str(gpf_record.get("relative_path") or ""),
            "size_bytes": gpf_path.stat().st_size,
            "sha256": _sha256(gpf_path),
            "required_lines": required_gpf_lines,
        },
        "autogrid": {
            key: tool.get(key)
            for key in ("path", "version", "source", "sha256", "command", "exit_code")
        }
        | {
            "command": autogrid_command,
            "caller_path_preserved": True,
        },
        "base_map_files": [
            str(item.get("name") or "")
            for item in ((base_manifest.get("maps") or {}).get("files") or [])
            if isinstance(item, Mapping)
        ],
        "water_map": {
            "relative_path": str(water_record.get("relative_path") or ""),
            "method": str(water_record.get("method") or ""),
            "value_count": len(generated_w.values),
            "geometry": generated_w.geometry.to_dict(),
            "maximum_absolute_value_difference": 0.0,
            "rms_value_difference": 0.0,
            "numerically_identical_to_official": True,
            "generated": generated_snapshot,
            "official": official_snapshot,
        },
    }


def _unexpected_control_bytes(payload: bytes) -> list[int]:
    return [
        value
        for value in payload
        if value == 0 or (value < 32 and value not in {9, 10, 13}) or value == 127
    ]


def _verify_project_run(
    project_root: Path,
    vina_executable: Path,
    crystal_reference_path: Path,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    project_expected = _project_expectations(expected)
    normalization_expected = project_expected.get("output_normalization")
    if not isinstance(normalization_expected, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest project_api.output_normalization section is invalid.",
        )
    expected_normalization_method = str(
        normalization_expected.get("method") or ""
    )
    if (
        not expected_normalization_method
        or normalization_expected.get(
            "raw_bytes_must_be_preserved_when_normalized"
        )
        is not True
        or normalization_expected.get(
            "normalized_output_must_be_utf8_and_control_free"
        )
        is not True
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest output-normalization requirements are incomplete.",
        )

    preflight = _require_api_success(
        "get_hydrated_run_preflight",
        get_hydrated_run_preflight(str(project_root)),
    )
    prepared = _require_api_success(
        "prepare_hydrated_run",
        prepare_hydrated_run(str(project_root)),
    )
    run_id = str(prepared.get("run_id") or "")
    executed = _require_api_success(
        "execute_prepared_vina_run",
        execute_prepared_vina_run(str(project_root), run_id),
    )
    metadata = executed.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if (
        metadata.get("status") != "finished"
        or (metadata.get("hydrated_postprocess") or {}).get("status")
        != "finished"
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_RUN_NOT_FINISHED",
            "The public run API did not finish Vina plus hydrated postprocessing.",
            details={
                "status": metadata.get("status"),
                "stage": metadata.get("stage"),
                "hydrated_postprocess": metadata.get("hydrated_postprocess"),
            },
        )

    normalization = metadata.get("output_normalization")
    normalization = (
        normalization if isinstance(normalization, Mapping) else {}
    )
    if (
        normalization.get("method") != expected_normalization_method
        or normalization.get("status") not in {"not_required", "normalized"}
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_OUTPUT_NORMALIZATION_MISSING",
            "The run lacks the required Vina PDBQT normalization record.",
            details={"record": dict(normalization)},
        )
    normalized_relative = str(
        normalization.get("normalized_file")
        or metadata.get("output_file")
        or ""
    )
    raw_relative = str(
        normalization.get("raw_output_file")
        or normalization.get("source_file")
        or normalized_relative
    )
    normalized_path = _project_artifact(
        project_root,
        normalized_relative,
        "normalized Vina PDBQT output",
    )
    raw_path = _project_artifact(
        project_root,
        raw_relative,
        "preserved raw Vina PDBQT output",
    )
    normalized_bytes = normalized_path.read_bytes()
    raw_bytes = raw_path.read_bytes()
    raw_nul_count = raw_bytes.count(b"\x00")
    normalized_controls = _unexpected_control_bytes(normalized_bytes)
    if (
        normalized_controls
        or _sha256_bytes(raw_bytes)
        != str(normalization.get("source_sha256") or "")
        or _sha256_bytes(normalized_bytes)
        != str(normalization.get("normalized_sha256") or "")
        or raw_nul_count
        != int(normalization.get("nul_bytes_detected") or 0)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_OUTPUT_NORMALIZATION_INVALID",
            "The raw/normalized Vina outputs do not match normalization evidence.",
            details={
                "record": dict(normalization),
                "raw_nul_count": raw_nul_count,
                "normalized_control_bytes": normalized_controls[:20],
            },
        )
    if raw_nul_count:
        if (
            normalization.get("status") != "normalized"
            or normalization.get("changed") is not True
            or int(normalization.get("nul_bytes_removed") or 0)
            != raw_nul_count
            or int(normalization.get("recognized_padding_blocks") or 0) <= 0
            or raw_path == normalized_path
        ):
            _fail(
                "HYDRATED_ACCEPTANCE_PROJECT_WINDOWS_NUL_GATE_FAILED",
                "Observed Windows Vina NUL padding was not safely normalized and archived.",
                details={"record": dict(normalization)},
            )
    elif (
        normalization.get("status") != "not_required"
        or normalization.get("changed") is not False
        or raw_path != normalized_path
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_NORMALIZATION_NOOP_INVALID",
            "A NUL-free Vina output has an inconsistent no-op normalization record.",
            details={"record": dict(normalization)},
        )

    models = _split_models(normalized_path)
    vina_expected = expected.get("vina")
    vina_expected = vina_expected if isinstance(vina_expected, Mapping) else {}
    mode_contract = _validate_output_mode_contract(models, vina_expected)
    affinities = [_model_affinity(model) for model in models]
    water_counts = [_count_w_atoms(model) for model in models]
    pose_expected = expected.get("pose_recovery")
    pose_expected = _validate_pose_recovery_contract(pose_expected)
    crystal_reference = _parse_crystal_reference_sdf(
        crystal_reference_path,
        pose_expected,
    )
    pose_recovery = _evaluate_pose_recovery(
        models,
        crystal_reference,
        pose_expected,
    )
    if (
        any(
            count != int(vina_expected.get("water_atoms_per_raw_pose") or 0)
            for count in water_counts
        )
        or abs(
            affinities[0] - float(vina_expected.get("best_affinity"))
        )
        > float(vina_expected.get("affinity_tolerance") or 0.0)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_VINA_RESULT_MISMATCH",
            "The project Vina result is outside the pinned 1UW6 acceptance bounds.",
            details={
                "mode_count": len(models),
                "mode_contract": mode_contract,
                "affinities": affinities,
                "water_counts": water_counts,
            },
        )
    command = metadata.get("command")
    normalized_command = _require_command_executable(
        command,
        vina_executable,
        code="HYDRATED_ACCEPTANCE_PROJECT_VINA_PATH_MISMATCH",
        label="Project hydrated Vina run",
    )
    preflight_binary = preflight.get("vina_binary")
    preflight_binary = (
        preflight_binary
        if isinstance(preflight_binary, Mapping)
        else {}
    )
    preflight_vina_path = str(
        preflight_binary.get("absolute_path")
        or preflight_binary.get("path")
        or ""
    )
    scoring_index = (
        normalized_command.index("--scoring")
        if "--scoring" in normalized_command
        else -1
    )
    if (
        "--maps" not in normalized_command
        or scoring_index < 0
        or scoring_index + 1 >= len(normalized_command)
        or normalized_command[scoring_index + 1] != "ad4"
        or not preflight_vina_path
        or _normalized_path(preflight_vina_path)
        != _normalized_path(vina_executable)
        or not metadata.get("vina_path")
        or _normalized_path(str(metadata.get("vina_path")))
        != _normalized_path(vina_executable)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_COMMAND_INVALID",
            "The prepared project command does not use hydrated AD4 maps.",
            details={
                "command": normalized_command,
                "preflight_vina": dict(preflight_binary),
                "metadata_vina_path": metadata.get("vina_path"),
                "supplied": str(vina_executable),
            },
        )
    return {
        "run_id": run_id,
        "preflight_vina": dict(preflight_binary),
        "command": normalized_command,
        "caller_vina_path_preserved": True,
        "mode_count": len(models),
        "mode_contract": mode_contract,
        "affinities": affinities,
        "water_atoms_by_mode": water_counts,
        "pose_recovery": pose_recovery,
        "output_normalization": {
            **dict(normalization),
            "raw_sha256_verified": True,
            "normalized_sha256_verified": True,
            "normalized_output_is_control_free_utf8": True,
            "windows_nul_padding_observed": bool(raw_nul_count),
        },
        "hydrated_postprocess": dict(
            metadata.get("hydrated_postprocess") or {}
        ),
    }


def _validate_report_semantics(
    report_text: str,
    project_expected: Mapping[str, Any],
) -> dict[str, Any]:
    required_phrases = [
        str(value)
        for value in project_expected.get("report_required_phrases") or []
    ]
    if not required_phrases or any(not phrase for phrase in required_phrases):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Hydrated report semantics must contain explicit non-empty phrases.",
        )
    missing_phrases = [
        phrase for phrase in required_phrases if phrase not in report_text
    ]
    if missing_phrases:
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_REPORT_CONTENT_MISSING",
            "The hydrated Markdown report lacks required scientific statements.",
            details={"missing_phrases": missing_phrases},
        )
    return {
        "required_phrases": required_phrases,
        "all_present": True,
    }


def _verify_project_results_and_report(
    project_root: Path,
    run_id: str,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    analyzed = _require_api_success(
        "analyze_vina_run_results",
        analyze_vina_run_results(str(project_root), run_id),
    )
    hydrated_results = _require_api_success(
        "load_hydrated_results",
        load_hydrated_results(str(project_root), run_id),
    )
    exported = _require_api_success(
        "export_markdown_report",
        export_markdown_report(str(project_root), run_id),
    )
    scores = hydrated_results.get("scores")
    scores = scores if isinstance(scores, list) else []
    modes = hydrated_results.get("modes")
    modes = modes if isinstance(modes, list) else []
    if not scores or len(scores) != len(modes):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_SCORES_INVALID",
            "Hydrated results and scores.csv do not contain the same modes.",
            details={"scores": len(scores), "modes": len(modes)},
        )

    scores_relative = str(
        analyzed.get("scores_file")
        or Path("runs", run_id, "scores.csv").as_posix()
    )
    project_scores_relative = str(
        analyzed.get("project_scores_file")
        or "results/hydrated_ad4_scores.csv"
    )
    scores_path = _project_artifact(
        project_root,
        scores_relative,
        "run scores.csv",
    )
    project_scores_path = _project_artifact(
        project_root,
        project_scores_relative,
        "project hydrated_ad4_scores.csv",
    )
    if scores_path.read_bytes() != project_scores_path.read_bytes():
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_SCORES_PUBLICATION_MISMATCH",
            "Run and project hydrated score tables differ.",
        )

    report_relative = str(exported.get("report_file") or "")
    project_report_relative = str(exported.get("project_report_file") or "")
    report_path = _project_artifact(
        project_root,
        report_relative,
        "run hydrated Markdown report",
    )
    project_report_path = _project_artifact(
        project_root,
        project_report_relative,
        "project hydrated Markdown report",
    )
    report_text = report_path.read_text(encoding="utf-8", errors="strict")
    if report_text != project_report_path.read_text(
        encoding="utf-8",
        errors="strict",
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_REPORT_PUBLICATION_MISMATCH",
            "Run and project hydrated Markdown reports differ.",
        )
    project_expected = _project_expectations(expected)
    report_semantics = _validate_report_semantics(
        report_text,
        project_expected,
    )

    summary = hydrated_results.get("water_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    if (
        int(summary.get("raw_water_count") or 0)
        != int(summary.get("strong_water_count") or 0)
        + int(summary.get("weak_water_count") or 0)
        + int(summary.get("displaced_water_count") or 0)
        or int(summary.get("retained_water_count") or 0)
        != int(summary.get("strong_water_count") or 0)
        + int(summary.get("weak_water_count") or 0)
    ):
        _fail(
            "HYDRATED_ACCEPTANCE_PROJECT_WATER_SUMMARY_INVALID",
            "The project water-summary totals are internally inconsistent.",
            details={"summary": dict(summary)},
        )
    return {
        "run_id": run_id,
        "score_count": len(scores),
        "best_affinity": scores[0].get("affinity_kcal_mol"),
        "scores": {
            "run": {
                "relative_path": scores_relative,
                "size_bytes": scores_path.stat().st_size,
                "sha256": _sha256(scores_path),
            },
            "project": {
                "relative_path": project_scores_relative,
                "size_bytes": project_scores_path.stat().st_size,
                "sha256": _sha256(project_scores_path),
            },
            "published_bytes_identical": True,
        },
        "water_summary": dict(summary),
        "reports": {
            "run": {
                "relative_path": report_relative,
                "size_bytes": report_path.stat().st_size,
                "sha256": _sha256(report_path),
            },
            "project": {
                "relative_path": project_report_relative,
                "size_bytes": project_report_path.stat().st_size,
                "sha256": _sha256(project_report_path),
            },
            "published_text_identical": True,
            **report_semantics,
        },
    }


def verify_hydrated_1uw6(
    upstream_root: str | Path,
    *,
    autogrid_executable: str | Path,
    python_executable: str | Path = DEFAULT_PYTHON,
    vina_executable: str | Path = DEFAULT_VINA,
) -> dict[str, Any]:
    manifest = _load_manifest()
    expected = manifest.get("expected")
    if not isinstance(expected, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest expected section is invalid.",
        )

    upstream_path = Path(upstream_root).expanduser()
    python_path = Path(python_executable).expanduser().resolve(strict=False)
    vina_path = Path(vina_executable).expanduser().resolve(strict=False)
    autogrid_path = (
        Path(autogrid_executable).expanduser().resolve(strict=False)
    )
    files: dict[str, Path] = {}
    upstream_evidence: dict[str, Any] = {}
    steps: dict[str, Any] = {}
    temporary_handle = tempfile.TemporaryDirectory(
        prefix="DockStart_hydrated_1uw6_project_",
    )
    work_root = Path(temporary_handle.name)
    settings_path = work_root / "dockstart_settings.json"
    previous_settings_path = os.environ.get(SETTINGS_ENV_VAR)
    previous_resource_dir = os.environ.get(RESOURCE_DIR_ENV_VAR)
    isolated_resource_dir = work_root / "no_bundled_toolchain"
    result_payload: dict[str, Any] | None = None
    caught: HydratedAcceptanceError | None = None
    try:
        os.environ[SETTINGS_ENV_VAR] = str(settings_path)
        os.environ[RESOURCE_DIR_ENV_VAR] = str(isolated_resource_dir)
        save_settings(
            DockStartSettings(
                tool_paths=ToolPaths(
                    vina=str(vina_path),
                    python=str(python_path),
                    autogrid4=str(autogrid_path),
                ),
            )
        )

        def verify_sources() -> Mapping[str, Any]:
            nonlocal files, upstream_evidence
            files, upstream_evidence = _verify_upstream(
                upstream_path,
                manifest,
            )
            return upstream_evidence

        _execute_step(steps, "upstream_identity", verify_sources)
        _execute_step(
            steps,
            "local_inputs_and_toolchain",
            lambda: {
                "network_or_download_used": False,
                "upstream_root": str(upstream_path),
                "settings_file": str(settings_path),
                "tools": _verify_configured_tools(
                    python_path,
                    vina_path,
                    autogrid_path,
                    expected,
                ),
            },
        )
        setup = _execute_step(
            steps,
            "project_create_import_and_parameters",
            lambda: _create_and_configure_project(
                work_root,
                files,
                expected,
            ),
        )
        project_root = Path(str(setup["project_dir"])).resolve()
        _execute_step(
            steps,
            "project_hydrated_ligand_preparation",
            lambda: _verify_project_hydrated_ligand(
                project_root,
                files["official_hydrated_ligand"],
                python_path,
                expected,
            ),
        )
        _execute_step(
            steps,
            "project_gpf_autogrid_base_and_water_maps",
            lambda: _verify_project_maps(
                project_root,
                files["map_W"],
                autogrid_path,
                expected,
            ),
        )
        run = _execute_step(
            steps,
            "project_vina_normalization_and_hydrated_postprocess",
            lambda: _verify_project_run(
                project_root,
                vina_path,
                files["crystal_reference_ligand"],
                expected,
            ),
        )
        _execute_step(
            steps,
            "project_scores_and_markdown_report",
            lambda: _verify_project_results_and_report(
                project_root,
                str(run["run_id"]),
                expected,
            ),
        )
        _execute_step(
            steps,
            "official_retained_water_reference",
            lambda: _verify_postprocess(
                files["official_raw_output"],
                files["receptor"],
                files["map_W"],
                work_root / "official_reference_postprocess",
                expected.get("postprocess")
                if isinstance(expected.get("postprocess"), Mapping)
                else {},
            ),
        )
        result_payload = {
            "ok": True,
            "fixture_id": str(manifest.get("fixture_id") or ""),
            "verification_scope": (
                "public_project_api_full_chain_with_external_scientific_oracles"
            ),
            "network_or_download_used": False,
            "upstream": upstream_evidence,
            "steps": steps,
        }
    except HydratedAcceptanceError as exc:
        caught = exc
    except Exception as exc:  # noqa: BLE001 - preserve JSON failure boundary.
        caught = HydratedAcceptanceError(
            "HYDRATED_ACCEPTANCE_UNEXPECTED_ERROR",
            "The project-level 1UW6 verifier failed unexpectedly.",
            details={"type": type(exc).__name__, "message": str(exc)},
        )
        caught.steps = dict(steps)
    finally:
        settings_restore_error = ""
        try:
            if previous_settings_path is None:
                os.environ.pop(SETTINGS_ENV_VAR, None)
            else:
                os.environ[SETTINGS_ENV_VAR] = previous_settings_path
        except Exception as exc:  # noqa: BLE001 - report restoration failure.
            settings_restore_error = str(exc)
        settings_restored = (
            SETTINGS_ENV_VAR not in os.environ
            if previous_settings_path is None
            else os.environ.get(SETTINGS_ENV_VAR) == previous_settings_path
        )
        settings_evidence = {
            "variable": SETTINGS_ENV_VAR,
            "previously_set": previous_settings_path is not None,
            "isolated_path": str(settings_path),
            "restored": settings_restored,
            "error": settings_restore_error,
        }

        resource_restore_error = ""
        try:
            if previous_resource_dir is None:
                os.environ.pop(RESOURCE_DIR_ENV_VAR, None)
            else:
                os.environ[RESOURCE_DIR_ENV_VAR] = previous_resource_dir
        except Exception as exc:  # noqa: BLE001 - report restoration failure.
            resource_restore_error = str(exc)
        resource_restored = (
            RESOURCE_DIR_ENV_VAR not in os.environ
            if previous_resource_dir is None
            else os.environ.get(RESOURCE_DIR_ENV_VAR)
            == previous_resource_dir
        )
        resource_evidence = {
            "variable": RESOURCE_DIR_ENV_VAR,
            "previously_set": previous_resource_dir is not None,
            "isolated_path": str(isolated_resource_dir),
            "restored": resource_restored,
            "error": resource_restore_error,
        }

        cleanup_error = ""
        try:
            temporary_handle.cleanup()
        except Exception as exc:  # noqa: BLE001 - report cleanup failure.
            cleanup_error = str(exc)
        cleanup_evidence = {
            "path": str(work_root),
            "cleanup_called": True,
            "removed": not work_root.exists(),
            "error": cleanup_error,
        }

    lifecycle_evidence = {
        "temporary_cleanup": cleanup_evidence,
        "settings_environment": settings_evidence,
        "toolchain_resource_environment": resource_evidence,
    }
    if caught is not None:
        caught.details = {
            **caught.details,
            **lifecycle_evidence,
        }
        if not caught.steps:
            caught.steps = dict(steps)
        raise caught
    if (
        result_payload is None
        or cleanup_error
        or cleanup_evidence["removed"] is not True
        or settings_restore_error
        or settings_evidence["restored"] is not True
        or resource_restore_error
        or resource_evidence["restored"] is not True
    ):
        lifecycle_error = HydratedAcceptanceError(
            "HYDRATED_ACCEPTANCE_TEMPORARY_LIFECYCLE_FAILED",
            (
                "The temporary project could not be removed or its isolated "
                "environment could not be restored."
            ),
            details=lifecycle_evidence,
        )
        lifecycle_error.steps = dict(steps)
        raise lifecycle_error
    result_payload.update(lifecycle_evidence)
    result_payload["temporary_directory_removed"] = True
    result_payload["temporary_artifacts_removed"] = True
    return result_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the DockStart public project API from 1UW6 raw input through "
            "hydrated AD4 maps, Vina, top-3 crystal-pose recovery, "
            "postprocessing, scores, and report."
        ),
    )
    parser.add_argument(
        "--upstream-root",
        required=True,
        help="Path to the pinned AutoDock Vina v1.2.7 source checkout.",
    )
    parser.add_argument(
        "--autogrid",
        required=True,
        help=(
            "Path to a user-supplied external AutoGrid4 4.2.6+ executable. "
            "The verifier never downloads or bundles AutoGrid."
        ),
    )
    parser.add_argument(
        "--python",
        default=str(DEFAULT_PYTHON),
        help="Python executable containing Meeko 0.7.1.",
    )
    parser.add_argument(
        "--vina",
        default=str(DEFAULT_VINA),
        help="AutoDock Vina 1.2.7 executable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = verify_hydrated_1uw6(
            arguments.upstream_root,
            autogrid_executable=arguments.autogrid,
            python_executable=arguments.python,
            vina_executable=arguments.vina,
        )
    except HydratedAcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "hydrated_1uw6_external",
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    },
                    "steps": exc.steps,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 1
    except Exception as exc:  # noqa: BLE001 - acceptance must fail as JSON.
        print(
            json.dumps(
                {
                    "ok": False,
                    "fixture_id": "hydrated_1uw6_external",
                    "error": {
                        "code": "HYDRATED_ACCEPTANCE_UNEXPECTED_ERROR",
                        "message": str(exc),
                        "details": {"type": type(exc).__name__},
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
