"""FastAPI review server: browse generated samples, edit, approve into the SFT pool, export."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from plotly.offline import get_plotlyjs_version
from pydantic import BaseModel, Field

from ..answer import apply_fix
from ..assembler import assemble as assemble_sample
from ..assembler import fixed_pipes_payload, pick_variant, prompt_messages
from ..generator import generate_dataset
from ..scene import Scene
from .db import Store
from .logic import answer_status, canonical, figure_json, regenerate_text, valid_payload

WEB = Path(__file__).resolve().parents[3] / "web"


class Preview(BaseModel):
    fixed_pipes: list[dict]


class NewVersion(BaseModel):
    fixed_pipes: list[dict] | None = None
    assistant_text: str | None = None
    regenerate_text: bool = True
    note: str = ""


class ReviewBody(BaseModel):
    decision: str = Field(pattern="^(approve|reject|reset)$")
    reason: str = ""
    override: bool = False


class GenerateBody(BaseModel):
    n: int = Field(default=10, ge=1, le=500)
    seed: int = 0
    difficulty: int | None = Field(default=None, ge=1, le=4)


def create_app(db_path: str = "data/review.db") -> FastAPI:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    app = FastAPI(title="MEP SFT review")

    def load(sample_id: str) -> dict:
        s = store.get_sample(sample_id)
        if s is None:
            raise HTTPException(404, "sample not found")
        return s

    def latest(sample_id: str) -> dict:
        return store.versions(sample_id)[-1]

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (WEB / "index.html").read_text(encoding="utf-8")
        return html.replace("{{PLOTLY_VERSION}}", get_plotlyjs_version())

    @app.get("/api/stats")
    def stats():
        return store.stats()

    @app.get("/api/samples")
    def list_samples(status: str | None = None, difficulty: int | None = None,
                     limit: int = Query(200, le=1000), offset: int = 0):
        return store.list_samples(status, difficulty, limit, offset)

    @app.get("/api/samples/{sample_id}")
    def get_sample(sample_id: str):
        s = load(sample_id)
        versions = store.versions(sample_id)
        cur = versions[-1]
        scene = Scene.from_dict(s["scene"])
        fixed = apply_fix(scene, cur["fixed_pipes"])
        from ..rules import check_all
        msgs = prompt_messages(scene, s["variant"])
        return {
            **s,
            "versions": [{k: v for k, v in x.items() if k != "assistant_text"} for x in versions],
            "current": cur,
            "status_check": answer_status(s, cur),
            "violations_after": [v.to_dict() for v in check_all(fixed)] if fixed else None,
            "system": msgs[0]["content"],
            "user": msgs[1]["content"],
            "reviews": store.reviews(sample_id),
            "fixed_scene": fixed.to_dict() if fixed else None,
        }

    @app.get("/api/samples/{sample_id}/figure")
    def figure(sample_id: str, version: int | None = None):
        s = load(sample_id)
        vs = store.versions(sample_id)
        v = vs[-1] if version is None else next((x for x in vs if x["version"] == version), None)
        if v is None:
            raise HTTPException(404, "version not found")
        fig, _ = figure_json(s, v["fixed_pipes"])
        return fig

    @app.post("/api/samples/{sample_id}/preview")
    def preview(sample_id: str, body: Preview):
        s = load(sample_id)
        if not valid_payload(body.fixed_pipes):
            raise HTTPException(422, "fixed_pipes 格式錯誤")
        try:
            fig, after = figure_json(s, body.fixed_pipes)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {"figure": fig, "violations_after": after}

    @app.post("/api/samples/{sample_id}/versions")
    def new_version(sample_id: str, body: NewVersion):
        s = load(sample_id)
        cur = latest(sample_id)
        pipes = body.fixed_pipes if body.fixed_pipes is not None else cur["fixed_pipes"]
        if not valid_payload(pipes) or apply_fix(Scene.from_dict(s["scene"]), pipes) is None:
            raise HTTPException(422, "fixed_pipes 格式錯誤或含不存在的管線")
        pipes_changed = canonical(pipes) != canonical(cur["fixed_pipes"])
        text = cur["assistant_text"]
        if body.assistant_text is not None:
            text = body.assistant_text
        elif pipes_changed and body.regenerate_text and s["variant"] != "detect_only":
            text = regenerate_text(s, pipes)
        if not pipes_changed and text == cur["assistant_text"]:
            raise HTTPException(400, "沒有任何修改")
        n = store.add_version(sample_id, pipes, text, "human", body.note)
        return {"version": n, "status_check": answer_status(s, latest(sample_id))}

    @app.post("/api/samples/{sample_id}/review")
    def review(sample_id: str, body: ReviewBody):
        s = load(sample_id)
        cur = latest(sample_id)
        if body.decision == "approve":
            check = answer_status(s, cur)
            if not check["ok"] and not body.override:
                raise HTTPException(409, {"message": "答案未通過規則檢查", **check})
            if not check["ok"] and not body.reason.strip():
                raise HTTPException(422, "override 需要填寫原因")
        store.review(sample_id, cur["version"], body.decision, body.reason, body.override)
        return store.get_sample(sample_id)["status"]

    @app.post("/api/generate")
    def generate(body: GenerateBody):
        samples, stats = generate_dataset(body.n, body.seed, body.difficulty)
        added = 0
        for raw in samples:
            variant = pick_variant(raw)
            scene = Scene.from_dict(raw["scene"])
            text = assemble_sample(raw, variant)["messages"][2]["content"]
            pipes = fixed_pipes_payload(scene, Scene.from_dict(raw["fixed"]))
            added += store.add_sample(raw, variant, pipes, text)
        return {"added": added, "duplicates": len(samples) - added,
                "attempts": {d: st.attempts for d, st in stats.items() if st.attempts}}

    @app.get("/api/export", response_class=PlainTextResponse)
    def export(include_human: bool = True):
        lines = []
        for s in store.approved():
            ver = next(v for v in store.versions(s["id"]) if v["version"] == s["approved_version"])
            if ver["source"] == "human" and not include_human:
                continue
            scene = Scene.from_dict(s["scene"])
            rec = {
                "messages": prompt_messages(scene, s["variant"])
                + [{"role": "assistant", "content": ver["assistant_text"]}],
                "meta": {
                    "id": s["id"], "seed": s["seed"], "difficulty": s["difficulty"],
                    "variant": s["variant"], "issue_types": s["issue_types"],
                    "scene_hash": s["scene_hash"], "source": ver["source"],
                    "version": ver["version"],
                },
            }
            lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        return PlainTextResponse(
            "\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=sft.jsonl"},
        )

    return app
