"""mathnet-full 译文契约抽检：全部使用 tmp_path 自造语料。"""
import hashlib
import importlib.util
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))

from translation_fidelity import FindingType, verify_translation  # noqa: E402

_CHECKER = os.path.join(_ROOT, 'scripts', 'checks', 'check_translation_contract.py')
_spec = importlib.util.spec_from_file_location('check_translation_contract_test', _CHECKER)
tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tc)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(data)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False)


SOURCE = b'# 0abc\n\n## \xe9\xa2\x98\xe9\x9d\xa2\n\nFind $x$.\n\n![](attached_image_1.png)\n'
ZH = b'# 0abc\n\n## \xe9\xa2\x98\xe9\x9d\xa2\n\n\xe6\xb1\x82 $x$\xe3\x80\x82\n\n![](attached_image_1.png)\n'


def make_problem(
    corpus,
    pid='0abc',
    source=SOURCE,
    en=None,
    zh=ZH,
    variants=None,
    source_lang='en',
):
    problem = os.path.join(corpus, 'by-topic', 'algebra', 'topic', pid)
    en = source if en is None else en
    _write_bytes(os.path.join(problem, 'index.md'), source)
    if en is not False:
        _write_bytes(os.path.join(problem, 'index.en.md'), en)
    if zh is not False:
        _write_bytes(os.path.join(problem, 'index.zh.md'), zh)
    variants = variants or {
        'en': {'mode': 'passthrough', 'sha256': _sha(en)},
        'zh': {'mode': 'translated', 'sha256': _sha(zh)},
    }
    payload = {
        'mathnet_id': pid,
        'source_sha256': _sha(source),
        'source_lang': source_lang,
        'source_lang_confidence': 'high',
        'variants': variants,
    }
    contract = os.path.join(problem, 'translation.json')
    _write_json(contract, payload)
    return problem, contract, payload


def test_missing_corpus_is_quiet_skip(tmp_path, capsys):
    result = tc.check_corpus(str(tmp_path), sample=3)
    tc.print_result(result)
    out = capsys.readouterr().out
    assert result.status == 'skipped'
    assert 'TRANSLATION CHECK skipped' in out
    assert 'mathnet-full/ 不存在' in out


def test_valid_sample_runs_and_reports_size_and_time(tmp_path, capsys):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus)
    result = tc.check_corpus(str(tmp_path), sample=10)
    tc.print_result(result)
    out = capsys.readouterr().out
    assert result.status == 'ok'
    assert '抽样 1/1 题' in out
    assert '保真校验 skipped' in out
    assert '耗时' in out


def test_stale_source_is_caught(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    problem, _contract, _payload = make_problem(corpus)
    _write_bytes(os.path.join(problem, 'index.md'), SOURCE + b'changed\n')
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('译文过期' in error for error in result.errors)


def test_changed_passthrough_is_caught_even_with_matching_variant_hash(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    changed = SOURCE + b'changed\n'
    variants = {
        'en': {'mode': 'passthrough', 'sha256': _sha(changed)},
        'zh': {'mode': 'translated', 'sha256': _sha(ZH)},
    }
    make_problem(corpus, en=changed, variants=variants)
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('passthrough 内容与原文不一致' in error for error in result.errors)


@pytest.mark.parametrize(
    ("source_lang", "confidence", "lang"),
    [("en", "medium", "en"), ("en", "low", "en"), ("it", "high", "en"),
     ("und", "low", "en"), ("en", "high", "zh")],
)
def test_passthrough_below_en_high_is_a_contract_error(
    tmp_path, source_lang, confidence, lang
):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    variants = {lang: {'mode': 'passthrough', 'sha256': _sha(SOURCE)}}
    problem, contract, payload = make_problem(
        corpus, en=SOURCE, zh=SOURCE, variants=variants
    )
    payload['source_lang'] = source_lang
    payload['source_lang_confidence'] = confidence
    _write_json(contract, payload)

    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('passthrough 阈值非法' in error for error in result.errors)


def test_missing_variant_file_is_caught(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus, zh=False, variants={
        'en': {'mode': 'passthrough', 'sha256': _sha(SOURCE)},
        'zh': {'mode': 'translated', 'sha256': _sha(ZH)},
    })
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('variant 文件缺失' in error for error in result.errors)


def test_variant_hash_mismatch_is_caught(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus, variants={
        'en': {'mode': 'passthrough', 'sha256': _sha(SOURCE)},
        'zh': {'mode': 'translated', 'sha256': '0' * 64},
    })
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('variant sha256 对不上' in error for error in result.errors)


@pytest.mark.parametrize('mode', ['copy', '', None])
def test_invalid_mode_is_schema_error(tmp_path, mode):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus, variants={'en': {'mode': mode, 'sha256': _sha(SOURCE)}})
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert any('schema 非法' in error and '.mode=' in error for error in result.errors)


def test_failed_variant_does_not_claim_a_file(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus, zh=False, variants={
        'en': {'mode': 'passthrough', 'sha256': _sha(SOURCE)},
        'zh': {'mode': 'failed'},
    })
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert result.status == 'ok', result.errors


def test_sample_limit_is_explicit(tmp_path, capsys):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    for pid in ('0aaa', '0bbb', '0ccc'):
        make_problem(corpus, pid=pid)
    result = tc.check_corpus(str(tmp_path), sample=2)
    tc.print_result(result)
    out = capsys.readouterr().out
    assert result.checked == 2
    assert result.discovered == 3
    assert '抽样 2/3 题' in out
    assert '覆盖受限：另 1 题未检查' in out


def test_cxb_497_hook_is_called_and_findings_fail(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus)
    calls = []

    def verify(source, translated, *, target_lang):
        calls.append((source, translated, target_lang))
        return [] if source == translated else [{'kind': 'math_mismatch', 'section': '题面'}]

    result = tc.check_corpus(str(tmp_path), sample=10, fidelity_verifier=verify)
    assert len(calls) == 1  # passthrough 由逐字相同这个更强条件覆盖，只抽检 translated
    assert result.fidelity == 'enabled'
    assert any('保真校验失败' in error and 'math_mismatch' in error for error in result.errors)
    assert calls[0][2] == 'zh'


def test_english_translated_variant_uses_english_fidelity_rules(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    chinese_source = '# 0abc\n\n## 题面\n\n求整数 $x$。\n'.encode()
    variants = {
        'en': {'mode': 'translated', 'sha256': _sha(chinese_source)},
    }
    make_problem(
        corpus,
        source=chinese_source,
        en=chinese_source,
        zh=False,
        variants=variants,
        source_lang='zh',
    )

    result = tc.check_corpus(
        str(tmp_path),
        sample=10,
        fidelity_verifier=verify_translation,
    )

    assert any(
        'index.en.md' in error and str(FindingType.UNTRANSLATED) in error
        for error in result.errors
    )
