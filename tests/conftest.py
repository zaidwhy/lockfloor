"""Fixture projects on disk plus a resolver stand-in that hands back a canned uv.lock.

The offline suite fakes only the resolver call; manifest parsing, lock reading,
rewriting and file handling all run for real.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REGISTRY = '{ registry = "https://pypi.org/simple" }'


def lock_text(*packages: str, requires_python: str = ">=3.9") -> str:
    head = f'version = 1\nrevision = 3\nrequires-python = "{requires_python}"\n'
    return head + "".join(f"\n{p}" for p in packages)


def package(name: str, version: str | None, source: str = REGISTRY, body: str = "") -> str:
    version_line = f'version = "{version}"\n' if version else ""
    return f'[[package]]\nname = "{name}"\n{version_line}source = {source}\n{body}'


def edges(*names: str) -> str:
    return "dependencies = [\n" + "".join(f"    {n},\n" for n in names) + "]\n"


class FakeResolver:
    """Returns a fixed lock; records what lockfloor asked uv to do."""

    def __init__(self, lock: str, tmp_path: Path, relock_error: Exception | None = None) -> None:
        self.lock = lock
        self.scratch = tmp_path / "scratch"
        self.relock_error = relock_error
        self.upgrade_calls: list[list[str] | None] = []
        self.relocked: list[Path] = []
        self.scratch_copy: Path | None = None
        self.escapes_root: bool | None = None

    def upgraded_lock(self, root, upgrade, prepare, escapes_root):
        self.upgrade_calls.append(upgrade)
        self.escapes_root = escapes_root
        shutil.rmtree(self.scratch, ignore_errors=True)
        shutil.copytree(root, self.scratch)
        prepare(self.scratch)
        self.scratch_copy = self.scratch
        return self.lock

    def relock(self, root):
        if self.relock_error:
            raise self.relock_error
        self.relocked.append(root)


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    return root
