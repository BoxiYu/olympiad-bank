"""MathNet 全量三语检索的自造语料回归测试。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / 'scripts' / 'bank.py'


def _write_problem(corpus, rel, orig, en=None, zh=None):
    source = corpus / rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(orig, encoding='utf-8')
    if en is not None:
        source.with_name('index.en.md').write_text(en, encoding='utf-8')
    if zh is not None:
        source.with_name('index.zh.md').write_text(zh, encoding='utf-8')


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / 'mathnet-sample'
    rows = [
        {
            'mathnet_id': '0001',
            'path': 'by-topic/algebra/不等式/0001/index.md',
            'category': 'algebra',
            'topics': ['不等式', '函数方程'],
            'difficulty_est': 4,
            'country': 'China',
            'variants': {'en': 'translated', 'zh': 'translated'},
            'translation_stale': False,
        },
        {
            'mathnet_id': '0002',
            'path': 'by-topic/geometry/圆/0002/index.md',
            'category': 'geometry',
            'topics': ['圆'],
            'difficulty_est': 3,
            'country': 'France',
            'variants': {'en': 'missing', 'zh': 'translated'},
            'translation_stale': True,
        },
        {
            'mathnet_id': '0003',
            'path': 'by-topic/combinatorics/计数/0003/index.md',
            'category': 'combinatorics',
            'topics': ['计数'],
            'difficulty_est': 2,
            'country': 'USA',
            'variants': {'en': {'mode': 'passthrough'}, 'zh': {'mode': 'passthrough'}},
            'translation_stale': False,
        },
    ]
    _write_problem(
        root, rows[0]['path'],
        '# 0001\n\nORIG_ONLY shared-token inequality',
        '# 0001\n\nEN_ONLY translated inequality',
        '# 0001\n\n中文唯一 不等式',
    )
    _write_problem(
        root, rows[1]['path'],
        '# 0002\n\nSECOND_ORIG shared-token circle',
        zh='# 0002\n\n第二题 过期译文',
    )
    _write_problem(
        root, rows[2]['path'],
        '# 0003\n\nTHIRD_ORIG shared-token counting',
        '# 0003\n\nPASSTHROUGH_EN counting',
        '# 0003\n\n原文直通 计数',
    )

    # 同一题第二知识点只挂符号链接；索引还故意重复一行，锁定 mathnet_id 防御性去重。
    real_dir = (root / rows[0]['path']).parent
    alias = root / 'by-topic/algebra/函数方程/0001'
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(os.path.relpath(real_dir, alias.parent), target_is_directory=True)
    duplicate = dict(rows[0], path='by-topic/algebra/函数方程/0001/index.md')

    root.mkdir(parents=True, exist_ok=True)
    with (root / 'index.jsonl').open('w', encoding='utf-8') as fh:
        for row in [*rows, duplicate]:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    return root


def _search(corpus, *args):
    return subprocess.run(
        [sys.executable, str(BANK), 'mathnet-search', *args, '--root', str(corpus)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ('lang', 'keyword', 'expected_path'),
    [('orig', 'ORIG_ONLY', 'index.md'),
     ('en', 'EN_ONLY', 'index.en.md'),
     ('zh', '中文唯一', 'index.zh.md')],
)
def test_each_language_searches_its_own_file_once(corpus, lang, keyword, expected_path):
    result = _search(corpus, keyword, '--lang', lang)

    assert result.returncode == 0
    assert result.stdout.count('0001  ') == 1
    assert expected_path in result.stdout
    assert '共 1 题' in result.stdout


def test_metadata_filters_compose(corpus):
    result = _search(
        corpus, 'inequality', '--lang', 'en', '--topic', '不等',
        '--category', 'algebra', '--difficulty', '4', '--country', 'hin',
    )

    assert result.returncode == 0
    assert result.stdout.count('0001  ') == 1
    assert '0002  ' not in result.stdout


@pytest.mark.parametrize(
    ('lang', 'coverage', 'expected', 'unexpected'),
    [('zh', 'translated', '0001', '0003'),
     ('en', 'passthrough', '0003', '0001'),
     ('zh', 'stale', '0002', '0001')],
)
def test_coverage_filters(corpus, lang, coverage, expected, unexpected):
    result = _search(corpus, '--lang', lang, '--coverage', coverage)

    assert result.returncode == 0
    assert f'{expected}  ' in result.stdout
    assert f'{unexpected}  ' not in result.stdout


def test_missing_coverage_lists_expected_path_and_generation_hint(corpus):
    result = _search(corpus, '--lang', 'en', '--coverage', 'missing')

    assert result.returncode == 0
    assert '0002  ' in result.stdout
    assert '（en 版本缺失）' in result.stdout
    assert '请生成：uv run python scripts/mathnet_translate.py export' in result.stdout


def test_missing_language_with_keyword_is_not_a_fake_empty_result(corpus):
    result = _search(corpus, 'anything', '--lang', 'en', '--country', 'France')

    assert result.returncode == 0
    assert '共 0 题' in result.stdout
    assert '有 1 题缺少英文版本' in result.stdout
    assert '请生成：uv run python scripts/mathnet_translate.py export' in result.stdout


def test_limit_reports_exact_truncated_count(corpus):
    result = _search(corpus, 'shared-token', '--limit', '1')

    assert result.returncode == 0
    assert '匹配 3 题，显示 1 题；已截断 2 题' in result.stdout


def test_missing_corpus_exits_successfully_with_rebuild_command(tmp_path):
    result = _search(tmp_path / 'absent', 'keyword')

    assert result.returncode == 0
    assert '全量语料目录不存在' in result.stdout
    assert '请重建：uv run --group mathnet python scripts/mathnet_export.py' in result.stdout
