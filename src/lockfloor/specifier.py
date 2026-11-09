"""Rewrite one requirement string so its lower bound equals a resolved version.

The rewrite is textual: only the version digits of the lower-bound clause change,
so spacing, extras, markers and quoting inside the string stay as the author wrote them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

_NAME_AND_EXTRAS = re.compile(
    r"^\s*[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\s*(?:\[[^\]]*\])?"
)
_CLAUSE = re.compile(r"(~=|===|==|!=|<=|>=|<|>)(\s*)([^\s,;()]+)")


@dataclass(frozen=True)
class Verdict:
    """What lockfloor will do with one requirement.

    Exactly one of `new` (raise to this text) and `skip` (reason it is left alone) is
    set, or neither when the lower bound already equals the resolved version.
    """

    new: str | None = None
    skip: str | None = None


def split_name(raw: str) -> tuple[str, str]:
    """Split a requirement into (name with extras, everything after it)."""
    match = _NAME_AND_EXTRAS.match(raw)
    if match is None:
        return raw, ""
    return raw[: match.end()], raw[match.end() :]


def static_skip(raw: str, *, pins: bool) -> str | None:
    """Reason to leave a requirement alone that does not depend on the lock, or None."""
    try:
        req = Requirement(raw)
    except InvalidRequirement:
        return "not a valid PEP 508 requirement"
    if req.url:
        return "direct reference (url, git or path); not a registry version"
    clauses = list(req.specifier)
    if not clauses:
        return "no version specifier; lockfloor does not invent one"
    if any(c.operator == "===" for c in clauses):
        return "=== arbitrary-equality pin"
    pinned = [c for c in clauses if c.operator == "=="]
    if pinned:
        if not pins:
            return "pinned with ==; pass --pins to move pins"
        if len(clauses) != 1 or "*" in pinned[0].version:
            return "== wildcard or == combined with other clauses"
        return None
    lower = [c for c in clauses if c.operator in (">=", "~=", ">")]
    if not lower:
        return "no lower bound to raise (only upper bounds or exclusions)"
    if len(lower) > 1:
        return "more than one lower-bound clause"
    if lower[0].operator == ">":
        return "strict > bound; write it as >= to let lockfloor manage it"
    return None


def is_pin(raw: str) -> bool:
    """True for a single exact `==` clause that --pins is allowed to move."""
    try:
        clauses = list(Requirement(raw).specifier)
    except InvalidRequirement:
        return False
    return len(clauses) == 1 and clauses[0].operator == "==" and "*" not in clauses[0].version


def loosen_pin(raw: str) -> str:
    """Turn `pkg==1.2` into `pkg>=1.2` so the resolver may move it (temp copy only)."""
    head, tail = split_name(raw)
    return head + tail.replace("==", ">=", 1)


def raise_floor(raw: str, resolved: Version, *, pins: bool) -> Verdict:
    """Decide how `raw` changes when uv resolved its package to `resolved`."""
    reason = static_skip(raw, pins=pins)
    if reason:
        return Verdict(skip=reason)
    spec = Requirement(raw).specifier
    floor = Version(resolved.public)
    clauses = list(spec)
    target = next(c for c in clauses if c.operator in ("==", ">=", "~="))
    # A pin is checked against the loosened (>=) form the resolver actually saw.
    declared = SpecifierSet(f">={target.version}") if target.operator == "==" else spec
    if not declared.contains(floor, prereleases=True):
        return Verdict(skip=f"locked {floor} is outside the declared range (stale lock?)")

    old = Version(target.version)
    if target.operator == "~=":
        width = len(old.release)
        new_version = ".".join(str(part) for part in floor.release[:width])
    else:
        new_version = str(floor)
    if Version(new_version) <= old:
        return Verdict()

    new_raw = _replace_clause(raw, target, new_version)
    new_spec = Requirement(new_raw).specifier
    if not new_spec.contains(floor, prereleases=True):
        return Verdict(skip=f"raising would exclude the locked {floor}")
    return Verdict(new=new_raw)


def _replace_clause(raw: str, target: Specifier, new_version: str) -> str:
    head, tail = split_name(raw)
    marker_at = tail.find(";")
    region, marker = (tail, "") if marker_at < 0 else (tail[:marker_at], tail[marker_at:])
    for match in _CLAUSE.finditer(region):
        op, gap, version = match.groups()
        if op == target.operator and version == target.version:
            region = region[: match.start(3)] + new_version + region[match.end(3) :]
            return head + region + marker
    # packaging normalises nothing in .version, so the clause text is always found.
    raise AssertionError(f"clause {target} not found in {raw!r}")
