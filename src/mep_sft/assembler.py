"""Turn (problem scene, solver result) into chat-format SFT records.

The <think> text is templated from the solver's trace, so reasoning and answer can never disagree.
Phrasing is varied by a per-sample seeded RNG, so output is still reproducible.
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

from .answer import render_block
from .scene import SYSTEM_PRIORITY, Scene

SYSTEM_ZH = {"drainage": "排水", "duct": "風管", "water": "給水", "electrical": "電氣"}
TYPE_ZH = {
    "clash": "管線碰撞", "wall_penetration": "穿牆", "clear_height": "淨高不足",
    "drain_slope": "排水坡度錯誤",
}
VARIANTS = {"fix_full": 0.6, "fix_direct": 0.2, "detect_only": 0.2}

SYSTEM_PROMPTS = {
    "fix_full": "你是 MEP 協調助手。先思考再給出最終修正結果。輸出必須是合法的結構化描述，"
                "優先遵守硬性規則（不可穿牆、不可碰撞、符合淨高與坡度）。",
    "fix_direct": "你是 MEP 協調助手。請簡短檢查後直接給出修正結果，"
                  "結果必須遵守硬性規則（不可穿牆、不可碰撞、符合淨高與坡度）。",
    "detect_only": "你是 MEP 協調助手。你的任務是檢查場景並列出所有違規，不需要提出修正。",
}


def n(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x else "0"


def pt(p) -> str:
    return "(" + ",".join(n(c) for c in p) + ")"


def path(points) -> str:
    return " → ".join(pt(p) for p in points)


# ------------------------------------------------------------------ user message

def describe_scene(scene: Scene) -> str:
    r = scene.rules
    lines = ["目前空間狀態："]
    for w in scene.walls:
        extra = ""
        if w.openings:
            ops = "；".join(
                f"沿牆 {n(o.u0)}~{n(o.u1)} m、標高 {n(o.z0)}~{n(o.z1)} m" for o in w.openings
            )
            extra = f"，開口：{ops}"
        lines.append(
            f"- 牆 {w.id}：從 ({n(w.p0[0])},{n(w.p0[1])}) 到 ({n(w.p1[0])},{n(w.p1[1])})，"
            f"厚 {n(w.thickness)} m，高 {n(w.height)} m{extra}"
        )
    for p in scene.pipes:
        flags = f"，保溫厚 {n(p.insulation)} m" if p.insulation else ""
        flags += "，兩端固定（接設備，不可整條平移）" if p.fixed_ends else ""
        lines.append(
            f"- 管線 {p.id}（{SYSTEM_ZH[p.system]}）：{path(p.points)}，直徑 {n(p.diameter)} m{flags}"
        )
    lines.append(
        f"規則：管底標高 ≥ {n(r.min_clear_height)} m 且管頂 ≤ 天花板 {n(r.ceiling_height)} m；"
        f"管線外緣間距 ≥ {n(r.clearance)} m；不可穿牆（開口除外）；"
        f"排水管由第一點往最後一點下降，坡度 {n(r.min_drain_slope * 100)}%~"
        f"{n(r.max_drain_slope * 100)}%。座標單位為公尺，Z 向上。"
    )
    return "\n".join(lines)


TASKS = {
    "fix_full": "請找出並修正所有違規，最後以 ```json 區塊輸出有修改的管線完整路徑，"
                '格式：{"fixed_pipes":[{"id":"管線編號","points":[[x,y,z],...]}]}，並說明理由。'
                "若無需修正，fixed_pipes 給空陣列。",
    "fix_direct": "請直接修正所有違規，以 ```json 區塊輸出有修改的管線完整路徑，"
                  '格式：{"fixed_pipes":[{"id":"管線編號","points":[[x,y,z],...]}]}。'
                  "若無需修正，fixed_pipes 給空陣列。",
    "detect_only": "請只列出所有違規，不要修正。最後以 ```json 區塊輸出，"
                   '格式：{"violations":[{"type":"clash|wall_penetration|clear_height|'
                   'drain_slope","ids":["涉及的編號"]}]}。無違規時 violations 給空陣列。',
}


# ------------------------------------------------------------------ reasoning text

def _priority_sentence(scene: Scene, a: dict) -> str:
    """Why this pipe ends up being the one that moves (who was passed over, what was tried)."""
    pid = a["pipe_id"]
    by_pipe: dict[str, list[dict]] = {}
    for s in a["skipped"]:
        by_pipe.setdefault(s["pipe_id"], []).append(s)

    def facts(items: list[dict]) -> str:
        bits = []
        if any(s["reason"] == "fixed_ends" for s in items):
            bits.append("兩端固定，不可整條平移")
        tried = "、".join(
            STRATEGY_ZH[s["strategy"]] for s in items if s["reason"] == "no_improvement"
        )
        if tried:
            bits.append(f"嘗試{tried}仍無法消除違規")
        return "，".join(bits)

    others = [f"{o}：{facts(items)}" for o, items in by_pipe.items() if o != pid and facts(items)]
    own = facts(by_pipe.get(pid, []))
    if others:
        head = "；".join(others)
        return f"{head}；因此改動 {pid}" + (f"（{pid} {own}）" if own else "")
    if own:
        return f"{pid} {own}"
    if a["vtype"] == "clash":
        pipes = [i for i in a["ids"] if any(p.id == i for p in scene.pipes)]
        other = next((i for i in pipes if i != pid), None)
        if other:
            sp, so = scene.pipe(pid).system, scene.pipe(other).system
            if SYSTEM_PRIORITY[sp] != SYSTEM_PRIORITY[so]:
                return f"{SYSTEM_ZH[sp]}（{pid}）優先級低於{SYSTEM_ZH[so]}（{other}），由 {pid} 讓位"
            return f"{pid} 與 {other} 同系統，依編號由 {pid} 讓位"
    return ""


STRATEGY_ZH = {
    "shift_z": "垂直位移", "shift_xy": "水平平移", "jog_z": "垂直繞道", "jog_xy": "水平繞道",
    "reslope": "調整坡度",
}


def _dir(v: float, pos: str, neg: str) -> str:
    return pos if v > 0 else neg


def _action_text(scene: Scene, fixed: Scene, a: dict) -> str:
    pid, d = a["pipe_id"], a["detail"]
    before, after = scene.pipe(pid).points, fixed.pipe(pid).points
    k = a["kind"]
    if k == "shift_z":
        return f"將 {pid} 整條{_dir(d['dz'], '上移', '下移')} {n(abs(d['dz']))} m"
    if k == "shift_xy":
        moves = []
        if d["dx"]:
            moves.append(f"{_dir(d['dx'], '往 +X', '往 -X')} 平移 {n(abs(d['dx']))} m")
        if d["dy"]:
            moves.append(f"{_dir(d['dy'], '往 +Y', '往 -Y')} 平移 {n(abs(d['dy']))} m")
        return f"將 {pid} 整條{'、'.join(moves)}"
    if k in ("jog_z", "jog_xy"):
        where = "上方" if k == "jog_z" and d["offset"] > 0 else "下方" if k == "jog_z" else "側邊"
        return (
            f"在 {pt(d['at'])} 附近讓 {pid} 從{where}繞過，繞道偏移 {n(abs(d['offset']))} m，"
            f"兩端 {pt(before[0])}、{pt(before[-1])} 保持不動"
        )
    if k == "reslope":
        run_pts = next(
            (q, r) for q, r in itertools.pairwise(after)
            if (r[0] - q[0]) ** 2 + (r[1] - q[1]) ** 2 > 1e-12
        )
        run = ((run_pts[1][0] - run_pts[0][0]) ** 2 + (run_pts[1][1] - run_pts[0][1]) ** 2) ** 0.5
        slope = n(round((run_pts[0][2] - run_pts[1][2]) / run * 100, 2))
        if d["mode"] == "lower_downstream":
            return f"維持 {pid} 上游端高度，將下游端降到 Z={n(after[-1][2])}，坡度 {slope}%"
        return f"維持 {pid} 下游端高度，將上游端抬高到 Z={n(after[0][2])}，坡度 {slope}%"
    raise ValueError(k)


def _think(sample: dict, scene: Scene, fixed: Scene, rng: random.Random, short: bool) -> str:
    vs = sample["violations"]
    lines = []
    if not vs:
        lines.append(
            rng.choice((
                "逐項檢查碰撞、穿牆、淨高與排水坡度，所有管線都符合規則。",
                "依序檢查管線間距、牆體、淨高與坡度，沒有發現違規。",
            ))
        )
        lines.append("結論：無需修正。")
        return "\n".join(lines)
    lines.append(rng.choice(("逐項檢查後發現以下問題：", "先檢查所有硬性規則，發現：")))
    for i, v in enumerate(vs, 1):
        lines.append(f"{i}. {TYPE_ZH[v['type']]}：{v['message']}")
    if short:
        return "\n".join(lines)
    changed = _changed(scene, fixed)
    if sample["actions"] or changed:
        lines.append("處理方式：")
    if not sample["actions"]:  # hand-edited answer: no solver trace, describe the result
        for p in changed:
            lines.append(f"- 將 {p.id} 改為 {path(p.points)}。")
    for a in sample["actions"]:
        why = _priority_sentence(scene, a)
        gain = a["violations_before"] - a["violations_after"]
        extra = f"，一次消除 {gain} 項違規" if gain > 1 else ""
        lines.append(f"- {why + '：' if why else ''}{_action_text(scene, fixed, a)}{extra}。")
    lines.append(
        rng.choice(("重新檢查修正後的場景：", "套用後再驗證一次："))
        + f"違規由 {len(vs)} 項降為 0。"
    )
    return "\n".join(lines)


def _changed(scene: Scene, fixed: Scene) -> list:
    old = {p.id: p.points for p in scene.pipes}
    return [p for p in fixed.pipes if p.points != old[p.id]]


def fixed_pipes_payload(scene: Scene, fixed: Scene) -> list[dict]:
    """Only the pipes whose path differs, in the answer's json shape."""
    return [{"id": p.id, "points": [list(q) for q in p.points]} for p in _changed(scene, fixed)]


def _violation_list(vs: list[dict]) -> str:
    return "\n".join(f"- {TYPE_ZH[v['type']]}（{'、'.join(v['ids'])}）：{v['message']}" for v in vs)


def _fix_answer(sample: dict, scene: Scene, fixed: Scene, with_reason: bool) -> str:
    changed = _changed(scene, fixed)
    if not changed:
        return "檢查結果：無需修正，所有管線維持原位。\n" + render_block({"fixed_pipes": []})
    lines = ["修正結果："]
    for p in changed:
        lines.append(f"- 管線 {p.id}：改為 {path(p.points)}")
    lines.append(f"- 其餘 {len(scene.pipes) - len(changed)} 條管線維持原位")
    if with_reason:
        seen, reasons = set(), []
        for msg in [a["resolved"] for a in sample["actions"]] or [
            v["message"] for v in sample["violations"]
        ]:
            if msg not in seen:
                seen.add(msg)
                reasons.append(msg)
        lines.append("理由：" + "；".join(reasons) + "。上述修改後，重新檢查已無任何違規。")
    return "\n".join(lines) + "\n" + render_block({"fixed_pipes": fixed_pipes_payload(scene, fixed)})


def _detect_answer(sample: dict) -> str:
    vs = sample["violations"]
    if not vs:
        return "檢查結果：未發現違規。\n" + render_block({"violations": []})
    payload = {"violations": [{"type": v["type"], "ids": v["ids"]} for v in vs]}
    return f"共發現 {len(vs)} 項違規：\n{_violation_list(vs)}\n" + render_block(payload)


# ------------------------------------------------------------------ public API

def prompt_messages(scene: Scene, variant: str) -> list[dict]:
    """The system + user turns; they depend only on the scene and the task variant."""
    return [
        {"role": "system", "content": SYSTEM_PROMPTS[variant]},
        {"role": "user", "content": describe_scene(scene) + "\n" + TASKS[variant]},
    ]


def pick_variant(sample: dict) -> str:
    rng = random.Random(f"variant:{sample['id']}")
    return rng.choices(list(VARIANTS), list(VARIANTS.values()))[0]


def assemble(sample: dict, variant: str | None = None, include_meta: bool = True) -> dict:
    variant = variant or pick_variant(sample)
    scene, fixed = Scene.from_dict(sample["scene"]), Scene.from_dict(sample["fixed"])
    rng = random.Random(f"asm:{sample['id']}:{variant}")
    think = _think(sample, scene, fixed, rng, short=variant != "fix_full")
    if variant == "detect_only":
        answer = _detect_answer(sample)
    else:
        answer = _fix_answer(sample, scene, fixed, with_reason=variant == "fix_full")
    record = {
        "messages": prompt_messages(scene, variant)
        + [{"role": "assistant", "content": f"<think>\n{think}\n</think>\n{answer}"}]
    }
    if include_meta:
        record["meta"] = {
            "id": sample["id"], "seed": sample["seed"], "difficulty": sample["difficulty"],
            "variant": variant, "issue_types": sorted({v["type"] for v in sample["violations"]}),
            "scene_hash": sample["scene_hash"], "source": "auto",
        }
    return record


def assemble_file(raw: Path, out: Path, include_meta: bool = True) -> int:
    count = 0
    out.parent.mkdir(parents=True, exist_ok=True)
    with raw.open(encoding="utf-8") as f, out.open("w", encoding="utf-8") as w:
        for line in f:
            rec = assemble(json.loads(line), include_meta=include_meta)
            w.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


# public names for the review server's step-by-step replay
action_text = _action_text
priority_sentence = _priority_sentence
