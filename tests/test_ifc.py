import pytest

from mep_sft.demo import opening, two_pipes

ifcopenshell = pytest.importorskip("ifcopenshell")


def test_ifc_roundtrip(tmp_path):
    from mep_sft.ifc_export import export_ifc

    path = str(tmp_path / "scene.ifc")
    export_ifc(opening(), path)
    f = ifcopenshell.open(path)
    assert len(f.by_type("IfcWall")) == 1
    assert len(f.by_type("IfcOpeningElement")) == 1
    assert len(f.by_type("IfcPipeSegment")) == 1


def test_ifc_pipe_segments_per_run(tmp_path):
    from mep_sft.ifc_export import export_ifc

    path = str(tmp_path / "two.ifc")
    export_ifc(two_pipes(), path)
    f = ifcopenshell.open(path)
    assert {s.Name for s in f.by_type("IfcPipeSegment")} == {"A-1", "B-1"}


def test_ifc_placements_match_scene_coordinates(tmp_path):
    from ifcopenshell.util.placement import get_local_placement

    from mep_sft.ifc_export import export_ifc

    path = str(tmp_path / "pos.ifc")
    export_ifc(two_pipes(), path)
    f = ifcopenshell.open(path)
    seg = next(s for s in f.by_type("IfcPipeSegment") if s.Name == "B-1")
    m = get_local_placement(seg.ObjectPlacement)
    assert list(m[:3, 3]) == pytest.approx([3.0, 0.5, 2.6])
    assert list(m[:3, 2]) == pytest.approx([0.0, 1.0, 0.0])  # extrudes along the pipe (+Y)


def test_ifc_uses_metres_and_pipe_radius_is_real(tmp_path):
    from mep_sft.ifc_export import export_ifc

    path = str(tmp_path / "unit.ifc")
    export_ifc(two_pipes(), path)
    f = ifcopenshell.open(path)
    unit = next(u for u in f.by_type("IfcSIUnit") if u.UnitType == "LENGTHUNIT")
    assert unit.Name == "METRE" and unit.Prefix is None
    radii = sorted(p.Radius for p in f.by_type("IfcCircleProfileDef"))
    assert radii == pytest.approx([0.075, 0.1])


def _bbox(f, product):
    import numpy as np
    from ifcopenshell import geom

    settings = geom.settings()
    settings.set("use-world-coords", True)
    shape = geom.create_shape(settings, product)
    v = np.array(shape.geometry.verts).reshape(-1, 3)
    return v.min(axis=0), v.max(axis=0), shape


def test_ifc_geometry_engine_matches_scene_boxes(tmp_path):
    """Triangulate every element and compare its bounding box with the scene coordinates."""
    from mep_sft.ifc_export import export_ifc

    path = str(tmp_path / "geo.ifc")
    export_ifc(two_pipes(), path)
    f = ifcopenshell.open(path)
    by = {p.Name: p for p in f.by_type("IfcPipeSegment") + f.by_type("IfcWall")}
    lo, hi, _ = _bbox(f, by["A-1"])  # (1,1,2.5)->(5,1,2.5), diameter 0.2
    assert list(lo) == pytest.approx([1.0, 0.9, 2.4], abs=1e-3)
    assert list(hi) == pytest.approx([5.0, 1.1, 2.6], abs=1e-3)
    lo, hi, _ = _bbox(f, by["B-1"])  # (3,0.5,2.6)->(3,3,2.6), diameter 0.15
    assert list(lo) == pytest.approx([2.925, 0.5, 2.525], abs=1e-3)
    assert list(hi) == pytest.approx([3.075, 3.0, 2.675], abs=1e-3)
    lo, hi, _ = _bbox(f, by["W1"])  # (0,0)->(10,0), thickness 0.2, height 3
    assert list(lo) == pytest.approx([0.0, -0.1, 0.0], abs=1e-3)
    assert list(hi) == pytest.approx([10.0, 0.1, 3.0], abs=1e-3)


def test_ifc_opening_really_cuts_the_wall(tmp_path):
    import numpy as np
    from ifcopenshell import geom

    from mep_sft.ifc_export import export_ifc

    scene = opening()
    scene.pipes = []  # the wall and its opening only
    path = str(tmp_path / "open.ifc")
    export_ifc(scene, path)
    f = ifcopenshell.open(path)
    wall = f.by_type("IfcWall")[0]
    assert [o.RelatedOpeningElement.Name for o in wall.HasOpenings] == ["W1-opening-1"]

    def volume(disable_subtraction: bool) -> float:
        st = geom.settings()
        st.set("disable-opening-subtractions", disable_subtraction)
        g = geom.create_shape(st, wall).geometry
        v = np.array(g.verts).reshape(-1, 3)
        t = np.array(g.faces).reshape(-1, 3)
        return abs(np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6)

    assert volume(True) == pytest.approx(10 * 0.2 * 3, abs=1e-6)  # solid slab
    # opening: u 4..5 m, z 0..2.6 m through a 0.2 m wall removes 1.0 * 2.6 * 0.2 = 0.52 m3
    assert volume(True) - volume(False) == pytest.approx(0.52, abs=1e-6)
