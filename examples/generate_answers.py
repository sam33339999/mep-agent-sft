"""Let a model (optionally with a LoRA adapter) answer held-out prompts, for `mep_sft eval`.

Runs in the LLaMA-Factory environment (torch + transformers + peft), not in this project's venv:
  python examples/generate_answers.py --raw data/exp1/val_raw.jsonl --out preds.jsonl \
      [--adapter outputs/mep_sft_lora] [--model Qwen/Qwen3-0.6B] [--limit 100]
then:  uv run python -m mep_sft eval --raw data/exp1/val_raw.jsonl --pred preds.jsonl

The prompt is exactly what training saw: system + user turns in ChatML, ending at the assistant
header, so the model has to produce <think>...</think> and the json block itself. Greedy decoding.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from mep_sft.assembler import assemble


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-0.6B")
    ap.add_argument("--adapter")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--max-new", type=int, default=1800)
    a = ap.parse_args()

    raw = [json.loads(x) for x in Path(a.raw).read_text(encoding="utf-8").splitlines() if x]
    if a.limit:
        raw = raw[: a.limit]
    tok = AutoTokenizer.from_pretrained(a.model, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16).cuda().eval()
    if a.adapter:
        model = PeftModel.from_pretrained(model, a.adapter).eval()

    items = []
    for s in raw:
        rec = assemble(s)
        sysm, user = rec["messages"][0]["content"], rec["messages"][1]["content"]
        prompt = (f"<|im_start|>system\n{sysm}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
                  "<|im_start|>assistant\n")
        items.append((s["id"], rec["meta"]["variant"], prompt))
    items.sort(key=lambda t: len(t[2]))  # similar lengths per batch: less padding

    out, t0 = [], time.time()
    for i in range(0, len(items), a.batch):
        chunk = items[i : i + a.batch]
        enc = tok([c[2] for c in chunk], return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=a.max_new, do_sample=False,
                                 eos_token_id=tok.convert_tokens_to_ids("<|im_end|>"),
                                 pad_token_id=tok.pad_token_id)
        for (sid, variant, _), g in zip(chunk, gen, strict=True):
            text = tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)
            out.append({"id": sid, "variant": variant, "output": text})
        print(f"{len(out)}/{len(items)} done, {time.time() - t0:.0f}s", flush=True)
    Path(a.out).write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out),
                           encoding="utf-8")


if __name__ == "__main__":
    main()
