"""End to end against the real uv resolver and PyPI. Run with `pytest -m network`."""

from __future__ import annotations

import shutil
import subprocess
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from conftest import snapshot, write
from lockfloor.cli import main

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH"),
]

ROOT = """\
[project]
name = "real-web"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    # small, pure-python packages keep the resolve fast
    "idna>=3.0",
    "six==1.16.0",  # pinned on purpose
    "real-core",
]

[dependency-groups]
test = ["pytest>=7.0"]

[tool.uv.sources]
real-core = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]
"""

CORE = """\
[project]
name = "real-core"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["iniconfig>=1.0,<3"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


def uv(*args, cwd):
    return subprocess.run(["uv", *args], cwd=cwd, capture_output=True, text=True, timeout=300)


def locked_versions(root, name):
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return sorted(Version(p["version"]) for p in lock["package"] if p["name"] == name)


def floor(root, relative, name):
    data = tomllib.loads((root / relative).read_text(encoding="utf-8"))
    project = data["project"]["dependencies"]
    groups = [r for g in data.get("dependency-groups", {}).values() for r in g if isinstance(r, str)]
    for raw in project + groups:
        requirement = Requirement(raw)
        if requirement.name == name:
            return next(Version(s.version) for s in requirement.specifier if s.operator in (">=", "=="))
    raise AssertionError(f"{name} not in {relative}")


@pytest.fixture
def workspace(project):
    write(project, "pyproject.toml", ROOT)
    write(project, "packages/core/pyproject.toml", CORE)
    assert uv("lock", cwd=project).returncode == 0
    return project


def test_real_dry_run_is_byte_identical(workspace, capsys):
    before = snapshot(workspace)
    assert main(["--project", str(workspace)]) == 0
    assert snapshot(workspace) == before
    assert "idna" in capsys.readouterr().out


def test_real_write_raises_floors_to_locked_versions_and_lock_stays_valid(workspace, capsys):
    assert main(["--project", str(workspace), "--check"]) == 1
    assert main(["--project", str(workspace), "--write"]) == 0

    assert floor(workspace, "pyproject.toml", "idna") == locked_versions(workspace, "idna")[0]
    assert floor(workspace, "packages/core/pyproject.toml", "iniconfig") == locked_versions(workspace, "iniconfig")[0]
    # pytest forks on requires-python >=3.9 (pytest 9 dropped 3.9); the floor is the 3.9 fork.
    assert floor(workspace, "pyproject.toml", "pytest") == locked_versions(workspace, "pytest")[0]
    assert len(locked_versions(workspace, "pytest")) >= 2
    text = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert '"six==1.16.0",  # pinned on purpose' in text
    assert "# small, pure-python packages keep the resolve fast" in text
    assert ",<3" in (workspace / "packages/core/pyproject.toml").read_text(encoding="utf-8")

    assert uv("lock", "--check", cwd=workspace).returncode == 0
    assert main(["--project", str(workspace), "--check"]) == 0


def test_real_path_source_outside_the_workspace_still_resolves(project, capsys):
    write(project.parent, "shared/vendored/pyproject.toml", VENDORED)
    write(project, "pyproject.toml", OUTSIDE_PATH)
    assert uv("lock", cwd=project).returncode == 0
    before = snapshot(project)

    assert main(["--project", str(project)]) == 0

    out = capsys.readouterr().out
    assert snapshot(project) == before
    assert "skipped: locked from a local directory" in out
    assert not list(project.parent.glob(".lockfloor-*")), "scratch copy left behind"


VENDORED = """[project]
name = "vendored"
version = "0.3.0"
requires-python = ">=3.9"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""

OUTSIDE_PATH = """[project]
name = "uses-shared"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["vendored>=0.1", "idna>=3.0"]

[tool.uv.sources]
vendored = { path = "../shared/vendored" }
"""
