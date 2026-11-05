import json

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
            fixed = apply_fix(Scene.from_dict(s["scene"]), data["fixed_pipes"])
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
