import json
import re

from mep_sft.evaluate import evaluate, gold_predictions, reward, score
from mep_sft.generator import generate_dataset

RAW, _ = generate_dataset(60, seed=700)
BY_ID = {s["id"]: s for s in RAW}
GOLD = gold_predictions(RAW)


def _pred(pid):
    return next(p for p in GOLD if p["id"] == pid)


def test_gold_answers_pass_everything():
    rep = evaluate(BY_ID, GOLD)
    assert rep["pass_rate"] == 1.0 and rep["format_rate"] == 1.0
    assert rep["fix"]["introduced_new_violations"] == 0 and rep["fix"]["unnecessary_changes"] == 0
    assert rep["detect"]["precision"] == rep["detect"]["recall"] == 1.0
    assert all(reward(BY_ID[p["id"]], p["output"], p["variant"]) >= 0.99 for p in GOLD)


def _first(variant_pred):
    return next(p for p in GOLD if variant_pred(p) and BY_ID[p["id"]]["violations"])


def test_missing_json_fails_format_with_zero_reward():
    p = _first(lambda p: p["variant"] != "detect_only")
    broken = re.sub(r"```json.*?```", "", p["output"], flags=re.DOTALL)
    r = score(BY_ID[p["id"]], broken, p["variant"])
    assert not r["format_ok"] and not r["passed"]
    assert reward(BY_ID[p["id"]], broken, p["variant"]) == 0.0


def test_doing_nothing_on_a_broken_scene_fails_with_partial_format_credit():
    p = _first(lambda p: p["variant"] != "detect_only")
    nothing = '<think>\nok\n</think>\n```json\n{"fixed_pipes": []}\n```'
    r = score(BY_ID[p["id"]], nothing, p["variant"])
    assert r["format_ok"] and not r["passed"] and r["resolved_ratio"] == 0.0
    assert reward(BY_ID[p["id"]], nothing, p["variant"]) == 0.1


def test_partial_fix_gets_partial_credit():
    for p in GOLD:
        s = BY_ID[p["id"]]
        if p["variant"] == "detect_only" or len(s["violations"]) < 3:
            continue
        head, _, rest = p["output"].partition("```json\n")
        data = json.loads(rest.split("\n```")[0])
        if len(data["fixed_pipes"]) < 2:
            continue
        data["fixed_pipes"] = data["fixed_pipes"][:1]  # apply only the first change
        partial = head + "```json\n" + json.dumps(data) + "\n```"
        r = score(s, partial, p["variant"])
        assert 0 < r["resolved_ratio"] < 1 and not r["passed"]
        assert 0.1 < reward(s, partial, p["variant"]) < 0.9
        return
    raise AssertionError("no multi-fix sample found")


def test_unnecessary_change_on_clean_scene_is_flagged():
    clean = next(s for s in RAW if not s["violations"])
    pipe = clean["scene"]["pipes"][0]
    moved = [[x, y, round(z + 0.05, 4)] for x, y, z in pipe["points"]]
    payload = json.dumps({"fixed_pipes": [{"id": pipe["id"], "points": moved}]})
    out = "<think>\nx\n</think>\n```json\n" + payload + "\n```"
    r = score(clean, out, "fix_direct")
    assert r["format_ok"] and r["unnecessary_change"]


def test_detect_scores_precision_and_recall():
    s = next(x for x in RAW if len(x["violations"]) >= 2)
    first = s["violations"][0]
    listed = json.dumps({"violations": [{"type": first["type"], "ids": first["ids"]}]})
    out = "<think>\nx\n</think>\n```json\n" + listed + "\n```"
    r = score(s, out, "detect_only")
    assert r["precision"] == 1.0 and 0 < r["recall"] < 1 and not r["passed"]


def test_unknown_ids_are_counted_not_crashing():
    rep = evaluate(BY_ID, [{"id": "nope", "output": "x"}] + GOLD[:3])
    assert rep["unknown_ids"] == 1 and rep["n"] == 3
