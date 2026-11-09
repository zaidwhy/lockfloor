"""Find the workspace's pyproject.toml files and every requirement array in them.

Files are parsed with tomlkit and kept as documents, so a write changes only the
strings that were raised; comments, ordering and quote style survive.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from tomlkit.items import Array, String, StringType
from tomlkit.toml_document import TOMLDocument

MAX_MANIFEST_BYTES = 1 << 20


class ProjectError(Exception):
    """The directory is not a project lockfloor can work on."""


@dataclass
class Manifest:
    path: Path
    original: bytes
    document: TOMLDocument
    project_name: str | None
    label: str

    def dump(self) -> bytes:
        return tomlkit.dumps(self.document).encode("utf-8")


@dataclass
class Slot:
    """One requirement string at a fixed position inside a manifest array."""

    manifest: Manifest
    section: str  # "main", "extra" or "group"
    group: str | None
    array: Array
    index: int
    raw: str = field(init=False)

    def __post_init__(self) -> None:
        self.raw = str(self.array[self.index])

    @property
    def group_label(self) -> str:
        if self.section == "main":
            return "main"
        if self.section == "extra":
            return f"extra:{self.group}"
        return self.group or ""

    def replace(self, new_raw: str) -> None:
        old = self.array[self.index]
        literal = isinstance(old, String) and old.type in (StringType.SLL, StringType.MLL)
        self.array[self.index] = tomlkit.string(new_raw, literal=literal)
        self.raw = new_raw


def load_manifest(path: Path, root: Path) -> Manifest:
    size = path.stat().st_size
    if size > MAX_MANIFEST_BYTES:
        raise ProjectError(f"{path}: {size} bytes exceeds the {MAX_MANIFEST_BYTES}-byte limit for pyproject.toml")
    original = path.read_bytes()
    try:
        document = tomlkit.parse(original.decode("utf-8"))
    except ValueError as exc:  # tomlkit's ParseError and UnicodeDecodeError both derive from it
        raise ProjectError(f"{path}: not valid TOML ({exc})") from exc
    project = document.get("project") or {}
    name = project.get("name")
    relative = path.parent.relative_to(root).as_posix()
    label = str(name) if name else ("(root)" if relative == "." else relative)
    return Manifest(path, original, document, str(name) if name else None, label)


def find_workspace_root(start: Path) -> Path:
    """The directory holding uv.lock: the nearest workspace that lists `start`, or `start`."""
    start = start.resolve()
    for directory in (start, *start.parents):
        workspace = _workspace_table(directory / "pyproject.toml")
        if workspace is not None and (directory == start or start in _member_dirs(directory, workspace)):
            return directory
    if not (start / "pyproject.toml").is_file():
        raise ProjectError(f"no pyproject.toml in {start}")
    return start


def workspace_manifests(root: Path) -> list[Manifest]:
    paths = [root / "pyproject.toml"]
    workspace = _workspace_table(root / "pyproject.toml")
    if workspace is not None:
        paths += [d / "pyproject.toml" for d in _member_dirs(root, workspace) if d != root]
    return [load_manifest(p, root) for p in paths]


def requirement_slots(manifest: Manifest) -> list[Slot]:
    document = manifest.document
    project = document.get("project") or {}
    tool_uv = (document.get("tool") or {}).get("uv") or {}
    arrays: list[tuple[str, str | None, object]] = [("main", None, project.get("dependencies"))]
    arrays += [("extra", k, v) for k, v in (project.get("optional-dependencies") or {}).items()]
    arrays += [("group", k, v) for k, v in (document.get("dependency-groups") or {}).items()]
    # uv's pre-PEP 735 spelling; the lock files it under the same "dev" group.
    arrays.append(("group", "dev", tool_uv.get("dev-dependencies")))

    slots = []
    for section, group, array in arrays:
        if not isinstance(array, Array):
            continue
        for index, item in enumerate(array):
            # dependency-groups may hold {include-group = "..."} tables; those are not requirements.
            if isinstance(item, str):
                slots.append(Slot(manifest, section, group, array, index))
    return slots


def _workspace_table(pyproject: Path) -> dict | None:
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ProjectError(f"{pyproject}: not valid TOML ({exc})") from exc
    return data.get("tool", {}).get("uv", {}).get("workspace")


def _member_dirs(root: Path, workspace: dict) -> list[Path]:
    excludes = workspace.get("exclude", [])
    members: list[Path] = []
    if (root / "pyproject.toml").is_file():
        members.append(root)
    for pattern in workspace.get("members", []):
        for candidate in sorted(root.glob(pattern)):
            relative = candidate.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(relative, ex) for ex in excludes):
                continue
            if (candidate / "pyproject.toml").is_file() and candidate.resolve() not in members:
                members.append(candidate.resolve())
    return members
