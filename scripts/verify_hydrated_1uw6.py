"""Run the external AutoDock Vina 1UW6 hydrated-docking acceptance gate.

No upstream scientific data is distributed with DockStart.  The caller must
provide a local checkout of AutoDock Vina v1.2.7 through ``--upstream-root``.
Every generated artifact is written below a temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
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

from dockstart_core.hydrated_maps import (  # noqa: E402
    AutoGridMap,
    generate_best_water_map,
    parse_autogrid_map,
)

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
StepResult = TypeVar("StepResult", bound=Mapping[str, Any])


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
    return payload


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        _fail(
            "HYDRATED_ACCEPTANCE_FILE_INVALID",
            f"{label} is missing, empty, or a symbolic link.",
            details={"path": str(path)},
        )
    return path


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
    if upstream_root.is_symlink() or not upstream_root.is_dir():
        _fail(
            "HYDRATED_ACCEPTANCE_UPSTREAM_INVALID",
            "--upstream-root must be a plain AutoDock Vina source directory.",
            details={"path": str(upstream_root)},
        )

    upstream = manifest.get("upstream")
    required_files = manifest.get("required_files")
    if not isinstance(upstream, Mapping) or not isinstance(required_files, Mapping):
        _fail(
            "HYDRATED_ACCEPTANCE_MANIFEST_INVALID",
            "Manifest upstream or required_files section is invalid.",
        )

    expected_commit = str(upstream.get("commit") or "")
    detected_commit = ""
    git_result = _run(
        ["git", "-C", str(upstream_root), "rev-parse", "HEAD"],
        cwd=upstream_root,
        timeout=20,
    )
    if git_result.returncode == 0:
        detected_commit = git_result.stdout.strip().lower()
        if detected_commit != expected_commit:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_COMMIT_MISMATCH",
                "The supplied AutoDock Vina checkout is not the pinned commit.",
                details={
                    "expected": expected_commit,
                    "actual": detected_commit,
                },
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
        path = _regular_file(upstream_root / relative, str(key))
        try:
            path.resolve().relative_to(upstream_root)
        except ValueError:
            _fail(
                "HYDRATED_ACCEPTANCE_FILE_OUTSIDE_UPSTREAM",
                f"Manifest file {key} resolves outside --upstream-root.",
                details={"path": str(path)},
            )

        actual_size = path.stat().st_size
        actual_sha = _sha256(path)
        expected_size = int(record_value.get("size_bytes") or 0)
        expected_sha = str(record_value.get("sha256") or "").lower()
        if actual_size != expected_size or actual_sha != expected_sha:
            _fail(
                "HYDRATED_ACCEPTANCE_UPSTREAM_HASH_MISMATCH",
                f"Upstream file {key} does not match the pinned Windows checkout.",
                details={
                    "path": relative.as_posix(),
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                    "expected_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                },
            )
        resolved[str(key)] = path.resolve()
        verified[str(key)] = {
            "path": relative.as_posix(),
            "size_bytes": actual_size,
            "sha256": actual_sha,
        }

    expected_w_sha = str(
        ((manifest.get("expected") or {}).get("water_map") or {}).get("sha256")
        or ""
    ).lower()
    actual_w_sha = verified.get("map_W", {}).get("sha256")
    if actual_w_sha != expected_w_sha:
        _fail(
            "HYDRATED_ACCEPTANCE_OFFICIAL_W_HASH_MISMATCH",
            "The official Windows-checkout W map hash is not the pinned value.",
            details={"expected": expected_w_sha, "actual": actual_w_sha},
        )

    return resolved, {
        "tag": str(upstream.get("tag") or ""),
        "expected_commit": expected_commit,
        "detected_commit": detected_commit or "not_available_hashes_verified",
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
    affinities = [_model_affinity(model) for model in models]
    water_counts = [_count_w_atoms(model) for model in models]
    requested_modes = int(expected.get("num_modes") or 0)
    minimum_modes = int(expected.get("minimum_output_modes") or 1)
    expected_waters = int(expected.get("water_atoms_per_raw_pose") or 0)
    expected_best = float(expected.get("best_affinity"))
    tolerance = float(expected.get("affinity_tolerance") or 0.0)
    if len(models) < minimum_modes or len(models) > requested_modes:
        _fail(
            "HYDRATED_ACCEPTANCE_VINA_MODE_COUNT",
            "Vina emitted an invalid number of hydrated modes.",
            details={
                "requested_maximum": requested_modes,
                "minimum": minimum_modes,
                "actual": len(models),
            },
        )
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
        "affinities": affinities,
        "water_atoms_by_mode": water_counts,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


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

    actual_clean_room = {
        "raw_water_count": int(summary.get("raw_water_count") or 0),
        "strong_water_count": int(summary.get("strong_water_count") or 0),
        "weak_water_count": int(summary.get("weak_water_count") or 0),
        "displaced_water_count": int(summary.get("displaced_water_count") or 0),
        "retained_by_mode": [
            int(
                (mode.get("strong_water_count") or 0)
                + (mode.get("weak_water_count") or 0)
            )
            for mode in modes
            if isinstance(mode, Mapping)
        ],
    }
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
    wanted_clean_room = {
        key: clean_room_expected.get(key)
        for key in (
            "raw_water_count",
            "strong_water_count",
            "weak_water_count",
            "displaced_water_count",
            "retained_by_mode",
        )
    }
    if actual_clean_room != wanted_clean_room:
        _fail(
            "HYDRATED_ACCEPTANCE_POSTPROCESS_MISMATCH",
            "The corrected 1UW6 retained-water classification changed.",
            details={
                "expected": wanted_clean_room,
                "actual": actual_clean_room,
            },
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


def verify_hydrated_1uw6(
    upstream_root: str | Path,
    *,
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

    upstream_path = Path(upstream_root).expanduser().resolve()
    files, upstream_evidence = _verify_upstream(upstream_path, manifest)
    steps: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="DockStart_hydrated_1uw6_") as temporary:
        work_root = Path(temporary)
        hydrated_ligand = work_root / "1uw6_ligand_meeko_hydrated.pdbqt"
        generated_w_map = work_root / "1uw6_receptor.W.map"
        vina_output = work_root / "1uw6_hydrated_ad4_out.pdbqt"

        _execute_step(
            steps,
            "meeko_hydrate",
            lambda: _verify_meeko(
                Path(python_executable).expanduser().resolve(),
                files["hydrated_ligand_input"],
                hydrated_ligand,
                expected.get("meeko")
                if isinstance(expected.get("meeko"), Mapping)
                else {},
            ),
        )
        _execute_step(
            steps,
            "clean_room_water_map",
            lambda: _verify_water_map(
                files["map_OA"],
                files["map_HD"],
                files["map_W"],
                generated_w_map,
                expected.get("water_map")
                if isinstance(expected.get("water_map"), Mapping)
                else {},
            ),
        )
        maps_prefix = _stage_vina_maps(
            files,
            generated_w_map,
            work_root / "maps",
        )
        _execute_step(
            steps,
            "vina_hydrated_ad4",
            lambda: _verify_vina(
                Path(vina_executable).expanduser().resolve(),
                hydrated_ligand,
                maps_prefix,
                vina_output,
                expected.get("vina")
                if isinstance(expected.get("vina"), Mapping)
                else {},
            ),
        )
        _execute_step(
            steps,
            "retained_water_postprocess",
            lambda: _verify_postprocess(
                files["official_raw_output"],
                files["receptor"],
                files["map_W"],
                work_root / "postprocess",
                expected.get("postprocess")
                if isinstance(expected.get("postprocess"), Mapping)
                else {},
            ),
        )

        return {
            "ok": True,
            "fixture_id": str(manifest.get("fixture_id") or ""),
            "upstream": upstream_evidence,
            "steps": steps,
            "temporary_artifacts_removed": True,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify DockStart hydrated AD4 primitives against an external "
            "AutoDock Vina v1.2.7 1UW6 checkout."
        ),
    )
    parser.add_argument(
        "--upstream-root",
        required=True,
        help="Path to the pinned AutoDock Vina v1.2.7 source checkout.",
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
