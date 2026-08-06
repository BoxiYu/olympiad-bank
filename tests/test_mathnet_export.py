"""MathNet 全量索引的三语投影测试；只使用 tmp_path 自造语料。"""
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.insert(0, SCRIPTS)
import mathnet_export as me  # noqa: E402

REPO_ROOT = Path(SCRIPTS).parent


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
            'en': {'mode': 'passthrough', 'sha256': _sha(originals['en-source'])},
            'zh': {'mode': 'translated', 'sha256': _sha('# en-source 中文\n')},
        },
    }), encoding='utf-8')
    (paths['en-source'].parent / 'index.en.md').write_text(
        originals['en-source'], encoding='utf-8')
    (paths['en-source'].parent / 'index.zh.md').write_text(
        '# en-source 中文\n', encoding='utf-8')
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
            'en': {'mode': 'translated', 'sha256': _sha('# translated English\n')},
        },
    }), encoding='utf-8')
    (paths['translated-en'].parent / 'index.en.md').write_text(
        '# translated English\n', encoding='utf-8')
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


def test_refresh_marks_missing_or_hash_mismatched_variant_missing(tmp_path, capsys):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    source = '# source\n'
    rel, path = _problem(out, 'broken-variants', source)
    (path.parent / 'index.zh.md').write_text('# tampered\n', encoding='utf-8')
    (path.parent / 'translation.json').write_text(json.dumps({
        'mathnet_id': 'broken-variants',
        'source_sha256': _sha(source),
        'source_lang': 'fr',
        'variants': {
            'en': {'mode': 'translated', 'sha256': _sha('# missing\n')},
            'zh': {'mode': 'translated', 'sha256': _sha('# expected\n')},
        },
    }), encoding='utf-8')
    me.write_index(str(out), [_row('broken-variants', rel)])

    me.refresh_index(str(out))

    row = json.loads((out / 'index.jsonl').read_text(encoding='utf-8'))
    assert row['variants'] == {'en': 'missing', 'zh': 'missing'}
    errors = capsys.readouterr().err
    assert 'index.en.md 缺失或不可读' in errors
    assert 'index.zh.md sha256 与 translation.json 不一致' in errors
    assert errors.count('索引写 missing') == 2


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
    assert os.path.dirname(stash_root) == str(out)  # 暂存位于被 ignore 的 out 内且同盘

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


def test_prepare_out_refusal_names_every_stash_and_translation_count(tmp_path, capsys):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    for suffix in ('one', 'two'):
        pdir = out / f'.translations-{suffix}' / suffix
        pdir.mkdir(parents=True)
        (pdir / 'translation.json').write_text('{}', encoding='utf-8')

    with pytest.raises(SystemExit):
        me.prepare_out(str(out))

    error = capsys.readouterr().err
    assert str(out / '.translations-one') in error
    assert str(out / '.translations-two') in error
    assert error.count('1 份 translation.json') == 2


def test_prepare_out_adopts_legacy_sibling_stash(tmp_path):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    (out / 'partial-output').mkdir()
    legacy = tmp_path / 'mathnet-full.translations-old-run'
    pdir = legacy / 'legacy1'
    pdir.mkdir(parents=True)
    (pdir / 'translation.json').write_text(
        '{"mathnet_id":"legacy1"}', encoding='utf-8')

    stash, stash_root = me.prepare_out(str(out))

    assert not legacy.exists()
    assert os.path.dirname(stash_root) == str(out)
    assert set(stash) == {'legacy1'}
    assert me.translation_count(stash_root) == 1
    assert not (out / 'partial-output').exists()


def test_prepare_out_recovers_stash_after_sigkill(tmp_path):
    """SIGKILL 不可捕获；持久暂存必须让下一次产品函数调用主动认领并完好回填。"""
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    (out / 'index.jsonl').write_text('', encoding='utf-8')
    _rel, path = _problem(out, 'kill1', '# kill1\n')
    (path.parent / 'index.en.md').write_text('# paid translation\n', encoding='utf-8')
    (path.parent / 'translation.json').write_text(
        '{"mathnet_id":"kill1"}', encoding='utf-8')

    pid = os.fork()
    if pid == 0:
        me.prepare_out(str(out))
        os.kill(os.getpid(), signal.SIGKILL)
    _pid, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL

    stash, stash_root = me.prepare_out(str(out))
    assert set(stash) == {'kill1'}
    assert me.translation_count(stash_root) == 1
    new_dir = out / 'by-topic' / 'algebra' / 'new' / 'kill1'
    new_dir.mkdir(parents=True)
    me.restore_translations(stash, 'kill1', str(new_dir))
    assert (new_dir / 'index.en.md').read_text(encoding='utf-8') == '# paid translation\n'
    assert json.loads((new_dir / 'translation.json').read_text(encoding='utf-8')) == {
        'mathnet_id': 'kill1'}


def test_export_finally_preserves_stash_and_restores_signal_handlers(tmp_path, monkeypatch, capsys):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    (out / 'index.jsonl').write_text('', encoding='utf-8')
    _rel, path = _problem(out, 'crash1', '# crash1\n')
    (path.parent / 'translation.json').write_text(
        '{"mathnet_id":"crash1"}', encoding='utf-8')
    snapshot = tmp_path / 'snapshot'
    shard_dir = snapshot / 'data' / 'all'
    shard_dir.mkdir(parents=True)
    (shard_dir / 'part.parquet').write_bytes(b'not read by test')
    monkeypatch.setattr(me, 'load_node_category', lambda: {})
    monkeypatch.setattr(me, 'load_pool', lambda: {})
    monkeypatch.setattr(me, 'in_bank_snapshot', lambda: {})
    monkeypatch.setattr(me, 'snapshot_dir', lambda: str(snapshot))
    monkeypatch.setattr(me, 'export_prepared', lambda *_args: (_ for _ in ()).throw(
        RuntimeError('synthetic crash')))
    old_sigterm = signal.getsignal(signal.SIGTERM)

    with pytest.raises(RuntimeError, match='synthetic crash'):
        me.export(str(out), with_images=False)

    roots = me.translation_stash_roots(str(out))
    assert len(roots) == 1 and me.translation_count(roots[0]) == 1
    assert signal.getsignal(signal.SIGTERM) == old_sigterm
    error = capsys.readouterr().err
    assert roots[0] in error
    assert '1 份译文' in error
    assert '下次重跑会主动认领' in error


def test_translation_stash_survives_git_clean(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / '.gitignore').write_text(
        (REPO_ROOT / '.gitignore').read_text(encoding='utf-8'), encoding='utf-8')
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    out = repo / 'mathnet-full'
    out.mkdir()
    (out / 'index.jsonl').write_text('', encoding='utf-8')
    _rel, path = _problem(out, 'clean1', '# clean1\n')
    (path.parent / 'translation.json').write_text(
        '{"mathnet_id":"clean1"}', encoding='utf-8')

    _stash, stash_root = me.prepare_out(str(out))
    subprocess.run(['git', 'clean', '-fd'], cwd=repo, check=True)

    assert os.path.isdir(stash_root)
    assert me.translation_count(stash_root) == 1


def test_backfill_index_metadata_only_changes_three_fields(tmp_path, monkeypatch):
    out = tmp_path / 'mathnet-full'
    out.mkdir()
    relative = 'by-topic/algebra/topic/meta1/index.md'
    original_line = (
        '{"mathnet_id":"meta1", "path":"' + relative
        + '", "keep" : [ 1, 2 ], "tail":{"x": 1}}\r\n')
    (out / 'index.jsonl').write_bytes(original_line.encode())
    source = out / 'by-topic' / 'algebra' / 'topic' / 'meta1' / 'index.md'
    source.parent.mkdir(parents=True)
    source.write_text(
        '# meta1\n\n- MathNet 原始标签：Algebra > Equations; Word Problems\n\n'
        '## 题面\n\nORIGINAL INDEX MARKDOWN\n', encoding='utf-8')
    before_source = source.read_bytes()
    monkeypatch.setattr(me, 'load_pool', lambda: {'meta1': {'difficulty_conf': 'high'}})
    monkeypatch.setattr(me, 'in_bank_snapshot', lambda: {'meta1': 'A-001'})

    assert me.backfill_index_metadata(str(out)) == 0

    assert (out / 'index.jsonl').read_bytes() == (
        '{"mathnet_id":"meta1", "path":"by-topic/algebra/topic/meta1/index.md", '
        '"keep" : [ 1, 2 ], "tail":{"x": 1}, '
        '"topics_flat": ["Algebra > Equations","Word Problems"], "difficulty_conf": "high", '
        '"in_bank": "A-001"}\r\n').encode()
    assert source.read_bytes() == before_source
