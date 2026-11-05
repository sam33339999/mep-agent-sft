from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo import demo_scene
from .rules import check_all
from .solver import solve
from .viz_plotly import render_before_after


def cmd_demo(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scene = demo_scene()
    before = check_all(scene)
    res = solve(scene)
    after = check_all(res.fixed)
    (out / "scene.json").write_text(scene.to_json(indent=2), encoding="utf-8")
    (out / "fixed.json").write_text(res.fixed.to_json(indent=2), encoding="utf-8")
    (out / "violations.json").write_text(
        json.dumps({"before": [v.to_dict() for v in before],
                    "after": [v.to_dict() for v in after]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    render_before_after(scene, before, res.fixed, after, {a.pipe_id for a in res.actions},
                        str(out / "scene.html"))
    print(f"違規 {len(before)} -> {len(after)}；solver {'成功' if res.ok else '失敗'}")
    for a in res.actions:
        print(f"  {a.pipe_id} 垂直位移 {a.dz:+.2f} m（{a.resolved}）")
    print(f"輸出：{out}/scene.html")
    return 0 if res.ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mep_sft")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="跑固定的兩管碰撞範例並輸出 HTML")
    d.add_argument("--out", default="out")
    d.set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
