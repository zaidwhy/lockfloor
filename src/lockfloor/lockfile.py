"""Read the versions uv's resolver chose from uv.lock, per project and dependency group.

A package can appear in uv.lock at several versions when the resolver forks on
markers (numpy 2.0.2 for Python 3.9, numpy 2.3.x for 3.11+). Collapsing the lock
into one version per name, as the copy-paste scripts do, raises the floor to a
version some supported environment cannot install.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from itertools import product

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

_PYTHON_MINORS = range(8, 16)
_PLATFORMS = (
    {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"},
    {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "aarch64"},
    {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix", "platform_machine": "arm64"},
    {"sys_platform": "darwin", "platform_system": "Darwin", "os_name": "posix", "platform_machine": "x86_64"},
    {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"},
)
_PROJECT_SOURCES = ("editable", "virtual")


@dataclass(frozen=True)
class Locked:
    """One version the resolver picked for a dependency edge."""

    version: Version | None
    marker: str | None
    source_kind: str

    @property
    def from_registry(self) -> bool:
        return self.source_kind == "registry"


class LockIndex:
    def __init__(self, text: str) -> None:
        data = tomllib.loads(text)
        self._by_name: dict[str, list[dict]] = {}
        for package in data.get("package", []):
            self._by_name.setdefault(canonicalize_name(package["name"]), []).append(package)
        requires_python = SpecifierSet(data.get("requires-python", ""))
        self._environments = [
            env for env in _sample_environments() if requires_python.contains(env["python_full_version"])
        ]

    def resolved(self, project: str | None, section: str, group: str | None, dependency: str) -> list[Locked]:
        """Versions locked for `dependency` along the edges of one project's group.

        `section` is "main", "extra" or "group". Falls back to every locked version of
        the name when the project or edge is absent (a virtual workspace root keeps its
        groups in [manifest], which carries specifiers, not resolved edges).
        """
        name = canonicalize_name(dependency)
        edges = self._edges(project, section, group, name)
        if not edges:
            return [_locked(package, None) for package in self._by_name.get(name, [])]
        picked: list[Locked] = []
        for edge in edges:
            candidates = self._by_name.get(name, [])
            if "version" in edge:
                candidates = [p for p in candidates if p.get("version") == edge["version"]]
            picked.extend(_locked(package, edge.get("marker")) for package in candidates)
        return picked

    def applicable(self, locked: list[Locked], requirement_marker: Marker | None) -> list[Locked]:
        """Keep the forks whose marker can hold together with the requirement's marker.

        Overlap is tested on a grid of real environments (CPython 3.8 to 3.15 on five
        platforms) inside the lock's requires-python. When the grid proves nothing,
        every fork is kept: the caller takes the minimum, so keeping more only lowers
        the floor, never raises it past what an environment can install.
        """
        if requirement_marker is None or len(locked) < 2:
            return locked
        kept = [
            entry
            for entry in locked
            if any(
                requirement_marker.evaluate(env) and (entry.marker is None or Marker(entry.marker).evaluate(env))
                for env in self._environments
            )
        ]
        return kept or locked

    def _edges(self, project: str | None, section: str, group: str | None, name: str) -> list[dict]:
        if project is None:
            return []
        owners = [
            p
            for p in self._by_name.get(canonicalize_name(project), [])
            if any(kind in p.get("source", {}) for kind in _PROJECT_SOURCES)
        ]
        if not owners:
            return []
        owner = owners[0]
        if section == "main":
            edges = owner.get("dependencies", [])
        elif section == "extra":
            edges = _group_table(owner.get("optional-dependencies", {}), group)
        else:
            edges = _group_table(owner.get("dev-dependencies", {}), group)
        return [edge for edge in edges if canonicalize_name(edge["name"]) == name]


def _group_table(table: dict, group: str | None) -> list[dict]:
    wanted = canonicalize_name(group or "")
    for key, edges in table.items():
        if canonicalize_name(key) == wanted:
            return edges
    return []


def _locked(package: dict, marker: str | None) -> Locked:
    source = package.get("source", {})
    kind = next(iter(source), "unknown")
    version = package.get("version")
    return Locked(Version(version) if version else None, marker, kind)


def _sample_environments() -> list[dict[str, str]]:
    environments = []
    for minor, platform in product(_PYTHON_MINORS, _PLATFORMS):
        full = f"3.{minor}.0"
        environments.append(
            {
                **platform,
                "python_version": f"3.{minor}",
                "python_full_version": full,
                "implementation_name": "cpython",
                "implementation_version": full,
                "platform_python_implementation": "CPython",
                "platform_release": "",
                "platform_version": "",
                "extra": "",
            }
        )
    return environments
