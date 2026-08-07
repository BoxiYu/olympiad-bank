"""MathNet 三语 export/apply 契约测试；只使用自造 fixture，不访问真实语料。"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
import re

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mathnet_translate as mt  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mathnet_translate"
REPO_ROOT = Path(__file__).resolve().parents[1]
BANK = REPO_ROOT / "scripts" / "bank.py"
NOW = "2026-08-06T12:00:00Z"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "mathnet-full"
    shutil.copytree(FIXTURE, root)
    write_corpus_index(root)
    return root


def index_row(root: Path, source: Path) -> dict:
    return {
        "mathnet_id": source.parent.name,
        "path": source.relative_to(root).as_posix(),
        "category": "algebra",
        "topics": ["自造测试"],
        "difficulty_est": 3,
        "country": "Testland",
        "source_lang": "und",
        "variants": {"en": "missing", "zh": "missing"},
        "translation_stale": False,
    }


def write_corpus_index(root: Path, only: set[str] | None = None) -> list[dict]:
    rows = [
        index_row(root, source)
        for source in sorted(root.rglob("index.md"))
        if only is None or source.parent.name in only
    ]
    (root / "index.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return rows


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


def identical_translated_variant(row: dict) -> dict:
    return {
        "mode": "translated",
        "model": "test-model",
        "generated_at": NOW,
        "units": {unit["id"]: unit["source"] for unit in row["units"]},
    }


def apply_input(tmp_path: Path, row: dict, variants: dict) -> Path:
    path = tmp_path / "apply.jsonl"
    record = {key: row[key] for key in (
        "mathnet_id", "path", "source_sha256", "source_lang", "source_lang_confidence"
    )}
    record["variants"] = variants
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def apply_records_input(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "apply-many.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def cxb_525_fixture() -> dict:
    return json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "translation_fidelity"
         / "cxb-525-placeholder-reordering.json").read_text(encoding="utf-8")
    )


def cxb_528_fixture() -> dict:
    return json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "translation_fidelity"
         / "cxb-528-mutation-boundaries.json").read_text(encoding="utf-8")
    )


def cxb_525_export(tmp_path: Path) -> tuple[Path, dict, dict]:
    fixture = cxb_525_fixture()
    source_unit = fixture["source_unit"]
    for placeholder, original in fixture["protected"].items():
        source_unit = source_unit.replace(placeholder, original)
    root = tmp_path / "mathnet-full-cxb-525"
    question = root / "by-topic" / "algebra" / "方程与设元" / "cxb525"
    question.mkdir(parents=True)
    (question / "index.md").write_text(
        f"# cxb525\n\n## 题面\n\n{source_unit}\n\n## 最终答案\n\nD\n",
        encoding="utf-8",
    )
    write_corpus_index(root)
    row = export(
        root,
        tmp_path / "cxb-525-export.jsonl",
        "--only", "cxb525",
        "--source-lang", "en",
        "--source-lang-confidence", "medium",
    )[0]
    statement = next(unit for unit in row["units"] if unit["id"] == "statement")
    assert statement["source"] == fixture["source_unit"]
    return root, row, fixture


def search(corpus: Path, lang: str, coverage: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(BANK), "mathnet-search", "--lang", lang,
            "--coverage", coverage, "--root", str(corpus),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


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


def test_only_and_limit_are_deterministic(corpus: Path, tmp_path: Path, capsys):
    rows = export(corpus, tmp_path / "only.jsonl", "--only", "slv1", "--only", "eng1", "--limit", "1")
    assert len(rows) == 1 and rows[0]["mathnet_id"] in {"eng1", "slv1"}
    assert "--limit 1 已截断" in capsys.readouterr().out


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


def test_failed_retranslation_removes_obsolete_variant(corpus: Path, tmp_path: Path):
    row = export(
        corpus, tmp_path / "before.jsonl", "--only", "slv1",
        "--source-lang", "sl", "--source-lang-confidence", "high",
    )[0]
    source = corpus / row["path"]
    success = apply_input(tmp_path, row, {"en": translated_variant(row, "en")})
    assert mt.main(["apply", "--root", str(corpus), "--in", str(success)]) == 0
    target = source.with_name("index.en.md")
    assert target.is_file()

    source.write_bytes(source.read_bytes() + b"\n<!-- revised source -->\n")
    revised_source = source.read_bytes()
    revised = export(
        corpus, tmp_path / "after.jsonl", "--only", "slv1",
        "--source-lang", "sl", "--source-lang-confidence", "high",
    )[0]
    failed = apply_input(tmp_path, revised, {"en": {
        "mode": "failed", "model": "test-model", "generated_at": NOW,
        "error": "synthetic gate rejection",
    }})

    assert mt.main(["apply", "--root", str(corpus), "--in", str(failed)]) == 0
    assert source.read_bytes() == revised_source
    assert not target.exists()
    state = json.loads((source.parent / "translation.json").read_text(encoding="utf-8"))
    assert state["variants"]["en"]["mode"] == "failed"
    result = search(corpus, "en", "failed")
    assert result.returncode == 0
    assert "slv1  " in result.stdout
    assert "（en 译文校验失败）" in result.stdout


def test_apply_immediately_refreshes_all_four_search_coverage_states(
    corpus: Path, tmp_path: Path
):
    selected = {"eng1", "slv1", "bad1", "mcq1"}
    write_corpus_index(corpus, selected)
    sources = list(corpus.rglob("index.md"))
    before = {path: digest(path) for path in sources}

    eng = export(
        corpus, tmp_path / "eng.jsonl", "--only", "eng1",
        "--source-lang", "en", "--source-lang-confidence", "high",
    )[0]
    slv = export(
        corpus, tmp_path / "slv.jsonl", "--only", "slv1",
        "--source-lang", "sl", "--source-lang-confidence", "high",
    )[0]
    bad = export(
        corpus, tmp_path / "bad.jsonl", "--only", "bad1",
        "--source-lang", "fr", "--source-lang-confidence", "high",
    )[0]
    records = []
    for row, variants in (
        (eng, {"en": {"mode": "passthrough", "model": None, "generated_at": NOW}}),
        (slv, {"en": translated_variant(slv, "en")}),
        (bad, {"en": {
            "mode": "failed", "model": "test-model", "generated_at": NOW,
            "error": "synthetic gate rejection",
        }}),
    ):
        record = {key: row[key] for key in (
            "mathnet_id", "path", "source_sha256", "source_lang", "source_lang_confidence"
        )}
        record["variants"] = variants
        records.append(record)

    assert mt.main([
        "apply", "--root", str(corpus), "--in", str(apply_records_input(tmp_path, records))
    ]) == 0
    expected = {
        "translated": "slv1",
        "passthrough": "eng1",
        "failed": "bad1",
        "missing": "mcq1",
    }
    for coverage, mathnet_id in expected.items():
        result = search(corpus, "en", coverage)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count(f"{mathnet_id}  ") == 1
        assert "共 1 题" in result.stdout
    assert {path: digest(path) for path in sources} == before


def test_reindex_only_touches_selected_line_and_preserves_other_bytes(
    corpus: Path, monkeypatch
):
    rows = write_corpus_index(corpus, {"eng1", "slv1"})
    by_id = {row["mathnet_id"]: row for row in rows}
    eng_source = corpus / by_id["eng1"]["path"]
    eng_target = eng_source.with_name("index.en.md")
    eng_target.write_bytes(eng_source.read_bytes())
    (eng_source.parent / "translation.json").write_text(json.dumps({
        "mathnet_id": "eng1",
        "source_sha256": digest(eng_source),
        "source_lang": "en",
        "variants": {"en": {
            "mode": "passthrough", "sha256": digest(eng_target),
        }},
    }), encoding="utf-8")

    eng_line = (
        '{"mathnet_id" : "eng1", "path":' + json.dumps(by_id["eng1"]["path"], ensure_ascii=False)
        + ', "note" : "keep  spaces", "nested": {"z": 1}, "source_lang" : "old", '
        '"variants": {"en": "missing"}, "translation_stale" : true}\n'
    )
    slv_line = json.dumps(by_id["slv1"], ensure_ascii=False) + "\n"
    (corpus / "index.jsonl").write_text(eng_line + slv_line, encoding="utf-8")
    calls = []
    real_projection = mt.translation_projection

    def tracking_projection(source: Path, mathnet_id: str):
        calls.append(mathnet_id)
        return real_projection(source, mathnet_id)

    monkeypatch.setattr(mt, "translation_projection", tracking_projection)
    assert mt.main(["reindex", "--root", str(corpus), "--only", "eng1"]) == 0

    actual_eng, actual_slv = (corpus / "index.jsonl").read_text(encoding="utf-8").splitlines(True)
    expected_eng = eng_line.replace('"old"', '"en"').replace(
        '{"en": "missing"}', '{"en":"passthrough","zh":"missing"}'
    ).replace('true}\n', 'false}\n')
    assert actual_eng == expected_eng
    assert actual_slv == slv_line
    assert calls == ["eng1"]


def test_reindex_requires_explicit_scope_and_all_processes_every_index_row(
    corpus: Path, monkeypatch
):
    rows = write_corpus_index(corpus, {"eng1", "slv1"})
    with pytest.raises(SystemExit) as exc_info:
        mt.main(["reindex", "--root", str(corpus)])
    assert exc_info.value.code == 2

    calls = []
    real_projection = mt.translation_projection

    def tracking_projection(source: Path, mathnet_id: str):
        calls.append(mathnet_id)
        return real_projection(source, mathnet_id)

    monkeypatch.setattr(mt, "translation_projection", tracking_projection)
    assert mt.main(["reindex", "--root", str(corpus), "--all"]) == 0
    assert calls == [row["mathnet_id"] for row in rows]


def test_apply_no_reindex_leaves_index_bytes_unchanged(corpus: Path, tmp_path: Path):
    write_corpus_index(corpus, {"eng1"})
    index_path = corpus / "index.jsonl"
    before = index_path.read_bytes()
    row = export(
        corpus, tmp_path / "eng-no-reindex.jsonl", "--only", "eng1",
        "--source-lang", "en", "--source-lang-confidence", "high",
    )[0]
    apply_path = apply_input(tmp_path, row, {
        "en": {"mode": "passthrough", "model": None, "generated_at": NOW}
    })

    assert mt.main([
        "apply", "--root", str(corpus), "--in", str(apply_path), "--no-reindex"
    ]) == 0
    assert index_path.read_bytes() == before
    assert ((corpus / row["path"]).parent / "translation.json").is_file()


def test_reindex_clears_missing_metadata_and_marks_stale_or_missing_variants(corpus: Path):
    rows = write_corpus_index(corpus, {"eng1", "slv1"})
    by_id = {row["mathnet_id"]: row for row in rows}
    index_path = corpus / "index.jsonl"
    old_rows = []
    for row in rows:
        row.update({
            "source_lang": "en",
            "variants": {"en": "translated", "zh": "translated"},
            "translation_stale": False,
        })
        old_rows.append(row)
    index_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in old_rows), encoding="utf-8"
    )

    slv_source = corpus / by_id["slv1"]["path"]
    (slv_source.parent / "translation.json").write_text(json.dumps({
        "mathnet_id": "slv1",
        "source_sha256": "outdated-source-hash",
        "source_lang": "sl",
        "variants": {"en": {"mode": "translated", "sha256": "missing-target-hash"}},
    }), encoding="utf-8")
    assert mt.main(["reindex", "--root", str(corpus), "--all"]) == 0

    refreshed = {
        row["mathnet_id"]: row
        for row in map(json.loads, index_path.read_text(encoding="utf-8").splitlines())
    }
    assert refreshed["eng1"]["source_lang"] == "und"
    assert refreshed["eng1"]["variants"] == {"en": "missing", "zh": "missing"}
    assert refreshed["eng1"]["translation_stale"] is False
    assert refreshed["slv1"]["source_lang"] == "sl"
    assert refreshed["slv1"]["variants"] == {"en": "missing", "zh": "missing"}
    assert refreshed["slv1"]["translation_stale"] is True


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


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_en_non_high_identical_model_result_is_translated_and_written(
    corpus: Path, tmp_path: Path, confidence: str
):
    row = export(
        corpus, tmp_path / "batch.jsonl", "--only", "eng1",
        "--source-lang", "en", "--source-lang-confidence", confidence,
    )[0]
    assert row["target_modes"]["en"] == "translated"
    apply_path = apply_input(tmp_path, row, {
        "en": identical_translated_variant(row),
    })

    assert mt.main(["apply", "--root", str(corpus), "--in", str(apply_path)]) == 0
    question = (corpus / row["path"]).parent
    assert (question / "index.en.md").read_bytes() == (question / "index.md").read_bytes()
    state = json.loads((question / "translation.json").read_text(encoding="utf-8"))
    assert state["variants"]["en"]["mode"] == "translated"
    assert apply_path.with_suffix(".jsonl.failures.jsonl").read_text(encoding="utf-8") == ""


def test_apply_payload_accepts_en_non_high_identical_model_result(
    corpus: Path, tmp_path: Path
):
    row = export(
        corpus, tmp_path / "batch.jsonl", "--only", "eng1",
        "--source-lang", "en", "--source-lang-confidence", "medium",
    )[0]

    mt.apply_payload(corpus, row, "en", identical_translated_variant(row))

    question = (corpus / row["path"]).parent
    assert (question / "index.en.md").read_bytes() == (question / "index.md").read_bytes()
    state = json.loads((question / "translation.json").read_text(encoding="utf-8"))
    assert state["variants"]["en"]["mode"] == "translated"


def test_apply_payload_rejects_mixed_section_despite_matching_file_language(
    corpus: Path, tmp_path: Path
):
    source = next(path for path in corpus.rglob("index.md") if path.parent.name == "eng1")
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "Factoring gives $x^2-1=(x-1)(x+1)=0$.", "Aucune solution."
        ),
        encoding="utf-8",
    )
    row = export(
        corpus, tmp_path / "batch.jsonl", "--only", "eng1",
        "--source-lang", "en", "--source-lang-confidence", "medium",
    )[0]

    with pytest.raises(mt.TranslateError, match="untranslated@解法 1"):
        mt.apply_payload(
            corpus, row, "en", identical_translated_variant(row)
        )

    assert not (source.parent / "index.en.md").exists()
    assert not (source.parent / "translation.json").exists()


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


def test_cxb_525_reordered_math_placeholders_apply_and_fidelity_pass(tmp_path: Path):
    root, row, fixture = cxb_525_export(tmp_path)
    variant = translated_variant(row)
    variant["units"]["statement"] = fixture["translated_unit"]
    apply_path = apply_input(tmp_path, row, {"zh": variant})
    question = (root / row["path"]).parent

    assert mt.main(["apply", "--root", str(root), "--in", str(apply_path)]) == 0
    source = (question / "index.md").read_text(encoding="utf-8")
    translated = (question / "index.zh.md").read_text(encoding="utf-8")
    assert mt.verify_translation(
        source,
        translated,
        target_lang="zh",
        source_lang="en",
        placeholder_pipeline=True,
    ) == []


@pytest.mark.parametrize("damage", ["missing", "duplicate", "tampered"])
def test_cxb_525_placeholder_multiset_damage_is_rejected(tmp_path: Path, damage: str):
    root, row, fixture = cxb_525_export(tmp_path)
    variant = translated_variant(row)
    statement = fixture["translated_unit"]
    if damage == "missing":
        statement = statement.replace("{{MNT_0004}}", "")
    elif damage == "duplicate":
        statement = statement.replace("{{MNT_0004}}", "{{MNT_0003}}")
    else:
        statement = statement.replace("{{MNT_0004}}", "{{MNT_9999}}")
    variant["units"]["statement"] = statement
    apply_path = apply_input(tmp_path, row, {"zh": variant})
    question = (root / row["path"]).parent

    assert mt.main(["apply", "--root", str(root), "--in", str(apply_path)]) == 1
    assert not (question / "index.zh.md").exists()
    assert not (question / "translation.json").exists()
    assert "缺失、重复或被篡改" in read_jsonl(
        apply_path.with_suffix(".jsonl.failures.jsonl")
    )[0]["error"]


def test_cxb_528_runtime_rejects_a_pure_placeholder_duplicate():
    row = next(
        item for item in cxb_528_fixture()["constructed_cases"]
        if item["fixture_id"]
        == "constructed-cxb528-runtime-pure-placeholder-duplicate"
    )
    unit = {
        "id": row["unit_id"],
        "source": row["source_body"],
        "protected": row["protected"],
        "translatable": True,
    }

    with pytest.raises(mt.TranslateError, match="不可译占位缺失、重复或被篡改"):
        mt.restore_translation(row["translated_body"], unit)


def test_cxb_528_two_letter_word_keeps_final_answer_translatable():
    row = next(
        item for item in cxb_528_fixture()["real_final_answers"]
        if item["case"] == "driver_two_letter_operator_answer"
    )
    document = mt.parse_document(
        f"# {row['mathnet_id']}\n\n## 题面\n\nQuestion.\n\n"
        f"## 最终答案\n\n{row['body']}\n"
    )
    final_answer = mt.export_units(document)[-1]

    assert not mt.is_symbolic_answer(row["body"])
    assert final_answer["translatable"] is True
    assert final_answer["protected"] == {}


def test_apply_keeps_image_placeholder_relative_order():
    document = mt.parse_document(
        "# images\n\n## 题面\n\nCompare ![](attached_image_1.png) with "
        "![](attached_image_2.png).\n\n## 最终答案\n\nD\n"
    )
    expected = mt.unit_index(document)
    translations = {unit_id: unit["source"] for unit_id, (_section, unit) in expected.items()}
    statement = expected["statement"][1]
    placeholders = list(statement["protected"])
    assert mt.placeholder_multisets_match(placeholders, list(reversed(placeholders)))
    replacements = iter(reversed(placeholders))
    translations["statement"] = re.sub(
        mt.PLACEHOLDER_RE, lambda _match: next(replacements), statement["source"]
    )

    with pytest.raises(mt.TranslateError, match="图片占位顺序被改动"):
        mt.render_variant(document, translations)


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


def test_batch_output_rejects_human_reported_boilerplate_excerpts(tmp_path: Path):
    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "translation_fidelity"
         / "human-reported-degenerate-excerpts.json")
        .read_text(encoding="utf-8")
    )["rows"]
    records = tuple({
        "mathnet_id": row["fixture_id"],
        "units": [{
            "id": "statement",
            "source": row["source"],
            "translatable": True,
        }],
    } for row in fixture)
    job = mt.BatchJob("zh-degenerate", "zh", records, tmp_path / "batch")
    job.directory.mkdir()
    job.output_path.write_text(json.dumps({
        "model": "fake-codex",
        "translations": [{
            "mathnet_id": row["fixture_id"],
            "units": {"statement": row["translated"]},
        } for row in fixture],
    }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(mt.TranslateError, match=r"批级退化校验失败（3/3 题）"):
        mt.validate_batch_output(job)


@pytest.mark.parametrize(
    ("target", "target_phrase", "example"),
    [
        (
            "zh",
            "翻译成**通顺、地道的中文**",
            "正确: 求所有二阶可导的函数对 {{MNT_0001}}，使得 {{MNT_0002}}",
        ),
        (
            "en",
            "翻译成**通顺、地道的英文**",
            "正确: Find all pairs of twice differentiable functions {{MNT_0001}}, such that {{MNT_0002}}",
        ),
    ],
)
def test_batch_prompt_renders_new_quality_template_and_absolute_paths(
    tmp_path: Path, target: str, target_phrase: str, example: str
):
    job = mt.BatchJob(f"{target}-fixture", target, (), tmp_path / "batches" / target)

    prompt = mt.render_batch_prompt(job)

    assert target_phrase in prompt
    assert "读取 batch.json 的全部 records，逐题翻译每个 unit 的 source。" in prompt
    assert "整句重写成自然的" in prompt and "绝不逐词替换" in prompt
    assert "散文部分不得残留任何源语言单词或短语" in prompt
    assert example in prompt
    assert "所有 {{MNT_NNNN}} 占位必须各出现一次，不得增删或改写占位符本身" in prompt
    assert "可按目标语言语法调整数学\n占位符语序" in prompt
    assert "图片占位之间的相对顺序不得改变" in prompt
    assert "## 译文必须由你自己产出（硬性）" in prompt
    assert "禁止把翻译外包给任何外部设施" in prompt
    assert "Apple Translation 框架" in prompt
    assert "import Translation" in prompt
    assert "LanguageAvailability" in prompt
    assert "本机 ollama / llama" in prompt
    assert "任何在线翻译 API 或机器翻译服务" in prompt
    assert "禁止 `xcrun swift`、`swift -e`、起子进程做翻译" in prompt
    assert "读 batch.json、自己逐题译好、写出 translations.json" in prompt
    assert f"批次目录的显式绝对路径：{job.directory.resolve()}" in prompt
    assert f"输入文件：{job.input_path.resolve()}" in prompt
    assert f"输出文件：{job.output_path.resolve()}" in prompt


def test_run_batch_size_defaults_to_25_and_allows_explicit_override():
    argument_parser = mt.parser()

    assert argument_parser.parse_args(["run"]).batch_size == 25
    assert argument_parser.parse_args(["run", "--batch-size", "100"]).batch_size == 100


def test_apply_preflight_rejects_only_human_reported_boilerplate_excerpts(tmp_path: Path):
    fixture = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "translation_fidelity"
         / "human-reported-degenerate-excerpts.json")
        .read_text(encoding="utf-8")
    )["rows"]
    root = tmp_path / "corpus"
    records = []
    for row in fixture:
        source_statement = (
            row["source"]
            .replace("{{MNT_0001}}", "$x$")
            .replace("{{MNT_0002}}", "$y$")
        )
        problem_dir = root / row["fixture_id"]
        problem_dir.mkdir(parents=True)
        source_path = problem_dir / "index.md"
        source_path.write_text(
            f"# {row['fixture_id']}\n\n## 题面\n{source_statement}\n\n## 最终答案\nD\n",
            encoding="utf-8",
        )
        units = mt.export_units(mt.parse_document(source_path.read_text(encoding="utf-8")))
        translations = {
            unit["id"]: row["translated"] if unit["id"] == "statement" else unit["source"]
            for unit in units
        }
        records.append({
            "mathnet_id": row["fixture_id"],
            "path": source_path.relative_to(root).as_posix(),
            "source_sha256": digest(source_path),
            "source_lang": "en",
            "source_lang_confidence": "high",
            "variants": {"zh": {
                "mode": "translated",
                "model": "fake-codex",
                "generated_at": NOW,
                "units": translations,
            }},
        })
    input_path = apply_records_input(tmp_path, records)
    args = type("Args", (), {
        "root": root,
        "input": input_path,
        "failures": tmp_path / "failures.jsonl",
    })()

    assert mt.apply_records(args) == 1
    failures = read_jsonl(args.failures)
    assert len(failures) == 3
    assert all("batch_boilerplate" in failure["error"] for failure in failures)
    assert not list(root.rglob("index.zh.md"))


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


FAKE_COMPANION = r"""import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const expectedEffort = process.env.FAKE_EXPECT_EFFORT || 'medium';
const effortIndexes = args.flatMap((value, index) => value === '--effort' ? [index] : []);
if (effortIndexes.length !== 1 || args[effortIndexes[0] + 1] !== expectedEffort) {
  console.error(`expected exactly --effort ${expectedEffort}; got ${JSON.stringify(args)}`);
  process.exit(64);
}
const expectedModel = process.env.FAKE_EXPECT_MODEL;
const modelIndexes = args.flatMap((value, index) => value === '--model' ? [index] : []);
if (expectedModel) {
  if (modelIndexes.length !== 1 || args[modelIndexes[0] + 1] !== expectedModel) {
    console.error(`expected exactly --model ${expectedModel}; got ${JSON.stringify(args)}`);
    process.exit(64);
  }
} else if (modelIndexes.length !== 0) {
  console.error(`expected --model to be omitted; got ${JSON.stringify(args)}`);
  process.exit(64);
}
const cwd = args[args.indexOf('--cwd') + 1];
const input = JSON.parse(fs.readFileSync(path.join(cwd, 'batch.json'), 'utf8'));
const promptPath = args[args.indexOf('--prompt-file') + 1];
const prompt = fs.readFileSync(promptPath, 'utf8');
const targetLanguage = input.target_lang === 'zh' ? '中文' : '英文';
const absoluteCwd = path.resolve(cwd);
const expectedPromptParts = [
  `翻译成**通顺、地道的${targetLanguage}**`,
  '读取 batch.json 的全部 records，逐题翻译每个 unit 的 source。',
  '绝不逐词替换',
  '散文部分不得残留任何源语言单词或短语',
  '所有 {{MNT_NNNN}} 占位必须各出现一次，不得增删或改写占位符本身',
  '可按目标语言语法调整数学\n占位符语序',
  '图片占位之间的相对顺序不得改变',
  `批次目录的显式绝对路径：${absoluteCwd}`,
  `输入文件：${path.join(absoluteCwd, 'batch.json')}`,
  `输出文件：${path.join(absoluteCwd, 'translations.json')}`,
];
const missingPromptPart = expectedPromptParts.find((part) => !prompt.includes(part));
if (missingPromptPart) {
  console.error(`task.md missing expected prompt part: ${missingPromptPart}`);
  process.exit(65);
}
const events = process.env.FAKE_EVENTS;
const concurrencyBarrier = Number(process.env.FAKE_CONCURRENCY_BARRIER || '0');
const sleepMs = Number(process.env.FAKE_SLEEP_MS || '100');
const successSleepMs = Number(process.env.FAKE_SUCCESS_SLEEP_MS || '10');
const mode = process.env.FAKE_MODE || 'success';
const failId = process.env.FAKE_FAIL_ID || '';
const failTarget = process.env.FAKE_FAIL_TARGET || '';
const shouldFail = input.records.some((record) => record.mathnet_id === failId)
  && (!failTarget || input.target_lang === failTarget);
const attemptsPath = path.join(cwd, 'attempts.txt');
const previous = fs.existsSync(attemptsPath) ? Number(fs.readFileSync(attemptsPath, 'utf8')) : 0;
fs.writeFileSync(attemptsPath, String(previous + 1));
const sleep = (milliseconds) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
if (events) fs.appendFileSync(events, `start ${process.pid} ${Date.now()} ${cwd}\n`);
if (events && concurrencyBarrier > 0) {
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const starts = fs.readFileSync(events, 'utf8').split('\n').filter((line) => line.startsWith('start '));
    if (starts.length >= concurrencyBarrier) break;
    sleep(5);
  }
}

if (shouldFail && mode === 'timeout') sleep(sleepMs);
if (shouldFail && mode === 'invalid') {
  fs.writeFileSync(path.join(cwd, 'translations.json'), '{ definitely invalid');
  if (events) fs.appendFileSync(events, `end ${process.pid} ${Date.now()} ${cwd}\n`);
  process.exit(0);
}
if (shouldFail && mode === 'no-output') {
  if (events) fs.appendFileSync(events, `end ${process.pid} ${Date.now()} ${cwd}\n`);
  process.exit(0);
}

const translated = input.records.map((record) => ({
  mathnet_id: record.mathnet_id,
  units: Object.fromEntries(record.units.map((unit) => {
    if (!unit.translatable) return [unit.id, unit.source];
    const placeholders = unit.source.match(/\{\{MNT_\d{4}\}\}/g) || [];
    const bad = shouldFail && mode === 'fidelity';
    const prose = bad
      ? (input.target_lang === 'zh' ? '作为一个 AI，以下是翻译' : 'As an AI, here is the translation')
      : (input.target_lang === 'zh' ? '这是经过校验的数学译文' : 'This is a faithful mathematical translation');
    return [unit.id, `${prose}${placeholders.length ? ' ' + placeholders.join(' ') : ''}`];
  })),
}));
const output = {model: 'fake-codex', translations: translated};
const temporary = path.join(cwd, `.translations.${process.pid}.tmp`);
fs.writeFileSync(temporary, JSON.stringify(output));
fs.renameSync(temporary, path.join(cwd, 'translations.json'));
sleep(successSleepMs);
if (events) fs.appendFileSync(events, `end ${process.pid} ${Date.now()} ${cwd}\n`);
"""


def fake_companion(tmp_path: Path) -> Path:
    path = tmp_path / "fake-companion.mjs"
    path.write_text(FAKE_COMPANION, encoding="utf-8")
    return path


def run_args(corpus: Path, work: Path, companion: Path, *extra: str) -> list[str]:
    return [
        "run", "--root", str(corpus), "--work-dir", str(work),
        "--companion", str(companion), "--timeout", "2", "--retry-backoff", "0",
        "--retry-backoff-max", "0", *extra,
    ]


def maximum_fake_concurrency(events: Path) -> int:
    active = maximum = 0
    parsed = []
    for line in events.read_text(encoding="utf-8").splitlines():
        kind, _pid, timestamp, _cwd = line.split(" ", 3)
        parsed.append((int(timestamp), 0 if kind == "start" else 1, kind))
    for _timestamp, _order, kind in sorted(parsed):
        active += 1 if kind == "start" else -1
        maximum = max(maximum, active)
    return maximum


def test_run_end_to_end_concurrent_and_resume_skips_completed(
    corpus: Path, tmp_path: Path, monkeypatch, capsys
):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run"
    events = tmp_path / "events.log"
    monkeypatch.setenv("FAKE_EVENTS", str(events))
    monkeypatch.setenv("FAKE_CONCURRENCY_BARRIER", "2")
    sources = list(corpus.rglob("index.md"))
    before = {path: digest(path) for path in sources}
    args = run_args(
        corpus, work, companion,
        "--only", "eng1", "--only", "slv1", "--only", "mcq1",
        "--limit", "3", "--batch-size", "1", "--concurrency", "2",
    )
    # 此用例只验证并发与断点续跑，不验证超时。移除测试 helper 的 2s 墙钟预算，
    # 避免把 node 冷启动和 runner 调度速度误当成被测语义；超时行为由专门用例覆盖。
    timeout_index = args.index("--timeout")
    del args[timeout_index:timeout_index + 2]

    assert mt.main(args) == 0
    output = capsys.readouterr().out
    assert "passthrough 1 份" in output and "真翻 5 份" in output and "并发 2" in output
    assert "派单参数：effort medium；model 沿用全局配置（未传 --model）" in output
    assert maximum_fake_concurrency(events) == 2
    first_events = events.read_text(encoding="utf-8")
    for mathnet_id in ("eng1", "slv1", "mcq1"):
        directory = next(path.parent for path in sources if path.parent.name == mathnet_id)
        assert (directory / "index.en.md").is_file()
        assert (directory / "index.zh.md").is_file()
        state = json.loads((directory / "translation.json").read_text(encoding="utf-8"))
        assert set(state["variants"]) == {"en", "zh"}
    indexed = {
        row["mathnet_id"]: row
        for row in map(json.loads, (corpus / "index.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert indexed["eng1"]["variants"] == {"en": "passthrough", "zh": "translated"}
    for mathnet_id in ("slv1", "mcq1"):
        assert indexed[mathnet_id]["variants"] == {"en": "translated", "zh": "translated"}
    assert {path: digest(path) for path in sources} == before
    progress = json.loads((work / ".translate-progress.json").read_text(encoding="utf-8"))
    assert progress["questions"] and progress["batches"]
    for dispatch_path in work.glob("batches/*/dispatch.json"):
        dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
        assert dispatch["effort"] == "medium"
        assert dispatch["model"] is None
        assert dispatch["model_source"] == "global-config"
        assert dispatch["command"].count("--effort") == 1
        assert dispatch["command"][dispatch["command"].index("--effort") + 1] == "medium"
        assert "--model" not in dispatch["command"]

    assert mt.main(args) == 0
    resumed = capsys.readouterr().out
    assert "显式跳过 3 题" in resumed and "没有待处理译文" in resumed
    assert events.read_text(encoding="utf-8") == first_events
    assert {path: digest(path) for path in sources} == before


def test_run_custom_effort_and_model_are_forwarded_and_recorded(
    corpus: Path, tmp_path: Path, monkeypatch, capsys
):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run-custom-dispatch"
    monkeypatch.setenv("FAKE_EXPECT_EFFORT", "xhigh")
    monkeypatch.setenv("FAKE_EXPECT_MODEL", "foo")

    assert mt.main(run_args(
        corpus, work, companion,
        "--only", "slv1", "--batch-size", "1", "--effort", "xhigh", "--model", "foo",
    )) == 0
    output = capsys.readouterr().out
    assert "派单参数：effort xhigh；model foo" in output
    dispatch_paths = list(work.glob("batches/*/dispatch.json"))
    assert len(dispatch_paths) == 2
    for dispatch_path in dispatch_paths:
        dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
        assert dispatch["effort"] == "xhigh"
        assert dispatch["model"] == "foo"
        assert dispatch["model_source"] == "argument"
        assert dispatch["command"][dispatch["command"].index("--model") + 1] == "foo"


def test_run_rejects_unknown_effort_with_choices(capsys):
    with pytest.raises(SystemExit):
        mt.main(["run", "--effort", "turbo"])
    error = capsys.readouterr().err
    assert "invalid choice: 'turbo'" in error
    for choice in mt.COMPANION_EFFORTS:
        assert choice in error


def test_run_no_reindex_leaves_index_bytes_unchanged(corpus: Path, tmp_path: Path):
    companion = fake_companion(tmp_path)
    index_path = corpus / "index.jsonl"
    before = index_path.read_bytes()

    assert mt.main(run_args(
        corpus, tmp_path / "run-no-reindex", companion,
        "--only", "eng1", "--batch-size", "1", "--no-reindex",
    )) == 0
    assert index_path.read_bytes() == before
    question = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "eng1")
    assert (question / "translation.json").is_file()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("timeout", "超时"), ("invalid", "非法"), ("no-output", "未产出")],
)
def test_run_retries_bad_batch_and_continues_other_batches(
    corpus: Path, tmp_path: Path, monkeypatch, mode: str, expected: str
):
    companion = fake_companion(tmp_path)
    work = tmp_path / f"run-{mode}"
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("FAKE_FAIL_ID", "bad1")
    args = run_args(
        corpus, work, companion,
        "--only", "bad1", "--only", "eng1", "--batch-size", "1",
        "--concurrency", "2", "--retries", "1",
    )
    if mode == "timeout":
        # 超时必须确定性注入：靠小 --timeout 与真实 sleep 赛跑会被机器负载翻盘
        # （node 启动慢会误杀好批次）。这里按批次 id 精确超时坏批次的每次派单，
        # 并等 companion 写完 attempts.txt 再抛，保证重试计数 [2, 2] 可靠；
        # 真实 --timeout 放大到 30s，对好批次永不触发。
        args[args.index("--timeout") + 1] = "30"
        monkeypatch.setenv("FAKE_SLEEP_MS", "60000")  # 坏批次挂死等 terminate 杀；杀失败也 60s 自退
        real_popen = subprocess.Popen

        class TimeoutInjectingPopen(real_popen):
            def __init__(self, cmd, *popen_args, **popen_kwargs):
                self._attempts_path = None
                self._attempts_before = 0
                if "--cwd" in cmd:
                    directory = Path(cmd[cmd.index("--cwd") + 1])
                    batch = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
                    if batch["records"][0]["mathnet_id"] == "bad1":
                        self._attempts_path = directory / "attempts.txt"
                        if self._attempts_path.is_file():
                            self._attempts_before = int(
                                self._attempts_path.read_text(encoding="utf-8")
                            )
                super().__init__(cmd, *popen_args, **popen_kwargs)

            def communicate(self, input=None, timeout=None):
                if timeout is None or self._attempts_path is None:
                    return super().communicate(input=input, timeout=timeout)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    counter = (
                        self._attempts_path.read_text(encoding="utf-8").strip()
                        if self._attempts_path.is_file() else ""
                    )
                    if counter.isdigit() and int(counter) > self._attempts_before:
                        break
                    time.sleep(0.005)
                raise subprocess.TimeoutExpired(self.args, timeout)

        monkeypatch.setattr(mt.subprocess, "Popen", TimeoutInjectingPopen)

    assert mt.main(args) == 1
    eng_dir = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "eng1")
    bad_dir = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "bad1")
    assert (eng_dir / "index.en.md").is_file() and (eng_dir / "index.zh.md").is_file()
    assert not (bad_dir / "index.en.md").exists() and not (bad_dir / "index.zh.md").exists()
    bad_attempts = []
    for attempt_file in work.glob("batches/*/attempts.txt"):
        batch = json.loads((attempt_file.parent / "batch.json").read_text(encoding="utf-8"))
        if batch["records"][0]["mathnet_id"] == "bad1":
            bad_attempts.append(int(attempt_file.read_text(encoding="utf-8")))
    assert bad_attempts == [2, 2]
    ledger = (work / ".translate-failures.jsonl").read_text(encoding="utf-8")
    assert expected in ledger and '"scope":"batch"' in ledger


def test_run_fidelity_failure_is_recorded_without_target_files(
    corpus: Path, tmp_path: Path, monkeypatch
):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run-fidelity"
    monkeypatch.setenv("FAKE_MODE", "fidelity")
    monkeypatch.setenv("FAKE_FAIL_ID", "slv1")

    assert mt.main(run_args(
        corpus, work, companion, "--only", "slv1", "--batch-size", "1", "--concurrency", "2"
    )) == 1
    question = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "slv1")
    assert not (question / "index.en.md").exists()
    assert not (question / "index.zh.md").exists()
    state = json.loads((question / "translation.json").read_text(encoding="utf-8"))
    assert state["variants"]["en"]["mode"] == "failed"
    assert state["variants"]["zh"]["mode"] == "failed"
    indexed = {
        row["mathnet_id"]: row
        for row in map(json.loads, (corpus / "index.jsonl").read_text(encoding="utf-8").splitlines())
    }
    assert indexed["slv1"]["variants"] == {"en": "failed", "zh": "failed"}
    ledger = (work / ".translate-failures.jsonl").read_text(encoding="utf-8")
    assert "保真校验失败" in ledger
    batch_outputs = list(work.glob("batches/*/translations.json"))
    assert batch_outputs == []

    monkeypatch.setenv("FAKE_MODE", "success")
    monkeypatch.delenv("FAKE_FAIL_ID")
    assert mt.main(run_args(
        corpus, work, companion, "--only", "slv1", "--batch-size", "1", "--concurrency", "2"
    )) == 0
    attempts = sorted(
        int(path.read_text(encoding="utf-8")) for path in work.glob("batches/*/attempts.txt")
    )
    assert attempts == [2, 2]
    assert (question / "index.en.md").is_file()
    assert (question / "index.zh.md").is_file()
    assert (work / ".translate-failures.jsonl").read_text(encoding="utf-8") == ""


def test_run_strict_stops_before_later_good_batch(corpus: Path, tmp_path: Path, monkeypatch):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run-strict"
    monkeypatch.setenv("FAKE_MODE", "invalid")
    monkeypatch.setenv("FAKE_FAIL_ID", "bad1")
    monkeypatch.setenv("FAKE_SLEEP_MS", "200")

    assert mt.main(run_args(
        corpus, work, companion,
        "--only", "bad1", "--only", "eng1", "--batch-size", "1", "--concurrency", "1",
        "--retries", "0", "--strict",
    )) == 1
    eng_dir = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "eng1")
    assert (eng_dir / "index.en.md").is_file()  # passthrough happens before dispatch
    assert not (eng_dir / "index.zh.md").exists()


@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup uses POSIX signals")
def test_run_submission_interrupt_cleans_started_child(
    corpus: Path, tmp_path: Path, monkeypatch
):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run-submit-interrupt"
    events = tmp_path / "submit-interrupt-events.log"
    monkeypatch.setenv("FAKE_MODE", "timeout")
    monkeypatch.setenv("FAKE_FAIL_ID", "slv1")
    monkeypatch.setenv("FAKE_SLEEP_MS", "5000")
    monkeypatch.setenv("FAKE_EVENTS", str(events))
    real_executor = mt.concurrent.futures.ThreadPoolExecutor

    class InterruptingExecutor:
        def __init__(self, *args, **kwargs):
            self.delegate = real_executor(*args, **kwargs)
            self.submissions = 0

        def submit(self, *args, **kwargs):
            self.submissions += 1
            if self.submissions == 2:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if events.is_file() and events.read_text(encoding="utf-8").startswith("start "):
                        break
                    time.sleep(0.01)
                raise KeyboardInterrupt
            return self.delegate.submit(*args, **kwargs)

        def shutdown(self, *args, **kwargs):
            return self.delegate.shutdown(*args, **kwargs)

    monkeypatch.setattr(mt.concurrent.futures, "ThreadPoolExecutor", InterruptingExecutor)
    rc = mt.main(run_args(
        corpus, work, companion,
        "--only", "slv1", "--batch-size", "1", "--concurrency", "1", "--timeout", "30",
    ))

    assert rc == 130
    child_pid = int(events.read_text(encoding="utf-8").split()[1])
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    json.loads((work / ".translate-progress.json").read_text(encoding="utf-8"))
    assert not list(work.rglob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup uses POSIX signals")
def test_run_sigint_cleans_child_and_atomic_progress(corpus: Path, tmp_path: Path):
    companion = fake_companion(tmp_path)
    work = tmp_path / "run-interrupt"
    events = tmp_path / "interrupt-events.log"
    environment = os.environ.copy()
    environment.update({
        "FAKE_MODE": "timeout",
        "FAKE_FAIL_ID": "slv1",
        "FAKE_FAIL_TARGET": "zh",
        "FAKE_SLEEP_MS": "5000",
        "FAKE_EVENTS": str(events),
    })
    command = [
        sys.executable, str(Path(mt.__file__)),
        *run_args(
            corpus, work, companion,
            "--only", "slv1", "--batch-size", "1", "--concurrency", "1", "--timeout", "30",
        ),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if events.is_file() and sum(
            line.startswith("start ") for line in events.read_text(encoding="utf-8").splitlines()
        ) >= 2:
            break
        time.sleep(0.02)
    assert events.is_file(), process.communicate(timeout=2)
    starts = [line for line in events.read_text(encoding="utf-8").splitlines() if line.startswith("start ")]
    assert len(starts) >= 2, process.communicate(timeout=2)
    child_pid = int(starts[-1].split()[1])

    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 130, (stdout, stderr)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    json.loads((work / ".translate-progress.json").read_text(encoding="utf-8"))
    assert not list(work.rglob(".*.tmp"))
    interrupted = [
        directory for directory in (work / "batches").iterdir()
        if json.loads((directory / "batch.json").read_text(encoding="utf-8"))["target_lang"] == "zh"
    ]
    assert interrupted and not (interrupted[0] / "translations.json").exists()

    resumed_environment = environment.copy()
    resumed_environment.update({"FAKE_MODE": "success", "FAKE_FAIL_ID": "", "FAKE_FAIL_TARGET": ""})
    resumed = subprocess.run(command, capture_output=True, text=True, env=resumed_environment, timeout=8)
    assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
    starts_after_resume = [
        line for line in events.read_text(encoding="utf-8").splitlines() if line.startswith("start ")
    ]
    assert len(starts_after_resume) == 3  # 已完成的 en 批次未重复派单，只补中断的 zh 批次
    question = next(path.parent for path in corpus.rglob("index.md") if path.parent.name == "slv1")
    state = json.loads((question / "translation.json").read_text(encoding="utf-8"))
    assert set(state["variants"]) == {"en", "zh"}
