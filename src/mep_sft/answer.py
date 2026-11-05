"""The machine-readable part of an assistant answer: a fenced ```json block of fixed pipes.

Shared by the assembler (writes it), the tests and the evaluator (reads it back and re-checks the
scene with the same hard rules).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace

from .scene import Opening, Pipe, Scene

FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def render_block(payload: dict) -> str:
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def extract_json(text: str) -> dict | None:
    """The last fenced json block after any <think> section, parsed; None if absent or invalid."""
    body = text.split("</think>")[-1]
    blocks = FENCE.findall(body)
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_fix(scene: Scene, fixed_pipes: list[dict], new_openings: list[dict] | None = None
              ) -> Scene | None:
    """Scene with the listed pipes' paths replaced and the listed openings cut into walls.

    None if the payload is malformed, names unknown elements, or asks for an opening in a wall
    that does not allow one.
    """
    by_id = {p.id: p for p in scene.pipes}
    new: dict[str, Pipe] = {}
    try:
        for item in fixed_pipes:
            pid = item["id"]
            pts = [tuple(float(c) for c in pt) for pt in item["points"]]
            if pid not in by_id or len(pts) < 2 or any(len(pt) != 3 for pt in pts):
                return None
            new[pid] = replace(by_id[pid], points=pts)
        walls = list(scene.walls)
        for o in new_openings or []:
            idx = next((i for i, w in enumerate(walls) if w.id == o["wall"]), None)
            vals = [float(o[k]) for k in ("u0", "u1", "z0", "z1")]
            if idx is None or not walls[idx].allow_openings or not all(map(math.isfinite, vals)):
                return None
            w = walls[idx]
            length = math.hypot(w.p1[0] - w.p0[0], w.p1[1] - w.p0[1])
            u0, u1, z0, z1 = vals
            if not (0 <= u0 < u1 <= length + 1e-9 and w.z0 - 1e-9 <= z0 < z1 <= w.z0 + w.height + 1e-9):
                return None
            walls[idx] = replace(w, openings=[*w.openings, Opening(u0, u1, z0, z1)])
    except (KeyError, TypeError, ValueError):
        return None
    return replace(scene, pipes=[new.get(p.id, p) for p in scene.pipes], walls=walls)


def new_openings_payload(scene: Scene, fixed: Scene) -> list[dict]:
    """Openings present in `fixed` but not in `scene`, in the answer's json shape."""
    out = []
    for w0, w1 in zip(scene.walls, fixed.walls, strict=True):
        for o in w1.openings:
            if o not in w0.openings:
                out.append({"wall": w1.id, "u0": o.u0, "u1": o.u1, "z0": o.z0, "z1": o.z1})
    return out
