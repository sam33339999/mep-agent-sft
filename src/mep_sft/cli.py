from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo import CASES, demo_scene
from .generator import generate_dataset
from .rules import check_all
from .solver import solve
from .viz_plotly import render_before_after


def cmd_demo(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scene = demo_scene(args.case)
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
        print(f"  {a.pipe_id} {a.kind} {a.detail}（{a.resolved}）")
    for v in res.remaining:
        print(f"  未解決：{v.message}")
    print(f"輸出：{out}/scene.html")
    return 0 if res.ok else 1


def cmd_generate(args) -> int:
    samples, stats = generate_dataset(args.n, args.seed, args.difficulty)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"寫入 {len(samples)} 筆 -> {out}")
    for d, st in sorted(stats.items()):
        if st.attempts:
            rate = st.accepted / st.attempts
            print(f"  難度 {d}: 接受 {st.accepted} / 嘗試 {st.attempts}（{rate:.0%}）丟棄原因 {st.reasons}")
    return 0 if len(samples) == args.n else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mep_sft")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="跑固定的兩管碰撞範例並輸出 HTML")
    d.add_argument("--out", default="out")
    d.add_argument("--case", default="two-pipes", choices=sorted(CASES))
    d.set_defaults(fn=cmd_demo)
    g = sub.add_parser("generate", help="產生場景與 solver 解（raw JSONL）")
    g.add_argument("--n", type=int, default=100)
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--difficulty", type=int, choices=[1, 2, 3, 4], default=None)
    g.add_argument("--out", default="data/raw.jsonl")
    g.set_defaults(fn=cmd_generate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
