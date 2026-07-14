from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path


NAME = "xcgw-askdata"
VERSION = "0.1.2"
DIST_INFO = f"{NAME.replace('-', '_')}-{VERSION}.dist-info"


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory)


def _build(wheel_directory):
    root = Path(__file__).resolve().parent
    wheel_dir = Path(wheel_directory)
    wheel_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{NAME.replace('-', '_')}-{VERSION}-py3-none-any.whl"
    wheel_path = wheel_dir / filename
    records: list[tuple[str, bytes]] = []

    def add_text(archive: zipfile.ZipFile, path: str, text: str) -> None:
        data = text.encode("utf-8")
        archive.writestr(path, data)
        records.append((path, data))

    def add_file(archive: zipfile.ZipFile, source: Path, path: str) -> None:
        data = source.read_bytes()
        archive.writestr(path, data)
        records.append((path, data))

    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        package_root = root / "src" / "askdata"
        for source in sorted(package_root.rglob("*.py")):
            add_file(archive, source, str(Path("askdata") / source.relative_to(package_root)))

        add_text(
            archive,
            f"{DIST_INFO}/METADATA",
            "\n".join(
                [
                    "Metadata-Version: 2.1",
                    f"Name: {NAME}",
                    f"Version: {VERSION}",
                    "Summary: BIRD Mini-Dev preprocessing utilities.",
                    "Requires-Python: >=3.10",
                    "",
                ]
            ),
        )
        add_text(
            archive,
            f"{DIST_INFO}/WHEEL",
            "\n".join(
                [
                    "Wheel-Version: 1.0",
                    "Generator: custom-offline-build-backend",
                    "Root-Is-Purelib: true",
                    "Tag: py3-none-any",
                    "",
                ]
            ),
        )
        add_text(
            archive,
            f"{DIST_INFO}/entry_points.txt",
            "[console_scripts]\naskdata = askdata.cli:main\n",
        )

        record_lines = []
        for path, data in records:
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            record_lines.append(f"{path},sha256={digest},{len(data)}")
        record_lines.append(f"{DIST_INFO}/RECORD,,")
        archive.writestr(f"{DIST_INFO}/RECORD", "\n".join(record_lines) + "\n")

    return filename
