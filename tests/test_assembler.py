import json

import pytest

from mep_sft.answer import apply_fix, extract_json
from mep_sft.assembler import VARIANTS, assemble, assemble_file, describe_scene
from mep_sft.generator import generate_dataset
from mep_sft.rules import check_all
from mep_sft.scene import Scene

SAMPLES, _ = generate_dataset(60, seed=500)


def _answer(rec):
    return rec["messages"][2]["content"]


def test_message_structure():
    for s in SAMPLES:
        rec = assemble(s)
        assert [m["role"] for m in rec["messages"]] == ["system", "user", "assistant"]
        text = _answer(rec)
        assert text.startswith("<think>\n") and text.count("<think>") == text.count("</think>") == 1
        assert extract_json(text) is not None


def test_fix_variants_json_actually_fixes_the_scene():
    for s in SAMPLES:
        for variant in ("fix_full", "fix_direct"):
            rec = assemble(s, variant)
            data = extract_json(_answer(rec))
            fixed = apply_fix(Scene.from_dict(s["scene"]), data["fixed_pipes"], data["new_openings"])
            assert fixed is not None and check_all(fixed) == [], (s["id"], variant)


def test_detect_variant_lists_exactly_the_violations():
    for s in SAMPLES:
        data = extract_json(_answer(assemble(s, "detect_only")))
        got = sorted((v["type"], tuple(v["ids"])) for v in data["violations"])
        want = sorted((v["type"], tuple(v["ids"])) for v in s["violations"])
        assert got == want


def test_clean_scene_says_no_fix_needed():
    clean = [s for s in SAMPLES if not s["violations"]]
    assert clean
    for s in clean:
        assert extract_json(_answer(assemble(s, "fix_full")))["fixed_pipes"] == []
        assert "無需修正" in _answer(assemble(s, "fix_full"))


def test_only_changed_pipes_are_listed():
    for s in SAMPLES:
        data = extract_json(_answer(assemble(s, "fix_direct")))
        moved = {a["pipe_id"] for a in s["actions"]}
        assert {p["id"] for p in data["fixed_pipes"]} <= moved


def test_deterministic_and_variants_mixed():
    a = [json.dumps(assemble(s), sort_keys=True) for s in SAMPLES]
    b = [json.dumps(assemble(s), sort_keys=True) for s in SAMPLES]
    assert a == b
    assert {assemble(s)["meta"]["variant"] for s in SAMPLES} == set(VARIANTS)


def test_user_prompt_mentions_every_element():
    s = SAMPLES[0]
    text = assemble(s)["messages"][1]["content"]
    scene = Scene.from_dict(s["scene"])
    for w in scene.walls:
        assert f"牆 {w.id}" in text
    for p in scene.pipes:
        assert f"管線 {p.id}" in text
    assert describe_scene(scene) in text


def test_assemble_file_no_meta(tmp_path):
    raw, out = tmp_path / "raw.jsonl", tmp_path / "train.jsonl"
    raw.write_text("".join(json.dumps(s) + "\n" for s in SAMPLES[:5]), encoding="utf-8")
    assert assemble_file(raw, out, include_meta=False) == 5
    for line in out.read_text(encoding="utf-8").splitlines():
        assert list(json.loads(line)) == ["messages"]


def test_think_never_contradicts_actions():
    for s in SAMPLES:
        think = _answer(assemble(s, "fix_full")).split("</think>")[0]
        for a in s["actions"]:
            assert a["pipe_id"] in think


def test_reasons_only_quote_the_violations_listed_in_the_prompt():
    for s in SAMPLES:
        text = _answer(assemble(s, "fix_full"))
        if "理由：" not in text:
            continue
        reasons = text.split("理由：")[1].split("```json")[0]
        for v in s["violations"]:
            assert v["message"] in reasons
        # nothing but the original violations (no intermediate solver states)
        assert reasons.count("；") == len(s["violations"]) - 1


def test_new_wall_actions_are_described():
    from mep_sft.assembler import action_text
    from mep_sft.demo import opening
    from mep_sft.solver import solve as solve_scene
    res = solve_scene(opening())
    a = res.actions[0]
    d = {"kind": a.kind, "pipe_id": a.pipe_id, "detail": a.detail, "ids": a.ids}
    assert "開口" in action_text(opening(), res.fixed, d)


def test_new_opening_is_in_the_answer_and_it_fixes_the_scene():
    from mep_sft.demo import opening as opening_scene
    from mep_sft.solver import solve as solve_scene
    sc = opening_scene()
    sc.walls[0].openings = []
    sc.pipe("C").fixed_ends = True
    res = solve_scene(sc)
    raw = {
        "id": "x", "seed": 0, "difficulty": 1, "scene_hash": "h", "scene": sc.to_dict(),
        "violations": [v.to_dict() for v in __import__("mep_sft.rules", fromlist=["x"]).check_all(sc)],
        "fixed": res.fixed.to_dict(),
        "actions": [{"kind": a.kind, "pipe_id": a.pipe_id, "detail": a.detail, "resolved": a.resolved,
                     "vtype": a.vtype, "ids": a.ids, "skipped": a.skipped, "points": a.points,
                     "violations_before": a.violations_before, "violations_after": a.violations_after}
                    for a in res.actions],
    }
    text = _answer(assemble(raw, "fix_full"))
    assert "新增開口" in text and "牆 W1" in text
    data = extract_json(text)
    assert data["fixed_pipes"] == [] and len(data["new_openings"]) == 1
    fixed = apply_fix(sc, data["fixed_pipes"], data["new_openings"])
    assert fixed is not None and check_all(fixed) == []
    # the prompt tells the model which walls accept new openings
    assert "可新增開口" in assemble(raw, "fix_full")["messages"][1]["content"]


def test_apply_fix_rejects_openings_in_structural_walls_and_nonsense():
    from mep_sft.demo import opening as opening_scene
    sc = opening_scene()
    ok = {"wall": "W1", "u0": 1.0, "u1": 1.5, "z0": 0.0, "z1": 1.0}
    assert apply_fix(sc, [], [ok]) is not None
    sc.walls[0].allow_openings = False
    assert apply_fix(sc, [], [ok]) is None
    sc.walls[0].allow_openings = True
    for bad in ({**ok, "wall": "NOPE"}, {**ok, "u0": 2.0, "u1": 1.0}, {**ok, "u1": 99.0},
                {**ok, "z1": 9.0}, {"wall": "W1"}):
        assert apply_fix(sc, [], [bad]) is None


def test_stepwise_replay_ends_exactly_at_the_stored_solution():
    """The reasoning explains each step from its own before/after state, so replaying every action
    must land on the stored fixed scene (paths and wall openings)."""
    from mep_sft.assembler import _step

    checked = 0
    for s in SAMPLES:
        cur = Scene.from_dict(s["scene"])
        for a in s["actions"]:
            cur = _step(cur, a)
        fixed = Scene.from_dict(s["fixed"])
        assert [p.points for p in cur.pipes] == [p.points for p in fixed.pipes], s["id"]
        assert [w.openings for w in cur.walls] == [w.openings for w in fixed.walls], s["id"]
        checked += bool(s["actions"])
    assert checked > 20


def test_every_action_gets_its_arithmetic():
    """fix_full shows the calculation behind each step; the numbers are measured, not templated."""
    from mep_sft.assembler import _calc, _step

    need = {"clash": ("shift_z", "shift_xy", "jog_z", "jog_xy"), "clear_height": ("shift_z", "jog_z"),
            "drain_slope": ("reslope",), "wall_penetration": ("trim", "slide_wall", "add_opening")}
    seen = set()
    for s in SAMPLES:
        cur = Scene.from_dict(s["scene"])
        for a in s["actions"]:
            nxt = _step(cur, a)
            if a["kind"] in need.get(a["vtype"], ()):
                calc = _calc(cur, nxt, a)
                assert calc.startswith("計算："), (s["id"], a["vtype"], a["kind"])
                seen.add((a["vtype"], a["kind"]))
            cur = nxt
    assert {v for v, _ in seen} == set(need)  # every violation type was exercised


def test_calc_numbers_match_the_scene():
    from mep_sft.assembler import _calc, n
    from mep_sft.demo import two_pipes
    from mep_sft.rules import clash_measure
    from mep_sft.solver import solve as solve_scene

    sc = two_pipes()
    res = solve_scene(sc)
    a = res.actions[0]
    d = {"kind": a.kind, "pipe_id": a.pipe_id, "vtype": a.vtype, "ids": a.ids, "detail": a.detail,
         "resolved": a.resolved, "points": a.points}
    text = _calc(sc, res.fixed, d)
    before = clash_measure(sc.pipe("A"), sc.pipe("B"))[0]
    after = clash_measure(res.fixed.pipe("A"), res.fixed.pipe("B"))[0]
    assert before == pytest.approx(-0.075) and after == pytest.approx(0.075)
    assert f"外緣間距 {n(before)} m" in text and f"移動後外緣間距 {n(after)} m" in text
    assert "-0.075" in text and "0.075 m。" in text and "還差 0.125 m" in text
