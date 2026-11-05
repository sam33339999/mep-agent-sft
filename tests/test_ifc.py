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
