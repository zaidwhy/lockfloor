from packaging.markers import Marker
from packaging.version import Version

from conftest import REGISTRY, edges, lock_text, package
from lockfloor.lockfile import LockIndex

NUMPY_FORKED = lock_text(
    package(
        "app",
        "0.1.0",
        '{ virtual = "." }',
        edges(
            f'{{ name = "numpy", version = "2.0.2", source = {REGISTRY}, marker = "python_full_version < \'3.10\'" }}',
            f'{{ name = "numpy", version = "2.2.6", source = {REGISTRY}, marker = "python_full_version >= \'3.10\'" }}',
        ),
    ),
    package("numpy", "2.0.2"),
    package("numpy", "2.2.6"),
)


def versions(locked):
    return sorted(str(entry.version) for entry in locked)


def test_forked_edge_returns_every_version_the_resolver_chose():
    index = LockIndex(NUMPY_FORKED)
    assert versions(index.resolved("app", "main", None, "numpy")) == ["2.0.2", "2.2.6"]


def test_requirement_marker_narrows_to_the_overlapping_fork():
    index = LockIndex(NUMPY_FORKED)
    locked = index.resolved("app", "main", None, "numpy")
    assert versions(index.applicable(locked, Marker("python_version >= '3.12'"))) == ["2.2.6"]
    assert versions(index.applicable(locked, Marker("sys_platform == 'win32'"))) == ["2.0.2", "2.2.6"]


def test_name_lookup_is_normalised():
    index = LockIndex(NUMPY_FORKED)
    assert versions(index.resolved("App", "main", None, "NumPy")) == ["2.0.2", "2.2.6"]


def test_project_without_lock_entry_falls_back_to_every_locked_version():
    index = LockIndex(lock_text(package("ruff", "0.8.0")))
    locked = index.resolved(None, "group", "lint", "ruff")
    assert [entry.version for entry in locked] == [Version("0.8.0")]


def test_dependency_groups_are_read_from_dev_dependencies_table():
    text = lock_text(
        package(
            "app",
            "0.1.0",
            '{ editable = "." }',
            '\n[package.dev-dependencies]\ntest = [\n    { name = "pytest" },\n]\n',
        ),
        package("pytest", "8.3.4"),
    )
    index = LockIndex(text)
    assert versions(index.resolved("app", "group", "test", "pytest")) == ["8.3.4"]
