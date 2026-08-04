"""Run the official 1IEP inputs through DockStart's standard AD4 maps path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dockstart_core.autogrid import generate_maps, set_scoring_protocol  # noqa: E402
from dockstart_core.project import (  # noqa: E402
    analyze_vina_run_results,
    create_project,
    execute_prepared_vina_run,
    export_markdown_report,
    generate_vina_config,
    import_ligand_pdbqt,
    import_receptor_pdbqt,
    prepare_vina_run,
    update_box_params,
    update_vina_params,
)
from dockstart_core.settings import (  # noqa: E402
    DockStartSettings,
    ToolPaths,
    save_settings,
)


class VerificationError(RuntimeError):
    def __init__(self, step: str, result: dict[str, Any]) -> None:
        super().__init__(step)
        self.step = step
        self.result = result


def _require(step: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("ok") is not True:
        raise VerificationError(step, result)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(
    receptor: Path,
    ligand: Path,
    vina: Path,
    autogrid4: Path,
) -> dict[str, Any]:
    inputs = {
        "receptor": receptor.resolve(),
        "ligand": ligand.resolve(),
        "vina": vina.resolve(),
        "autogrid4": autogrid4.resolve(),
    }
    for label, path in inputs.items():
        if not path.is_file() or path.stat().st_size <= 0:
            return {
                "ok": False,
                "error": {
                    "code": "AD4_1IEP_INPUT_MISSING",
                    "message": f"{label} input is missing or empty.",
                    "path": str(path),
                },
            }

    previous_settings = os.environ.get("DOCKSTART_SETTINGS_PATH")
    try:
        with tempfile.TemporaryDirectory(prefix="DockStart_ad4_1iep_") as temporary:
            root = Path(temporary)
            os.environ["DOCKSTART_SETTINGS_PATH"] = str(root / "settings.json")
            save_settings(
                DockStartSettings(
                    tool_paths=ToolPaths(
                        vina=str(inputs["vina"]),
                        autogrid4=str(inputs["autogrid4"]),
                    )
                )
            )
            created = _require("create_project", create_project("ad4_1iep", str(root)))
            project_dir = Path(created["project_dir"])
            _require(
                "import_receptor",
                import_receptor_pdbqt(str(project_dir), str(inputs["receptor"])),
            )
            _require(
                "import_ligand",
                import_ligand_pdbqt(str(project_dir), str(inputs["ligand"])),
            )
            _require(
                "set_box",
                update_box_params(
                    str(project_dir),
                    {
                        "center_x": 15.190,
                        "center_y": 53.903,
                        "center_z": 16.917,
                        "size_x": 20,
                        "size_y": 20,
                        "size_z": 20,
                    },
                ),
            )
            _require(
                "set_vina_parameters",
                update_vina_params(
                    str(project_dir),
                    {
                        "exhaustiveness": 8,
                        "num_modes": 9,
                        "energy_range": 3,
                        "cpu": 0,
                        "seed": 1952347903,
                    },
                ),
            )
            _require("set_ad4_protocol", set_scoring_protocol(str(project_dir), "ad4_maps"))
            maps = _require("generate_maps", generate_maps(str(project_dir)))
            config = _require("generate_config", generate_vina_config(str(project_dir)))
            prepared = _require("prepare_run", prepare_vina_run(str(project_dir)))
            run_id = str(prepared["run_id"])
            executed = _require(
                "execute_run",
                execute_prepared_vina_run(str(project_dir), run_id),
            )
            analyzed = _require(
                "analyze_run",
                analyze_vina_run_results(str(project_dir), run_id),
            )
            report = _require(
                "export_report",
                export_markdown_report(str(project_dir), run_id),
            )
            manifest_path = project_dir / str(maps["manifest_file"])
            report_path = project_dir / str(report["report_file"])
            scores = analyzed.get("scores") if isinstance(analyzed.get("scores"), list) else []
            command = prepared.get("command") if isinstance(prepared.get("command"), list) else []
            return {
                "ok": True,
                "fixture": "AutoDock-Vina example/python_scripting 1IEP",
                "inputs": {
                    label: {
                        "path": str(path),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                    for label, path in inputs.items()
                },
                "maps": {
                    "map_set_id": maps.get("map_set_id"),
                    "manifest_sha256": _sha256(manifest_path),
                    "ligand_atom_types": maps.get("manifest", {})
                    .get("maps", {})
                    .get("ligand_atom_types", []),
                },
                "run": {
                    "run_id": run_id,
                    "status": executed.get("metadata", {}).get("status"),
                    "command": command,
                    "uses_maps": "--maps" in command,
                    "uses_ad4_scoring": "ad4" in command,
                    "score_count": len(scores),
                    "best_affinity": (
                        scores[0].get("affinity_kcal_mol") if scores else None
                    ),
                },
                "config": {
                    "file": config.get("config_file"),
                    "scoring_protocol": prepared.get("metadata", {}).get(
                        "scoring_protocol"
                    ),
                },
                "report": {
                    "file": report.get("report_file"),
                    "sha256": _sha256(report_path),
                },
            }
    except VerificationError as exc:
        return {
            "ok": False,
            "error": {
                "code": "AD4_1IEP_STEP_FAILED",
                "step": exc.step,
                "result": exc.result,
            },
        }
    finally:
        if previous_settings is None:
            os.environ.pop("DOCKSTART_SETTINGS_PATH", None)
        else:
            os.environ["DOCKSTART_SETTINGS_PATH"] = previous_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receptor", type=Path, required=True)
    parser.add_argument("--ligand", type=Path, required=True)
    parser.add_argument("--vina", type=Path, required=True)
    parser.add_argument("--autogrid4", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(
        arguments.receptor,
        arguments.ligand,
        arguments.vina,
        arguments.autogrid4,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
