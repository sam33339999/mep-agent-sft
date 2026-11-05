import pytest

from mep_sft.assembler import describe_scene
from mep_sft.demo import demo_scene, two_floors
from mep_sft.explain import explain
from mep_sft.generator import generate_dataset, generate_scene
from mep_sft.rules import CLEAR, check_all
from mep_sft.scene import Pipe, Rules, Scene
from mep_sft.solver import solve


def test_rules_limits_per_storey():
    r = Rules(floors=3)
    assert r.limits(0) == (2.4, 3.0)
    assert r.limits(1) == (5.6, 6.2)
    assert r.limits(2) == (8.8, 9.4)
    assert r.top() == 9.4


def test_same_height_is_valid_on_one_storey_and_a_violation_on_another():
    s = two_floors()
    # A at 2.6 (storey 1) is fine; C at 5.5 on storey 2 is too low for *its* storey
    vs = check_all(s)
    assert [(v.type, v.ids) for v in vs] == [(CLEAR, ["C"])]
    assert vs[0].threshold == 5.6
    s.pipe("C").floor = 0  # claim it is on storey 1: now it pokes through that ceiling
    assert [v.type for v in check_all(s)] == [CLEAR] and check_all(s)[0].threshold == 3.0


def test_solver_fixes_the_right_storey():
    res = solve(two_floors())
    assert res.ok and [(a.kind, a.pipe_id, a.detail) for a in res.actions] == [
        ("shift_z", "C", {"dz": 0.15})
    ]
    assert res.fixed.pipe("C").points[0][2] == 5.65
    assert res.fixed.pipe("A").points[0][2] == 2.6  # other storey untouched


def test_single_floor_defaults_are_unchanged():
    s = demo_scene("two-pipes")
    assert s.rules.floors == 1 and all(p.floor == 0 for p in s.pipes)
    assert s.rules.limits(0) == (2.4, 3.0)


def test_old_json_without_floor_fields_still_loads():
    d = demo_scene("two-pipes").to_dict()
    for p in d["pipes"]:
        del p["floor"]
    del d["rules"]["floors"], d["rules"]["floor_pitch"]
    assert Scene.from_dict(d).rules.floors == 1


def test_explain_mentions_storey_limits():
    s = two_floors()
    e = explain(s, check_all(s)[0].to_dict())
    assert "5.6" in e["calc"] and "第 2 層" in e["calc"]


def test_prompt_states_absolute_limits_per_storey():
    text = describe_scene(two_floors())
    assert "第 1 層管底 ≥ 2.4 m 且管頂 ≤ 3 m" in text and "第 2 層管底 ≥ 5.6 m 且管頂 ≤ 6.2 m" in text
    assert "第 2 層" in text and "底部標高 3.2 m" in text


def test_difficulty_4_builds_two_storeys_with_pipes_on_both():
    seen_both = 0
    for seed in range(8):
        sc = generate_scene(seed, 4)
        assert sc.rules.floors == 2
        assert {w.z0 for w in sc.walls} == {0.0, 3.2}
        if {p.floor for p in sc.pipes} == {0, 1}:
            seen_both += 1
        for p in sc.pipes:  # every pipe's z lies in its own storey band, or it is an injected issue
            lo, hi = sc.rules.limits(p.floor)
            zs = [q[2] for q in p.points]
            assert max(zs) > lo - 1.0 and min(zs) < hi + 1.0
    assert seen_both >= 5


def test_generated_floor_samples_solve_and_stay_on_their_storey():
    samples, _ = generate_dataset(30, seed=2000, difficulty=4)
    assert samples
    for s in samples:
        fixed = Scene.from_dict(s["fixed"])
        assert check_all(fixed) == []
        for p in fixed.pipes:
            lo, hi = fixed.rules.limits(p.floor)
            assert min(q[2] for q in p.points) - p.outer_radius >= lo - 1e-9
            assert max(q[2] for q in p.points) + p.outer_radius <= hi + 1e-9


def test_pipes_on_other_storey_never_clash():
    s = Scene(walls=[], pipes=[
        Pipe("A", "water", 0.2, [(0, 0, 2.6), (4, 0, 2.6)]),
        Pipe("B", "water", 0.2, [(2, -1, 5.8), (2, 1, 5.8)], floor=1),
    ], rules=Rules(floors=2))
    assert check_all(s) == []


@pytest.mark.parametrize("case", ["two-floors"])
def test_demo_cli_case(case, tmp_path):
    from mep_sft.cli import main
    assert main(["demo", "--case", case, "--out", str(tmp_path)]) == 0
    assert (tmp_path / "scene.html").exists()
