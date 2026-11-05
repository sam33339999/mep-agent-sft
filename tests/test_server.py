import json

import pytest
from fastapi.testclient import TestClient

from mep_sft.server.app import create_app


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "review.db")))
    r = c.post("/api/generate", json={"n": 12, "seed": 0, "difficulty": 2})
    assert r.json()["added"] == 12
    return c


def _ids(c, status="pending"):
    return [s["id"] for s in c.get("/api/samples", params={"status": status}).json()]


def _fix_sample(c):
    """A pending sample whose answer is an actual fix (not a detect-only task)."""
    for s in c.get("/api/samples").json():
        d = c.get(f"/api/samples/{s['id']}").json()
        if s["variant"] != "detect_only" and d["current"]["fixed_pipes"]:
            return d
    raise AssertionError("no fix sample")


def test_generate_is_idempotent(client):
    r = client.post("/api/generate", json={"n": 12, "seed": 0, "difficulty": 2}).json()
    assert r["added"] == 0 and r["duplicates"] == 12


def test_index_and_stats(client):
    assert "plotly" in client.get("/").text
    assert client.get("/api/stats").json()["by_status"] == {"pending": 12}


def test_detail_has_figure_and_check(client):
    d = _fix_sample(client)
    assert d["status_check"]["ok"] and d["violations_after"] == []
    fig = client.get(f"/api/samples/{d['id']}/figure").json()
    assert fig["data"] and fig["layout"]


def test_approve_then_export(client):
    sid = _ids(client)[0]
    assert client.post(f"/api/samples/{sid}/review", json={"decision": "approve"}).json() == "approved"
    lines = client.get("/api/export").text.splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["meta"]["id"] == sid and rec["meta"]["source"] == "auto"
    assert [m["role"] for m in rec["messages"]] == ["system", "user", "assistant"]


def test_rejected_and_pending_are_not_exported(client):
    a, b = _ids(client)[:2]
    client.post(f"/api/samples/{a}/review", json={"decision": "reject", "reason": "bad"})
    assert client.get("/api/export").text == ""
    assert client.get("/api/stats").json()["by_status"]["rejected"] == 1
    assert b in _ids(client)


def test_bad_edit_cannot_be_approved_without_override(client):
    d = _fix_sample(client)
    sid = d["id"]
    pipes = d["current"]["fixed_pipes"]
    broken = [{"id": pipes[0]["id"], "points": [[x, y, 2.0] for x, y, _ in pipes[0]["points"]]}]
    prev = client.post(f"/api/samples/{sid}/preview", json={"fixed_pipes": broken}).json()
    assert prev["violations_after"]  # live validation sees the problem
    v = client.post(f"/api/samples/{sid}/versions", json={"fixed_pipes": broken}).json()
    assert v["version"] == 2 and v["status_check"]["ok"] is False
    r = client.post(f"/api/samples/{sid}/review", json={"decision": "approve"})
    assert r.status_code == 409
    r = client.post(f"/api/samples/{sid}/review", json={"decision": "approve", "override": True})
    assert r.status_code == 422  # override needs a reason
    r = client.post(f"/api/samples/{sid}/review",
                    json={"decision": "approve", "override": True, "reason": "demo"})
    assert r.status_code == 200
    assert client.get("/api/stats").json()["by_status"]["approved"] == 1


def test_valid_human_edit_regenerates_text_and_is_marked_human(client):
    d = _fix_sample(client)
    sid, pipes = d["id"], d["current"]["fixed_pipes"]
    # lift the first moved pipe a hair: may or may not stay valid, but text must follow the edit
    edited = [dict(p) for p in pipes]
    edited[0] = {"id": pipes[0]["id"],
                 "points": [[x, y, z + 0.0] for x, y, z in pipes[0]["points"]]}
    edited[0]["points"][0][2] = round(edited[0]["points"][0][2] + 0.05, 4)
    r = client.post(f"/api/samples/{sid}/versions", json={"fixed_pipes": edited})
    assert r.status_code == 200
    cur = client.get(f"/api/samples/{sid}").json()["current"]
    assert cur["source"] == "human"
    assert str(edited[0]["points"][0][2]) in cur["assistant_text"] or "0.05" in cur["assistant_text"]
    assert client.get(f"/api/samples/{sid}").json()["status_check"]["stale"] is False


def test_editing_after_approval_withdraws_it(client):
    d = _fix_sample(client)
    sid = d["id"]
    client.post(f"/api/samples/{sid}/review", json={"decision": "approve"})
    client.post(f"/api/samples/{sid}/versions", json={"assistant_text": d["current"]["assistant_text"] + " "})
    assert client.get(f"/api/samples/{sid}").json()["status"] == "pending"
    assert client.get("/api/export").text == ""


def test_text_with_stale_answer_is_flagged(client):
    d = _fix_sample(client)
    sid, pipes = d["id"], d["current"]["fixed_pipes"]
    moved = [{"id": pipes[0]["id"], "points": [[x, y, z + 0.05] for x, y, z in pipes[0]["points"]]}]
    client.post(f"/api/samples/{sid}/versions", json={"fixed_pipes": moved, "regenerate_text": False})
    chk = client.get(f"/api/samples/{sid}").json()["status_check"]
    assert chk["stale"] and not chk["ok"]


def test_export_can_exclude_human(client):
    d = _fix_sample(client)
    sid = d["id"]
    client.post(f"/api/samples/{sid}/versions", json={"assistant_text": d["current"]["assistant_text"] + "\n"})
    client.post(f"/api/samples/{sid}/review", json={"decision": "approve"})
    assert json.loads(client.get("/api/export").text)["meta"]["source"] == "human"
    assert client.get("/api/export", params={"include_human": "false"}).text == ""


def test_malformed_payloads_rejected(client):
    sid = _ids(client)[0]
    assert client.post(f"/api/samples/{sid}/preview", json={"fixed_pipes": [{"id": "NOPE", "points": [[0, 0, 0], [1, 1, 1]]}]}).status_code == 422
    assert client.post(f"/api/samples/{sid}/versions", json={"fixed_pipes": [{"id": "P1"}]}).status_code == 422
    assert client.get("/api/samples/missing").status_code == 404


def test_viewer_page_is_served_and_reads_the_json_api(client):
    html = client.get("/viewer").text
    assert "three" in html and "OrbitControls" in html and "/api/samples/" in html
    d = _fix_sample(client)
    # everything the viewer draws comes from this one payload
    assert d["scene"]["walls"] and d["fixed_scene"]["pipes"] and "violations" in d
    assert all(len(p["points"]) >= 2 for p in d["fixed_scene"]["pipes"])
