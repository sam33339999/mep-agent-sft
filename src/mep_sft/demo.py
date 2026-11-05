"""Fixed minimal example (SPEC section 9, step 1): two crossing pipes next to a wall."""

from __future__ import annotations

from .scene import Pipe, Rules, Scene, Wall


def demo_scene() -> Scene:
    return Scene(
        walls=[Wall("W1", (0.0, 0.0), (10.0, 0.0), thickness=0.2, height=3.0)],
        pipes=[
            Pipe("A", "water", 0.2, [(1.0, 1.0, 2.5), (5.0, 1.0, 2.5)]),
            Pipe("B", "electrical", 0.15, [(3.0, 0.5, 2.6), (3.0, 3.0, 2.6)]),
        ],
        rules=Rules(ceiling_height=3.0, min_clear_height=2.4, clearance=0.05),
        injected=["clash"],
        meta={"name": "demo-two-pipes"},
    )
