from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from askdata.core.paths import project_path


def test_project_path_resolves_relative_paths_from_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    resolved = project_path("data/bird")

    assert resolved == Path(__file__).resolve().parents[1] / "data" / "bird"


def test_project_path_keeps_absolute_paths(tmp_path):
    assert project_path(tmp_path) == tmp_path
