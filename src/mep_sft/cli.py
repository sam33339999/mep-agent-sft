from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assembler import assemble_file
from .demo import CASES, demo_scene
from .evaluate import evaluate, format_report, gold_predictions, load_jsonl
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


def cmd_assemble(args) -> int:
    n = assemble_file(Path(args.raw), Path(args.out), include_meta=not args.no_meta)
    print(f"組裝 {n} 筆 -> {args.out}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .server.app import create_app

    uvicorn.run(create_app(args.db), host=args.host, port=args.port)
    return 0


def cmd_eval(args) -> int:
    raw = load_jsonl(Path(args.raw))
    preds = gold_predictions(raw) if args.gold else load_jsonl(Path(args.pred))
    rep = evaluate({s["id"]: s for s in raw}, preds)
    print(format_report(rep))
    if args.json:
        Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def cmd_ifc(args) -> int:
    from .demo import demo_scene
    from .ifc_export import export_ifc

    scene = demo_scene(args.case)
    export_ifc(scene, args.out)
    print(f"IFC -> {args.out}")
    return 0


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
    a = sub.add_parser("assemble", help="raw JSONL -> 訓練用 JSONL")
    a.add_argument("--raw", default="data/raw.jsonl")
    a.add_argument("--out", default="data/train.jsonl")
    a.add_argument("--no-meta", action="store_true", help="輸出只含 messages")
    a.set_defaults(fn=cmd_assemble)
    sv = sub.add_parser("serve", help="啟動審核網站")
    sv.add_argument("--db", default="data/review.db")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)
    e = sub.add_parser("eval", help="用規則檢查器替模型輸出打分")
    e.add_argument("--raw", default="data/raw.jsonl", help="含場景的 raw JSONL")
    e.add_argument("--pred", help='預測 JSONL，每行 {"id":..., "output":...}')
    e.add_argument("--gold", action="store_true", help="用標準答案當預測（自我檢查，應為 100%）")
    e.add_argument("--json", help="另存完整結果")
    e.set_defaults(fn=cmd_eval)
    i = sub.add_parser("ifc", help="把示範場景輸出成 IFC（需 --extra ifc）")
    i.add_argument("--case", default="two-pipes", choices=sorted(CASES))
    i.add_argument("--out", default="out/scene.ifc")
    i.set_defaults(fn=cmd_ifc)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
