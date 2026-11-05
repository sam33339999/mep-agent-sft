"""3D geometry primitives (units: metres, Z up)."""

from __future__ import annotations

import math

Vec = tuple[float, float, float]

_EPS = 1e-12


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else min(x, 1.0)


def segment_closest_points(p1: Vec, q1: Vec, p2: Vec, q2: Vec) -> tuple[Vec, Vec]:
    """Closest points between segments p1-q1 and p2-q2 (Ericson, Real-Time Collision Detection)."""
    d1, d2, r = sub(q1, p1), sub(q2, p2), sub(p1, p2)
    a, e, f = dot(d1, d1), dot(d2, d2), dot(d2, r)
    if a <= _EPS and e <= _EPS:
        return p1, p2
    if a <= _EPS:
        s, t = 0.0, _clamp01(f / e)
    else:
        c = dot(d1, r)
        if e <= _EPS:
            s, t = _clamp01(-c / a), 0.0
        else:
            b = dot(d1, d2)
            denom = a * e - b * b
            s = _clamp01((b * f - c * e) / denom) if denom > _EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, _clamp01(-c / a)
            elif t > 1.0:
                t, s = 1.0, _clamp01((b - c) / a)
    return add(p1, scale(d1, s)), add(p2, scale(d2, t))


def segment_distance(p1: Vec, q1: Vec, p2: Vec, q2: Vec) -> tuple[float, Vec, Vec]:
    c1, c2 = segment_closest_points(p1, q1, p2, q2)
    return norm(sub(c1, c2)), c1, c2


def segment_box_intersect(
    p: Vec, q: Vec, lo: Vec, hi: Vec
) -> tuple[float, float] | None:
    """Slab test of segment p-q against axis-aligned box. Returns (t_enter, t_exit) or None."""
    t0, t1 = 0.0, 1.0
    d = sub(q, p)
    for i in range(3):
        if abs(d[i]) < _EPS:
            if p[i] < lo[i] or p[i] > hi[i]:
                return None
        else:
            ta, tb = (lo[i] - p[i]) / d[i], (hi[i] - p[i]) / d[i]
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
            if t0 > t1:
                return None
    return t0, t1
