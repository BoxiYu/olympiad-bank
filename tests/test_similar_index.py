"""similar_index dupes 的小样本测试；不访问真实语料或 embedding 模型。"""
import hashlib
import json
import os
import sys

import numpy as np

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.insert(0, SCRIPTS)
import similar_index as si  # noqa: E402


def _unit(dim, terms):
    vector = np.zeros(dim, dtype='float32')
    for position, value in terms.items():
        vector[position] = value
    vector /= np.linalg.norm(vector)
    return vector


def _write_fixture(tmp_path):
    corpus = tmp_path / 'mathnet-full'
    index_dir = tmp_path / 'simindex'
    corpus.mkdir()
    index_dir.mkdir()
    ids = ['a', 'b', 'c', 'at-threshold', 'threshold-peer',
           'above', 'above-peer', 'below', 'below-peer']
    topics = {
        'a': ['方程与设元'],
        'b': ['方程与设元'],
        'c': ['平面几何综合'],  # 三题连通组跨知识点
        'above': ['计数与容斥'],
        'above-peer': ['计数与容斥'],
    }
    rows = []
    source_paths = []
    for mid in ids:
        problem = corpus / 'by-topic' / 'algebra' / '方程与设元' / mid / 'index.md'
        problem.parent.mkdir(parents=True)
        problem.write_text(f'# {mid}\n\n## 题面\n\nSynthetic {mid}.\n', encoding='utf-8')
        source_paths.append(problem)
        rows.append({
            'mathnet_id': mid,
            'path': str(problem.relative_to(corpus)),
            'topics': topics.get(mid, []),
        })
    with (corpus / 'index.jsonl').open('w', encoding='utf-8') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    # 同一道题的第二知识点挂载是符号链接；dupes 必须只消费 index.jsonl，不能遍历到第二次。
    alias = corpus / 'by-topic' / 'geometry' / '平面几何综合' / 'a'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(os.path.relpath(source_paths[0].parent, alias.parent),
                     target_is_directory=True)

    dim = 14
    text = np.zeros((len(ids), dim), dtype='float32')
    sol = np.zeros_like(text)
    text[0] = _unit(dim, {0: 1})
    text[1] = _unit(dim, {0: .95, 1: np.sqrt(1 - .95 ** 2)})
    text[2] = _unit(dim, {2: 1})
    sol[0] = _unit(dim, {3: 1})
    sol[1] = _unit(dim, {4: 1})
    sol[2] = _unit(dim, {4: .95, 5: np.sqrt(1 - .95 ** 2)})
    text[3] = _unit(dim, {6: 1})
    text[4] = _unit(dim, {6: .5, 7: np.sqrt(1 - .5 ** 2)})
    text[5] = _unit(dim, {8: 1})
    text[6] = _unit(dim, {8: .51, 9: np.sqrt(1 - .51 ** 2)})
    text[7] = _unit(dim, {10: 1})
    text[8] = _unit(dim, {10: .49, 11: np.sqrt(1 - .49 ** 2)})
    text_mask = np.ones(len(ids), dtype=bool)
    sol_mask = np.array([True, True, True] + [False] * (len(ids) - 3))
    np.savez(index_dir / 'cand.npz', ids=np.array(['MN-' + mid for mid in ids]),
             text=text, text_mask=text_mask, sol=sol, sol_mask=sol_mask)

    shared_formula = sorted(si.extract_formulas(r'Given $x^2=1$, find $x$.'))
    meta = []
    for mid in ids:
        formulas = shared_formula if mid in {'a', 'b'} else []
        meta.append({
            'id': 'MN-' + mid,
            'topics': topics.get(mid, []),
            'formulas': formulas,
        })
    with (index_dir / 'cand_meta.jsonl').open('w', encoding='utf-8') as fh:
        for row in meta:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    return corpus, index_dir, source_paths


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dupes_clusters_transitively_and_preserves_originals(tmp_path, capsys):
    corpus, index_dir, source_paths = _write_fixture(tmp_path)
    before = {path: _sha256(path) for path in source_paths}

    groups = si.find_duplicate_groups(
        threshold=.5, block_size=2, index_dir=str(index_dir), corpus_root=str(corpus))

    assert [group['members'] for group in groups] == [
        ['a', 'b', 'c'],
        ['above', 'above-peer'],
    ]
    first = groups[0]
    assert first == {
        'group_id': 'DG-00001',
        'canonical': 'a',
        'members': ['a', 'b', 'c'],
        'score_text': .95,
        'score_solution': .95,
        'score_formula': 1.0,
        'same_topic': False,
    }
    assert groups[1]['same_topic'] is True
    assert 'at-threshold' not in {mid for group in groups for mid in group['members']}
    assert 'below' not in {mid for group in groups for mid in group['members']}
    assert {path: _sha256(path) for path in source_paths} == before

    written = [
        json.loads(line)
        for line in (corpus / 'duplicates.jsonl').read_text(encoding='utf-8').splitlines()
    ]
    assert written == groups
    projected = {
        row['mathnet_id']: row
        for row in map(json.loads, (corpus / 'index.jsonl').read_text(encoding='utf-8').splitlines())
    }
    assert projected['a']['duplicate_group'] == 'DG-00001'
    assert projected['above']['duplicate_group'] == 'DG-00002'
    assert 'duplicate_group' not in projected['at-threshold']
    assert sum('a' in group['members'] for group in groups) == 1
    err = capsys.readouterr().err
    assert '候选重复：2 组，5 题' in err
    assert '组大小分布：2题×1组, 3题×1组' in err
    assert 'threshold=0.5000' in err
    assert '峰值 RSS' in err


def test_dupes_limit_and_dry_run_report_truncated_coverage(tmp_path, capsys):
    corpus, index_dir, _ = _write_fixture(tmp_path)
    index_before = (corpus / 'index.jsonl').read_bytes()

    groups = si.find_duplicate_groups(
        threshold=.5, limit=2, dry_run=True, block_size=1,
        index_dir=str(index_dir), corpus_root=str(corpus))

    assert [group['members'] for group in groups] == [['a', 'b']]
    assert not (corpus / 'duplicates.jsonl').exists()
    assert (corpus / 'index.jsonl').read_bytes() == index_before
    err = capsys.readouterr().err
    assert '覆盖已截断：--limit 2' in err
    assert 'DRY RUN：未写' in err


def test_dupes_large_fake_index_uses_row_blocks(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / 'mathnet-full'
    index_dir = tmp_path / 'simindex'
    corpus.mkdir()
    index_dir.mkdir()
    n, dim, block_size = 1024, 8, 31
    ids = [f'fake-{i:04d}' for i in range(n)]
    rng = np.random.default_rng(511)
    text = rng.normal(size=(n, dim)).astype('float32')
    text /= np.linalg.norm(text, axis=1, keepdims=True)
    sol = rng.normal(size=(n, dim)).astype('float32')
    sol /= np.linalg.norm(sol, axis=1, keepdims=True)
    mask = np.ones(n, dtype=bool)
    np.savez(index_dir / 'cand.npz', ids=np.array(['MN-' + mid for mid in ids]),
             text=text, text_mask=mask, sol=sol, sol_mask=mask)
    with (corpus / 'index.jsonl').open('w', encoding='utf-8') as index_fh, \
            (index_dir / 'cand_meta.jsonl').open('w', encoding='utf-8') as meta_fh:
        for mid in ids:
            index_fh.write(json.dumps({'mathnet_id': mid, 'path': f'{mid}/index.md'}) + '\n')
            meta_fh.write(json.dumps({'id': 'MN-' + mid, 'topics': [], 'formulas': []}) + '\n')

    calls = []
    original = si._cosine_block

    def recording_block(left, right):
        calls.append((left.shape, right.shape))
        return original(left, right)

    monkeypatch.setattr(si, '_cosine_block', recording_block)
    groups = si.find_duplicate_groups(
        threshold=1.0, dry_run=True, block_size=block_size,
        index_dir=str(index_dir), corpus_root=str(corpus))

    assert groups == []
    assert calls
    assert all(left[0] <= block_size and right[0] == n for left, right in calls)
    assert all(left[0] < n for left, _ in calls)
    err = capsys.readouterr().err
    assert '相似度浮点块上界' in err
    assert '非 N×N' in err
