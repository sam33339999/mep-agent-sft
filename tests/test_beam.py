import json

from mep_sft.assembler import _step
from mep_sft.generator import generate_scene
from mep_sft.rules import check_all
from mep_sft.scene import Scene
from mep_sft.solver import _beam, _greedy, solve

SCENES = [s for s in (generate_scene(i, 4, 0) for i in range(60)) if check_all(s)]
STUCK = [s for s in SCENES if not _greedy(s).ok]
RESCUED = [(s, r) for s in STUCK if (r := _beam(s)) is not None]


def test_there_are_scenes_the_greedy_walk_cannot_solve_and_the_beam_can():
    assert len(STUCK) >= 5 and len(RESCUED) >= 3


def test_beam_solutions_are_valid_and_replay_exactly():
    for scene, res in RESCUED:
        assert res.ok and check_all(res.fixed) == []
        cur = scene
        for a in res.actions:  # every recorded step, replayed, lands on the returned scene
            cur = _step(cur, {"kind": a.kind, "pipe_id": a.pipe_id, "detail": a.detail,
                              "points": a.points})
        assert [p.points for p in cur.pipes] == [p.points for p in res.fixed.pipes]
        assert [w.openings for w in cur.walls] == [w.openings for w in res.fixed.walls]
        assert res.actions[-1].violations_after == 0
        assert all(a.violations_before >= 1 for a in res.actions)


def test_beam_is_deterministic():
    scene, res = RESCUED[0]
    again = _beam(scene)
    assert again.fixed.to_json() == res.fixed.to_json()
    assert [(a.kind, a.pipe_id, a.detail) for a in again.actions] == [
        (a.kind, a.pipe_id, a.detail) for a in res.actions]


def test_solve_keeps_the_greedy_answer_when_greedy_succeeds():
    """The fallback must not change any scene the greedy walk already solves (stable datasets)."""
    checked = 0
    for scene in SCENES:
        g = _greedy(scene)
        if g.ok:
            s = solve(scene)
            assert s.fixed.to_json() == g.fixed.to_json()
            assert [(a.kind, a.pipe_id) for a in s.actions] == [(a.kind, a.pipe_id) for a in g.actions]
            checked += 1
    assert checked > 10


def test_solve_uses_the_beam_for_stuck_scenes():
    scene, res = RESCUED[0]
    assert solve(scene).fixed.to_json() == res.fixed.to_json()


def test_hopeless_scenes_still_fail_quickly_and_honestly():
    from mep_sft.demo import opening

    s = opening()
    s.walls[0].openings = []
    s.walls[0].allow_openings = False
    s.pipe("C").fixed_ends = True
    res = solve(s)
    assert not res.ok and [v.type for v in res.remaining] == ["wall_penetration"]


def test_stored_solutions_from_before_the_speedup_are_reproduced():
    """Regression: solutions saved by the earlier greedy-only solver come out identical."""
    import pathlib

    path = pathlib.Path("data/exp1/train_raw.jsonl")
    if not path.exists():
        return  # data/ is local, not in the repository
    for line in path.read_text(encoding="utf-8").splitlines()[:80]:
        raw = json.loads(line)
        res = solve(Scene.from_dict(raw["scene"]))
        assert res.fixed.to_json() == Scene.from_dict(raw["fixed"]).to_json()
