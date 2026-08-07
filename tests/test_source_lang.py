"""Fixture-driven contract tests for conservative MathNet language detection."""

import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from source_lang import detect_source_lang, should_passthrough  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "source_lang_cases.json")
CASES = json.load(open(FIXTURE, encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_annotated_source_language_case(case):
    assert detect_source_lang(case["body"], case["meta"]) == tuple(case["expected"])


def test_fixture_has_required_coverage():
    ids = {case["id"] for case in CASES}
    languages = {case["expected"][0] for case in CASES}
    assert len(CASES) >= 20
    assert {
        "en", "de", "it", "es", "pt", "fr", "nl", "ro", "sl", "ru",
        "mn", "mk", "el", "zh", "und",
    } <= languages
    assert any("conflict" in ident for ident in ids)
    assert any("problem_prefix" in ident or "problem_solution_prefix" in ident for ident in ids)
    assert any(ident.startswith("und_") for ident in ids)


def test_real_mathnet_source_fixtures_are_verbatim_and_traceable():
    real_cases = [case for case in CASES if "provenance" in case]
    assert {case["provenance"]["row_id"] for case in real_cases} == {
        "026c", "027k", "08im", "0dmb", "0aeo",
    }
    for case in real_cases:
        provenance = case["provenance"]
        assert provenance["dataset"] == "ShadenA/MathNet"
        assert provenance["revision"] == "33e6b3bc254e6f0f0c1479b4252f5e9dd551d56c"
        assert hashlib.sha256(case["body"].encode()).hexdigest() == (
            provenance["problem_markdown_sha256"]
        )


def test_026c_uses_the_complete_real_problem_text():
    case = next(case for case in CASES if case["id"] == "026c")
    assert case["provenance"]["row_id"] == "026c"
    assert "$AD = AB = BC = 1~\\mathrm{cm}$" in case["body"]
    assert "(E) $120^\\circ$" in case["body"]
    assert "..." not in case["body"]
    assert detect_source_lang(case["body"], case["meta"])[0] == "pt"


def test_english_high_confidence_recall_is_at_least_ninety_percent():
    # Multilingual labels containing English are contractually capped at
    # medium, so the high-confidence denominator is the pure-English cohort.
    english = [case for case in CASES if case["expected"] == ["en", "high"]]
    high = sum(detect_source_lang(case["body"], case["meta"]) == ("en", "high") for case in english)
    assert high / len(english) >= 0.9


def test_detector_threshold_boundaries_are_observable():
    assert detect_source_lang(
        "Prove that Fibonacci recursion terminates.", {}
    ) == ("en", "high")
    assert detect_source_lang(
        "Let παραλληλόγραμμο be the name of this figure.", {}
    ) == ("en", "high")
    assert detect_source_lang(
        "Let αβ and γδ be positive real numbers.", {}
    ) == ("en", "high")
    assert detect_source_lang("In Ĳmuiden en Ĳlst.", {}) == ("nl", "medium")


def test_no_non_english_fixture_is_misclassified_as_english():
    non_english = [case for case in CASES if case["expected"][0] != "en"]
    false_english = [
        case["id"] for case in non_english
        if detect_source_lang(case["body"], case["meta"])[0] == "en"
    ]
    assert false_english == []


def test_empty_and_non_dict_metadata_are_safe():
    assert detect_source_lang("", {}) == ("und", "low")
    assert detect_source_lang("$x^2+y^2=z^2$", None) == ("und", "low")


@pytest.mark.parametrize(
    ("source_lang", "confidence", "target_lang", "expected"),
    [
        ("en", "high", "en", True),
        ("en", "medium", "en", False),
        ("en", "low", "en", False),
        ("en-US", "high", "en", False),
        ("it", "high", "en", False),
        ("und", "low", "en", False),
        ("en", "high", "zh", False),
    ],
)
def test_passthrough_policy_has_one_strict_gate(
    source_lang, confidence, target_lang, expected
):
    assert should_passthrough(source_lang, confidence, target_lang) is expected
