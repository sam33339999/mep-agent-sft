"""Fixed example scenes (SPEC section 9, step 1, plus one per solver strategy)."""

from __future__ import annotations

from .scene import Opening, Pipe, Rules, Scene, Wall


def two_pipes() -> Scene:
    return Scene(
        walls=[Wall("W1", (0.0, 0.0), (10.0, 0.0), thickness=0.2, height=3.0)],
        pipes=[
            Pipe("A", "water", 0.2, [(1.0, 1.0, 2.5), (5.0, 1.0, 2.5)]),
            Pipe("B", "electrical", 0.15, [(3.0, 0.5, 2.6), (3.0, 3.0, 2.6)]),
        ],
        rules=Rules(ceiling_height=3.0, min_clear_height=2.4, clearance=0.05),
        injected=["clash"],
        meta={"name": "two-pipes"},
    )


def jog() -> Scene:
    """B is tied to equipment at both ends, so it cannot slide: it must detour around A."""
    return Scene(
        walls=[],
        pipes=[
            Pipe("A", "water", 0.2, [(2.5, 1.0, 2.6), (3.5, 1.0, 2.6)]),
            Pipe("B", "electrical", 0.2, [(3.0, -2.0, 2.6), (3.0, 4.0, 2.6)], fixed_ends=True),
        ],
        rules=Rules(ceiling_height=2.8, min_clear_height=2.4, clearance=0.05),
        injected=["clash"],
        meta={"name": "jog"},
    )


def opening() -> Scene:
    """Pipe C cuts through a solid part of the wall; the wall has an opening further along."""
    return Scene(
        walls=[Wall("W1", (0.0, 0.0), (10.0, 0.0), openings=[Opening(4.0, 5.0, 0.0, 2.6)])],
        pipes=[Pipe("C", "water", 0.1, [(3.0, -1.0, 2.5), (3.0, 1.0, 2.5)])],
        injected=["wall_penetration"],
        meta={"name": "opening"},
    )


def slope() -> Scene:
    """Level drainage pipe: needs a fall."""
    return Scene(
        walls=[],
        pipes=[Pipe("D", "drainage", 0.1, [(1.0, 1.0, 2.5), (6.0, 1.0, 2.5)])],
        injected=["drain_slope"],
        meta={"name": "slope"},
    )


def two_floors() -> Scene:
    """Storey 2 sits 3.2 m up. Its pipe C is 0.15 m too low *for that storey* (bottom 5.45 < 5.6),
    while the same height would be a ceiling violation on storey 1."""
    return Scene(
        walls=[Wall("W1", (0.0, 0.0), (8.0, 0.0)), Wall("F2-W1", (0.0, 0.0), (8.0, 0.0), z0=3.2)],
        pipes=[
            Pipe("A", "water", 0.1, [(1.0, 1.0, 2.6), (6.0, 1.0, 2.6)]),
            Pipe("C", "water", 0.1, [(1.0, 1.0, 5.5), (6.0, 1.0, 5.5)], floor=1),
        ],
        rules=Rules(floors=2),
        injected=["clear_height"],
        meta={"name": "two-floors"},
    )


CASES = {"two-pipes": two_pipes, "jog": jog, "opening": opening, "slope": slope,
         "two-floors": two_floors}


def demo_scene(case: str = "two-pipes") -> Scene:
    return CASES[case]()
