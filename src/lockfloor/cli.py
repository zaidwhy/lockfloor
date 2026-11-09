"""lockfloor: raise pyproject.toml lower bounds to the versions uv's resolver locks together."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from lockfloor import __version__
from lockfloor.lockfile import LockIndex
from lockfloor.manifest import (
    Manifest,
    ProjectError,
    Slot,
    find_workspace_root,
    load_manifest,
    requirement_slots,
    workspace_manifests,
)
from lockfloor.plan import Row, plan_rows
from lockfloor.resolver import ResolveError, UvResolver
from lockfloor.specifier import is_pin, loosen_pin, split_name

logger = logging.getLogger("lockfloor")

EXIT_OK = 0
EXIT_UPGRADES_PENDING = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lockfloor",
        description=(
            "Raise pyproject.toml lower bounds to the versions uv's resolver locks together. "
            "Dry run by default."
        ),
    )
    parser.add_argument("names", nargs="*", metavar="DEPENDENCY", help="only these dependencies (default: all)")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="project or workspace directory")
    parser.add_argument(
        "--package", action="append", default=[], metavar="MEMBER", help="only this workspace member (repeatable)"
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="GROUP",
        help="only this group: main, a dependency-group name, or extra:NAME (repeatable)",
    )
    parser.add_argument("--pins", action="store_true", help="also move single == pins to the resolved version")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite pyproject.toml files and re-lock")
    mode.add_argument("--check", action="store_true", help="exit 1 when any floor can be raised (for CI)")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds allowed per uv call (default 300)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log the uv commands")
    parser.add_argument("--version", action="version", version=f"lockfloor {__version__}")
    return parser


def main(argv: list[str] | None = None, resolver: object | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(name)s: %(message)s")
    if args.timeout <= 0:
        print("lockfloor: --timeout must be positive", file=sys.stderr)
        return EXIT_ERROR
    try:
        return _run(args, resolver or UvResolver(args.timeout))
    except (ProjectError, ResolveError) as exc:
        print(f"lockfloor: {exc}", file=sys.stderr)
        return EXIT_ERROR


def _run(args: argparse.Namespace, resolver) -> int:
    root = find_workspace_root(args.project)
    manifests = workspace_manifests(root)
    slots = _select(manifests, args)
    if not slots:
        print("lockfloor: no requirements to look at")
        return EXIT_OK

    filtered = bool(args.names or args.group or args.package)
    upgrade = sorted({_name(s.raw) for s in slots if _name(s.raw)}) if filtered else None
    pins = [s for s in slots if args.pins and is_pin(s.raw)]
    lock_text = resolver.upgraded_lock(root, upgrade, _pin_loosener(root, pins), _paths_escape(root, manifests))

    rows = plan_rows(slots, LockIndex(lock_text), pins=args.pins)
    print(render_table(rows, show_member=len(manifests) > 1))
    raised = [r for r in rows if r.raises]
    skipped = [r for r in rows if r.verdict.skip]
    current = len(rows) - len(raised) - len(skipped)
    print(f"\n{len(raised)} to raise, {len(skipped)} left alone, {current} already at the resolved version.")

    if args.check:
        return EXIT_UPGRADES_PENDING if raised else EXIT_OK
    if not args.write:
        print("Dry run: no file was written. Pass --write to apply and re-lock.")
        return EXIT_OK
    if not raised:
        return EXIT_OK
    _apply(root, raised, lock_text, resolver)
    files = sorted({r.slot.manifest.path.relative_to(root).as_posix() for r in raised})
    print(f"Wrote {', '.join(files)} and re-locked uv.lock.")
    return EXIT_OK


def _select(manifests: list[Manifest], args: argparse.Namespace) -> list[Slot]:
    slots = [slot for manifest in manifests for slot in requirement_slots(manifest)]
    if args.package:
        known = {canonicalize_name(m.label): m for m in manifests}
        wanted = {canonicalize_name(p) for p in args.package}
        unknown = wanted - known.keys()
        if unknown:
            raise ProjectError(f"--package {', '.join(sorted(unknown))} is not a workspace member; members: {', '.join(m.label for m in manifests)}")
        slots = [s for s in slots if canonicalize_name(s.manifest.label) in wanted]
    if args.group:
        labels = {s.group_label for s in slots}
        unknown = set(args.group) - labels
        if unknown:
            raise ProjectError(f"--group {', '.join(sorted(unknown))} not found; groups here: {', '.join(sorted(labels))}")
        slots = [s for s in slots if s.group_label in args.group]
    if args.names:
        wanted_names = {canonicalize_name(n) for n in args.names}
        slots = [s for s in slots if _name(s.raw) in wanted_names]
        if not slots:
            raise ProjectError(f"no requirement named {', '.join(args.names)} in the selected groups")
    return slots


def _name(raw: str) -> str | None:
    try:
        return canonicalize_name(Requirement(raw).name)
    except InvalidRequirement:
        return None


def _pin_loosener(root: Path, pins: list[Slot]):
    """Return a callback that rewrites `==X` to `>=X` in the scratch copy, so uv may move the pin."""

    def loosen(copy: Path) -> None:
        by_file: dict[Path, list[Slot]] = {}
        for slot in pins:
            by_file.setdefault(slot.manifest.path, []).append(slot)
        for path, file_pins in by_file.items():
            scratch_manifest = load_manifest(copy / path.relative_to(root), copy)
            scratch_slots = {(s.section, s.group, s.index): s for s in requirement_slots(scratch_manifest)}
            for pin in file_pins:
                scratch_slots[(pin.section, pin.group, pin.index)].replace(loosen_pin(pin.raw))
            scratch_manifest.path.write_bytes(scratch_manifest.dump())

    return loosen


def _paths_escape(root: Path, manifests: list[Manifest]) -> bool:
    """True when a [tool.uv.sources] path points outside the workspace root."""
    for manifest in manifests:
        sources = ((manifest.document.get("tool") or {}).get("uv") or {}).get("sources") or {}
        for source in sources.values():
            for entry in source if isinstance(source, list) else [source]:
                path = entry.get("path") if hasattr(entry, "get") else None
                if path and not (manifest.path.parent / str(path)).resolve().is_relative_to(root):
                    return True
    return False


def _apply(root: Path, raised: list[Row], lock_text: str, resolver) -> None:
    """Rewrite manifests, adopt the resolved lock, re-lock; restore every byte if any step fails."""
    lock_path = root / "uv.lock"
    lock_before = lock_path.read_bytes() if lock_path.exists() else None
    touched = {id(r.slot.manifest): r.slot.manifest for r in raised}.values()
    done = False
    try:
        for row in raised:
            row.slot.replace(row.verdict.new)
        for manifest in touched:
            manifest.path.write_bytes(manifest.dump())
        lock_path.write_bytes(lock_text.encode("utf-8"))
        resolver.relock(root)
        done = True
    finally:
        if not done:
            for manifest in touched:
                manifest.path.write_bytes(manifest.original)
            if lock_before is None:
                lock_path.unlink(missing_ok=True)
            else:
                lock_path.write_bytes(lock_before)
            print("lockfloor: write failed; pyproject.toml files and uv.lock restored", file=sys.stderr)


def render_table(rows: list[Row], *, show_member: bool) -> str:
    header = (["member"] if show_member else []) + ["group", "package", "old", "new", "note"]
    lines = [header]
    for row in rows:
        if not row.raises and not row.verdict.skip:
            continue
        old = split_name(row.slot.raw)[1].strip()
        new = split_name(row.verdict.new)[1].strip() if row.verdict.new else "-"
        note = row.verdict.skip or row.note
        if row.verdict.skip:
            note = f"skipped: {note}"
        cells = ([row.slot.manifest.label] if show_member else []) + [row.slot.group_label, row.package, old, new, note]
        lines.append(cells)
    if len(lines) == 1:
        return "Every floor already matches the resolved lock."
    widths = [max(len(line[i]) for line in lines) for i in range(len(header))]
    rendered = ["  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip() for line in lines]
    rendered.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(rendered)
