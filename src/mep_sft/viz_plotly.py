"""Plotly POC viewer: 3D before/after plus two elevation views, written as one HTML file.

Reads only Scene + Violation data, so it can be replaced by a Three.js viewer later.
"""

from __future__ import annotations

import math

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .geometry import Vec, cross, norm, sub
from .rules import CLASH, CLEAR, Violation, clash_measure
from .scene import Pipe, Scene, Wall, r4

SYSTEM_COLOR = {
    "drainage": "#8c564b",
    "duct": "#7f7f7f",
    "water": "#1f77b4",
    "electrical": "#e6a700",
}
MOVED_COLOR = "#2ca02c"
BAD, GOOD = "#d62728", "#2ca02c"
SIDES = 14
VIEWS = {"xz": (0, "X"), "yz": (1, "Y")}  # view -> (horizontal axis index, label)


def _cylinder(a: Vec, b: Vec, r: float):
    d = sub(b, a)
    L = norm(d)
    u = (d[0] / L, d[1] / L, d[2] / L)
    ref = (0.0, 0.0, 1.0) if abs(u[2]) < 0.9 else (1.0, 0.0, 0.0)
    n1 = cross(u, ref)
    n1 = tuple(c / norm(n1) for c in n1)
    n2 = cross(u, n1)
    xs, ys, zs = [], [], []
    for end in (a, b):
        for k in range(SIDES):
            th = 2 * math.pi * k / SIDES
            xs.append(end[0] + r * (math.cos(th) * n1[0] + math.sin(th) * n2[0]))
            ys.append(end[1] + r * (math.cos(th) * n1[1] + math.sin(th) * n2[1]))
            zs.append(end[2] + r * (math.cos(th) * n1[2] + math.sin(th) * n2[2]))
    i, j, k_ = [], [], []
    for k in range(SIDES):
        k2 = (k + 1) % SIDES
        i += [k, k2]
        j += [k2, SIDES + k2]
        k_ += [SIDES + k, SIDES + k]
    return xs, ys, zs, i, j, k_


def _pipe_color(p: Pipe, highlight: bool) -> str:
    return MOVED_COLOR if highlight else SYSTEM_COLOR.get(p.system, "#444")


def _pipe_traces(p: Pipe, highlight: bool, show_legend: bool):
    traces = []
    for n, (a, b) in enumerate(zip(p.points[:-1], p.points[1:], strict=True)):
        x, y, z, i, j, k = _cylinder(a, b, p.diameter / 2)
        traces.append(
            go.Mesh3d(
                x=x, y=y, z=z, i=i, j=j, k=k, color=_pipe_color(p, highlight), opacity=1.0,
                flatshading=True, name=f"{p.id} ({p.system})", legendgroup=p.id,
                showlegend=show_legend and n == 0,
                hovertext=f"{p.id} {p.system} Ø{p.diameter} m", hoverinfo="text",
            )
        )
    return traces


def _wall_base(w: Wall) -> list[tuple[float, float]]:
    dx, dy = w.p1[0] - w.p0[0], w.p1[1] - w.p0[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * w.thickness / 2, dx / L * w.thickness / 2
    return [
        (w.p0[0] + nx, w.p0[1] + ny), (w.p1[0] + nx, w.p1[1] + ny),
        (w.p1[0] - nx, w.p1[1] - ny), (w.p0[0] - nx, w.p0[1] - ny),
    ]


def _wall_trace(w: Wall):
    base = _wall_base(w)
    xs = [c[0] for c in base] * 2
    ys = [c[1] for c in base] * 2
    zs = [w.z0] * 4 + [w.z0 + w.height] * 4
    tris = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=[t[0] for t in tris], j=[t[1] for t in tris], k=[t[2] for t in tris],
        color="#999999", opacity=0.25, name=f"wall {w.id}", hovertext=f"wall {w.id}",
        hoverinfo="text", showlegend=False,
    )


def _opening_corners(w: Wall, o) -> list[tuple[float, float, float]]:
    """Opening rectangle on the wall mid-plane, world coordinates."""
    dx, dy = w.p1[0] - w.p0[0], w.p1[1] - w.p0[1]
    L = math.hypot(dx, dy)
    pt = lambda u, z: (w.p0[0] + dx / L * u, w.p0[1] + dy / L * u, z)
    return [pt(o.u0, o.z0), pt(o.u1, o.z0), pt(o.u1, o.z1), pt(o.u0, o.z1)]


def _opening_traces_3d(w: Wall):
    out = []
    for o in w.openings:
        c = _opening_corners(w, o)
        c.append(c[0])
        out.append(go.Scatter3d(
            x=[q[0] for q in c], y=[q[1] for q in c], z=[q[2] for q in c], mode="lines",
            line={"color": "#ff7f0e", "width": 5}, name="開口", hoverinfo="name", showlegend=False,
        ))
    return out


def _violation_trace(vs: list[Violation]):
    return go.Scatter3d(
        x=[v.location[0] for v in vs], y=[v.location[1] for v in vs],
        z=[v.location[2] for v in vs], mode="markers",
        marker={"size": 9, "color": "red", "symbol": "diamond"},
        text=[v.message for v in vs], hoverinfo="text", name="違規", showlegend=False,
    )


# ---------------------------------------------------------------- elevation views

def _h(pt: Vec, view: str) -> float:
    return pt[VIEWS[view][0]]


def _fill(xs, ys, color, name, hover, opacity=1.0):
    return go.Scatter(
        x=xs + [xs[0]], y=ys + [ys[0]], mode="lines", fill="toself", fillcolor=color,
        opacity=opacity, line={"color": "#333", "width": 1}, name=name, hovertext=hover,
        hoverinfo="text", showlegend=False,
    )


def _pipe_shapes(p: Pipe, view: str, color: str):
    """A pipe seen side-on is a rectangle; seen end-on it is a circle."""
    r = p.diameter / 2
    out = []
    hover = f"{p.id} {p.system} Ø{p.diameter} m"
    for a, b in zip(p.points[:-1], p.points[1:], strict=True):
        ha, va, hb, vb = _h(a, view), a[2], _h(b, view), b[2]
        length = math.hypot(hb - ha, vb - va)
        if length < 1e-6:  # looking down the pipe axis
            pts = [(ha + r * math.cos(t), va + r * math.sin(t))
                   for t in (2 * math.pi * k / 32 for k in range(32))]
            out.append(_fill([q[0] for q in pts], [q[1] for q in pts], color, p.id, hover))
            label_at = (ha, va + r)
        else:
            nh, nv = -(vb - va) / length * r, (hb - ha) / length * r
            xs = [ha + nh, hb + nh, hb - nh, ha - nh]
            ys = [va + nv, vb + nv, vb - nv, va - nv]
            out.append(_fill(xs, ys, color, p.id, hover))
            label_at = ((ha + hb) / 2, max(va, vb) + r)
    out.append(
        go.Scatter(x=[label_at[0]], y=[label_at[1] + 0.04], mode="text", text=[p.id],
                   textposition="top center", hoverinfo="skip", showlegend=False)
    )
    return out


def _wall_shape(w: Wall, view: str):
    hs = [c[VIEWS[view][0]] for c in _wall_base(w)]
    h0, h1, z0, z1 = min(hs), max(hs), w.z0, w.z0 + w.height
    return _fill([h0, h1, h1, h0], [z0, z0, z1, z1], "#bbbbbb", f"wall {w.id}",
                 f"wall {w.id}", opacity=0.5)


def _dimension(scene: Scene, v: Violation, view: str) -> tuple[list, tuple[str, bool] | None]:
    """Dimension line for a rule measurement, recomputed on `scene` (so it works before and after)."""
    rules = scene.rules
    if v.type == CLASH:
        a, b = scene.pipe(v.ids[0]), scene.pipe(v.ids[1])
        gap, c1, c2 = clash_measure(a, b)
        d = sub(c2, c1)
        n = norm(d)
        u = (d[0] / n, d[1] / n, d[2] / n) if n > 1e-9 else (0.0, 0.0, 1.0)
        p1 = tuple(c1[i] + u[i] * a.outer_radius for i in range(3))
        p2 = tuple(c2[i] - u[i] * b.outer_radius for i in range(3))
        ok = gap >= rules.clearance - 1e-9
        text = f"{a.id}-{b.id} 間距 {r4(gap)} m（需 ≥ {rules.clearance}）"
    elif v.type == CLEAR:
        p = scene.pipe(v.ids[0])
        low = abs(v.threshold - rules.min_clear_height) < 1e-9
        if low:
            pt = min(p.points, key=lambda q: q[2])
            val = pt[2] - p.outer_radius
            ok = val >= rules.min_clear_height - 1e-9
            text = f"{p.id} 管底 {r4(val)} m（需 ≥ {rules.min_clear_height}）"
        else:
            pt = max(p.points, key=lambda q: q[2])
            val = pt[2] + p.outer_radius
            ok = val <= rules.ceiling_height + 1e-9
            text = f"{p.id} 管頂 {r4(val)} m（需 ≤ {rules.ceiling_height}）"
        p1, p2 = (pt[0], pt[1], val), (pt[0], pt[1], v.threshold)
    else:
        return [], None
    label = (text, ok)
    h1, h2 = _h(p1, view), _h(p2, view)
    if math.hypot(h2 - h1, p2[2] - p1[2]) < 1e-6:
        return [], label  # measurement runs along the view direction: no line, still listed
    line = go.Scatter(
        x=[h1, h2], y=[p1[2], p2[2]], mode="lines+markers",
        line={"color": GOOD if ok else BAD, "width": 3},
        marker={"size": 8, "symbol": "line-ew-open"}, hoverinfo="skip", showlegend=False,
    )
    return [line], label


def _section_traces(scene: Scene, watch: list[Violation], view: str, moved: set[str]):
    """Returns (traces, dimension labels)."""
    traces = []
    for w in scene.walls:
        traces.append(_wall_shape(w, view))
        for o in w.openings:
            c = _opening_corners(w, o)
            hs = [q[VIEWS[view][0]] for q in c]
            if max(hs) - min(hs) > 1e-6:  # edge-on openings have no width in this view
                traces.append(_fill([min(hs), max(hs), max(hs), min(hs)], [o.z0, o.z0, o.z1, o.z1],
                                    "#ffffff", f"opening {w.id}", f"{w.id} 開口"))
    for p in scene.pipes:
        traces += _pipe_shapes(p, view, _pipe_color(p, p.id in moved))
    labels = []
    for v in watch:
        ts, label = _dimension(scene, v, view)
        traces += ts
        if label:
            labels.append(label)
    return traces, labels


HALF_WIDTH = 1.6  # zoom window half-width (m) around the violations


def _window(scene: Scene, watch: list[Violation], view: str):
    """Zoom to the violations; fall back to the whole scene when there are none."""
    z_hi = scene.rules.ceiling_height + 0.15
    if watch:
        hc = sum(_h(v.location, view) for v in watch) / len(watch)
        zc = sum(v.location[2] for v in watch) / len(watch)
        return (hc - HALF_WIDTH, hc + HALF_WIDTH), (min(zc - 1.0, z_hi - 1.6), z_hi)
    hs = [_h(pt, view) for p in scene.pipes for pt in p.points]
    return (min(hs) - 0.8, max(hs) + 0.8), (-0.2, z_hi)


def _style_section(fig, scene, watch, labels, view, row, col):
    sp = fig.get_subplot(row, col)
    xa, ya = sp.xaxis.plotly_name.replace("axis", ""), sp.yaxis.plotly_name.replace("axis", "")
    (x0, x1), (y0, y1) = _window(scene, watch, view)
    fig.update_xaxes(range=[x0, x1], title_text=f"{VIEWS[view][1]} (m)", row=row, col=col)
    fig.update_yaxes(range=[y0, y1], title_text="標高 Z (m)", scaleanchor=xa, scaleratio=1,
                     row=row, col=col)
    r = scene.rules
    for z, label, color, dash in (
        (0.0, "樓板 0", "#555", "solid"),
        (r.min_clear_height, f"淨高線 {r.min_clear_height}", "#e07b00", "dash"),
        (r.ceiling_height, f"天花板 {r.ceiling_height}", "#555", "solid"),
    ):
        fig.add_shape(type="line", x0=x0, x1=x1, y0=z, y1=z, xref=xa, yref=ya,
                      line={"color": color, "dash": dash, "width": 1.5})
        fig.add_annotation(x=x1, y=z, xref=xa, yref=ya, text=label, showarrow=False,
                           xanchor="right", yanchor="bottom", font={"color": color, "size": 11})
    for n, (text, ok) in enumerate(labels):
        fig.add_annotation(
            x=0.01, y=0.98 - 0.09 * n, xref=f"{xa} domain", yref=f"{ya} domain",
            text=("✓ " if ok else "✗ ") + text, showarrow=False, xanchor="left", yanchor="top",
            font={"color": GOOD if ok else BAD, "size": 13}, bgcolor="rgba(255,255,255,0.8)",
        )


def render_before_after(
    before: Scene, before_violations: list[Violation],
    after: Scene, after_violations: list[Violation],
    moved: set[str], out_path: str, title: str = "MEP 修正前後",
) -> None:
    s3d = {"type": "scene"}
    xy = {"type": "xy"}
    fig = make_subplots(
        rows=3, cols=2, row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.06,
        specs=[[s3d, s3d], [xy, xy], [xy, xy]],
        subplot_titles=(
            f"3D 修正前（違規 {len(before_violations)}）",
            f"3D 修正後（違規 {len(after_violations)}；綠色＝被移動）",
            "立面圖（沿 Y 看）修正前", "立面圖（沿 Y 看）修正後",
            "立面圖（沿 X 看）修正前", "立面圖（沿 X 看）修正後",
        ),
    )
    for col, (scene, vs, mv) in enumerate(
        ((before, before_violations, set()), (after, after_violations, moved)), start=1
    ):
        for w in scene.walls:
            fig.add_trace(_wall_trace(w), row=1, col=col)
            for t in _opening_traces_3d(w):
                fig.add_trace(t, row=1, col=col)
        for p in scene.pipes:
            for t in _pipe_traces(p, p.id in mv, show_legend=(col == 2)):
                fig.add_trace(t, row=1, col=col)
        if vs:
            fig.add_trace(_violation_trace(vs), row=1, col=col)
        for row, view in ((2, "xz"), (3, "yz")):
            # dimensions track the originally-violating measurements, so "after" shows them passing
            traces, labels = _section_traces(scene, before_violations, view, mv)
            for t in traces:
                fig.add_trace(t, row=row, col=col)
            _style_section(fig, scene, before_violations, labels, view, row, col)
    for name in ("scene", "scene2"):
        fig.update_layout(**{
            name: {
                "aspectmode": "data",
                "zaxis": {"range": [0, before.rules.ceiling_height + 0.2]},
                "camera": {"eye": {"x": 1.4, "y": -1.6, "z": 0.7}},
            }
        })
    fig.update_layout(title=title, height=1500, margin={"l": 40, "r": 20, "t": 70, "b": 30})
    fig.write_html(out_path, include_plotlyjs="cdn")
