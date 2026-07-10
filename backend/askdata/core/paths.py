"""Project path utilities — resolves relative paths from the project root for consistent file access regardless of CWD."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def project_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate
