import json

import pytest

from mep_sft.assembler import assemble
from mep_sft.generator import generate_dataset
from mep_sft.trainfmt import check_record, dataset_info, validate_file

SAMPLES, _ = generate_dataset(40, seed=800)


def _write(tmp_path, with_meta):
    path = tmp_path / "train.jsonl"
    path.write_text("".join(json.dumps(assemble(s, include_meta=with_meta), ensure_ascii=False) + "\n"
                            for s in SAMPLES), encoding="utf-8")
    return path


def test_every_assembled_record_passes_structure_checks():
    for s in SAMPLES:
        assert check_record(assemble(s)) == []


def test_bad_records_are_caught():
    ok = assemble(SAMPLES[0])
    assert check_record({"messages": []})
    swapped = {"messages": [ok["messages"][0], ok["messages"][2], ok["messages"][1]]}
    assert any("roles" in e for e in check_record(swapped))
    no_think = {"messages": [*ok["messages"][:2], {"role": "assistant", "content": "hi"}]}
    assert any("think" in e for e in check_record(no_think))
    extra = {"messages": [{**m, "x": 1} for m in ok["messages"]]}
    assert any("keys" in e for e in check_record(extra))


def test_file_validation_without_datasets(tmp_path):
    rep = validate_file(_write(tmp_path, True), load_with_datasets=False)
    assert rep["records"] == 40 and rep["error_count"] == 0


def test_hf_datasets_can_load_it(tmp_path):
    pytest.importorskip("datasets")
    for with_meta in (True, False):  # meta column present or messages-only
        rep = validate_file(_write(tmp_path, with_meta))
        ds = rep["datasets"]
        assert ds["rows"] == 40 and "messages" in ds["columns"]
        assert ds["first_roles"] == ["system", "user", "assistant"]


def test_dataset_info_matches_llamafactory_sharegpt_shape():
    info = dataset_info("train.jsonl")["mep_sft"]
    assert info["formatting"] == "sharegpt" and info["columns"] == {"messages": "messages"}
    assert info["tags"]["assistant_tag"] == "assistant" and info["tags"]["system_tag"] == "system"
