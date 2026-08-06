"""mathnet-full 译文契约抽检：全部使用 tmp_path 自造语料。"""
import hashlib
import importlib.util
import json
import os
import sys

import pytest

_CHECKER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scripts', 'checks', 'check_translation_contract.py')
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def make_problem(corpus, pid='0abc', source=SOURCE, en=None, zh=ZH, variants=None):
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
        'source_lang': 'en',
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


def test_repository_fidelity_module_loads_and_runs(tmp_path, capsys):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus)
    result = tc.check_corpus(_ROOT, corpus=corpus, sample=10)
    tc.print_result(result)
    out = capsys.readouterr().out
    assert result.status == 'ok', result.errors
    assert result.fidelity == 'enabled'
    assert result.fidelity_note == 'scripts/translation_fidelity.py'
    assert '保真校验 enabled' in out
    assert '保真校验 skipped' not in out


def test_existing_broken_fidelity_module_fails_check_and_cleans_module(tmp_path, capsys):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus)
    stub = os.path.join(tmp_path, 'scripts', 'mathnet_translation_verify.py')
    _write_bytes(stub, b"raise RuntimeError('broken stub')\n")
    previous = sys.modules.get(tc._FIDELITY_MODULE_NAME)

    result = tc.check_corpus(str(tmp_path), sample=10)
    tc.print_result(result)
    out = capsys.readouterr().out
    assert result.status == 'failed'
    assert result.fidelity == 'failed'
    assert any('加载失败' in error and 'broken stub' in error for error in result.errors)
    assert tc.main(['--root', str(tmp_path), '--sample', '10']) == 1
    assert '保真校验 skipped' not in out
    assert sys.modules.get(tc._FIDELITY_MODULE_NAME) is previous


def test_existing_fidelity_module_without_entrypoint_fails_check(tmp_path):
    corpus = os.path.join(tmp_path, 'mathnet-full')
    make_problem(corpus)
    stub = os.path.join(tmp_path, 'scripts', 'mathnet_translation_verify.py')
    _write_bytes(stub, b'VALUE = 1\n')
    result = tc.check_corpus(str(tmp_path), sample=10)
    assert result.status == 'failed'
    assert result.fidelity == 'failed'
    assert any('缺少可调用的 verify_translation' in error for error in result.errors)


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

    def verify(source, translated):
        calls.append((source, translated))
        return [] if source == translated else [{'kind': 'math_mismatch', 'section': '题面'}]

    result = tc.check_corpus(str(tmp_path), sample=10, fidelity_verifier=verify)
    assert len(calls) == 1  # passthrough 由逐字相同这个更强条件覆盖，只抽检 translated
    assert result.fidelity == 'enabled'
    assert any('保真校验失败' in error and 'math_mismatch' in error for error in result.errors)
