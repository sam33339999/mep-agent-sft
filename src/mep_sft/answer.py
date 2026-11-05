"""The machine-readable part of an assistant answer: a fenced ```json block of fixed pipes.

Shared by the assembler (writes it), the tests and the evaluator (reads it back and re-checks the
scene with the same hard rules).
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

from .scene import Pipe, Scene

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


def apply_fix(scene: Scene, fixed_pipes: list[dict]) -> Scene | None:
    """Scene with the listed pipes' paths replaced; None if the payload is malformed."""
    by_id = {p.id: p for p in scene.pipes}
    new: dict[str, Pipe] = {}
    try:
        for item in fixed_pipes:
            pid = item["id"]
            pts = [tuple(float(c) for c in pt) for pt in item["points"]]
            if pid not in by_id or len(pts) < 2 or any(len(pt) != 3 for pt in pts):
                return None
            new[pid] = replace(by_id[pid], points=pts)
    except (KeyError, TypeError, ValueError):
        return None
    return replace(scene, pipes=[new.get(p.id, p) for p in scene.pipes])
