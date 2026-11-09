"""Run uv's resolver without touching the user's files.

The upgrade lock is produced in a scratch copy of the workspace, so a dry run is
byte-identical even if the process is killed half way. The copy is a sibling of the
workspace when any path source climbs out of it (`../shared`), so those relative
paths still resolve from the copy.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_COPY_BYTES = 512 << 20
_NOT_COPIED = shutil.ignore_patterns(
    ".git", ".hg", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox",
)


class ResolveError(Exception):
    """uv could not produce a lock; the message carries uv's own explanation."""


class UvResolver:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.exe = os.environ.get("UV") or shutil.which("uv")
        if not self.exe:
            raise ResolveError("uv was not found on PATH; install it from https://docs.astral.sh/uv/")

    def upgraded_lock(
        self,
        root: Path,
        upgrade: list[str] | None,
        prepare: Callable[[Path], None],
        escapes_root: bool,
    ) -> str:
        """Lock a scratch copy of `root` with upgrades allowed and return the new uv.lock text.

        `upgrade` None means every package; otherwise only those names move.
        `prepare` edits the copy before locking (loosening pins for --pins).
        """
        parent = root.parent if escapes_root else None
        # The scratch directory is the copy itself, so it sits at the workspace's depth
        # and `../shared` from the copy names the same directory as from the original.
        scratch = Path(tempfile.mkdtemp(prefix=".lockfloor-", dir=parent))
        try:
            _copy_capped(root, scratch)
            prepare(scratch)
            flags = ["--upgrade"] if upgrade is None else [f"--upgrade-package={name}" for name in upgrade]
            self._run(["lock", *flags], scratch)
            return (scratch / "uv.lock").read_bytes().decode("utf-8")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def relock(self, root: Path) -> None:
        """Bring uv.lock's recorded requirements in line with the rewritten manifests."""
        self._run(["lock"], root)

    def _run(self, args: list[str], cwd: Path) -> None:
        command = [self.exe, *args, "--no-progress", "--color", "never"]
        logger.debug("running %s in %s", command, cwd)
        try:
            completed = subprocess.run(
                command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise ResolveError(f"`uv {' '.join(args)}` exceeded the {self.timeout:g}s timeout") from exc
        if completed.returncode != 0:
            tail = "\n".join(completed.stderr.strip().splitlines()[-25:])
            raise ResolveError(f"`uv {' '.join(args)}` failed:\n{tail}")


def _copy_capped(source: Path, destination: Path) -> None:
    copied = 0

    def copy_file(src: str, dst: str) -> str:
        nonlocal copied
        copied += os.path.getsize(src)
        if copied > MAX_COPY_BYTES:
            raise ResolveError(
                f"workspace copy passed {MAX_COPY_BYTES >> 20} MiB; move large data out of {source} "
                "or into an ignored directory (.venv, node_modules)"
            )
        return shutil.copy2(src, dst)

    shutil.copytree(source, destination, ignore=_NOT_COPIED, copy_function=copy_file, symlinks=True, dirs_exist_ok=True)
