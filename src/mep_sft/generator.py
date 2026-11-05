"""Seeded scene generator: build a valid room, then plant issues, then keep only solvable scenes.

Every random choice comes from one `random.Random`, and every coordinate is snapped to a 0.05 m
grid, so the same (seed, difficulty) always yields the same scene byte for byte.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

from .rules import CLASH, CLEAR, SLOPE, WALL, check_all
from .scene import Opening, Pipe, Rules, Scene, Wall, r4
from .solver import solve

GRID = 0.05
CLEAN_PROB = 0.12  # share of samples with nothing to fix ("no change needed" examples)
WALL_T = 0.2
MARGIN = 0.5  # keep clean pipes this far from walls

DIAMETERS = {
    "drainage": (0.1, 0.15),
    "water": (0.05, 0.1, 0.15),
    "duct": (0.3, 0.4),
    "electrical": (0.05, 0.1),
}
INSULATION = {"drainage": (0.0,), "water": (0.0, 0.025), "duct": (0.0,), "electrical": (0.0,)}

TAG_TO_VIOLATION = {
    "clash": CLASH, "wall_penetration": WALL, "clear_height": CLEAR, "drain_slope": SLOPE,
}


@dataclass(frozen=True)
class Level:
    pipes: tuple[int, int]
    issues: tuple[int, int]
    systems: tuple[str, ...]
    interior_wall: bool
    fixed_prob: float
    ceilings: tuple[float, ...]
    clearances: tuple[float, ...]


LEVELS = {
    1: Level((2, 3), (1, 1), ("water", "electrical"), False, 0.0, (3.0,), (0.05,)),
    2: Level((3, 5), (1, 2), ("water", "electrical", "drainage"), False, 0.0, (3.0,), (0.05,)),
    3: Level((5, 8), (2, 3), ("water", "electrical", "drainage", "duct"), True, 0.25, (3.0, 2.9),
             (0.05, 0.1)),
    4: Level((8, 12), (3, 4), ("water", "electrical", "drainage", "duct"), True, 0.35,
             (2.9, 2.8), (0.05, 0.1)),
}


def g(x: float) -> float:
    """Snap to the 0.05 m grid."""
    return r4(round(x / GRID) * GRID)


def _ceil_g(x: float) -> float:
    return r4(math.ceil(x / GRID - 1e-9) * GRID)


def _floor_g(x: float) -> float:
    return r4(math.floor(x / GRID + 1e-9) * GRID)


def _band(scene: Scene, radius: float) -> tuple[float, float]:
    return _ceil_g(scene.rules.min_clear_height + radius), _floor_g(
        scene.rules.ceiling_height - radius
    )


def _next_id(scene: Scene) -> str:
    return f"P{len(scene.pipes) + 1}"


# ------------------------------------------------------------------ clean scene

def _room(rng: random.Random, lv: Level) -> Scene:
    w, d = rng.randint(6, 12), rng.randint(4, 8)
    ceiling = rng.choice(lv.ceilings)
    rules = Rules(ceiling_height=ceiling, min_clear_height=2.4, clearance=rng.choice(lv.clearances))
    walls = [
        Wall("W1", (0.0, 0.0), (float(w), 0.0), WALL_T, ceiling),
        Wall("W2", (float(w), 0.0), (float(w), float(d)), WALL_T, ceiling),
        Wall("W3", (float(w), float(d)), (0.0, float(d)), WALL_T, ceiling),
        Wall("W4", (0.0, float(d)), (0.0, 0.0), WALL_T, ceiling),
    ]
    scene = Scene(walls=walls, pipes=[], rules=rules, meta={"room": [w, d]})
    if lv.interior_wall:
        xw = float(w // 2)
        u0 = g(rng.uniform(1.0, d - 2.5))
        walls.append(
            Wall("W5", (xw, 0.0), (xw, float(d)), WALL_T, ceiling,
                 openings=[Opening(u0, r4(u0 + 1.2), 2.3, ceiling)])
        )
    return scene


def _try_pipe(rng: random.Random, scene: Scene, system: str) -> Pipe | None:
    w, d = scene.meta["room"]
    diam = rng.choice(DIAMETERS[system])
    ins = rng.choice(INSULATION[system])
    radius = diam / 2 + ins
    zlo, zhi = _band(scene, radius)
    if zlo > zhi:
        return None
    ax = rng.choice((0, 1))  # run direction: 0 = along x, 1 = along y
    span = (w, d)[ax]
    length = g(rng.uniform(1.0, max(1.0, span - 2 * MARGIN)))
    start = g(rng.uniform(MARGIN, max(MARGIN, span - MARGIN - length)))
    lateral = g(rng.uniform(MARGIN, (d, w)[ax] - MARGIN))
    a, b = start, g(start + length)
    if rng.random() < 0.5:
        a, b = b, a
    if system == "drainage":
        fall = _ceil_g(0.02 * length)
        if zlo + fall > zhi:
            return None
        za = g(rng.uniform(zlo + fall, zhi))
        zb = r4(za - fall)
    else:
        za = zb = g(rng.uniform(zlo, zhi))

    def pt(along, z):
        return (along, lateral, z) if ax == 0 else (lateral, along, z)

    return Pipe(_next_id(scene), system, diam, [pt(a, za), pt(b, zb)], ins)


def _through_opening_pipe(rng: random.Random, scene: Scene) -> Pipe | None:
    """A pipe that legitimately passes through the interior wall's opening."""
    w, _ = scene.meta["room"]
    wall = scene.walls[-1]
    op = wall.openings[0]
    system = rng.choice(("water", "electrical"))
    diam = rng.choice(DIAMETERS[system])
    zlo, zhi = _band(scene, diam / 2)
    if zlo > zhi:
        return None
    y = g((op.u0 + op.u1) / 2)
    z = g(rng.uniform(zlo, zhi))
    z = min(max(z, r4(op.z0 + diam / 2)), r4(op.z1 - diam / 2))
    return Pipe(_next_id(scene), system, diam, [(MARGIN, y, z), (float(w) - MARGIN, y, z)])


def build_clean(rng: random.Random, difficulty: int) -> Scene:
    lv = LEVELS[difficulty]
    scene = _room(rng, lv)
    target = rng.randint(*lv.pipes)
    if lv.interior_wall:
        p = _through_opening_pipe(rng, scene)
        if p:
            scene.pipes.append(p)
    for _ in range(target * 12):
        if len(scene.pipes) >= target:
            break
        p = _try_pipe(rng, scene, rng.choice(lv.systems))
        if p is None:
            continue
        scene.pipes.append(p)
        if check_all(scene):
            scene.pipes.pop()
    return scene


# ------------------------------------------------------------------ issue mutators
# Each returns True when it planted its issue. They only append pipes or edit existing ones.

def _horizontal(p: Pipe) -> int | None:
    """Axis (0 = x, 1 = y) a straight 2-point pipe runs along, else None."""
    if len(p.points) != 2:
        return None
    a, b = p.points
    dx, dy = abs(b[0] - a[0]), abs(b[1] - a[1])
    if dx > 1e-6 and dy < 1e-6:
        return 0
    if dy > 1e-6 and dx < 1e-6:
        return 1
    return None


def _cross_pipe(scene: Scene, ax: int, along: float, lateral: float, z: float, reach: tuple,
                system: str, diam: float, ins: float) -> Pipe:
    """Pipe running perpendicular to `ax`, crossing the line at `along`."""
    lo, hi = lateral - reach[0], lateral + reach[1]

    def pt(v):
        return (along, v, z) if ax == 0 else (v, along, z)

    return Pipe(_next_id(scene), system, diam, [pt(r4(lo)), pt(r4(hi))], ins)


def mut_clash(rng: random.Random, scene: Scene, lv: Level) -> bool:
    w, d = scene.meta["room"]
    cands = [p for p in scene.pipes if _horizontal(p) is not None]
    rng.shuffle(cands)
    for p in cands:
        ax = _horizontal(p)
        a, b = p.points
        lo, hi = sorted((a[ax], b[ax]))
        if hi - lo < 1.0:
            continue
        along = g(rng.uniform(lo + 0.3, hi - 0.3))
        t = (along - a[ax]) / (b[ax] - a[ax])
        zp = a[2] + (b[2] - a[2]) * t
        lateral = a[1 - ax]
        system = rng.choice(lv.systems)
        diam = rng.choice(DIAMETERS[system])
        ins = rng.choice(INSULATION[system])
        radius = diam / 2 + ins
        zlo, zhi = _band(scene, radius)
        if zlo > zhi:
            continue
        room_lat = (d, w)[ax]  # room extent along the crossing pipe's own axis
        r_lo = min(g(rng.uniform(0.6, 1.5)), g(lateral - MARGIN))
        r_hi = min(g(rng.uniform(0.6, 1.5)), g(room_lat - MARGIN - lateral))
        if r_lo < 0.3 or r_hi < 0.3:
            continue
        limit = p.outer_radius + radius + scene.rules.clearance - GRID / 2
        dzs = [k * GRID for k in range(-8, 9) if abs(k * GRID) < limit]
        z = g(zp + rng.choice(dzs))
        z = min(max(z, zlo), zhi)
        q = _cross_pipe(scene, ax, along, lateral, z, (r_lo, r_hi), system, diam, ins)
        scene.pipes.append(q)
        if any(v.type == CLASH and q.id in v.ids for v in check_all(scene)):
            return True
        scene.pipes.pop()
    return False


def mut_wall(rng: random.Random, scene: Scene, lv: Level) -> bool:
    walls = list(scene.walls)
    rng.shuffle(walls)
    for wall in walls:
        horiz = abs(wall.p1[1] - wall.p0[1]) < 1e-9  # wall runs along x
        ax = 1 if horiz else 0  # pipe runs along the other axis
        lo, hi = sorted((wall.p0[1 - ax], wall.p1[1 - ax]))
        if hi - lo < 2.0:
            continue
        u = g(rng.uniform(lo + 0.5, hi - 0.5))
        u_local = abs(u - wall.p0[1 - ax])  # distance from the wall's p0, as openings measure it
        if any(o.u0 - 0.3 <= u_local <= o.u1 + 0.3 for o in wall.openings):
            continue
        system = rng.choice(lv.systems)
        diam = rng.choice(DIAMETERS[system])
        ins = rng.choice(INSULATION[system])
        zlo, zhi = _band(scene, diam / 2 + ins)
        if zlo > zhi:
            continue
        z = g(rng.uniform(zlo, zhi))
        wall_at = wall.p0[ax]
        reach = (g(rng.uniform(0.6, 1.2)), g(rng.uniform(0.6, 1.2)))
        # the new pipe runs along `ax`, crossing the wall at coordinate wall_at on that axis
        q = _cross_pipe(scene, 1 - ax, u, wall_at, z, reach, system, diam, ins)
        scene.pipes.append(q)
        if any(v.type == WALL and q.id in v.ids for v in check_all(scene)):
            return True
        scene.pipes.pop()
    return False


def mut_clear(rng: random.Random, scene: Scene, lv: Level) -> bool:
    cands = list(scene.pipes)
    rng.shuffle(cands)
    for p in cands:
        r = p.outer_radius
        if rng.random() < 0.7:  # underside too low
            bottom = min(z for _, _, z in p.points) - r
            dz = g(2.4 - rng.randint(1, 4) * GRID - bottom)
        else:  # top above the ceiling
            top = max(z for _, _, z in p.points) + r
            dz = g(scene.rules.ceiling_height + rng.randint(1, 3) * GRID - top)
        if dz == 0:
            continue
        old = p.points
        p.points = [(x, y, r4(z + dz)) for x, y, z in old]
        if any(v.type == CLEAR and p.id in v.ids for v in check_all(scene)):
            return True
        p.points = old
    return False


def mut_slope(rng: random.Random, scene: Scene, lv: Level) -> bool:
    cands = [p for p in scene.pipes if p.system == "drainage" and len(p.points) == 2]
    rng.shuffle(cands)
    for p in cands:
        a, b = p.points
        run = math.hypot(b[0] - a[0], b[1] - a[1])
        mode = rng.choice(("level", "uphill", "steep"))
        if mode == "level":
            zb = a[2]
        elif mode == "uphill":
            zb = r4(a[2] + rng.randint(1, 3) * GRID)
        else:
            zb = r4(a[2] - _ceil_g(0.13 * run))
        old = p.points
        p.points = [a, (b[0], b[1], zb)]
        if any(v.type == SLOPE and p.id in v.ids for v in check_all(scene)):
            return True
        p.points = old
    return False


MUTATORS = {
    "clash": mut_clash, "wall_penetration": mut_wall, "clear_height": mut_clear,
    "drain_slope": mut_slope,
}
TAG_WEIGHTS = {"clash": 4, "wall_penetration": 2, "clear_height": 2, "drain_slope": 2}


def inject(rng: random.Random, scene: Scene, lv: Level, n_issues: int) -> None:
    tags = list(TAG_WEIGHTS)
    weights = [TAG_WEIGHTS[t] for t in tags]
    for _ in range(n_issues):
        for tag in rng.choices(tags, weights, k=len(tags)):  # fallback order if one is infeasible
            if MUTATORS[tag](rng, scene, lv):
                scene.injected.append(tag)
                break


# ------------------------------------------------------------------ public API

def scene_hash(scene: Scene) -> str:
    return hashlib.sha1(scene.to_json().encode()).hexdigest()[:16]


def generate_scene(seed: int, difficulty: int, attempt: int = 0) -> Scene:
    rng = random.Random(f"{seed}:{difficulty}:{attempt}")
    lv = LEVELS[difficulty]
    scene = build_clean(rng, difficulty)
    n_issues = 0 if rng.random() < CLEAN_PROB else rng.randint(*lv.issues)
    inject(rng, scene, lv, n_issues)
    if lv.fixed_prob:
        for p in scene.pipes:
            p.fixed_ends = rng.random() < lv.fixed_prob
    scene.meta.update({"seed": seed, "difficulty": difficulty, "attempt": attempt})
    return scene


@dataclass
class Stats:
    attempts: int = 0
    accepted: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.reasons[why] = self.reasons.get(why, 0) + 1


def generate_sample(seed: int, difficulty: int, stats: Stats | None = None,
                    max_attempts: int = 40) -> dict | None:
    """One accepted training pair: a scene with real problems and a verified solution."""
    stats = stats or Stats()
    for attempt in range(max_attempts):
        stats.attempts += 1
        scene = generate_scene(seed, difficulty, attempt)
        if len(scene.pipes) < LEVELS[difficulty].pipes[0]:
            stats.drop("too_few_pipes")
            continue
        violations = check_all(scene)
        if bool(scene.injected) != bool(violations):
            stats.drop("injection_mismatch")
            continue
        want = {TAG_TO_VIOLATION[t] for t in scene.injected}
        if not want <= {v.type for v in violations}:
            stats.drop("injection_mismatch")
            continue
        res = solve(scene)
        if not res.ok:
            stats.drop("unsolved")
            continue
        stats.accepted += 1
        return {
            "id": f"d{difficulty}-s{seed}",
            "seed": seed,
            "difficulty": difficulty,
            "scene_hash": scene_hash(scene),
            "scene": scene.to_dict(),
            "violations": [v.to_dict() for v in violations],
            "fixed": res.fixed.to_dict(),
            "actions": [
                {"kind": a.kind, "pipe_id": a.pipe_id, "detail": a.detail,
                 "resolved": a.resolved}
                for a in res.actions
            ],
        }
    return None


def generate_dataset(n: int, seed: int = 0, difficulty: int | None = None,
                     weights: tuple[float, ...] = (0.2, 0.3, 0.3, 0.2)):
    """n unique accepted samples (dedup by scene hash). difficulty=None mixes levels by weights.

    Returns (samples, {difficulty: Stats}). Sample i always uses seed `seed + i`, so a given
    (seed, n, difficulty) reproduces exactly.
    """
    samples: list[dict] = []
    seen: set[str] = set()
    stats = {d: Stats() for d in LEVELS}
    i = 0
    while len(samples) < n and i < n * 20:
        s = seed + i
        d = difficulty or random.Random(f"mix:{s}").choices(list(LEVELS), weights)[0]
        i += 1
        sample = generate_sample(s, d, stats[d])
        if sample is None or sample["scene_hash"] in seen:
            continue
        seen.add(sample["scene_hash"])
        samples.append(sample)
    return samples, stats
