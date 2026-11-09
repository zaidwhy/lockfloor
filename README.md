# lockfloor

Raise the lower bounds in `pyproject.toml` to the versions uv's resolver actually locks together.

**The new floor comes from `uv lock --upgrade`, never from "latest on PyPI".** The latest release of each package, picked one by one, is a set nobody has resolved; it can be uninstallable as a whole, or uninstallable on a Python version the project still supports. lockfloor upgrades the lock in a scratch copy of the workspace, reads what uv chose from that `uv.lock`, and moves each `>=` (or `~=`) up to it. If uv cannot resolve it, lockfloor does not write it.

This answers [astral-sh/uv#6794](https://github.com/astral-sh/uv/issues/6794) ("Upgrade dependencies in pyproject.toml") until uv ships its own command.

```
uvx lockfloor            # dry run: print the plan, write nothing
uvx lockfloor --write    # rewrite pyproject.toml files, then re-lock
uvx lockfloor --check    # exit 1 if any floor lags the lock (CI)
```

## What a run looks like

The fixture in [`examples/forked-workspace`](examples/forked-workspace) is a two-member workspace with `requires-python = ">=3.9"`, an extra, a dependency group, a pin, a marker and a workspace dependency. After `uv lock`, `lockfloor` printed:

```
member     group       package    old                               new                                 note
---------  ----------  ---------  --------------------------------  ----------------------------------  ---------------------------------------------------------------------
demo-app   main        httpx      >=0.25,<1                         >=0.28.1,<1
demo-app   main        numpy      >=1.24                            >=2.0.2                             resolver forked (2.0.2, 2.2.6, 2.4.6, 2.5.4); floor set to the lowest
demo-app   main        rich       == 13.7.0                         -                                   skipped: pinned with ==; pass --pins to move pins
demo-app   main        click      >=8.0 ; python_version >= '3.10'  >=8.5.1 ; python_version >= '3.10'
demo-app   main        demo-core                                    -                                   skipped: no version specifier; lockfloor does not invent one
demo-app   extra:yaml  pyyaml     >=6.0                             >=6.0.3
demo-app   dev         pytest     >=7.0                             >=8.4.2                             resolver forked (8.4.2, 9.2.0); floor set to the lowest
demo-app   dev         packaging                                    -                                   skipped: no version specifier; lockfloor does not invent one
demo-core  main        attrs      >=22.1                            >=26.1.0
demo-core  main        idna       ~=3.4                             ~=3.20

7 to raise, 3 left alone, 0 already at the resolved version.
Dry run: no file was written. Pass --write to apply and re-lock.
```

The three files hashed the same before and after. `--write` then changed only the version digits on seven lines; the comments on those lines stayed:

```diff
-    "numpy>=1.24",  # the resolver forks this one across Python versions
+    "numpy>=2.0.2",  # the resolver forks this one across Python versions
-    "click>=8.0 ; python_version >= '3.10'",
+    "click>=8.5.1 ; python_version >= '3.10'",
```

`uv lock --check` passed afterwards, and `lockfloor --check` exited 0.

## Why the lowest fork

With `requires-python = ">=3.9"` uv locks numpy four times: 2.0.2 for 3.9, 2.2.6 for 3.10, 2.4.6 for 3.11, 2.5.4 for 3.12+. A tool that keeps one version per package name writes whichever it saw last. Writing `numpy>=2.5.4` into that fixture gives:

```
  × No solution found when resolving dependencies for split (markers:
  │ python_full_version == '3.11.*'):
  ╰─▶ Because the requested Python version (>=3.9) does not satisfy
      Python>=3.12 and numpy==2.5.4 depends on Python>=3.12, we can conclude
      that numpy==2.5.4 cannot be used.
```

lockfloor reads the per-edge versions uv records for each project and group, keeps the forks whose marker can hold together with the requirement's own marker (a requirement guarded by `python_version >= '3.12'` gets 2.5.4), and raises the floor to the lowest of those. Every supported environment can still install it.

## What it leaves alone, and says so

Every requirement it does not raise appears in the table with the reason:

| Requirement | Why it is skipped |
|---|---|
| `rich==13.7.0` | A pin is a decision. `--pins` moves single `==` pins: the scratch copy sees `>=`, the result is written back as `==`. |
| `rich==13.*`, `x===1.0` | Wildcard and arbitrary-equality pins are never moved. |
| `attrs<24`, `numpy!=1.25` | No lower bound to raise. Upper bounds and exclusions are kept in every rewrite. |
| `attrs>22` | Strict `>`; rewrite it as `>=` if lockfloor should manage it. |
| `packaging` | No specifier. lockfloor does not invent one. |
| `tool @ git+https://...` | Direct reference. |
| `[tool.uv.sources]` path, git, url or workspace | Detected from the lock entry's source, not guessed from the manifest. |

`~=` keeps its width so its upper edge holds: `idna~=3.4` becomes `~=3.20`, still below 4. Local labels such as `+cpu` are dropped from the floor because PEP 440 forbids them in `>=`.

## Workspaces and groups

lockfloor finds the workspace root from any member, reads every member's `pyproject.toml`, and covers `[project.dependencies]`, every `[project.optional-dependencies]` extra, every PEP 735 `[dependency-groups]` group (skipping `{include-group = ...}` entries) and the older `[tool.uv] dev-dependencies`.

```
lockfloor httpx numpy             # only these dependencies
lockfloor --package demo-core     # only this workspace member
lockfloor --group dev --group extra:yaml
```

A filtered run asks uv to upgrade only the selected names (`--upgrade-package`), so the lock moves no further than the rows shown. An unfiltered run is `uv lock --upgrade`, which also moves transitive dependencies.

## How a write stays safe

1. The workspace is copied to a scratch directory (without `.venv`, `.git`, caches; capped at 512 MiB) and `uv lock --upgrade` runs there. When a path source climbs out of the workspace (`../shared`), the copy is made beside the workspace so that path still resolves.
2. Manifests are edited with tomlkit: comments, ordering, quote style and line endings survive.
3. The scratch `uv.lock` is adopted and `uv lock` re-records the new requirements.
4. If any step fails, every `pyproject.toml` and `uv.lock` is restored byte for byte.

lockfloor itself makes no network calls; uv does all the fetching. Each uv call has a timeout (`--timeout`, default 300 s).

## Exit codes

`0` done or nothing to raise, `1` with `--check` when a floor can be raised, `2` on an error (uv failed, unknown `--group` or `--package`, no `pyproject.toml`).

## Compared with uv-upx

[uv-upx](https://github.com/Alirex/uv_upgrade) also takes versions from `uv.lock` and preserves comments. From its source at the time of writing: its lock reader keeps [one version per package name](https://github.com/Alirex/uv_upgrade/blob/main/src/uv_upx/services/dependencies_from_project/parse_from_uv_lock_file.py), so a forked package gets the last version listed, the case shown above; it raises `>=` only and [ignores `~=`](https://github.com/Alirex/uv_upgrade/blob/main/src/uv_upx/services/dependency_up/constants/operators.py); it adds `>=locked` to bare names unless told not to; and its `--help` lists no CI exit-code mode. It has an interactive accept/reject mode, which lockfloor does not.

## Limits

- Raising floors is what an application wants. A library that should keep supporting old releases should not run `--write` blindly; read the table.
- Fork overlap is tested on a grid of CPython 3.8 to 3.15 on Linux (x86_64, aarch64), macOS (arm64, x86_64) and Windows. A marker on anything else (PyPy, an exotic platform) keeps every fork, which only lowers the floor.
- `[tool.uv] constraint-dependencies` and `override-dependencies`, and `requirements.txt` files, are not touched.
- A member whose metadata is built dynamically from git (setuptools-scm, hatch-vcs) may fail to lock in the scratch copy, which has no `.git`.
- There is no interactive mode and no major/minor/patch filter.

## Development

```
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -e . pytest
.venv/Scripts/python -m pytest              # offline suite, fake resolver
.venv/Scripts/python -m pytest -m network   # real uv against PyPI
```

MIT licensed.
