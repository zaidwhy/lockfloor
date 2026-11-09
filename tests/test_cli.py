"""What a user sees from the command: the table, the exit code and the bytes on disk."""

from __future__ import annotations

from conftest import REGISTRY, FakeResolver, edges, lock_text, package, snapshot, write
from lockfloor.cli import main
from lockfloor.resolver import ResolveError

APP = """\
[project]
name = "app"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    # HTTP client; stay below 1.0
    "httpx>=0.25,<1",
    'rich == 13.7.0',  # pinned on purpose
    "click >= 8.0 ; python_version >= '3.10'",
    "numpy>=1.24",
    "tool @ git+https://github.com/example/tool@v1",
    "vendored>=0.1",
    "attrs<24",
]

[project.optional-dependencies]
yaml = ['pyyaml>=6.0']

[dependency-groups]
test = ["pytest>=7.0", { include-group = "lint" }]
lint = ["ruff>=0.4"]

[tool.uv.sources]
vendored = { path = "libs/vendored" }
"""

APP_LOCK = lock_text(
    package(
        "app",
        "0.1.0",
        '{ virtual = "." }',
        edges(
            '{ name = "attrs" }',
            "{ name = \"click\", marker = \"python_full_version >= '3.10'\" }",
            '{ name = "httpx" }',
            f'{{ name = "numpy", version = "2.0.2", source = {REGISTRY}, marker = "python_full_version < \'3.10\'" }}',
            f'{{ name = "numpy", version = "2.2.6", source = {REGISTRY}, marker = "python_full_version >= \'3.10\'" }}',
            '{ name = "rich" }',
            '{ name = "tool" }',
            '{ name = "vendored" }',
        )
        + '\n[package.optional-dependencies]\nyaml = [\n    { name = "pyyaml" },\n]\n'
        + '\n[package.dev-dependencies]\nlint = [\n    { name = "ruff" },\n]\n'
        + 'test = [\n    { name = "pytest" },\n    { name = "ruff" },\n]\n',
    ),
    package("attrs", "23.2.0"),
    package("click", "8.1.8"),
    package("httpx", "0.28.1"),
    package("numpy", "2.0.2"),
    package("numpy", "2.2.6"),
    package("pytest", "8.3.4"),
    package("pyyaml", "6.0.2"),
    package("rich", "14.0.0"),
    package("ruff", "0.8.0"),
    package("tool", "1.0.0", '{ git = "https://github.com/example/tool?rev=v1#abc" }'),
    package("vendored", "0.3.0", '{ directory = "libs/vendored" }'),
)


def run(root, resolver, *argv):
    return main(["--project", str(root), *argv], resolver=resolver)


def test_dry_run_prints_the_plan_and_leaves_every_byte(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    write(project, "uv.lock", "old lock\n")
    before = snapshot(project)

    code = run(project, FakeResolver(APP_LOCK, tmp_path))

    out = capsys.readouterr().out
    assert code == 0
    assert snapshot(project) == before
    assert ">=0.25,<1" in out and ">=0.28.1,<1" in out
    assert "6 to raise, 4 left alone, 0 already at the resolved version." in out
    assert "Dry run: no file was written" in out


def test_write_raises_floors_and_keeps_comments_quotes_and_order(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    resolver = FakeResolver(APP_LOCK, tmp_path)

    assert run(project, resolver, "--write") == 0

    after = (project / "pyproject.toml").read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(APP.splitlines(), after.splitlines()) if a != b]
    assert changed == [
        ('    "httpx>=0.25,<1",', '    "httpx>=0.28.1,<1",'),
        ("    \"click >= 8.0 ; python_version >= '3.10'\",", "    \"click >= 8.1.8 ; python_version >= '3.10'\","),
        ('    "numpy>=1.24",', '    "numpy>=2.0.2",'),
        ("yaml = ['pyyaml>=6.0']", "yaml = ['pyyaml>=6.0.2']"),
        ('test = ["pytest>=7.0", { include-group = "lint" }]', 'test = ["pytest>=8.3.4", { include-group = "lint" }]'),
        ('lint = ["ruff>=0.4"]', 'lint = ["ruff>=0.8.0"]'),
    ]
    assert len(after.splitlines()) == len(APP.splitlines())
    assert (project / "uv.lock").read_text(encoding="utf-8") == APP_LOCK
    assert resolver.relocked == [project.resolve()]


def test_constraints_lockfloor_must_not_touch_are_reported_with_reasons(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    run(project, FakeResolver(APP_LOCK, tmp_path), "--write")

    out = capsys.readouterr().out
    after = (project / "pyproject.toml").read_text(encoding="utf-8")
    for untouched in ("'rich == 13.7.0',  # pinned on purpose", "tool @ git+https://github.com/example/tool@v1",
                      '"vendored>=0.1"', '"attrs<24"'):
        assert untouched in after
    assert "skipped: pinned with ==" in out
    assert "skipped: direct reference" in out
    assert "skipped: locked from a local directory" in out
    assert "skipped: no lower bound to raise" in out


def test_forked_resolution_raises_to_the_lowest_fork(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    run(project, FakeResolver(APP_LOCK, tmp_path))
    out = capsys.readouterr().out
    numpy_line = next(line for line in out.splitlines() if " numpy " in line)
    assert ">=2.0.2" in numpy_line
    assert "resolver forked (2.0.2, 2.2.6)" in numpy_line


def test_check_exits_one_when_floors_lag_and_zero_when_current(project, tmp_path):
    write(project, "pyproject.toml", APP)
    assert run(project, FakeResolver(APP_LOCK, tmp_path), "--check") == 1
    assert run(project, FakeResolver(APP_LOCK, tmp_path), "--write") == 0
    assert run(project, FakeResolver(APP_LOCK, tmp_path), "--check") == 0


def test_group_filter_limits_rows_and_the_packages_uv_may_upgrade(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    resolver = FakeResolver(APP_LOCK, tmp_path)

    run(project, resolver, "--group", "lint", "--group", "extra:yaml")

    out = capsys.readouterr().out
    assert resolver.upgrade_calls == [["pyyaml", "ruff"]]
    assert "pyyaml" in out and "ruff" in out
    assert "httpx" not in out and "pytest" not in out


def test_unknown_group_is_an_error_that_names_the_real_groups(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    code = run(project, FakeResolver(APP_LOCK, tmp_path), "--group", "docs")
    err = capsys.readouterr().err
    assert code == 2
    assert "--group docs not found" in err and "extra:yaml" in err and "lint" in err


def test_dependency_names_select_rows_and_unfiltered_runs_upgrade_everything(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    named = FakeResolver(APP_LOCK, tmp_path)
    run(project, named, "HTTPX")
    everything = FakeResolver(APP_LOCK, tmp_path)
    run(project, everything)
    assert named.upgrade_calls == [["httpx"]]
    assert everything.upgrade_calls == [None]


def test_failed_relock_restores_manifest_and_lock_bytes(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    write(project, "uv.lock", "the lock before\n")
    before = snapshot(project)
    resolver = FakeResolver(APP_LOCK, tmp_path, relock_error=ResolveError("`uv lock` failed:\nno solution"))

    code = run(project, resolver, "--write")

    assert code == 2
    assert snapshot(project) == before
    assert "restored" in capsys.readouterr().err


def test_pins_flag_loosens_only_the_scratch_copy_then_writes_the_new_pin(project, tmp_path, capsys):
    write(project, "pyproject.toml", APP)
    resolver = FakeResolver(APP_LOCK, tmp_path)

    run(project, resolver, "--pins", "--write")

    scratch = (resolver.scratch_copy / "pyproject.toml").read_text(encoding="utf-8")
    assert "'rich >= 13.7.0',  # pinned on purpose" in scratch
    after = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert "'rich == 14.0.0',  # pinned on purpose" in after


def test_windows_line_endings_survive_a_write(project, tmp_path):
    write(project, "pyproject.toml", APP.replace("\n", "\r\n"))
    run(project, FakeResolver(APP_LOCK, tmp_path), "--write")
    after = (project / "pyproject.toml").read_bytes()
    assert b'"httpx>=0.28.1,<1",\r\n' in after
    assert after.count(b"\r\n") == APP.count("\n")


WORKSPACE_ROOT = """\
[project]
name = "web"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["core", "httpx>=0.25"]

[tool.uv.sources]
core = { workspace = true }

[tool.uv.workspace]
members = ["packages/*"]
"""

WORKSPACE_CORE = """\
[project]
name = "core"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["attrs>=22.1"]

[dependency-groups]
dev = ["pytest>=7.0"]
"""

WORKSPACE_LOCK = lock_text(
    package("web", "0.1.0", '{ virtual = "." }', edges('{ name = "core" }', '{ name = "httpx" }')),
    package(
        "core",
        "0.1.0",
        '{ editable = "packages/core" }',
        edges('{ name = "attrs" }') + '\n[package.dev-dependencies]\ndev = [\n    { name = "pytest" },\n]\n',
    ),
    package("attrs", "24.2.0"),
    package("httpx", "0.28.1"),
    package("pytest", "8.3.4"),
)


def make_workspace(root):
    write(root, "pyproject.toml", WORKSPACE_ROOT)
    write(root, "packages/core/pyproject.toml", WORKSPACE_CORE)


def test_workspace_members_are_planned_and_written_from_one_lock(project, tmp_path, capsys):
    make_workspace(project)

    assert run(project, FakeResolver(WORKSPACE_LOCK, tmp_path), "--write") == 0

    out = capsys.readouterr().out
    assert "member" in out.splitlines()[0]
    assert "skipped: no version specifier" in out
    assert '"httpx>=0.28.1"' in (project / "pyproject.toml").read_text(encoding="utf-8")
    core = (project / "packages/core/pyproject.toml").read_text(encoding="utf-8")
    assert '"attrs>=24.2.0"' in core and '"pytest>=8.3.4"' in core
    assert "Wrote packages/core/pyproject.toml, pyproject.toml and re-locked uv.lock." in out


def test_package_filter_and_running_from_inside_a_member(project, tmp_path, capsys):
    make_workspace(project)
    resolver = FakeResolver(WORKSPACE_LOCK, tmp_path)

    code = main(["--project", str(project / "packages/core"), "--package", "core"], resolver=resolver)

    out = capsys.readouterr().out
    assert code == 0
    assert resolver.upgrade_calls == [["attrs", "pytest"]]
    assert "attrs" in out and "httpx" not in out


def test_unknown_member_is_an_error(project, tmp_path, capsys):
    make_workspace(project)
    assert run(project, FakeResolver(WORKSPACE_LOCK, tmp_path), "--package", "api") == 2
    assert "not a workspace member" in capsys.readouterr().err


def test_missing_pyproject_is_an_error(tmp_path, capsys):
    assert main(["--project", str(tmp_path)], resolver=FakeResolver("", tmp_path)) == 2
    assert "no pyproject.toml" in capsys.readouterr().err


def test_path_source_outside_the_workspace_asks_for_a_sibling_scratch_copy(project, tmp_path):
    inside = FakeResolver(APP_LOCK, tmp_path)
    write(project, "pyproject.toml", APP)
    run(project, inside)
    outside = FakeResolver(APP_LOCK, tmp_path)
    write(project, "pyproject.toml", APP.replace('path = "libs/vendored"', 'path = "../shared/vendored"'))
    run(project, outside)
    assert (inside.escapes_root, outside.escapes_root) == (False, True)
