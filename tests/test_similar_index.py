"""similar_index dupes 的小样本测试；不访问真实语料或 embedding 模型。"""
import hashlib
import json
import os
import sys

import numpy as np
import pytest

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.insert(0, SCRIPTS)
import similar_index as si  # noqa: E402


TRUE_DUPLICATE_STATEMENTS = {
    # 工单只给出重复关系而未提供真实题面；fixture 只编码已核实的关系，不虚构题意。
    '0i43': '[Fixture: 0i43/0i6q verified duplicate] Complete source statement.',
    '0i6q': '[Fixture: 0i43/0i6q verified duplicate] Complete source statement.',
    # 同一道意大利题的四份逐字相同副本。
    '081y': '[Fixture: Italian same-problem rows] Byte-identical source statement.',
    '081z': '[Fixture: Italian same-problem rows] Byte-identical source statement.',
    '0822': '[Fixture: Italian same-problem rows] Byte-identical source statement.',
    '0823': '[Fixture: Italian same-problem rows] Byte-identical source statement.',
    # AMC 10A 与 AMC 12A 共题。
    '0kjl': '[Fixture: AMC 10A/12A shared problem] Byte-identical source statement.',
    '0kjy': '[Fixture: AMC 10A/12A shared problem] Byte-identical source statement.',
}

FALSE_POSITIVE_STATEMENTS = {
    '0alt': r'Find integers $m,n$ such that $4^m-4^n=255$.',
    '0hv0': r'Find integers $m,n$ such that $231m^2=130n^2$.',
    '0182': (
        r'Find all functions $f:\mathbb R\to\mathbb R$ such that '
        r'$f(x+f(y))-f(x)=(x+f(y))^3-x^3$.'),
    '0eb3': (
        r'Find all functions $f:\mathbb R\to\mathbb R$ such that '
        r'$f(xy)=xf(y)+3f(x)+3$.'),
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(tmp_path, statements, similarity_groups, extra_index_ids=()):
    corpus = tmp_path / 'mathnet-full'
    index_dir = tmp_path / 'simindex'
    corpus.mkdir()
    index_dir.mkdir()
    ids = list(statements)
    source_paths = []
    index_lines = []
    for mid, statement in statements.items():
        problem = corpus / 'by-topic' / 'algebra' / '方程与设元' / mid / 'index.md'
        problem.parent.mkdir(parents=True)
        problem.write_text(
            f'# {mid}\n\n## 题面\n\n{statement}\n\n## 最终答案\n\nFixture.\n',
            encoding='utf-8')
        source_paths.append(problem)
        row = {
            'mathnet_id': mid,
            'path': str(problem.relative_to(corpus)),
            'variants': {'en': 'passthrough'},
        }
        # 刻意使用紧凑序列化；--write 后也必须逐字节保持不变。
        index_lines.append(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
    for mid in extra_index_ids:
        index_lines.append(json.dumps(
            {'mathnet_id': mid, 'path': f'{mid}/index.md'}, separators=(',', ':')) + '\n')
    (corpus / 'index.jsonl').write_text(''.join(index_lines), encoding='utf-8')

    # 同一道题的第二知识点挂载是符号链接；以 index.jsonl 为清单时只能计一次。
    first = source_paths[0]
    alias = corpus / 'by-topic' / 'geometry' / '平面几何综合' / ids[0]
    alias.parent.mkdir(parents=True)
    alias.symlink_to(os.path.relpath(first.parent, alias.parent), target_is_directory=True)

    dim = len(similarity_groups) + len(ids)
    text = np.zeros((len(ids), dim), dtype='float32')
    grouped = set()
    for axis, group in enumerate(similarity_groups):
        for mid in group:
            text[ids.index(mid), axis] = 1.0
            grouped.add(mid)
    next_axis = len(similarity_groups)
    for mid in ids:
        if mid not in grouped:
            text[ids.index(mid), next_axis] = 1.0
            next_axis += 1
    sol = text.copy()
    mask = np.ones(len(ids), dtype=bool)
    np.savez(index_dir / 'cand.npz', ids=np.array(['MN-' + mid for mid in ids]),
             text=text, text_mask=mask, sol=sol, sol_mask=mask)

    with (index_dir / 'cand_meta.jsonl').open('w', encoding='utf-8') as fh:
        for mid in ids:
            fh.write(json.dumps({
                'id': 'MN-' + mid,
                'topics': ['方程与设元'],
                'formulas': sorted(si.extract_formulas(statements[mid])),
            }, ensure_ascii=False) + '\n')
    return corpus, index_dir, source_paths


def _semantic_fixture(tmp_path, extra_index_ids=()):
    statements = {**TRUE_DUPLICATE_STATEMENTS, **FALSE_POSITIVE_STATEMENTS}
    similarity_groups = [
        ('0i43', '0i6q'),
        ('081y', '081z', '0822', '0823'),
        ('0kjl', '0kjy'),
        # 两组都模拟 MiniLM 被模板拉到极高 cosine，但题面实质不同。
        ('0alt', '0hv0'),
        ('0182', '0eb3'),
    ]
    return _write_fixture(tmp_path, statements, similarity_groups, extra_index_ids)


def test_dupes_two_stage_filter_and_write_preserve_source_bytes(tmp_path, capsys):
    corpus, index_dir, source_paths = _semantic_fixture(tmp_path)
    index_before = (corpus / 'index.jsonl').read_bytes()
    source_before = {path: _sha256(path) for path in source_paths}

    assert si.DEFAULT_DUPES_THRESHOLD == .995
    groups = si.find_duplicate_groups(
        block_size=3, index_dir=str(index_dir), corpus_root=str(corpus))

    assert [group['members'] for group in groups] == [
        ['081y', '081z', '0822', '0823'],
        ['0i43', '0i6q'],
        ['0kjl', '0kjy'],
    ]
    members = {mid for group in groups for mid in group['members']}
    assert not members.intersection(FALSE_POSITIVE_STATEMENTS)
    assert all({'score_text', 'score_solution', 'score_formula'} <= group.keys()
               for group in groups)
    assert all(group['score_lexical'] == 1.0 for group in groups)
    assert not (corpus / 'duplicates.jsonl').exists()
    assert (corpus / 'index.jsonl').read_bytes() == index_before
    assert {path: _sha256(path) for path in source_paths} == source_before

    err = capsys.readouterr().err
    assert '候选重复：3 组，8 题' in err
    assert 'cosine 召回 10 条边，词面通过 8 条' in err
    assert '组大小分布：2题×2组, 4题×1组' in err
    assert 'threshold=0.9950' in err
    assert 'DRY RUN：未写任何文件' in err

    written_groups = si.find_duplicate_groups(
        write=True, block_size=3, index_dir=str(index_dir), corpus_root=str(corpus))
    written = [json.loads(line) for line in
               (corpus / 'duplicates.jsonl').read_text(encoding='utf-8').splitlines()]
    assert written == written_groups == groups
    assert (corpus / 'index.jsonl').read_bytes() == index_before
    assert {path: _sha256(path) for path in source_paths} == source_before
    assert 'index.jsonl / index.md 未改动' in capsys.readouterr().err


def test_dupes_cli_root_index_dir_and_explicit_write(tmp_path, monkeypatch, capsys):
    corpus, index_dir, _ = _semantic_fixture(tmp_path)
    base_args = [
        'similar_index.py', 'dupes', '--root', str(corpus),
        '--index-dir', str(index_dir), '--block-size', '4',
    ]
    monkeypatch.setattr(sys, 'argv', base_args)
    si.main()
    assert not (corpus / 'duplicates.jsonl').exists()
    assert 'DRY RUN' in capsys.readouterr().err

    monkeypatch.setattr(sys, 'argv', base_args + ['--write'])
    si.main()
    assert (corpus / 'duplicates.jsonl').exists()


def test_dupes_limit_reports_full_index_coverage_gap(tmp_path, capsys):
    corpus, index_dir, _ = _semantic_fixture(tmp_path, extra_index_ids=('out-of-scope',))

    groups = si.find_duplicate_groups(
        limit=2, block_size=1, index_dir=str(index_dir), corpus_root=str(corpus))

    assert [group['members'] for group in groups] == [['0i43', '0i6q']]
    assert not (corpus / 'duplicates.jsonl').exists()
    err = capsys.readouterr().err
    assert '索引覆盖率：cand.npz 覆盖 12 / 13 (92.3%)，缺口 1' in err
    assert '覆盖已截断：--limit 2' in err

    with pytest.raises(ValueError, match='拒绝用局部结果覆盖'):
        si.find_duplicate_groups(
            limit=2, write=True, index_dir=str(index_dir), corpus_root=str(corpus))


def test_dupes_large_group_skips_quadratic_formula_work(tmp_path, capsys):
    ids = [f'large-{i}' for i in range(4)]
    statement = '[Fixture: abnormal component] Identical source statement.'
    corpus, index_dir, _ = _write_fixture(
        tmp_path, {mid: statement for mid in ids}, [tuple(ids)])

    groups = si.find_duplicate_groups(
        formula_group_limit=3, index_dir=str(index_dir), corpus_root=str(corpus))

    assert len(groups) == 1
    assert groups[0]['members'] == ids
    assert groups[0]['score_formula'] is None
    err = capsys.readouterr().err
    assert '公式比对保护：large-0 组共 4 题，超过上限 3' in err
    assert '跳过 6 对组内公式比对' in err
    assert 'large-0, large-1, large-2, large-3' in err


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
            meta_fh.write(json.dumps(
                {'id': 'MN-' + mid, 'topics': [], 'formulas': []}) + '\n')

    calls = []
    original = si._cosine_block

    def recording_block(left, right):
        calls.append((left.shape, right.shape))
        return original(left, right)

    monkeypatch.setattr(si, '_cosine_block', recording_block)
    groups = si.find_duplicate_groups(
        threshold=1.0, block_size=block_size,
        index_dir=str(index_dir), corpus_root=str(corpus))

    assert groups == []
    assert calls
    assert all(left[0] <= block_size and right[0] == n for left, right in calls)
    assert all(left[0] < n for left, _ in calls)
    err = capsys.readouterr().err
    assert '相似度浮点块上界' in err
    assert '非 N×N' in err
