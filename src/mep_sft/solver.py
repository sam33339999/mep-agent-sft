"""Deterministic solver.

Strategy ladder per violation, for the lowest-priority pipe involved first:
rigid vertical shift -> rigid lateral shift -> vertical jog -> lateral jog; drainage slope errors
use re-sloping. A candidate is accepted only if it strictly lowers the total violation count,
so the loop terminates and the result is always re-verified by the same rules.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from itertools import pairwise

from .rules import CLASH, CLEAR, SLOPE, WALL, Violation, _wall_local, check_all, recheck
from .scene import SYSTEM_PRIORITY, Opening, Pipe, Scene, r4

STEP = 0.05
MAX_SHIFT = 1.5
MAX_JOG = 1.0
MAX_ITER = 25
BEAM_WIDTH = 4  # fallback search: states kept per depth
BEAM_PER_STRATEGY = 2  # best candidates kept per (pipe, strategy)
BEAM_NODES = 60  # states expanded at most (a budget, so hopeless scenes fail fast)
TARGET_SLOPE = 0.02
SIDESTEP_SLACK = 3  # extra violations a headroom/slope sidestep may cause


def _ladder(max_mag: float) -> list[float]:
    return [s * k * STEP for k in range(1, round(max_mag / STEP) + 1) for s in (1, -1)]


SHIFT_Z = _ladder(MAX_SHIFT)  # smallest |dz| first; ties prefer up
JOG_OFFSETS = _ladder(MAX_JOG)
SHIFT_XY = [
    (sx * k * STEP, sy * k * STEP)
    for k in range(1, round(MAX_SHIFT / STEP) + 1)
    for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1))
]


@dataclass
class Action:
    kind: str  # shift_z | shift_xy | jog_z | jog_xy | reslope | slide_wall | trim | add_opening
    pipe_id: str
    detail: dict
    resolved: str  # message of the violation that triggered it
    violations_before: int
    violations_after: int
    vtype: str = ""  # type of the violation that triggered it
    ids: list[str] = field(default_factory=list)  # elements involved in that violation
    # strategies passed over first: {"pipe_id", "strategy", "reason": fixed_ends | no_improvement}
    skipped: list[dict] = field(default_factory=list)
    points: list = field(default_factory=list)  # the pipe's full path after this action


@dataclass
class SolveResult:
    ok: bool
    fixed: Scene
    actions: list[Action] = field(default_factory=list)
    remaining: list[Violation] = field(default_factory=list)


def _with_points(scene: Scene, pid: str, pts: list) -> Scene:
    pipes = [replace(p, points=pts) if p.id == pid else p for p in scene.pipes]
    return replace(scene, pipes=pipes)


def _snap_up(x: float) -> float:
    return r4(math.ceil(x / STEP - 1e-9) * STEP)


def _movers(scene: Scene, v: Violation) -> list[str]:
    ids = [i for i in v.ids if any(p.id == i for p in scene.pipes)]
    # lowest-priority system moves first; ties broken by id
    return sorted(ids, key=lambda i: (-SYSTEM_PRIORITY[scene.pipe(i).system], i))


# ------------------------------------------------------------------ candidate generators
# Each yields (detail, new_points) in a fixed order: smallest change first.

def _shift_z(p: Pipe, v: Violation, scene: Scene):
    for dz in SHIFT_Z:
        yield {"dz": r4(dz)}, [(x, y, r4(z + dz)) for x, y, z in p.points]


def _shift_xy(p: Pipe, v: Violation, scene: Scene):
    for dx, dy in SHIFT_XY:
        yield {"dx": r4(dx), "dy": r4(dy)}, [(r4(x + dx), r4(y + dy), z) for x, y, z in p.points]


def _nearest_segment(p: Pipe, loc) -> tuple[int, float]:
    best = None
    for i, (a, b) in enumerate(zip(p.points[:-1], p.points[1:], strict=True)):
        d = [b[k] - a[k] for k in range(3)]
        dd = sum(c * c for c in d)
        t = 0.0 if dd == 0 else max(0.0, min(1.0, sum((loc[k] - a[k]) * d[k] for k in range(3)) / dd))
        dist = math.dist(loc, [a[k] + d[k] * t for k in range(3)])
        if best is None or dist < best[0] - 1e-9:
            best = (dist, i, t * math.sqrt(dd))
    return best[1], best[2]


def _jog_half_width(p: Pipe, v: Violation, scene: Scene) -> float:
    if v.type == CLASH:
        other = scene.pipe(v.ids[1] if v.ids[0] == p.id else v.ids[0])
        return _snap_up(p.outer_radius + other.outer_radius + scene.rules.clearance + 0.1)
    if v.type == WALL:
        wall = next(w for w in scene.walls if w.id == v.ids[1])
        return _snap_up(wall.thickness / 2 + p.outer_radius + 0.1)
    return _snap_up(2 * p.outer_radius + 0.1)


def _jog(p: Pipe, v: Violation, scene: Scene, axis: str):
    """Detour around the violation, keeping both pipe ends fixed (45 degree ramps)."""
    i, s = _nearest_segment(p, v.location)
    a, b = p.points[i], p.points[i + 1]
    d = [b[k] - a[k] for k in range(3)]
    length = math.sqrt(sum(c * c for c in d))
    u = [c / length for c in d]
    run = math.hypot(u[0], u[1])
    if run < 1e-6:  # riser: a sideways/up detour has no meaning
        return
    n = (-u[1] / run, u[0] / run, 0.0)
    s0 = round(s / STEP) * STEP
    half = _jog_half_width(p, v, scene)
    for off in JOG_OFFSETS:
        ramp = abs(off)
        sa, sb = s0 - half - ramp, s0 + half + ramp
        if sa < -1e-9 or sb > length + 1e-9:
            continue
        ov = (0.0, 0.0, off) if axis == "z" else (n[0] * off, n[1] * off, 0.0)

        def at(t, extra=(0.0, 0.0, 0.0)):
            return tuple(r4(a[k] + u[k] * t + extra[k]) for k in range(3))

        new = [at(sa), at(s0 - half, ov), at(s0 + half, ov), at(sb)]
        pts = list(p.points[: i + 1]) + new + list(p.points[i + 1 :])
        pts = [q for k, q in enumerate(pts) if k == 0 or q != pts[k - 1]]
        yield {"offset": r4(off), "at": [r4(c) for c in at(s0)], "half_width": r4(half)}, pts


def _jog_z(p, v, scene):
    return _jog(p, v, scene, "z")


def _jog_xy(p, v, scene):
    return _jog(p, v, scene, "xy")


def _slide_wall(p: Pipe, v: Violation, scene: Scene):
    """Slide the whole pipe along the wall: only helps when it lands in an opening."""
    wall = next(w for w in scene.walls if w.id == v.ids[1])
    dx, dy = wall.p1[0] - wall.p0[0], wall.p1[1] - wall.p0[1]
    if abs(dy) < 1e-9:
        steps = [(s_ * k * STEP, 0.0) for k in range(1, round(MAX_SHIFT / STEP) + 1) for s_ in (1, -1)]
    elif abs(dx) < 1e-9:
        steps = [(0.0, s_ * k * STEP) for k in range(1, round(MAX_SHIFT / STEP) + 1) for s_ in (1, -1)]
    else:
        return
    for mx, my in steps:
        yield {"dx": r4(mx), "dy": r4(my)}, [(r4(x + mx), r4(y + my), z) for x, y, z in p.points]


def _snap_down(x: float) -> float:
    return r4(math.floor(x / STEP + 1e-9) * STEP)


def _add_opening(p: Pipe, v: Violation, scene: Scene):
    """Ask for a new opening (sleeve) in the wall around where the pipe crosses it."""
    wall = next(w for w in scene.walls if w.id == v.ids[1])
    if not wall.allow_openings:
        return
    i, _ = _nearest_segment(p, v.location)
    a, b = p.points[i], p.points[i + 1]
    la, lb = _wall_local(wall, a), _wall_local(wall, b)
    if la[1] * lb[1] >= 0:
        return
    t = la[1] / (la[1] - lb[1])
    u, z = la[0] + (lb[0] - la[0]) * t, la[2] + (lb[2] - la[2]) * t
    length = math.hypot(wall.p1[0] - wall.p0[0], wall.p1[1] - wall.p0[1])
    r = p.outer_radius
    for margin in (0.1, 0.15, 0.2):
        u0, u1 = max(_snap_down(u - r - margin), 0.0), min(_snap_up(u + r + margin), length)
        z0 = max(_snap_down(z - r - margin), wall.z0)
        z1 = min(_snap_up(z + r + margin), r4(wall.z0 + wall.height))
        if any(u0 < o.u1 and o.u0 < u1 and z0 < o.z1 and o.z0 < z1 for o in wall.openings):
            continue  # would overlap an existing opening
        new = Opening(u0, u1, z0, z1)
        walls = [replace(w, openings=[*w.openings, new]) if w.id == wall.id else w
                 for w in scene.walls]
        yield ({"wall": wall.id, "u0": u0, "u1": u1, "z0": z0, "z1": z1},
               replace(scene, walls=walls))


MIN_KEEP = 0.5  # a trimmed pipe must stay at least this long


def _trim(p: Pipe, v: Violation, scene: Scene):
    """Cut the pipe short in front of the wall, keeping the longer side."""
    wall = next(w for w in scene.walls if w.id == v.ids[1])
    i, _ = _nearest_segment(p, v.location)
    a, b = p.points[i], p.points[i + 1]
    ya, yb = _wall_local(wall, a)[1], _wall_local(wall, b)[1]
    if ya * yb >= 0:  # never crosses the wall's mid-plane
        return
    t = ya / (ya - yb)
    seg = [b[k] - a[k] for k in range(3)]
    seg_len = math.sqrt(sum(c * c for c in seg))
    u = [c / seg_len for c in seg]
    before = sum(math.dist(q, r) for q, r in pairwise(p.points[: i + 1])) + t * seg_len
    total = sum(math.dist(q, r) for q, r in pairwise(p.points))
    after = total - before
    cut = _snap_up(wall.thickness / 2 + p.outer_radius + 0.05)
    cross = [a[k] + seg[k] * t for k in range(3)]
    if before >= after:  # keep the start side
        end = tuple(r4(cross[k] - u[k] * cut) for k in range(3))
        pts = list(p.points[: i + 1]) + [end]
        kept, keep = before - cut, "start"
    else:
        end = tuple(r4(cross[k] + u[k] * cut) for k in range(3))
        pts = [end] + list(p.points[i + 1 :])
        kept, keep = after - cut, "end"
    pts = [q for k, q in enumerate(pts) if k == 0 or q != pts[k - 1]]
    if kept < MIN_KEEP or len(pts) < 2:
        return
    yield {"keep": keep, "end": list(end), "removed": r4(total - kept)}, pts


def _reslope(p: Pipe, v: Violation, scene: Scene):
    """Keep the upstream end and lower the downstream end, or keep the downstream end and raise
    the upstream end. Vertical drops are left untouched."""
    pts = p.points
    max_slope = scene.rules.max_drain_slope

    def fall(run: float) -> float:
        """Drop over `run` metres: on the 0.05 grid when that keeps the slope legal."""
        snapped = _snap_up(TARGET_SLOPE * run)
        return snapped if snapped <= max_slope * run else TARGET_SLOPE * run

    down, up = [pts[0]], [pts[-1]]
    for a, b in pairwise(pts):
        run = math.hypot(b[0] - a[0], b[1] - a[1])
        drop = fall(run) if run >= 1e-6 else a[2] - b[2]
        down.append((b[0], b[1], r4(down[-1][2] - drop)))
    for up_pt, down_pt in reversed(list(pairwise(pts))):
        run = math.hypot(down_pt[0] - up_pt[0], down_pt[1] - up_pt[1])
        rise = fall(run) if run >= 1e-6 else up_pt[2] - down_pt[2]
        up.append((up_pt[0], up_pt[1], r4(up[-1][2] + rise)))
    yield {"mode": "lower_downstream", "slope": TARGET_SLOPE}, down
    yield {"mode": "raise_upstream", "slope": TARGET_SLOPE}, list(reversed(up))


SCENE_STRATEGIES = {"add_opening": _add_opening}  # these change the wall, not the pipe
STRATEGIES = {
    "shift_z": _shift_z, "shift_xy": _shift_xy, "jog_z": _jog_z, "jog_xy": _jog_xy,
    "reslope": _reslope, "slide_wall": _slide_wall, "trim": _trim,
}
LADDER = {
    CLASH: ["shift_z", "shift_xy", "jog_z", "jog_xy"],
    WALL: ["slide_wall", "trim", "add_opening", "shift_xy", "shift_z", "jog_xy", "jog_z"],
    CLEAR: ["shift_z", "jog_z", "shift_xy"],
    SLOPE: ["reslope"],
}


MOVES_ENDS = {"shift_z", "shift_xy", "reslope", "slide_wall", "trim"}


def _state_key(scene: Scene) -> tuple:
    return (tuple((p.id, tuple(p.points)) for p in scene.pipes),
            tuple((w.id, len(w.openings)) for w in scene.walls))


def _dead_end(scene: Scene, v: Violation) -> bool:
    """A violation no later step can cure: a single-pipe problem on a pipe whose ends are fixed
    (a wall can still be cured by a new opening when that wall allows one)."""
    if v.type not in (CLEAR, SLOPE, WALL):
        return False
    if v.type == WALL and next(w for w in scene.walls if w.id == v.ids[1]).allow_openings:
        return False
    return all(scene.pipe(i).fixed_ends for i in v.ids if any(p.id == i for p in scene.pipes))


def _candidates(scene: Scene, pid: str, strategy: str, v: Violation):
    """(detail, candidate scene) for every option of one strategy, smallest change first."""
    if strategy in SCENE_STRATEGIES:
        yield from SCENE_STRATEGIES[strategy](scene.pipe(pid), v, scene)
        return
    for detail, pts in STRATEGIES[strategy](scene.pipe(pid), v, scene):
        yield detail, _with_points(scene, pid, pts)


def _best(scene: Scene, pid: str, strategy: str, v: Violation, count: int,
          sidestep: bool = False, seen: set | None = None, base: list[Violation] | None = None):
    """Smallest change for `pid` using `strategy`.

    Normally it must strictly lower the violation count. With `sidestep` it may instead keep the
    count equal as long as it cures the target violation itself (the problem moves elsewhere and is
    handled next) and reaches a state not visited before.
    """
    best = None
    target = (v.type, tuple(v.ids))
    base = check_all(scene) if base is None else base
    known = {(x.type, tuple(x.ids)) for x in base} if sidestep else set()
    for detail, cand in _candidates(scene, pid, strategy, v):
        # only `pid` moved (a wall change can affect any pipe, so that one is checked in full)
        vs = check_all(cand) if strategy in SCENE_STRATEGIES else recheck(cand, base, pid)
        n = len(vs)
        if sidestep:
            # a single-pipe problem (headroom, slope) can only be cured by moving that pipe, which
            # may collide with a few others; those collisions are then settled by the normal ladder
            slack = SIDESTEP_SLACK if v.type in (CLEAR, SLOPE) else 0
            if n > count + slack or any((x.type, tuple(x.ids)) == target for x in vs):
                continue
            if seen is not None and _state_key(cand) in seen:
                continue
            if any(_dead_end(cand, x) for x in vs if (x.type, tuple(x.ids)) not in known):
                continue
        elif n >= count:
            continue
        if best is None or n < best[0]:
            best = (n, detail, cand)
            if n == 0:
                break
    return best


def _beam(scene: Scene) -> SolveResult | None:
    """Fallback when the greedy walk is stuck: keep several partial fixes and explore them.

    Every move must cure the violation it targets and must not create one that can never be cured;
    states are ranked by how many violations remain (ties keep the smaller change). Deterministic.
    """
    start = copy.deepcopy(scene)
    frontier = [(start, check_all(start), [])]
    seen = {_state_key(start)}
    expanded = 0
    while frontier and expanded < BEAM_NODES:
        children = []
        for cur, vs, acts in frontier:
            expanded += 1
            target = vs[0]
            tid = (target.type, tuple(target.ids))
            known = {(x.type, tuple(x.ids)) for x in vs}
            for pid in _movers(cur, target):
                for strategy in LADDER[target.type]:
                    if cur.pipe(pid).fixed_ends and strategy in MOVES_ENDS:
                        continue
                    scored = []
                    for k, (detail, cand) in enumerate(_candidates(cur, pid, strategy, target)):
                        v2 = check_all(cand) if strategy in SCENE_STRATEGIES else recheck(cand, vs, pid)
                        if any((x.type, tuple(x.ids)) == tid for x in v2):
                            continue
                        if any(_dead_end(cand, x) for x in v2 if (x.type, tuple(x.ids)) not in known):
                            continue
                        key = _state_key(cand)
                        if key in seen:
                            continue
                        scored.append((len(v2), k, detail, cand, v2, key))
                    scored.sort(key=lambda t: (t[0], t[1]))
                    for n2, _, detail, cand, v2, key in scored[:BEAM_PER_STRATEGY]:
                        seen.add(key)
                        act = Action(strategy, pid, detail, target.message, len(vs), n2, target.type,
                                     list(target.ids), [], [list(q) for q in cand.pipe(pid).points])
                        if not v2:
                            return SolveResult(True, cand, [*acts, act])
                        children.append((n2, len(acts), len(children), cand, v2, [*acts, act]))
        children.sort(key=lambda t: (t[0], t[1], t[2]))
        frontier = [(c, v, a) for _, _, _, c, v, a in children[:BEAM_WIDTH]]
    return None


def solve(scene: Scene, max_iter: int = MAX_ITER) -> SolveResult:
    """Greedy walk first (cheap, and the result most scenes get); the beam only when it is stuck."""
    result = _greedy(scene, max_iter)
    return result if result.ok else (_beam(scene) or result)


def _greedy(scene: Scene, max_iter: int = MAX_ITER) -> SolveResult:
    cur = copy.deepcopy(scene)
    actions: list[Action] = []
    seen = {_state_key(cur)}
    for _ in range(max_iter):
        vs = check_all(cur)
        if not vs:
            return SolveResult(True, cur, actions)
        target = vs[0]
        hit_any = False
        for sidestep in (False, True):  # first a strictly better move, else a sidestep
            skipped: list[dict] = []
            for pid in _movers(cur, target):
                hit = None
                for strategy in LADDER[target.type]:
                    if cur.pipe(pid).fixed_ends and strategy in MOVES_ENDS:
                        skipped.append({"pipe_id": pid, "strategy": strategy,
                                        "reason": "fixed_ends"})
                        continue
                    hit = _best(cur, pid, strategy, target, len(vs), sidestep, seen, vs)
                    if hit:
                        actions.append(
                            Action(strategy, pid, hit[1], target.message, len(vs), hit[0],
                                   target.type, list(target.ids), skipped,
                                   [list(q) for q in hit[2].pipe(pid).points])
                        )
                        break
                    skipped.append({"pipe_id": pid, "strategy": strategy,
                                    "reason": "no_improvement"})
                if hit:
                    cur = hit[2]
                    seen.add(_state_key(cur))
                    hit_any = True
                    break
            if hit_any:
                break
        if not hit_any:
            return SolveResult(False, cur, actions, vs)
    return SolveResult(False, cur, actions, check_all(cur))
