# lockfloor handoff (2026-11-10)

State: pushed to private zaidwhy/lockfloor. 34 offline + 3 network tests. Targets uv#6794: raises pyproject lower bounds to uv-resolved versions, lowest version across resolver forks.

Next: PyPI publish and any comment on uv#6794 are Zaid's call (outward-facing).

## Release (prepared 2026-11-10, not uploaded)
`uv build` produces lockfloor-0.1.0 sdist + wheel; `twine check` passes; the wheel installs and runs `lockfloor --help` in a clean env. The name is free on PyPI (JSON API 404 on 2026-11-10).
Recommended first of the four Python packages (uv#6794 has the most demand). Steps for Zaid, in order:
1. Make the repo public if PyPI users should see the source: `gh repo edit zaidwhy/lockfloor --visibility public --accept-visibility-change-consequences`, then add `[project.urls]` Repository to pyproject.toml.
2. `cd "C:/Users/Asus/projects/lockfloor" && uv build && uvx twine upload dist/*` (asks for a PyPI API token).
3. Comment on astral-sh/uv#6794 in his own words.
