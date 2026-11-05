"""Deterministic solver. Phase 1: vertical shift only (later: lateral shift, dogleg detours)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .rules import Violation, check_all
from .scene import SYSTEM_PRIORITY, Scene, r4

STEP = 0.05
MAX_SHIFT = 1.5
# smallest |dz| first; on ties prefer moving up
CANDIDATES = [s * k * STEP for k in range(1, int(MAX_SHIFT / STEP) + 1) for s in (1, -1)]


@dataclass
class Action:
    pipe_id: str
    dz: float
    resolved: str  # message of the violation that triggered it
    violations_before: int
    violations_after: int


@dataclass
class SolveResult:
    ok: bool
    fixed: Scene
    actions: list[Action] = field(default_factory=list)
    remaining: list[Violation] = field(default_factory=list)


def _shifted(scene: Scene, pipe_id: str, dz: float) -> Scene:
    s = copy.deepcopy(scene)
    p = s.pipe(pipe_id)
    p.points = [(x, y, r4(z + dz)) for x, y, z in p.points]
    return s


def _movers(scene: Scene, v: Violation) -> list[str]:
    ids = [i for i in v.ids if any(p.id == i for p in scene.pipes)]
    # lowest-priority system moves first; ties broken by id
    return sorted(ids, key=lambda i: (-SYSTEM_PRIORITY[scene.pipe(i).system], i))


def solve(scene: Scene, max_iter: int = 20) -> SolveResult:
    cur = copy.deepcopy(scene)
    actions: list[Action] = []
    for _ in range(max_iter):
        vs = check_all(cur)
        if not vs:
            return SolveResult(True, cur, actions)
        target = vs[0]
        done = False
        for pid in _movers(cur, target):
            best = None
            for dz in CANDIDATES:
                cand = _shifted(cur, pid, dz)
                n = len(check_all(cand))
                if n < len(vs) and (best is None or n < best[0]):
                    best = (n, dz, cand)
                    if n == 0:
                        break
            if best:
                actions.append(Action(pid, r4(best[1]), target.message, len(vs), best[0]))
                cur = best[2]
                done = True
                break
        if not done:
            return SolveResult(False, cur, actions, vs)
    return SolveResult(False, cur, actions, check_all(cur))
