"""Plain-language explanations for each rule violation, with the actual numbers.

Written for learning to read MEP drawings: what was measured, what it means on site, and where to
look for it in the plan / elevation views.
"""

from __future__ import annotations

import math

from .rules import CLASH, CLEAR, SLOPE, WALL, clash_measure
from .scene import Scene, r4

TYPE_ZH = {
    CLASH: "管線碰撞", WALL: "穿牆", CLEAR: "淨高不足 / 超出天花板", SLOPE: "排水坡度錯誤",
}


def _pct(x: float) -> str:
    return f"{r4(x * 100)}%"


def _clash(scene: Scene, v: dict, n: str) -> dict:
    a, b = scene.pipe(v["ids"][0]), scene.pipe(v["ids"][1])
    gap, _, _ = clash_measure(a, b)
    ra, rb, clr = a.outer_radius, b.outer_radius, scene.rules.clearance
    dist = r4(gap + ra + rb)
    meaning = (
        "間距是負的：兩條管的實體已經互相穿入，現場根本裝不下。"
        if gap < 0
        else "兩條管沒有互相穿入，但留的空隙不夠：保溫、吊架、閥件與日後維修都需要這段空間。"
    )
    return {
        "title": f"{n} {TYPE_ZH[CLASH]}：{a.id} 與 {b.id}",
        "calc": (
            f"外半徑 = 管徑 ÷ 2 + 保溫厚。{a.id} 外半徑 {r4(ra)} m、{b.id} 外半徑 {r4(rb)} m，"
            f"合計 {r4(ra + rb)} m。兩條管中心線最近距離 {dist} m，"
            f"所以外緣間距 = {dist} − {r4(ra + rb)} = {r4(gap)} m；規則要求 ≥ {clr} m。"
        ),
        "meaning": meaning,
        "look": (
            f"平面圖找紅色標記 {n}（發生的位置）；立面圖看兩個管截面之間的尺寸線，"
            "紅色＝不合格、綠色＝合格。平面上交叉、立面上又上下貼近，就是碰撞。"
        ),
    }


def _wall(scene: Scene, v: dict, n: str) -> dict:
    pipe_id, wall_id = v["ids"][0], v["ids"][1]
    wall = next(w for w in scene.walls if w.id == wall_id)
    if wall.openings:
        ops = "；".join(f"沿牆 {o.u0}~{o.u1} m、標高 {o.z0}~{o.z1} m" for o in wall.openings)
        why = f"牆 {wall_id} 有開口（{ops}），但 {pipe_id} 沒有完整落在開口內（管的外緣也要在邊界內）。"
    else:
        why = f"牆 {wall_id} 上沒有任何開口，{pipe_id} 的路徑直接穿過牆體。"
    return {
        "title": f"{n} {TYPE_ZH[WALL]}：{pipe_id} 穿過 {wall_id}",
        "calc": why,
        "meaning": "結構牆不能隨意打洞；管線要穿牆，必須事先預留開口（套管），"
                  "位置與大小要和結構專業協調好。",
        "look": "平面圖：管是細長色條，牆是灰色長條，橘色線段是開口。管穿牆的位置要落在橘線上。"
                "立面圖：牆是灰色區塊，開口是白色矩形。",
    }


def _clear(scene: Scene, v: dict, n: str) -> dict:
    p = scene.pipe(v["ids"][0])
    r, rules = p.outer_radius, scene.rules
    low = abs(v["threshold"] - rules.min_clear_height) < 1e-9
    if low:
        centre = r4(v["value"] + r)
        calc = (f"管底標高 = 管中心標高 − 外半徑 = {centre} − {r4(r)} = {v['value']} m；"
                f"規則要求 ≥ {rules.min_clear_height} m（淨高線）。")
        meaning = "淨高是人走過去頭上要留的空間。管底太低會擋住通道、撞到頭，也會讓後續設備進不來。"
    else:
        centre = r4(v["value"] - r)
        calc = (f"管頂標高 = 管中心標高 + 外半徑 = {centre} + {r4(r)} = {v['value']} m；"
                f"天花板在 {rules.ceiling_height} m，超出 {r4(v['value'] - rules.ceiling_height)} m。")
        meaning = "管頂超過天花板，表示這條管會穿出天花板，吊頂內放不下。"
    return {
        "title": f"{n} {TYPE_ZH[CLEAR]}：{p.id}",
        "calc": calc,
        "meaning": meaning,
        "look": "立面圖：橘色虛線是淨高線、深灰實線是天花板。整條管要落在兩條線之間。",
    }


def _slope(scene: Scene, v: dict, n: str) -> dict:
    p = scene.pipe(v["ids"][0])
    rules = scene.rules
    lo, hi = rules.min_drain_slope, rules.max_drain_slope
    calc = ""
    slope = v["value"]
    for a, b in zip(p.points, p.points[1:], strict=False):
        run = math.hypot(b[0] - a[0], b[1] - a[1])
        if run < 1e-6:
            continue
        s = (a[2] - b[2]) / run
        if s < lo - 1e-9 or s > hi + 1e-9:
            slope = s
            calc = (f"坡度 = 高差 ÷ 水平距離 = ({a[2]} − {b[2]}) ÷ {r4(run)} = {_pct(s)}；"
                    f"規則要求 {_pct(lo)}~{_pct(hi)}（由第一點往最後一點下降）。")
            break
    if slope < 0:
        meaning = "反坡：下游端比上游端高，水會倒流。"
    elif slope < lo:
        meaning = "太平：水流不動，會積水、結垢，最後堵塞。"
    else:
        meaning = "太陡：水流太快，水先流走、固體留在管內，反而容易堵。"
    return {
        "title": f"{n} {TYPE_ZH[SLOPE]}：{p.id}",
        "calc": calc,
        "meaning": meaning + "排水靠重力流動，所以高低關係比其他管線更重要。",
        "look": "立面圖：排水管應是由高（第一點）到低（最後一點）的斜線。系統顏色是棕色。",
    }


_EXPLAINERS = {CLASH: _clash, WALL: _wall, CLEAR: _clear, SLOPE: _slope}


def explain(scene: Scene, violation: dict, number: str = "") -> dict:
    """{title, calc, meaning, look} for one violation dict (as produced by Violation.to_dict)."""
    return _EXPLAINERS[violation["type"]](scene, violation, number)
