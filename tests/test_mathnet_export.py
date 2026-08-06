"""MathNet 全量索引的三语投影测试；只使用 tmp_path 自造语料。"""
import hashlib
import json
import os
import sys

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.insert(0, SCRIPTS)
import mathnet_export as me  # noqa: E402


def _row(mid, path):
    return {
        'mathnet_id': mid,
        'path': path,
        'category': 'algebra',
        'topics': ['方程与设元'],
        'difficulty_est': 3,
        'country': 'Testland',
        'contest': 'Synthetic Contest',
        'year': 2026,
        'problem_type': 'proof',
        'language': None,
        'n_images': 0,
        'n_solutions': 1,
        'final_answer': None,
        'status': 'ok',
    }


def _problem(out, mid, body):
    rel = f'by-topic/algebra/方程与设元/{mid}/index.md'
    path = out / rel
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding='utf-8')
    return rel, path


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def test_refresh_index_projects_translation_state_and_readme(tmp_path):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    originals = {
        'en-source': '# en-source\n\n## 题面\n\nFind $x$.\n',
        'translated-en': '# translated-en\n\n## 题面\n\nTrouver $x$.\n',
        'failed-en': '# failed-en\n\n## 题面\n\nHalla $x$.\n',
        'missing-meta': '# missing-meta\n\n## 题面\n\n求 $x$。\n',
    }
    rows = []
    paths = {}
    for mid, body in originals.items():
        rel, path = _problem(out, mid, body)
        rows.append(_row(mid, rel))  # 特意模拟尚无三语字段的旧 index.jsonl
        paths[mid] = path

    (paths['en-source'].parent / 'translation.json').write_text(json.dumps({
        'mathnet_id': 'en-source',
        'source_sha256': _sha(originals['en-source']),
        'source_lang': 'en',
        'variants': {
            'en': {'mode': 'passthrough'},
            'zh': {'mode': 'translated'},
        },
    }), encoding='utf-8')
    (paths['failed-en'].parent / 'translation.json').write_text(json.dumps({
        'mathnet_id': 'failed-en',
        'source_sha256': _sha(originals['failed-en']),
        'source_lang': 'es',
        'variants': {
            'en': {'mode': 'failed'},
        },
    }), encoding='utf-8')
    (paths['translated-en'].parent / 'translation.json').write_text(json.dumps({
        'mathnet_id': 'translated-en',
        'source_sha256': _sha(originals['translated-en']),
        'source_lang': 'fr',
        'variants': {
            'en': {'mode': 'translated'},
        },
    }), encoding='utf-8')
    me.write_index(str(out), rows)

    assert me.refresh_index(str(out)) == 0

    projected = {
        row['mathnet_id']: row
        for row in map(json.loads, (out / 'index.jsonl').read_text(encoding='utf-8').splitlines())
    }
    assert projected['en-source']['source_lang'] == 'en'
    assert projected['en-source']['variants'] == {'en': 'passthrough', 'zh': 'translated'}
    assert projected['en-source']['translation_stale'] is False
    assert projected['translated-en']['variants'] == {'en': 'translated', 'zh': 'missing'}
    assert projected['failed-en']['variants'] == {'en': 'failed', 'zh': 'missing'}
    assert projected['missing-meta']['source_lang'] == 'und'
    assert projected['missing-meta']['variants'] == {'en': 'missing', 'zh': 'missing'}
    assert projected['missing-meta']['translation_stale'] is False
    assert paths['en-source'].read_text(encoding='utf-8') == originals['en-source']

    readme = (out / 'README.md').read_text(encoding='utf-8')
    assert '| 语言 | passthrough | translated | failed | missing |' in readme
    assert '| en | 1 | 1 | 1 | 1 |' in readme
    assert '| zh | 0 | 1 | 0 | 3 |' in readme
    assert 'index.en.md' in readme
    assert 'index.zh.md' in readme
    assert 'translation.json' in readme
    assert '机器生成、未经人工核验' in readme
    assert "(r.get('variants') or {}).get(target_lang, 'missing')" in readme


def test_refresh_marks_translation_stale_after_source_changes(tmp_path):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    source = '# stale\n\n## 题面\n\nOriginal.\n'
    rel, path = _problem(out, 'stale', source)
    me.write_index(str(out), [_row('stale', rel)])
    (path.parent / 'translation.json').write_text(json.dumps({
        'mathnet_id': 'stale',
        'source_sha256': _sha(source),
        'source_lang': 'en',
        'variants': {'en': {'mode': 'passthrough'}, 'zh': {'mode': 'translated'}},
    }), encoding='utf-8')

    me.refresh_index(str(out))
    path.write_text(source + 'Changed without retranslating.\n', encoding='utf-8')
    me.refresh_index(str(out))

    row = json.loads((out / 'index.jsonl').read_text(encoding='utf-8'))
    assert row['translation_stale'] is True


def test_old_index_variant_reader_defaults_to_missing():
    assert me.variant_status({}, 'en') == 'missing'
    assert me.variant_status({'variants': None}, 'zh') == 'missing'
    assert me.variant_status({'variants': {'en': 'unexpected'}}, 'en') == 'missing'


def test_prepare_out_stashes_and_restores_translation_artifacts(tmp_path):
    """全量重导出不得销毁译文产物（付费模型产出）：rmtree 前按 mathnet_id 暂存，
    写完新原文后回填——分类挪了窝也跟着走，原文变动交给 source_sha256 判定失效。"""
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    (out / 'index.jsonl').write_text('', encoding='utf-8')
    _rel, path = _problem(out, 'keep1', '# keep1\n')
    pdir = path.parent
    (pdir / 'index.zh.md').write_text('# keep1 中文\n', encoding='utf-8')
    (pdir / 'translation.json').write_text('{"mathnet_id": "keep1"}', encoding='utf-8')

    stash, stash_root = me.prepare_out(str(out))
    assert set(stash) == {'keep1'}
    assert not path.exists()                       # 原文树整体清空
    assert os.path.isdir(stash_root)               # 暂存目录在 out 兄弟位（同盘）

    new_pdir = out / 'by-topic' / 'algebra' / '换了知识点' / 'keep1'
    new_pdir.mkdir(parents=True)
    me.restore_translations(stash, 'keep1', str(new_pdir))
    assert (new_pdir / 'index.zh.md').read_text(encoding='utf-8') == '# keep1 中文\n'
    assert json.loads((new_pdir / 'translation.json').read_text(encoding='utf-8')) == {
        'mathnet_id': 'keep1'}
    assert stash == {}                             # 就地消耗，剩余项即为找不到归属的译文


def test_prepare_out_fresh_dir_has_nothing_to_stash(tmp_path):
    out = tmp_path / 'mathnet-full'
    stash, stash_root = me.prepare_out(str(out))
    assert stash == {} and stash_root is None
    assert os.path.isdir(str(out))
