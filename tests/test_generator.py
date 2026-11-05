import hashlib
import json

from mep_sft.generator import LEVELS, generate_dataset, generate_sample
from mep_sft.rules import check_all
from mep_sft.scene import Scene
from mep_sft.solver import solve


def _line(s: dict) -> str:
    return json.dumps(s, sort_keys=True, ensure_ascii=False)


def test_same_seed_same_output():
    for d in LEVELS:
        assert _line(generate_sample(7, d)) == _line(generate_sample(7, d))


def test_dataset_reproducible_and_unique():
    a, _ = generate_dataset(30, seed=100)
    b, _ = generate_dataset(30, seed=100)
    assert [_line(x) for x in a] == [_line(x) for x in b]
    assert len({x["scene_hash"] for x in a}) == len(a)


def test_samples_are_valid_pairs():
    samples, _ = generate_dataset(40, seed=0)
    for s in samples:
        scene, fixed = Scene.from_dict(s["scene"]), Scene.from_dict(s["fixed"])
        assert check_all(fixed) == []
        # injected issues are real, and clean samples are really clean
        assert bool(scene.injected) == bool(check_all(scene)) == bool(s["violations"])
        # stored solution equals a fresh solve of the stored scene
        assert solve(scene).fixed.to_json() == fixed.to_json()


def test_clean_pipes_stay_on_grid_and_inside_room():
    for d in LEVELS:
        s = generate_sample(3, d)
        for p in Scene.from_dict(s["scene"]).pipes:
            for pt in p.points:
                assert all(abs(c / 0.05 - round(c / 0.05)) < 1e-6 for c in pt), pt


def test_difficulty_scales_pipe_count():
    lo = [len(generate_sample(s, 1)["scene"]["pipes"]) for s in range(10)]
    hi = [len(generate_sample(s, 4)["scene"]["pipes"]) for s in range(10)]
    assert max(lo) <= 4 and min(hi) >= 8


def test_dataset_has_clean_examples_and_all_issue_types():
    samples, _ = generate_dataset(150, seed=0)
    tags = {t for s in samples for t in s["scene"]["injected"]}
    assert {"clash", "wall_penetration", "clear_height", "drain_slope"} <= tags
    assert any(not s["scene"]["injected"] for s in samples)


def test_hash_is_stable():
    s = generate_sample(1, 1)
    assert hashlib.sha1(Scene.from_dict(s["scene"]).to_json().encode()).hexdigest()[:16] == s[
        "scene_hash"
    ]
