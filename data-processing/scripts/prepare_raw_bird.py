#!/usr/bin/env python3
"""Normalize the downloaded BIRD Mini-Dev archive into the raw input layout."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


ARTIFACT_DIR = "__MACOSX"
ARTIFACT_FILE_PREFIX = "._"
ARTIFACT_FILES = {".DS_Store"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract BIRD Mini-Dev and copy required raw files into data/bird/raw/minidev/MINIDEV.",
    )
    parser.add_argument(
        "--zip-path",
        type=Path,
        default=Path("data/downloads/bird_minidev.zip"),
        help="Downloaded BIRD Mini-Dev zip path.",
    )
    parser.add_argument(
        "--extract-dir",
        type=Path,
        default=Path("data/downloads/bird_minidev_extract"),
        help="Temporary extraction directory. Existing contents are replaced.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path("data/bird/raw/minidev/MINIDEV"),
        help="Normalized raw BIRD Mini-Dev target directory.",
    )
    args = parser.parse_args()

    prepare_raw_bird(
        zip_path=args.zip_path,
        extract_dir=args.extract_dir,
        target_dir=args.target_dir,
    )
    return 0


def prepare_raw_bird(zip_path: Path, extract_dir: Path, target_dir: Path) -> None:
    zip_path = zip_path.resolve()
    extract_dir = extract_dir.resolve()
    target_dir = target_dir.resolve()

    if not zip_path.exists():
        raise SystemExit(f"Missing download: {zip_path}")

    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)

    dev_tables = find_one(extract_dir, "dev_tables.json")
    mini_dev_sqlite = find_one(extract_dir, "mini_dev_sqlite.json")
    dev_databases = find_dev_databases(extract_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    copy_raw_file(dev_tables, target_dir / "dev_tables.json")
    copy_raw_file(mini_dev_sqlite, target_dir / "mini_dev_sqlite.json")

    target_databases = target_dir / "dev_databases"
    if target_databases.exists():
        shutil.rmtree(target_databases)
    copy_raw_tree(dev_databases, target_databases)

    print(f"Prepared raw files in: {target_dir}")
    print(f"SQLite databases copied from: {dev_databases}")


def find_one(root: Path, name: str) -> Path:
    matches = sorted(path for path in root.rglob(name) if not is_artifact_path(path))
    if not matches:
        raise SystemExit(f"Could not find {name} under {root}")
    return matches[0]


def copy_raw_file(source: Path | str, target: Path | str) -> str:
    """Copy file contents without preserving metadata.

    Some mounted filesystems reject timestamp/mode updates from shutil.copy2.
    The preprocessing contract only needs file contents, so avoid copystat.
    """
    shutil.copyfile(source, target)
    return str(target)


def copy_raw_tree(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        if is_artifact_path(path):
            continue
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_raw_file(path, destination)


def find_dev_databases(root: Path) -> Path:
    candidates = [
        path
        for path in root.rglob("dev_databases")
        if path.is_dir() and not is_artifact_path(path)
    ]
    if not candidates:
        raise SystemExit(f"Could not find dev_databases under {root}")
    candidates_with_counts = [
        (path, count_real_sqlite_files(path))
        for path in candidates
    ]
    candidates_with_counts = [(path, count) for path, count in candidates_with_counts if count > 0]
    if not candidates_with_counts:
        raise SystemExit(f"Could not find real SQLite files under any dev_databases directory in {root}")
    return max(candidates_with_counts, key=lambda item: item[1])[0]


def count_real_sqlite_files(path: Path) -> int:
    return sum(
        1
        for sqlite_file in path.rglob("*.sqlite")
        if not is_artifact_path(sqlite_file)
    )


def is_artifact_path(path: Path) -> bool:
    return any(
        part == ARTIFACT_DIR
        or part in ARTIFACT_FILES
        or part.startswith(ARTIFACT_FILE_PREFIX)
        for part in path.parts
    )


def ignore_artifacts(directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == ARTIFACT_DIR
        or name in ARTIFACT_FILES
        or name.startswith(ARTIFACT_FILE_PREFIX)
    }


if __name__ == "__main__":
    raise SystemExit(main())
