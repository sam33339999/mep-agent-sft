"""Score model outputs with the same hard rules that produced the ground truth.

A prediction is {"id": sample id, "output": assistant text[, "variant": ...]}; the scenes come from
the raw samples file. Fix tasks pass when the proposed paths leave zero violations; detect tasks pass
on an exact match of the listed violations.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .answer import apply_fix, extract_json
from .assembler import pick_variant
from .rules import check_all
from .scene import Scene


def _pairs(items) -> set[tuple]:
    return {(v.get("type"), tuple(v.get("ids", []))) for v in items if isinstance(v, dict)}


def score(sample: dict, output: str, variant: str | None = None) -> dict:
    """Metrics for one output. Always returns every key so results can be averaged."""
    variant = variant or pick_variant(sample)
    scene = Scene.from_dict(sample["scene"])
    before = check_all(scene)
    res = {
        "id": sample["id"], "variant": variant, "difficulty": sample["difficulty"],
        "issue_types": sorted({v.type for v in before}), "format_ok": False, "passed": False,
        "resolved_ratio": 0.0, "new_violations": 0, "unnecessary_change": False,
        "changed_pipes": 0, "gold_changed_pipes": len(sample["actions"]) and len(
            {a["pipe_id"] for a in sample["actions"]}), "precision": 0.0, "recall": 0.0,
    }
    think_ok = output.count("<think>") == 1 and output.count("</think>") == 1
    data = extract_json(output)
    if not think_ok or data is None:
        return res
    if variant == "detect_only":
        listed = data.get("violations")
        if not isinstance(listed, list):
            return res
        got, want = _pairs(listed), {(v.type, tuple(v.ids)) for v in before}
        hit = len(got & want)
        res.update(
            format_ok=True, passed=got == want,
            precision=hit / len(got) if got else float(not want),
            recall=hit / len(want) if want else 1.0,
        )
        return res
    pipes = data.get("fixed_pipes")
    fixed = apply_fix(scene, pipes) if isinstance(pipes, list) else None
    if fixed is None:
        return res
    after = check_all(fixed)
    res.update(
        format_ok=True, passed=not after, changed_pipes=len(pipes),
        resolved_ratio=1.0 if not before and not after else
        max(0.0, (len(before) - len(after)) / len(before)) if before else 0.0,
        new_violations=len({(v.type, tuple(v.ids)) for v in after}
                           - {(v.type, tuple(v.ids)) for v in before}),
        unnecessary_change=not before and bool(pipes),
    )
    return res


def reward(sample: dict, output: str, variant: str | None = None) -> float:
    """Scalar in [0, 1] usable as an RL reward: format 0.1, progress 0.5, fully valid 0.4,
    minus 0.1 per newly introduced violation."""
    r = score(sample, output, variant)
    if not r["format_ok"]:
        return 0.0
    if r["variant"] == "detect_only":
        return 0.1 + 0.5 * (r["precision"] + r["recall"]) / 2 + 0.4 * r["passed"]
    value = 0.1 + 0.5 * r["resolved_ratio"] + 0.4 * r["passed"] - 0.1 * r["new_violations"]
    return max(0.0, min(1.0, value))


def _rate(rows: list[dict], key: str = "passed") -> float:
    return sum(r[key] for r in rows) / len(rows) if rows else 0.0


def evaluate(samples: dict[str, dict], preds: list[dict]) -> dict:
    rows, missing = [], 0
    for p in preds:
        sample = samples.get(p["id"])
        if sample is None:
            missing += 1
            continue
        rows.append(score(sample, p.get("output", ""), p.get("variant")))
    by = lambda keyfn: {
        k: {"n": len(v), "pass_rate": round(_rate(v), 4)}
        for k, v in sorted(_group(rows, keyfn).items(), key=lambda kv: str(kv[0]))
    }
    fix_rows = [r for r in rows if r["variant"] != "detect_only"]
    detect_rows = [r for r in rows if r["variant"] == "detect_only"]
    return {
        "n": len(rows), "unknown_ids": missing,
        "pass_rate": round(_rate(rows), 4),
        "format_rate": round(_rate(rows, "format_ok"), 4),
        "by_difficulty": by(lambda r: r["difficulty"]),
        "by_variant": by(lambda r: r["variant"]),
        "by_issue_type": by_issue(rows),
        "fix": {
            "n": len(fix_rows), "mean_resolved": round(
                sum(r["resolved_ratio"] for r in fix_rows) / len(fix_rows), 4) if fix_rows else 0.0,
            "introduced_new_violations": sum(1 for r in fix_rows if r["new_violations"]),
            "unnecessary_changes": sum(r["unnecessary_change"] for r in fix_rows),
        },
        "detect": {
            "n": len(detect_rows),
            "precision": round(sum(r["precision"] for r in detect_rows) / len(detect_rows), 4)
            if detect_rows else 0.0,
            "recall": round(sum(r["recall"] for r in detect_rows) / len(detect_rows), 4)
            if detect_rows else 0.0,
        },
        "rows": rows,
    }


def _group(rows, keyfn):
    out = defaultdict(list)
    for r in rows:
        out[keyfn(r)].append(r)
    return out


def by_issue(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        for t in r["issue_types"] or ["(clean)"]:
            groups[t].append(r)
    return {k: {"n": len(v), "pass_rate": round(_rate(v), 4)} for k, v in sorted(groups.items())}


by_issue_type = by_issue


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def gold_predictions(raw: list[dict]) -> list[dict]:
    """The assembler's own answers, as predictions: a perfect model. Used as a self-check."""
    from .assembler import assemble

    return [
        {"id": s["id"], "variant": (rec := assemble(s))["meta"]["variant"],
         "output": rec["messages"][2]["content"]}
        for s in raw
    ]


def format_report(rep: dict) -> str:
    lines = [
        f"樣本 {rep['n']}（未知 id {rep['unknown_ids']}）",
        f"通過率 {rep['pass_rate']:.1%}　格式正確率 {rep['format_rate']:.1%}",
        "依難度：" + "　".join(f"{k}: {v['pass_rate']:.0%}（{v['n']}）"
                              for k, v in rep["by_difficulty"].items()),
        "依題型：" + "　".join(f"{k}: {v['pass_rate']:.0%}（{v['n']}）"
                              for k, v in rep["by_variant"].items()),
        "依問題類型：" + "　".join(f"{k}: {v['pass_rate']:.0%}（{v['n']}）"
                                  for k, v in rep["by_issue_type"].items()),
        (
            f"修正題：平均消除率 {rep['fix']['mean_resolved']:.1%}；"
            f"引入新違規 {rep['fix']['introduced_new_violations']} 題；"
            f"無謂修改 {rep['fix']['unnecessary_changes']} 題"
        ),
        f"偵測題：precision {rep['detect']['precision']:.1%}　recall {rep['detect']['recall']:.1%}",
    ]
    return "\n".join(lines)
