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
    assert [(a.kind, a.pipe_id, a.detail) for a in res.actions] == [("shift_z", "B", {"dz": 0.15})]


def test_solver_deterministic():
    a, b = solve(demo_scene()), solve(demo_scene())
    assert a.fixed.to_json() == b.fixed.to_json()


def test_scene_json_roundtrip():
    s = demo_scene()
    assert Scene.from_json(s.to_json()).to_json() == s.to_json()


# ---------------------------------------------------------------- phase 2

from mep_sft.demo import jog, opening, slope
from mep_sft.rules import SLOPE
from mep_sft.scene import Opening


def test_slope_rule():
    vs = check_all(slope())
    assert [v.type for v in vs] == [SLOPE]
    s = slope()
    s.pipe("D").points = [(1.0, 1.0, 2.6), (6.0, 1.0, 2.5)]  # 2% fall
    assert check_all(s) == []
    s.pipe("D").points = [(1.0, 1.0, 2.5), (6.0, 1.0, 2.6)]  # uphill
    assert [v.type for v in check_all(s)] == [SLOPE]


def test_slope_only_applies_to_drainage():
    s = slope()
    s.pipe("D").system = "water"
    assert check_all(s) == []


def test_solver_reslope_prefers_raising_upstream_when_lowering_breaks_clear_height():
    res = solve(slope())
    assert res.ok and [a.kind for a in res.actions] == ["reslope"]
    assert res.actions[0].detail["mode"] == "raise_upstream"
    assert res.fixed.pipe("D").points[0][2] > res.fixed.pipe("D").points[-1][2]


def test_wall_opening_allows_pipe():
    s = opening()
    s.pipe("C").points = [(4.5, -1.0, 2.5), (4.5, 1.0, 2.5)]
    assert check_all(s) == []
    s.pipe("C").points = [(4.99, -1.0, 2.5), (4.99, 1.0, 2.5)]  # pipe edge clips the opening
    assert [v.type for v in check_all(s)] == [WALL]


def test_solver_slides_pipe_into_opening():
    res = solve(opening())
    assert res.ok
    assert [(a.kind, a.detail) for a in res.actions] == [("slide_wall", {"dx": 1.05, "dy": 0.0})]


def test_solid_wall_is_unsolvable():
    s = opening()
    s.walls[0].openings = []
    s.pipe("C").fixed_ends = True  # cannot slide to the other side of the wall
    res = solve(s)
    assert not res.ok and [v.type for v in res.remaining] == [WALL]


def test_fixed_ends_forces_detour_and_keeps_ends():
    s = jog()
    res = solve(s)
    assert res.ok and check_all(res.fixed) == []
    assert [a.kind for a in res.actions] == ["jog_xy"]
    old, new = s.pipe("B").points, res.fixed.pipe("B").points
    assert new[0] == old[0] and new[-1] == old[-1] and len(new) == 6


def test_drainage_keeps_priority_over_water():
    s = Scene(
        walls=[],
        pipes=[
            Pipe("W", "water", 0.1, [(0.0, 1.0, 2.6), (4.0, 1.0, 2.6)]),
            Pipe("D", "drainage", 0.1, [(2.0, -1.0, 2.62), (2.0, 3.0, 2.58)]),
        ],
    )
    # D has 0.02 m fall over 4 m = 0.5% -> also a slope violation; water must be the one to move
    res = solve(s)
    assert "W" in {a.pipe_id for a in res.actions if a.kind != "reslope"}


def test_wall_openings_json_roundtrip():
    s = opening()
    assert Scene.from_json(s.to_json()).walls[0].openings == [Opening(4.0, 5.0, 0.0, 2.6)]


def test_wall_without_opening_is_trimmed_not_dragged_outside():
    s = Scene(
        walls=[Wall("W", (0, 0), (10, 0))],
        pipes=[Pipe("P", "water", 0.1, [(3.0, -0.8, 2.5), (3.0, 3.0, 2.5)])],
    )
    res = solve(s)
    assert res.ok and [a.kind for a in res.actions] == ["trim"]
    # longer (inside) side kept; new end is wall half-thickness + radius + 0.05 -> snapped 0.2 from the wall
    assert res.fixed.pipe("P").points == [(3.0, 0.2, 2.5), (3.0, 3.0, 2.5)]
    assert res.actions[0].detail["keep"] == "end"


def test_trim_is_refused_when_too_little_pipe_would_remain():
    s = Scene(
        walls=[Wall("W", (0, 0), (10, 0))],
        pipes=[Pipe("P", "water", 0.1, [(3.0, -0.4, 2.5), (3.0, 0.6, 2.5)])],
    )
    res = solve(s)
    assert res.ok and res.actions[0].kind != "trim"  # 0.4 m would remain, below the 0.5 m floor
