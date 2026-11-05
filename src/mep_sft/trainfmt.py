"""Check that a training JSONL is what SFT frameworks expect, and describe it for them.

LLaMA-Factory / Axolotl / Unsloth all read JSONL through Hugging Face `datasets`; LLaMA-Factory's
"sharegpt"-style (OpenAI messages) loader additionally needs: roles in {system, user, assistant},
an optional leading system turn, then strictly alternating user / assistant, ending on assistant.
"""

from __future__ import annotations

import json
from pathlib import Path

DATASET_NAME = "mep_sft"


def check_record(rec: dict, line: int = 0) -> list[str]:
    errs: list[str] = []
    msgs = rec.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return [f"line {line}: missing messages"]
    for m in msgs:
        if set(m) != {"role", "content"} or not isinstance(m["content"], str) or not m["content"]:
            errs.append(f"line {line}: bad message keys/content {sorted(m)}")
    roles = [m.get("role") for m in msgs]
    body = roles[1:] if roles[:1] == ["system"] else roles
    if not body or body[-1] != "assistant" or any(
        r != ("user", "assistant")[i % 2] for i, r in enumerate(body)
    ):
        errs.append(f"line {line}: roles must be [system] + user/assistant alternating, got {roles}")
    tail = msgs[-1]["content"] if msgs else ""
    if tail.count("<think>") != 1 or tail.count("</think>") != 1 or not tail.startswith("<think>"):
        errs.append(f"line {line}: assistant must start with exactly one <think>...</think>")
    return errs


def validate_file(path: Path, load_with_datasets: bool = True) -> dict:
    """Structural checks on every record, then a real `datasets.load_dataset` round-trip."""
    errors: list[str] = []
    n = 0
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {i}: invalid JSON ({e})")
            continue
        n += 1
        errors += check_record(rec, i)
    report = {"records": n, "errors": errors[:20], "error_count": len(errors), "datasets": None}
    if load_with_datasets:
        try:
            from datasets import load_dataset
        except ImportError:
            report["datasets"] = "not installed (uv sync --extra trainfmt)"
        else:
            ds = load_dataset("json", data_files=str(path), split="train")
            first = ds[0]["messages"]
            report["datasets"] = {
                "rows": len(ds), "columns": ds.column_names,
                "first_roles": [m["role"] for m in first],
            }
    return report


def dataset_info(file_name: str = "train.jsonl") -> dict:
    """Entry for LLaMA-Factory's data/dataset_info.json (OpenAI-style messages)."""
    return {
        DATASET_NAME: {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role", "content_tag": "content", "user_tag": "user",
                "assistant_tag": "assistant", "system_tag": "system",
            },
        }
    }
