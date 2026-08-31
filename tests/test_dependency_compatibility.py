import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def _project_requirement(name: str) -> Requirement:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    return next(
        requirement
        for dependency in dependencies
        if (requirement := Requirement(dependency)).name == name
    )


def test_joblib_excludes_release_incompatible_with_skrub() -> None:
    joblib = _project_requirement("joblib")

    assert Version("1.5.3") in joblib.specifier
    assert Version("1.6.0") not in joblib.specifier
