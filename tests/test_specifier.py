from packaging.version import Version

from lockfloor.specifier import Verdict, raise_floor


def test_lower_bound_rises_and_upper_bound_stays():
    assert raise_floor("httpx>=0.25,<1", Version("0.28.1"), pins=False) == Verdict(new="httpx>=0.28.1,<1")


def test_author_spacing_extras_and_marker_survive():
    raw = "httpx[http2] >= 0.25 , <1 ; python_version >= '3.10'"
    verdict = raise_floor(raw, Version("0.28.1"), pins=False)
    assert verdict.new == "httpx[http2] >= 0.28.1 , <1 ; python_version >= '3.10'"


def test_compatible_release_keeps_its_width_so_the_upper_edge_holds():
    assert raise_floor("idna~=3.4", Version("3.10"), pins=False).new == "idna~=3.10"
    assert raise_floor("django~=4.2.0", Version("4.2.16"), pins=False).new == "django~=4.2.16"


def test_compatible_release_already_at_the_resolved_minor_is_current():
    assert raise_floor("idna~=3.10", Version("3.10.2"), pins=False) == Verdict()


def test_resolved_equal_to_floor_is_current_not_a_change():
    assert raise_floor("rich>=13.7.0", Version("13.7.0"), pins=False) == Verdict()


def test_local_version_label_is_dropped_because_pep_440_forbids_it_in_bounds():
    assert raise_floor("torch>=2.0", Version("2.5.1+cpu"), pins=False).new == "torch>=2.5.1"


def test_pins_are_reported_not_moved_by_default():
    verdict = raise_floor("rich==13.7.0", Version("14.0.0"), pins=False)
    assert verdict.new is None
    assert "pinned with ==" in verdict.skip


def test_pins_move_when_asked_and_keep_their_operator():
    assert raise_floor("rich == 13.7.0", Version("14.0.0"), pins=True).new == "rich == 14.0.0"


def test_wildcard_pin_is_left_even_with_pins():
    assert "wildcard" in raise_floor("rich==13.*", Version("14.0.0"), pins=True).skip


def test_arbitrary_equality_and_strict_greater_are_left_with_reasons():
    assert "===" in raise_floor("legacy===1.0", Version("2.0"), pins=True).skip
    assert "strict >" in raise_floor("attrs>22", Version("24.2.0"), pins=False).skip


def test_upper_bound_only_and_bare_names_are_left():
    assert "no lower bound" in raise_floor("numpy<2", Version("1.26.4"), pins=False).skip
    assert "no version specifier" in raise_floor("packaging", Version("24.1"), pins=False).skip


def test_direct_references_are_left():
    raw = "tool @ git+https://github.com/example/tool@v1"
    assert "direct reference" in raise_floor(raw, Version("2.0"), pins=False).skip


def test_version_outside_the_declared_range_is_refused_not_written():
    verdict = raise_floor("click>=8.0,<8.2", Version("8.3.0"), pins=False)
    assert verdict.new is None
    assert "outside the declared range" in verdict.skip
