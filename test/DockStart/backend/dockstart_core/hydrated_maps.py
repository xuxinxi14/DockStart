"""Clean-room AutoGrid water-map primitives for hydrated AD4 docking.

The hydrated protocol needs one ``W`` affinity map derived from the matching
``OA`` and ``HD`` AutoGrid maps.  This module deliberately implements only the
fixed, audited first-version BEST policy:

* OA and HD coefficients are both 1.0;
* if either source value is strictly positive, the displacement entropy value
  is -0.2 kcal/mol;
* otherwise the more favourable (smaller) source value is scaled by 0.6.

No upstream helper script is imported or embedded.  Inputs are parsed as the
documented AutoGrid ASCII format: exactly six header lines followed by one
finite energy value per line, ordered with X changing fastest.
"""

from __future__ import annotations

import hashlib
import math
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dockstart_core.persistence import atomic_write_text

AUTOGRID_HEADER_KEYS = (
    "GRID_PARAMETER_FILE",
    "GRID_DATA_FILE",
    "MACROMOLECULE",
    "SPACING",
    "NELEMENTS",
    "CENTER",
)

BEST_WEIGHT = 0.6
DISPLACEMENT_ENTROPY = -0.2
OA_WEIGHT = 1.0
HD_WEIGHT = 1.0
OUTPUT_DECIMALS = 4

MAX_MAP_FILE_BYTES = 512 * 1024 * 1024
MAX_MAP_POINTS = 20_000_000
GEOMETRY_ABS_TOLERANCE = 1e-9


class HydratedMapError(ValueError):
    """A fail-closed validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AutoGridGeometry:
    """Spatial metadata shared by all maps in one AutoGrid collection."""

    spacing: float
    nelements: tuple[int, int, int]
    center: tuple[float, float, float]

    @property
    def shape(self) -> tuple[int, int, int]:
        """Return the number of grid points, not the number of intervals."""

        return tuple(value + 1 for value in self.nelements)

    @property
    def point_count(self) -> int:
        return math.prod(self.shape)

    @property
    def origin(self) -> tuple[float, float, float]:
        return tuple(
            center - (intervals * self.spacing / 2.0)
            for center, intervals in zip(
                self.center,
                self.nelements,
                strict=True,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "spacing": self.spacing,
            "nelements": {
                "x": self.nelements[0],
                "y": self.nelements[1],
                "z": self.nelements[2],
            },
            "shape": {
                "x": self.shape[0],
                "y": self.shape[1],
                "z": self.shape[2],
            },
            "center": {
                "x": self.center[0],
                "y": self.center[1],
                "z": self.center[2],
            },
            "origin": {
                "x": self.origin[0],
                "y": self.origin[1],
                "z": self.origin[2],
            },
            "point_count": self.point_count,
        }


@dataclass(frozen=True)
class AutoGridMap:
    """A validated AutoGrid map plus coordinate-aware lookup helpers."""

    path: Path
    header_lines: tuple[str, str, str, str, str, str]
    header_references: tuple[str, str, str]
    geometry: AutoGridGeometry
    values: array
    sha256: str
    size_bytes: int
    minimum: float
    maximum: float

    def flat_index(self, x_index: int, y_index: int, z_index: int) -> int:
        """Return an X-fastest index for AutoGrid's nested z(y(x)) order."""

        x_size, y_size, z_size = self.geometry.shape
        if not (
            0 <= x_index < x_size
            and 0 <= y_index < y_size
            and 0 <= z_index < z_size
        ):
            raise IndexError("AutoGrid index is outside the map.")
        return (z_index * y_size + y_index) * x_size + x_index

    def value_at_index(self, x_index: int, y_index: int, z_index: int) -> float:
        return float(self.values[self.flat_index(x_index, y_index, z_index)])

    def nearest_grid_index(
        self,
        x: float,
        y: float,
        z: float,
    ) -> tuple[int, int, int]:
        """Map Cartesian coordinates to the nearest integer grid point."""

        coordinates = (x, y, z)
        if not all(math.isfinite(value) for value in coordinates):
            raise HydratedMapError(
                "HYDRATED_MAP_COORDINATE_NONFINITE",
                "Map lookup coordinates must all be finite.",
            )
        return tuple(
            math.floor(((coordinate - origin) / self.geometry.spacing) + 0.5)
            for coordinate, origin in zip(
                coordinates,
                self.geometry.origin,
                strict=True,
            )
        )

    def minimum_near(
        self,
        x: float,
        y: float,
        z: float,
        *,
        radius_steps: int,
    ) -> dict[str, Any] | None:
        """Return the minimum grid value in a clipped index-space cube.

        ``radius_steps`` is supplied explicitly so callers can record the
        exact ``round(radius_angstrom / spacing)`` decision in their evidence.
        ``None`` means the cube does not intersect this map.
        """

        if isinstance(radius_steps, bool) or not isinstance(radius_steps, int):
            raise HydratedMapError(
                "HYDRATED_MAP_RADIUS_INVALID",
                "Map sampling radius_steps must be an integer.",
            )
        if radius_steps < 0:
            raise HydratedMapError(
                "HYDRATED_MAP_RADIUS_INVALID",
                "Map sampling radius_steps cannot be negative.",
            )

        center_indices = self.nearest_grid_index(x, y, z)
        shape = self.geometry.shape
        bounds: list[tuple[int, int]] = []
        for center_index, axis_size in zip(center_indices, shape, strict=True):
            lower = max(0, center_index - radius_steps)
            upper = min(axis_size - 1, center_index + radius_steps)
            if lower > upper:
                return None
            bounds.append((lower, upper))

        best_value: float | None = None
        best_index: tuple[int, int, int] | None = None
        sample_count = 0
        for z_index in range(bounds[2][0], bounds[2][1] + 1):
            for y_index in range(bounds[1][0], bounds[1][1] + 1):
                for x_index in range(bounds[0][0], bounds[0][1] + 1):
                    value = self.value_at_index(x_index, y_index, z_index)
                    sample_count += 1
                    if best_value is None or value < best_value:
                        best_value = value
                        best_index = (x_index, y_index, z_index)

        if best_value is None or best_index is None:
            return None
        origin = self.geometry.origin
        sampled_coordinate = tuple(
            origin[axis] + best_index[axis] * self.geometry.spacing
            for axis in range(3)
        )
        return {
            "value": best_value,
            "grid_index": {
                "x": best_index[0],
                "y": best_index[1],
                "z": best_index[2],
            },
            "grid_coordinate": {
                "x": sampled_coordinate[0],
                "y": sampled_coordinate[1],
                "z": sampled_coordinate[2],
            },
            "sample_count": sample_count,
        }

    def to_metadata(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "header_references": {
                "grid_parameter_file": self.header_references[0],
                "grid_data_file": self.header_references[1],
                "macromolecule": self.header_references[2],
            },
            "geometry": self.geometry.to_dict(),
            "statistics": {
                "minimum": self.minimum,
                "maximum": self.maximum,
                "point_count": len(self.values),
            },
        }


def _error(code: str, message: str) -> HydratedMapError:
    return HydratedMapError(code, message)


def _finite_float(token: str, *, label: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise _error(
            "HYDRATED_MAP_NUMBER_INVALID",
            f"{label} is not a floating-point number.",
        ) from exc
    if not math.isfinite(value):
        raise _error(
            "HYDRATED_MAP_NUMBER_NONFINITE",
            f"{label} must be finite.",
        )
    return value


def _parse_header(
    header_lines: list[str],
    *,
    label: str,
) -> tuple[tuple[str, str, str], AutoGridGeometry]:
    if len(header_lines) != len(AUTOGRID_HEADER_KEYS):
        raise _error(
            "HYDRATED_MAP_HEADER_INCOMPLETE",
            f"{label} must contain exactly six AutoGrid header lines.",
        )

    fields_by_line: list[list[str]] = []
    for line_number, (line, expected_key) in enumerate(
        zip(header_lines, AUTOGRID_HEADER_KEYS, strict=True),
        start=1,
    ):
        fields = line.split()
        if not fields or fields[0] != expected_key:
            raise _error(
                "HYDRATED_MAP_HEADER_INVALID",
                f"{label} header line {line_number} must start with "
                f"{expected_key}.",
            )
        fields_by_line.append(fields)

    for line_number in range(3):
        if len(fields_by_line[line_number]) != 2:
            raise _error(
                "HYDRATED_MAP_HEADER_INVALID",
                f"{label} header line {line_number + 1} must contain one "
                "filename value.",
            )
    if len(fields_by_line[3]) != 2:
        raise _error(
            "HYDRATED_MAP_HEADER_INVALID",
            f"{label} SPACING header must contain one value.",
        )
    if len(fields_by_line[4]) != 4:
        raise _error(
            "HYDRATED_MAP_HEADER_INVALID",
            f"{label} NELEMENTS header must contain three values.",
        )
    if len(fields_by_line[5]) != 4:
        raise _error(
            "HYDRATED_MAP_HEADER_INVALID",
            f"{label} CENTER header must contain three values.",
        )

    spacing = _finite_float(fields_by_line[3][1], label=f"{label} SPACING")
    if spacing <= 0:
        raise _error(
            "HYDRATED_MAP_SPACING_INVALID",
            f"{label} SPACING must be positive.",
        )

    try:
        nelements = tuple(int(token) for token in fields_by_line[4][1:4])
    except ValueError as exc:
        raise _error(
            "HYDRATED_MAP_NELEMENTS_INVALID",
            f"{label} NELEMENTS values must be integers.",
        ) from exc
    if any(value <= 0 or value % 2 != 0 for value in nelements):
        raise _error(
            "HYDRATED_MAP_NELEMENTS_INVALID",
            f"{label} NELEMENTS values must be positive even integers.",
        )

    center = tuple(
        _finite_float(token, label=f"{label} CENTER")
        for token in fields_by_line[5][1:4]
    )
    geometry = AutoGridGeometry(
        spacing=spacing,
        nelements=nelements,  # type: ignore[arg-type]
        center=center,  # type: ignore[arg-type]
    )
    if geometry.point_count > MAX_MAP_POINTS:
        raise _error(
            "HYDRATED_MAP_POINT_LIMIT_EXCEEDED",
            f"{label} declares {geometry.point_count} points, exceeding the "
            f"limit of {MAX_MAP_POINTS}.",
        )
    references = tuple(fields[1] for fields in fields_by_line[:3])
    return references, geometry  # type: ignore[return-value]


def parse_autogrid_map(path: str | Path) -> AutoGridMap:
    """Strictly parse one AutoGrid ASCII map.

    The parser rejects symlinks, blank data lines, multiple values on a data
    line, non-ASCII text, non-finite values and any declared/actual point-count
    mismatch.
    """

    map_path = Path(path).expanduser()
    if map_path.is_symlink() or not map_path.is_file():
        raise _error(
            "HYDRATED_MAP_FILE_INVALID",
            f"AutoGrid map is not a regular file: {map_path}",
        )
    try:
        size_bytes = map_path.stat().st_size
    except OSError as exc:
        raise _error(
            "HYDRATED_MAP_FILE_INVALID",
            f"Unable to inspect AutoGrid map: {map_path}",
        ) from exc
    if size_bytes <= 0:
        raise _error(
            "HYDRATED_MAP_FILE_EMPTY",
            f"AutoGrid map is empty: {map_path}",
        )
    if size_bytes > MAX_MAP_FILE_BYTES:
        raise _error(
            "HYDRATED_MAP_FILE_TOO_LARGE",
            f"AutoGrid map exceeds {MAX_MAP_FILE_BYTES} bytes: {map_path}",
        )

    digest = hashlib.sha256()
    header_lines: list[str] = []
    values = array("d")
    minimum: float | None = None
    maximum: float | None = None
    geometry: AutoGridGeometry | None = None
    references: tuple[str, str, str] | None = None

    try:
        with map_path.open("rb") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                digest.update(raw_line)
                try:
                    decoded = raw_line.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise _error(
                        "HYDRATED_MAP_TEXT_INVALID",
                        f"{map_path.name} line {line_number} is not ASCII.",
                    ) from exc
                line = decoded.rstrip("\r\n")
                if line_number <= 6:
                    if not line.strip():
                        raise _error(
                            "HYDRATED_MAP_HEADER_INVALID",
                            f"{map_path.name} header line {line_number} is blank.",
                        )
                    header_lines.append(line)
                    if line_number == 6:
                        references, geometry = _parse_header(
                            header_lines,
                            label=map_path.name,
                        )
                    continue

                if not line.strip():
                    raise _error(
                        "HYDRATED_MAP_DATA_INVALID",
                        f"{map_path.name} line {line_number} is blank.",
                    )
                fields = line.split()
                if len(fields) != 1:
                    raise _error(
                        "HYDRATED_MAP_DATA_INVALID",
                        f"{map_path.name} line {line_number} must contain exactly "
                        "one grid value.",
                    )
                value = _finite_float(
                    fields[0],
                    label=f"{map_path.name} line {line_number}",
                )
                values.append(value)
                minimum = value if minimum is None else min(minimum, value)
                maximum = value if maximum is None else max(maximum, value)
                if geometry is not None and len(values) > geometry.point_count:
                    raise _error(
                        "HYDRATED_MAP_POINT_COUNT_MISMATCH",
                        f"{map_path.name} contains more than the declared "
                        f"{geometry.point_count} grid values.",
                    )
    except OSError as exc:
        raise _error(
            "HYDRATED_MAP_READ_FAILED",
            f"Unable to read AutoGrid map: {map_path}",
        ) from exc

    if geometry is None or references is None:
        raise _error(
            "HYDRATED_MAP_HEADER_INCOMPLETE",
            f"{map_path.name} does not contain a complete six-line header.",
        )
    if len(values) != geometry.point_count:
        raise _error(
            "HYDRATED_MAP_POINT_COUNT_MISMATCH",
            f"{map_path.name} declares {geometry.point_count} grid values but "
            f"contains {len(values)}.",
        )
    assert minimum is not None
    assert maximum is not None
    return AutoGridMap(
        path=map_path.resolve(),
        header_lines=tuple(header_lines),  # type: ignore[arg-type]
        header_references=references,
        geometry=geometry,
        values=values,
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
        minimum=minimum,
        maximum=maximum,
    )


def _float_equal(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=0.0,
        abs_tol=GEOMETRY_ABS_TOLERANCE,
    )


def _validate_compatible_sources(
    oa_map: AutoGridMap,
    hd_map: AutoGridMap,
) -> None:
    if oa_map.path == hd_map.path:
        raise _error(
            "HYDRATED_MAP_SOURCE_DUPLICATED",
            "OA and HD maps must be different files.",
        )
    if oa_map.header_references != hd_map.header_references:
        raise _error(
            "HYDRATED_MAP_PROVENANCE_MISMATCH",
            "OA and HD map header references do not match.",
        )
    if oa_map.geometry.nelements != hd_map.geometry.nelements:
        raise _error(
            "HYDRATED_MAP_GEOMETRY_MISMATCH",
            "OA and HD maps have different NELEMENTS.",
        )
    if not _float_equal(oa_map.geometry.spacing, hd_map.geometry.spacing):
        raise _error(
            "HYDRATED_MAP_GEOMETRY_MISMATCH",
            "OA and HD maps have different SPACING.",
        )
    if any(
        not _float_equal(left, right)
        for left, right in zip(
            oa_map.geometry.center,
            hd_map.geometry.center,
            strict=True,
        )
    ):
        raise _error(
            "HYDRATED_MAP_GEOMETRY_MISMATCH",
            "OA and HD maps have different CENTER coordinates.",
        )
    if len(oa_map.values) != len(hd_map.values):
        raise _error(
            "HYDRATED_MAP_POINT_COUNT_MISMATCH",
            "OA and HD maps contain different numbers of grid values.",
        )


def _best_water_values(
    oa_values: Iterable[float],
    hd_values: Iterable[float],
) -> tuple[list[str], dict[str, Any]]:
    serialized: list[str] = []
    selected_oa = 0
    selected_hd = 0
    entropy_points = 0
    minimum: float | None = None
    maximum: float | None = None

    for oa_value, hd_value in zip(oa_values, hd_values, strict=True):
        weighted_oa = oa_value * OA_WEIGHT
        weighted_hd = hd_value * HD_WEIGHT
        if weighted_oa > 0.0 or weighted_hd > 0.0:
            water_value = DISPLACEMENT_ENTROPY
            entropy_points += 1
        elif weighted_oa <= weighted_hd:
            water_value = weighted_oa * BEST_WEIGHT
            selected_oa += 1
        else:
            water_value = weighted_hd * BEST_WEIGHT
            selected_hd += 1

        # The official format publishes four decimal places.  Statistics and
        # hashes describe those published values rather than hidden precision.
        published = float(f"{water_value:.{OUTPUT_DECIMALS}f}")
        if published == 0.0:
            published = 0.0
        serialized.append(f"{published:.{OUTPUT_DECIMALS}f}")
        minimum = published if minimum is None else min(minimum, published)
        maximum = published if maximum is None else max(maximum, published)

    assert minimum is not None
    assert maximum is not None
    point_count = len(serialized)
    return serialized, {
        "point_count": point_count,
        "minimum": minimum,
        "maximum": maximum,
        "selected_oa_points": selected_oa,
        "selected_hd_points": selected_hd,
        "entropy_points": entropy_points,
        "selected_oa_percent": round(selected_oa * 100.0 / point_count, 6),
        "selected_hd_percent": round(selected_hd * 100.0 / point_count, 6),
        "entropy_percent": round(entropy_points * 100.0 / point_count, 6),
    }


def generate_best_water_map(
    oa_map_path: str | Path,
    hd_map_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Validate OA/HD maps and atomically publish a fixed-policy W map."""

    oa_map = parse_autogrid_map(oa_map_path)
    hd_map = parse_autogrid_map(hd_map_path)
    _validate_compatible_sources(oa_map, hd_map)

    destination = Path(output_path).expanduser()
    destination_resolved = destination.resolve(strict=False)
    if destination_resolved in {oa_map.path, hd_map.path}:
        raise _error(
            "HYDRATED_MAP_OUTPUT_COLLISION",
            "The W map output must not replace either source map.",
        )
    if destination.exists() and (
        destination.is_symlink() or not destination.is_file()
    ):
        raise _error(
            "HYDRATED_MAP_OUTPUT_INVALID",
            "The W map destination must be a regular file path.",
        )

    serialized_values, statistics = _best_water_values(
        oa_map.values,
        hd_map.values,
    )
    payload = (
        "\n".join((*oa_map.header_lines, *serialized_values))
        + "\n"
    )
    atomic_write_text(destination, payload, encoding="ascii")
    output_map = parse_autogrid_map(destination)

    return {
        "ok": True,
        "method": "hydrated_ad4_best_v1",
        "parameters": {
            "mode": "BEST",
            "weight": BEST_WEIGHT,
            "entropy": DISPLACEMENT_ENTROPY,
            "oa_weight": OA_WEIGHT,
            "hd_weight": HD_WEIGHT,
            "output_decimals": OUTPUT_DECIMALS,
            "positive_value_rule": "entropy_if_oa_gt_0_or_hd_gt_0",
        },
        "geometry": output_map.geometry.to_dict(),
        "sources": {
            "oa": oa_map.to_metadata(),
            "hd": hd_map.to_metadata(),
        },
        "output": output_map.to_metadata(),
        "statistics": statistics,
    }


__all__ = [
    "AUTOGRID_HEADER_KEYS",
    "BEST_WEIGHT",
    "DISPLACEMENT_ENTROPY",
    "HD_WEIGHT",
    "OA_WEIGHT",
    "AutoGridGeometry",
    "AutoGridMap",
    "HydratedMapError",
    "generate_best_water_map",
    "parse_autogrid_map",
]
