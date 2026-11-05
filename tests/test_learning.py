import json

import pytest
from fastapi.testclient import TestClient

from mep_sft.demo import demo_scene, opening, slope
from mep_sft.explain import explain
from mep_sft.generator import generate_dataset
from mep_sft.rules import check_all
from mep_sft.scene import Scene
from mep_sft.server.app import create_app
from mep_sft.server.logic import canonical, replay_steps


def _v(scene):
    return [v.to_dict() for v in check_all(scene)]


def test_explain_clash_numbers_match_the_rule():
    scene = demo_scene()
    e = explain(scene, _v(scene)[0], "①")
    assert "-0.075" in e["calc"] and "0.175" in e["calc"] and "0.05" in e["calc"]
    assert "穿入" in e["meaning"] and "①" in e["title"]


def test_explain_slope_wall_and_clear():
    s = slope()
    assert "0%" in explain(s, _v(s)[0])["calc"] and "太平" in explain(s, _v(s)[0])["meaning"]
    o = opening()
    e = explain(o, _v(o)[0])
    assert "開口" in e["calc"] and "W1" in e["title"]
    o.walls[0].openings = []
    assert "沒有任何開口" in explain(o, _v(o)[0])["calc"]
    from mep_sft.rules import CLEAR
    scene = demo_scene()
    scene.pipe("A").points = [(1.0, 1.0, 2.2), (5.0, 1.0, 2.2)]
    clear = next(v for v in _v(scene) if v["type"] == CLEAR)
    assert "2.1" in explain(scene, clear)["calc"]  # 2.2 - 0.1


def test_every_generated_violation_is_explainable():
    samples, _ = generate_dataset(40, seed=900)
    for s in samples:
        scene = Scene.from_dict(s["scene"])
        for i, v in enumerate(s["violations"]):
            e = explain(scene, v, str(i))
            assert all(e[k] for k in ("title", "calc", "meaning", "look")), (s["id"], v["type"])


def test_replay_ends_at_the_stored_solution():
    samples, _ = generate_dataset(40, seed=900)
    checked = 0
    for s in samples:
        steps = replay_steps(s)
        assert steps[0]["violations"] == len(s["violations"])
        if not s["actions"]:
            assert len(steps) == 1
            continue
        checked += 1
        final = Scene.from_dict(s["fixed"])
        moved = [{"id": p.id, "points": [list(q) for q in p.points]}
                 for p in final.pipes
                 if p.points != Scene.from_dict(s["scene"]).pipe(p.id).points]
        assert canonical(steps[-1]["fixed_pipes"]) == canonical(moved)
        assert steps[-1]["violations"] == 0
        counts = [st["violations"] for st in steps]
        assert counts == sorted(counts, reverse=True)  # every step strictly helps
        assert all(st["highlight"][0] in st["text"] for st in steps[1:])
    assert checked > 10


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "r.db")))
    c.post("/api/generate", json={"n": 10, "seed": 0, "difficulty": 3})
    return c


def test_api_steps_explain_and_blind(client):
    items = client.get("/api/samples").json()
    with_issue = next(s for s in items if s["issue_types"])
    d = client.get(f"/api/samples/{with_issue['id']}").json()
    assert len(d["explain_before"]) == len(d["violations"]) > 0
    assert d["explain_before"][0]["title"].startswith("①")
    steps = client.get(f"/api/samples/{with_issue['id']}/steps").json()
    assert steps[0]["step"] == 0 and steps[-1]["violations"] == 0
    sid = with_issue["id"]
    normal = client.get(f"/api/samples/{sid}/figure").json()
    blind = client.get(f"/api/samples/{sid}/figure", params={"blind": "true"}).json()
    text = json.dumps(blind["layout"], ensure_ascii=False)
    assert "違規" not in text and "✗" not in text and "✓" not in text
    assert "違規" in json.dumps(normal["layout"], ensure_ascii=False)
    assert not any(t.get("name") == "違規" for t in blind["data"])


def test_preview_highlight_only_marks_given_pipes(client):
    items = client.get("/api/samples").json()
    s = next(x for x in items if x["issue_types"])
    d = client.get(f"/api/samples/{s['id']}").json()
    pipes = d["current"]["fixed_pipes"]
    r = client.post(f"/api/samples/{s['id']}/preview",
                    json={"fixed_pipes": pipes, "highlight": []})
    assert r.status_code == 200
    green = [t for t in r.json()["figure"]["data"] if t.get("color") == "#2ca02c"]
    assert green == []
