import random
from dataclasses import replace

from mep_sft.generator import generate_scene
from mep_sft.rules import check_all, check_pipe, recheck
from mep_sft.scene import r4


def _same(a, b):
    return [v.to_dict() for v in a] == [v.to_dict() for v in b]


def _moved(scene, pid, rng):
    """One random change to one pipe: vertical shift, lateral shift, or a detour point."""
    p = scene.pipe(pid)
    kind = rng.choice(("z", "xy", "bend"))
    if kind == "z":
        dz = rng.choice((-0.5, -0.2, -0.05, 0.05, 0.2, 0.5))
        pts = [(x, y, r4(z + dz)) for x, y, z in p.points]
    elif kind == "xy":
        dx, dy = rng.choice((-1.0, -0.3, 0.0, 0.3, 1.0)), rng.choice((-1.0, 0.0, 0.5, 1.2))
        pts = [(r4(x + dx), r4(y + dy), z) for x, y, z in p.points]
    else:
        a, b = p.points[0], p.points[-1]
        mid = tuple(r4((a[i] + b[i]) / 2 + (0.0, rng.choice((-0.4, 0.4)), rng.choice((-0.3, 0.3)))[i])
                    for i in range(3))
        pts = [a, mid, b]
    return replace(scene, pipes=[replace(q, points=pts) if q.id == pid else q for q in scene.pipes])


def test_recheck_equals_full_check_on_random_moves():
    """The speed-up is only acceptable if it is exactly equivalent to the full check."""
    rng = random.Random(4)
    compared = changed = 0
    for seed in range(25):
        for d in (2, 3, 4):
            scene = generate_scene(seed, d, 0)
            base = check_all(scene)
            for _ in range(6):
                pid = rng.choice(scene.pipes).id
                cand = _moved(scene, pid, rng)
                got, want = recheck(cand, base, pid), check_all(cand)
                assert _same(got, want), (seed, d, pid)
                compared += 1
                changed += not _same(want, base)
    assert compared > 300 and changed > 100  # the moves really do change the violation set


def test_check_pipe_is_the_subset_that_involves_the_pipe():
    scene = generate_scene(3, 4, 0)
    full = check_all(scene)
    for p in scene.pipes:
        assert _same(check_pipe(scene, p.id), [v for v in full if p.id in v.ids])
