import math

from mep_sft.demo import demo_scene
from mep_sft.geometry import segment_box_intersect, segment_distance
from mep_sft.rules import CLASH, CLEAR, WALL, check_all
from mep_sft.scene import Pipe, Scene, Wall
from mep_sft.solver import solve


def test_segment_distance_crossing():
    d, _, _ = segment_distance((0, 0, 0), (2, 0, 0), (1, -1, 1), (1, 1, 1))
    assert math.isclose(d, 1.0)


def test_segment_distance_parallel_and_endpoint():
    d, _, _ = segment_distance((0, 0, 0), (1, 0, 0), (3, 0, 0), (4, 0, 0))
    assert math.isclose(d, 2.0)


def test_segment_box():
    assert segment_box_intersect((-1, 0, 0), (1, 0, 0), (-0.5,) * 3, (0.5,) * 3) is not None
    assert segment_box_intersect((-1, 2, 0), (1, 2, 0), (-0.5,) * 3, (0.5,) * 3) is None


def test_demo_has_clash_only():
    vs = check_all(demo_scene())
    assert [v.type for v in vs] == [CLASH]
    assert math.isclose(vs[0].value, 0.1 - 0.175, abs_tol=1e-9)


def test_clearance_boundary_is_valid():
    s = demo_scene()
    # centre distance 0.225 -> gap exactly 0.05 == clearance: allowed
    s.pipe("B").points = [(3.0, 0.5, 2.725), (3.0, 3.0, 2.725)]
    assert check_all(s) == []


def test_wall_penetration_and_clear_height():
    s = Scene(
        walls=[Wall("W", (0, 0), (10, 0))],
        pipes=[Pipe("P", "water", 0.2, [(5, -1, 2.0), (5, 1, 2.0)])],
    )
    assert {v.type for v in check_all(s)} == {WALL, CLEAR}


def test_pipe_beside_wall_ok():
    s = Scene(
        walls=[Wall("W", (0, 0), (10, 0))],
        pipes=[Pipe("P", "water", 0.2, [(1, 0.5, 2.5), (5, 0.5, 2.5)])],
    )
    assert check_all(s) == []


def test_solver_fixes_demo_by_moving_up():
    res = solve(demo_scene())
    assert res.ok and check_all(res.fixed) == []
    assert [(a.pipe_id, a.dz) for a in res.actions] == [("B", 0.15)]


def test_solver_deterministic():
    a, b = solve(demo_scene()), solve(demo_scene())
    assert a.fixed.to_json() == b.fixed.to_json()


def test_scene_json_roundtrip():
    s = demo_scene()
    assert Scene.from_json(s.to_json()).to_json() == s.to_json()
