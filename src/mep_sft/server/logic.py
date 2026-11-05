"""Pure review logic (no HTTP): answer validation and figure building."""

from __future__ import annotations

import json
import math
from dataclasses import replace

from ..answer import apply_fix, extract_json
from ..assembler import action_text, assemble, priority_sentence
from ..rules import check_all
from ..scene import Scene
from ..viz_plotly import build_figure


def canonical(fixed_pipes: list[dict]) -> list:
    return sorted((p["id"], [[round(c, 4) for c in q] for q in p["points"]]) for p in fixed_pipes)


def valid_payload(fixed_pipes) -> bool:
    try:
        return all(
            isinstance(p["id"], str)
            and len(p["points"]) >= 2
            and all(len(q) == 3 and all(math.isfinite(float(c)) for c in q) for q in p["points"])
            for p in fixed_pipes
        )
    except (KeyError, TypeError, ValueError):
        return False


def answer_status(sample: dict, version: dict) -> dict:
    """Does the stored assistant text really answer the scene? This gates approval."""
    scene, variant, text = Scene.from_dict(sample["scene"]), sample["variant"], version["assistant_text"]
    problems: list[str] = []
    stale = False
    if text.count("<think>") != 1 or text.count("</think>") != 1:
        problems.append("<think> 區塊必須恰好出現一次")
    data = extract_json(text)
    if data is None:
        problems.append("答案缺少可解析的 json 區塊")
    elif variant == "detect_only":
        got = sorted((v.get("type"), tuple(v.get("ids", []))) for v in data.get("violations", []))
        want = sorted((v["type"], tuple(v["ids"])) for v in sample["violations"])
        if got != want:
            problems.append("列出的違規與實際違規不符")
    else:
        pipes = data.get("fixed_pipes")
        fixed = apply_fix(scene, pipes) if isinstance(pipes, list) else None
        if fixed is None:
            problems.append("fixed_pipes 格式錯誤或含不存在的管線")
        else:
            left = check_all(fixed)
            if left:
                problems.append(f"答案套用後仍有 {len(left)} 項違規")
            stale = canonical(pipes) != canonical(version["fixed_pipes"])
            if stale:
                problems.append("文字內的答案與目前修正不一致（請重新產生文字）")
    return {"ok": not problems, "problems": problems, "stale": stale}


def figure_json(sample: dict, fixed_pipes: list[dict], highlight: list[str] | None = None,
                blind: bool = False) -> tuple[dict, list[dict]]:
    scene = Scene.from_dict(sample["scene"])
    if blind:  # quiz: the bare scene, no markers, counts or dimensions
        fig = build_figure(scene, [], scene, [], set(), title=sample["id"], blind=True)
        return json.loads(fig.to_json()), []
    fixed = apply_fix(scene, fixed_pipes)
    if fixed is None:
        raise ValueError("fixed_pipes 格式錯誤或含不存在的管線")
    before, after = check_all(scene), check_all(fixed)
    moved = set(highlight) if highlight is not None else {p["id"] for p in fixed_pipes}
    fig = build_figure(scene, before, fixed, after, moved, title=sample["id"])
    return json.loads(fig.to_json()), [v.to_dict() for v in after]


def replay_steps(sample: dict) -> list[dict]:
    """The solver's actions as cumulative states, for the step-by-step tour."""
    scene = Scene.from_dict(sample["scene"])
    steps = [{
        "step": 0, "text": f"原始場景：共 {len(sample['violations'])} 項違規。",
        "fixed_pipes": [], "violations": len(sample["violations"]), "highlight": [],
    }]
    cur, cum = scene, {}
    for i, a in enumerate(sample.get("actions", []), 1):
        if not a.get("points"):
            return steps[:1]  # sample predates trace points: nothing to replay
        pts = [tuple(q) for q in a["points"]]
        nxt = replace(cur, pipes=[replace(p, points=pts) if p.id == a["pipe_id"] else p
                                  for p in cur.pipes])
        why = priority_sentence(cur, a)
        cum[a["pipe_id"]] = pts
        steps.append({
            "step": i, "text": (why + "：" if why else "") + action_text(cur, nxt, a) + "。",
            "fixed_pipes": [{"id": k, "points": [list(q) for q in v]} for k, v in cum.items()],
            "violations": a["violations_after"], "highlight": [a["pipe_id"]],
            "resolved": a["resolved"],
        })
        cur = nxt
    return steps


def regenerate_text(sample: dict, fixed_pipes: list[dict]) -> str:
    """Assistant text for a hand-edited answer (no solver trace, so it describes the result)."""
    scene = Scene.from_dict(sample["scene"])
    fixed = apply_fix(scene, fixed_pipes)
    raw = {
        "id": sample["id"], "seed": sample["seed"], "difficulty": sample["difficulty"],
        "scene_hash": sample["scene_hash"], "scene": sample["scene"],
        "violations": sample["violations"], "fixed": fixed.to_dict(), "actions": [],
    }
    return assemble(raw, sample["variant"])["messages"][2]["content"]
