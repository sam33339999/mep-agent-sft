"""Scene model. JSON is the only interface between generator, solver, rules and viewers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from . import SCHEMA_VERSION
from .geometry import Vec

# Lower number = higher priority = moves last (drainage keeps its slope, so it stays put).
SYSTEM_PRIORITY = {"drainage": 0, "duct": 1, "water": 2, "electrical": 3}


def r4(x: float) -> float:
    return round(x + 0.0, 4)


@dataclass
class Opening:
    """Rectangular hole in a wall: u0..u1 measured along the wall from p0, z0..z1 absolute."""

    u0: float
    u1: float
    z0: float
    z1: float


@dataclass
class Wall:
    id: str
    p0: tuple[float, float]
    p1: tuple[float, float]
    thickness: float = 0.2
    height: float = 3.0
    z0: float = 0.0
    openings: list[Opening] = field(default_factory=list)


@dataclass
class Pipe:
    id: str
    system: str
    diameter: float
    points: list[Vec]
    insulation: float = 0.0
    fixed_ends: bool = False  # ends are tied to equipment: may be detoured, never moved rigidly

    @property
    def outer_radius(self) -> float:
        return self.diameter / 2 + self.insulation


@dataclass
class Rules:
    ceiling_height: float = 3.0
    min_clear_height: float = 2.4  # underside of any pipe must be >= this
    clearance: float = 0.05  # minimum gap between two pipe outer surfaces
    min_drain_slope: float = 0.01  # drainage falls from points[0] to points[-1]
    max_drain_slope: float = 0.10


@dataclass
class Scene:
    walls: list[Wall]
    pipes: list[Pipe]
    rules: Rules = field(default_factory=Rules)
    injected: list[str] = field(default_factory=list)  # issue tags planted by generator
    meta: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def pipe(self, pipe_id: str) -> Pipe:
        return next(p for p in self.pipes if p.id == pipe_id)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, **kw)

    @classmethod
    def from_dict(cls, d: dict) -> Scene:
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {d.get('schema_version')}")
        return cls(
            walls=[
                Wall(**{
                    **w, "p0": tuple(w["p0"]), "p1": tuple(w["p1"]),
                    "openings": [Opening(**o) for o in w.get("openings", [])],
                })
                for w in d["walls"]
            ],
            pipes=[Pipe(**{**p, "points": [tuple(pt) for pt in p["points"]]}) for p in d["pipes"]],
            rules=Rules(**d["rules"]),
            injected=list(d.get("injected", [])),
            meta=dict(d.get("meta", {})),
        )

    @classmethod
    def from_json(cls, s: str) -> Scene:
        return cls.from_dict(json.loads(s))
