"""Hard-rule checker. The single source of truth for 'is this scene valid'."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .geometry import Vec, segment_box_intersect, segment_distance
from .scene import Pipe, Scene, Wall, r4

CLASH = "clash"
WALL = "wall_penetration"
CLEAR = "clear_height"
SLOPE = "drain_slope"


@dataclass
class Violation:
    type: str
    ids: list[str]  # element ids involved, pipes first
    value: float  # measured value
    threshold: float  # required value
    location: Vec
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _segments(p: Pipe):
    return list(zip(p.points[:-1], p.points[1:]))


def clash_measure(a: Pipe, b: Pipe) -> tuple[float, Vec, Vec]:
    """Surface gap between two pipes plus the closest centreline points (gap < 0 = overlap)."""
    best = None
    for sa in _segments(a):
        for sb in _segments(b):
            d, c1, c2 = segment_distance(*sa, *sb)
            if best is None or d < best[0]:
                best = (d, c1, c2)
    return best[0] - a.outer_radius - b.outer_radius, best[1], best[2]


def check_clashes(scene: Scene) -> list[Violation]:
    out = []
    pipes = scene.pipes
    for i, a in enumerate(pipes):
        for b in pipes[i + 1 :]:
            gap, c1, c2 = clash_measure(a, b)
            if gap < scene.rules.clearance - 1e-9:
                loc = tuple(r4((u + v) / 2) for u, v in zip(c1, c2, strict=True))
                out.append(
                    Violation(
                        CLASH, [a.id, b.id], r4(gap), scene.rules.clearance, loc,
                        f"{a.id} 與 {b.id} 外緣間距 {r4(gap)} m，低於要求 {scene.rules.clearance} m",
                    )
                )
    return out


def _wall_local(w: Wall, pt: Vec) -> Vec:
    """World point -> wall frame (x along wall, y across thickness, z up)."""
    dx, dy = w.p1[0] - w.p0[0], w.p1[1] - w.p0[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    rx, ry = pt[0] - w.p0[0], pt[1] - w.p0[1]
    return (rx * ux + ry * uy, -rx * uy + ry * ux, pt[2])


def check_walls(scene: Scene) -> list[Violation]:
    out = []
    for w in scene.walls:
        length = math.hypot(w.p1[0] - w.p0[0], w.p1[1] - w.p0[1])
        for p in scene.pipes:
            r = p.outer_radius
            lo = (0.0, -w.thickness / 2 - r, w.z0 - r)
            hi = (length, w.thickness / 2 + r, w.z0 + w.height + r)
            for a, b in _segments(p):
                la, lb = _wall_local(w, a), _wall_local(w, b)
                hit = segment_box_intersect(la, lb, lo, hi)
                if hit:
                    t = (hit[0] + hit[1]) / 2
                    cx = tuple(la[i] + (lb[i] - la[i]) * t for i in range(3))
                    if any(
                        o.u0 + r <= cx[0] <= o.u1 - r and o.z0 + r <= cx[2] <= o.z1 - r
                        for o in w.openings
                    ):
                        continue  # passes cleanly through an opening
                    loc = tuple(r4(a[i] + (b[i] - a[i]) * t) for i in range(3))
                    out.append(
                        Violation(
                            WALL, [p.id, w.id], 0.0, 0.0, loc,
                            f"{p.id} 穿過牆 {w.id}（無開口）",
                        )
                    )
                    break
    return out


def check_clear_height(scene: Scene) -> list[Violation]:
    out = []
    lo, hi = scene.rules.min_clear_height, scene.rules.ceiling_height
    for p in scene.pipes:
        r = p.outer_radius
        low = min(pt[2] for pt in p.points)
        high = max(pt[2] for pt in p.points)
        if low - r < lo - 1e-9:
            pt = min(p.points, key=lambda q: q[2])
            out.append(
                Violation(
                    CLEAR, [p.id], r4(low - r), lo, pt,
                    f"{p.id} 管底標高 {r4(low - r)} m，低於淨高要求 {lo} m",
                )
            )
        elif high + r > hi + 1e-9:
            pt = max(p.points, key=lambda q: q[2])
            out.append(
                Violation(
                    CLEAR, [p.id], r4(high + r), hi, pt,
                    f"{p.id} 管頂標高 {r4(high + r)} m，超過天花板 {hi} m",
                )
            )
    return out


def check_slopes(scene: Scene) -> list[Violation]:
    out = []
    lo, hi = scene.rules.min_drain_slope, scene.rules.max_drain_slope
    for p in scene.pipes:
        if p.system != "drainage":
            continue
        for a, b in _segments(p):
            run = math.hypot(b[0] - a[0], b[1] - a[1])
            if run < 1e-6:  # vertical drop, no slope to check
                continue
            slope = (a[2] - b[2]) / run
            if slope < lo - 1e-9 or slope > hi + 1e-9:
                bound = lo if slope < lo else hi
                loc = tuple(r4((u + v) / 2) for u, v in zip(a, b, strict=True))
                out.append(
                    Violation(
                        SLOPE, [p.id], r4(slope), bound, loc,
                        f"{p.id} 坡度 {r4(slope * 100)}%，不符合要求"
                        f" {r4(lo * 100)}%~{r4(hi * 100)}%",
                    )
                )
                break
    return out


def check_all(scene: Scene) -> list[Violation]:
    vs = (
        check_clashes(scene) + check_walls(scene) + check_clear_height(scene) + check_slopes(scene)
    )
    return sorted(vs, key=lambda v: (v.type, v.ids))
