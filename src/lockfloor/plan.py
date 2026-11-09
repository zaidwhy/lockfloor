"""Pair every selected requirement with the version uv locked for it and decide its fate."""

from __future__ import annotations

from dataclasses import dataclass

from packaging.requirements import Requirement

from lockfloor.lockfile import LockIndex
from lockfloor.manifest import Slot
from lockfloor.specifier import Verdict, raise_floor, static_skip

_SOURCE_REASONS = {
    "editable": "workspace member or editable path; not a registry version",
    "virtual": "workspace member; not a registry version",
    "git": "locked from a git source",
    "path": "locked from a local path",
    "directory": "locked from a local directory",
    "url": "locked from a direct url",
}


@dataclass
class Row:
    slot: Slot
    package: str
    verdict: Verdict
    note: str = ""

    @property
    def raises(self) -> bool:
        return self.verdict.new is not None


def plan_rows(slots: list[Slot], index: LockIndex, *, pins: bool) -> list[Row]:
    return [_plan_one(slot, index, pins=pins) for slot in slots]


def _plan_one(slot: Slot, index: LockIndex, *, pins: bool) -> Row:
    reason = static_skip(slot.raw, pins=pins)
    if reason:
        return Row(slot, _display_name(slot.raw), Verdict(skip=reason))
    requirement = Requirement(slot.raw)
    locked = index.resolved(slot.manifest.project_name, slot.section, slot.group, requirement.name)
    locked = index.applicable(locked, requirement.marker)
    if not locked:
        return Row(slot, requirement.name, Verdict(skip="not in uv.lock (marker matches no environment?)"))
    foreign = next((entry for entry in locked if not entry.from_registry), None)
    if foreign is not None:
        reason = _SOURCE_REASONS.get(foreign.source_kind, f"locked from a {foreign.source_kind} source")
        return Row(slot, requirement.name, Verdict(skip=reason))

    versions = sorted({entry.version for entry in locked if entry.version is not None})
    if not versions:
        return Row(slot, requirement.name, Verdict(skip="uv.lock records no version for it"))
    # Every fork must still satisfy the new floor, so the floor is the lowest fork.
    floor = versions[0]
    note = ""
    if len(versions) > 1:
        note = f"resolver forked ({', '.join(map(str, versions))}); floor set to the lowest"
    return Row(slot, requirement.name, raise_floor(slot.raw, floor, pins=pins), note)


def _display_name(raw: str) -> str:
    try:
        return Requirement(raw).name
    except ValueError:
        return raw.strip()
