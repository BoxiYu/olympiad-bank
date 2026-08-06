"""MathNet 三语 export/apply 契约测试；只使用自造 fixture，不访问真实语料。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mathnet_translate as mt  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mathnet_translate"
NOW = "2026-08-06T12:00:00Z"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "mathnet-full"
    shutil.copytree(FIXTURE, root)
    return root


def export(corpus: Path, out: Path, *extra: str) -> list[dict]:
    rc = mt.main(["export", "--root", str(corpus), "--out", str(out), *extra])
    assert rc == 0
    return read_jsonl(out)


def translated_variant(row: dict, language: str = "zh") -> dict:
    units = {}
    for unit in row["units"]:
        source = unit["source"]
        units[unit["id"]] = f"译文：{source}" if unit["translatable"] else source
    return {
        "mode": "translated",
        "model": "test-model",
        "generated_at": NOW,
        "units": units,
    }


def apply_input(tmp_path: Path, row: dict, variants: dict) -> Path:
    path = tmp_path / "apply.jsonl"
    record = {key: row[key] for key in (
        "mathnet_id", "path", "source_sha256", "source_lang", "source_lang_confidence"
    )}
    record["variants"] = variants
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def test_export_covers_five_fixture_types_and_protects_content(corpus: Path, tmp_path: Path):
    language_map = tmp_path / "languages.json"
    language_map.write_text(json.dumps({
        "eng1": {"source_lang": "en", "source_lang_confidence": "high"},
        "slv1": {"source_lang": "sl", "source_lang_confidence": "high"},
        "img1": "en", "mcq1": "en", "bad1": "fr",
    }), encoding="utf-8")
    rows = export(corpus, tmp_path / "batch.jsonl", "--source-lang-map", str(language_map))
    assert {row["mathnet_id"] for row in rows} == {"eng1", "slv1", "img1", "mcq1", "bad1"}
    assert all(row["targets"] == ["en", "zh"] for row in rows)

    by_id = {row["mathnet_id"]: row for row in rows}
    assert by_id["eng1"]["target_modes"] == {"en": "passthrough", "zh": "translated"}
    assert by_id["slv1"]["target_modes"] == {"en": "translated", "zh": "translated"}
    assert by_id["img1"]["target_modes"] == {"en": "translated", "zh": "translated"}
    eng_statement = by_id["eng1"]["units"][0]
    assert "$x$" not in eng_statement["source"] and "`x`" not in eng_statement["source"]
    assert len(eng_statement["protected"]) == 3

    image_units = {unit["id"]: unit for unit in by_id["img1"]["units"]}
    assert "attached_image_1.png" not in image_units["statement"]["source"]
    assert any(value == "![](attached_image_1.png)" for value in image_units["statement"]["protected"].values())
    assert any("\\begin{align}" in value for value in image_units["solution_1"]["protected"].values())

    mcq_answer = by_id["mcq1"]["units"][-1]
    assert mcq_answer["translatable"] is False and list(mcq_answer["protected"].values()) == ["D"]
    eng_answer = by_id["eng1"]["units"][-1]
    assert eng_answer["translatable"] is False and list(eng_answer["protected"].values()) == [
        "$x\\in\\{-1,1\\}$"
    ]
    bad_source = (corpus / by_id["bad1"]["path"]).read_text(encoding="utf-8")
    assert "⚠️" in bad_source and by_id["bad1"]["source_lang"] == "fr"


def test_only_and_limit_are_deterministic(corpus: Path, tmp_path: Path):
    rows = export(corpus, tmp_path / "only.jsonl", "--only", "slv1", "--only", "eng1", "--limit", "1")
    assert len(rows) == 1 and rows[0]["mathnet_id"] in {"eng1", "slv1"}


def test_passthrough_is_exact_and_reapply_is_noop(corpus: Path, tmp_path: Path):
    row = export(
        corpus, tmp_path / "batch.jsonl", "--only", "eng1", "--source-lang", "en",
        "--source-lang-confidence", "high"
    )[0]
    source = corpus / row["path"]
    before = digest(source)
    apply_path = apply_input(tmp_path, row, {
        "en": {"mode": "passthrough", "model": None, "generated_at": NOW}
    })

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 0
    target = source.parent / "index.en.md"
    metadata = source.parent / "translation.json"
    assert target.read_bytes() == source.read_bytes()
    first_bytes = (target.read_bytes(), metadata.read_bytes())
    first_mtimes = (target.stat().st_mtime_ns, metadata.stat().st_mtime_ns)

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 0
    assert (target.read_bytes(), metadata.read_bytes()) == first_bytes
    assert (target.stat().st_mtime_ns, metadata.stat().st_mtime_ns) == first_mtimes
    assert digest(source) == before
    state = json.loads(metadata.read_text(encoding="utf-8"))
    assert state["variants"]["en"]["mode"] == "passthrough"
    assert state["variants"]["en"]["sha256"] == before


@pytest.mark.parametrize(
    ("source_lang", "confidence"),
    [("en", "medium"), ("en", "low"), ("en-US", "high"), ("it", "high"), ("und", "low")],
)
def test_apply_rejects_every_passthrough_below_en_high(
    corpus: Path, tmp_path: Path, source_lang: str, confidence: str
):
    row = export(
        corpus, tmp_path / "batch.jsonl", "--only", "eng1",
        "--source-lang", source_lang, "--source-lang-confidence", confidence,
    )[0]
    apply_path = apply_input(tmp_path, row, {
        "en": {"mode": "passthrough", "model": None, "generated_at": NOW}
    })

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 1
    question = (corpus / row["path"]).parent
    assert not (question / "index.en.md").exists()
    assert not (question / "translation.json").exists()
    assert "仅 source_lang=en" in read_jsonl(
        apply_path.with_suffix(".jsonl.failures.jsonl")
    )[0]["error"]


def test_missing_unit_writes_nothing_to_question(corpus: Path, tmp_path: Path):
    row = export(corpus, tmp_path / "batch.jsonl", "--only", "slv1", "--source-lang", "sl")[0]
    variant = translated_variant(row)
    variant["units"].pop("final_answer")
    apply_path = apply_input(tmp_path, row, {"zh": variant})
    question = (corpus / row["path"]).parent
    before = digest(question / "index.md")

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 1
    assert not (question / "index.zh.md").exists()
    assert not (question / "translation.json").exists()
    assert digest(question / "index.md") == before
    failures = read_jsonl(apply_path.with_suffix(".jsonl.failures.jsonl"))
    assert failures[0]["mathnet_id"] == "slv1" and "final_answer" in failures[0]["error"]


def test_reordered_protected_tokens_are_rejected(corpus: Path, tmp_path: Path):
    row = export(corpus, tmp_path / "batch.jsonl", "--only", "eng1", "--source-lang", "en")[0]
    variant = translated_variant(row)
    statement = next(unit for unit in row["units"] if unit["id"] == "statement")
    reversed_tokens = iter(reversed(list(statement["protected"])))
    variant["units"]["statement"] = re.sub(mt.PLACEHOLDER_RE, lambda _: next(reversed_tokens), statement["source"])
    apply_path = apply_input(tmp_path, row, {"zh": variant})
    question = (corpus / row["path"]).parent

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 1
    assert not (question / "index.zh.md").exists()
    assert not (question / "translation.json").exists()


def test_source_hash_change_triggers_retranslation(corpus: Path, tmp_path: Path):
    row = export(corpus, tmp_path / "first.jsonl", "--only", "slv1", "--source-lang", "sl")[0]
    apply_path = apply_input(tmp_path, row, {"zh": translated_variant(row)})
    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 0

    current = export(corpus, tmp_path / "current.jsonl", "--only", "slv1", "--source-lang", "sl")
    assert len(current) == 1 and current[0]["targets"] == ["en"]
    source = corpus / row["path"]
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale = export(corpus, tmp_path / "stale.jsonl", "--only", "slv1", "--source-lang", "sl")
    assert len(stale) == 1 and stale[0]["targets"] == ["en", "zh"]
    assert stale[0]["source_sha256"] != row["source_sha256"]


def test_translated_output_restores_math_image_and_symbolic_answer(corpus: Path, tmp_path: Path):
    image_row = export(corpus, tmp_path / "image.jsonl", "--only", "img1", "--source-lang", "en")[0]
    image_apply = apply_input(tmp_path, image_row, {"zh": translated_variant(image_row)})
    image_source = corpus / image_row["path"]
    before = digest(image_source)
    assert mt.main(["apply", "--root", str(corpus), "--in", str(image_apply)]) == 0
    rendered = (image_source.parent / "index.zh.md").read_text(encoding="utf-8")
    assert "![](attached_image_1.png)" in rendered
    assert "\\begin{align}" in rendered and "\\end{align}" in rendered
    assert [line for line in rendered.splitlines() if line.startswith("## ")] == [
        "## 题面", "## 解法 1", "## 最终答案"
    ]
    assert digest(image_source) == before

    mcq_row = export(corpus, tmp_path / "mcq.jsonl", "--only", "mcq1", "--source-lang", "en")[0]
    mcq_apply = apply_input(tmp_path, mcq_row, {"zh": translated_variant(mcq_row)})
    assert mt.main(["apply", "--root", str(corpus), "--in", str(mcq_apply)]) == 0
    mcq_text = ((corpus / mcq_row["path"]).parent / "index.zh.md").read_text(encoding="utf-8")
    assert mcq_text.rstrip().endswith("## 最终答案\n\nD")


def test_atomic_write_uses_same_directory_replace_and_cleans_failed_temp(tmp_path: Path, monkeypatch):
    target = tmp_path / "translation.json"
    target.write_bytes(b"old")
    calls = []
    real_replace = os.replace

    def tracking_replace(source, destination):
        calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(mt.os, "replace", tracking_replace)
    assert mt.atomic_write(target, b"new") is True
    assert calls[0][0].parent == target.parent and calls[0][1] == target
    assert not list(tmp_path.glob(".translation.json.*.tmp"))

    def failing_replace(source, destination):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(mt.os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated"):
        mt.atomic_write(target, b"newer")
    assert target.read_bytes() == b"new"
    assert not list(tmp_path.glob(".translation.json.*.tmp"))


def test_apply_never_changes_any_fixture_index(corpus: Path, tmp_path: Path):
    sources = list(corpus.rglob("index.md"))
    before = {path: digest(path) for path in sources}
    rows = export(corpus, tmp_path / "all.jsonl", "--source-lang", "en")
    for number, row in enumerate(rows):
        variants = {"zh": translated_variant(row)}
        path = apply_input(tmp_path, row, variants)
        path = path.with_name(f"apply-{number}.jsonl")
        original = tmp_path / "apply.jsonl"
        original.rename(path)
        assert mt.main(["apply", "--root", str(corpus), "--in", str(path)]) == 0
    assert {path: digest(path) for path in sources} == before
