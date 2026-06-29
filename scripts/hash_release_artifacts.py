"""Calculate SHA256 checksums for release artifacts and print Markdown."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def calculate_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_size(size_bytes: int) -> str:
    return f"{size_bytes:,} bytes"


def build_markdown_table(paths: list[Path], include_profile_note: bool = False) -> str:
    lines = [
        "| File | Size | SHA256 |",
        "| --- | ---: | --- |",
    ]
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"未找到 release artifact：{resolved}")
        lines.append(
            f"| `{resolved.name}` | {format_size(resolved.stat().st_size)} | `{calculate_sha256(resolved)}` |"
        )
    if include_profile_note:
        lines.extend(
            [
                "",
                "Release capability profile: see `docs/release/release_artifact_profile.md`.",
            ]
        )
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate SHA256 checksums for DockStart release artifacts."
    )
    parser.add_argument(
        "artifacts",
        nargs="+",
        help="Release artifact files such as .msi or .exe installers.",
    )
    parser.add_argument(
        "--include-profile-note",
        action="store_true",
        help="Append a note pointing to docs/release/release_artifact_profile.md.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        print(build_markdown_table([Path(item) for item in args.artifacts], args.include_profile_note))
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
