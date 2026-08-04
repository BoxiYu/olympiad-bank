"""学段下界（SPEC §4：本库只收初中+高中，★1 不入库）在各准入点的回归用例。

锁定的契约是「下界必须在写盘之前拦住，而不只在最终 lint 拦住」：
lint 是最后一道门，但入库脚本若放行 ★1，题号与板块配额已被消耗，
事后修复要动重号，成本远高于在准入路径直接跳过。

运行：uv run --group dev pytest -q
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402
from bank import apply_grade_floor  # noqa: E402
import mathnet_import as mi  # noqa: E402
import mathnet_review as mr  # noqa: E402


def _verdict(codex, **kw):
    v = {'difficulty_codex': codex, 'topics_verdict': 'ok',
         'text_quality': 'ok', 'needs_figure': False, 'recommend': 'claim'}
    v.update(kw)
    return v


# ---------------- 1. 阈值只有一个正本 ----------------

def test_min_difficulty_is_two():
    """学段下界 = ★2。语义正本在 SPEC §4，此处锁执行值。"""
    assert bank.MIN_DIFFICULTY == 2


def test_no_second_threshold_constant():
    """下界不许在别的脚本里另设一份——规则复制是本仓库明令禁止的反模式。"""
    assert mi.MIN_DIFFICULTY is bank.MIN_DIFFICULTY
    assert mr.MIN_DIFFICULTY is bank.MIN_DIFFICULTY


# ---------------- 2. 入库准入：写盘之前就要拦住 ----------------

def test_floor_catches_what_needs_review_misses():
    """评审指出的核心缺口：分歧 1 档的 ★1 组合躲得过 needs_review。

    est★2 + Codex★1 与 est★1 + Codex★2 的分歧都是 1 档（< 2），
    needs_review 放行，但「就低不就高」定稿是 ★1。下界必须独立拦。
    """
    for est, codex in ((2, 1), (1, 2), (1, 1)):
        v = _verdict(codex)
        assert not mi.needs_review(v, est), 'needs_review 本就拦不住，这正是下界存在的理由'
        assert mi.below_floor(est, codex), f'est★{est}/Codex★{codex} 定稿 ★1，必须拒收'


def test_floor_does_not_touch_legal_gradings():
    """合法组合不得被误伤——少收是可接受的，错杀不是。"""
    for est, codex in ((2, 2), (3, 2), (2, 3), (4, 5), (5, 5)):
        assert not mi.below_floor(est, codex)


def test_floor_uses_the_same_low_side_rule_as_render():
    """下界判定与 render 写入 difficulty 的口径必须一致（都取 min）。

    若两者口径漂移，会出现「判定放行、写盘却是 ★1」或反之的错位。
    """
    for est in range(1, 6):
        for codex in range(1, 6):
            assert mi.below_floor(est, codex) == (min(est, codex) < bank.MIN_DIFFICULTY)


# ---------------- 3. 库内现状：不变量 ----------------

def test_bank_has_nothing_below_floor():
    """已入库的题一律不低于下界（lint 之外的独立复核）。"""
    below = [(p['fm'] or {}).get('id') for p in bank.load_all()
             if isinstance((p['fm'] or {}).get('difficulty'), int)
             and (p['fm'] or {})['difficulty'] < bank.MIN_DIFFICULTY]
    assert below == [], f'库内存在低于学段下界的题：{below}'


# ---------------- 4. 池过滤：共用过滤器与浏览工具 ----------------

def _cand(mid, est, cat='algebra', **kw):
    """合成候选行——候选池 candidates/mathnet.jsonl 是 gitignore 的，测试不依赖它。"""
    r = {'mathnet_id': mid, 'difficulty_est': est, 'category': cat, 'status': 'ok',
         'has_images': False, 'difficulty_conf': 'high', 'topics': ['不等式'],
         'contest_raw': 'Test Cup', 'comp_norm': 'test cup', 'language': 'english',
         'head': 'prove that', 'year': 2020}
    r.update(kw)
    return r


def test_apply_grade_floor_drops_and_counts():
    """共用过滤器：滤掉低于下界的行并如实报被滤条数。"""
    rows = [_cand('a', 1), _cand('b', 2), _cand('c', 1), _cand('d', 5)]
    kept, dropped = apply_grade_floor(rows)
    assert [r['mathnet_id'] for r in kept] == ['b', 'd']
    assert dropped == 2


def test_apply_grade_floor_is_noop_when_all_legal():
    kept, dropped = apply_grade_floor([_cand('a', 2), _cand('b', 3)])
    assert len(kept) == 2 and dropped == 0


def test_review_batch_uses_the_shared_floor():
    """评审池与共用过滤器必须是同一个实现，不是各写一份。"""
    import mathnet_review
    assert mathnet_review.apply_grade_floor is apply_grade_floor


def _args(**kw):
    ns = argparse.Namespace(gaps=False, with_images=False, conf='mid', category=None,
                            difficulty=None, node=None, contest=None, lang=None,
                            grep=None, stats=False, limit=50)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _run_candidates(monkeypatch, capsys, rows, **kw):
    monkeypatch.setattr(bank, 'load_candidates', lambda: rows)
    bank.candidates_cmd([], _args(**kw))
    return capsys.readouterr().out


def test_candidates_defaults_to_floor(monkeypatch, capsys):
    """不给 --difficulty 时，★1 不该占选题视野。"""
    out = _run_candidates(monkeypatch, capsys,
                          [_cand('low1', 1), _cand('ok2', 2), _cand('ok4', 4)])
    assert 'MN-low1' not in out
    assert 'MN-ok2' in out and 'MN-ok4' in out


def test_candidates_honours_explicit_low_bound(monkeypatch, capsys):
    """显式点名低档要照出并警告——赛名表校准需要翻低档候选，硬闸不该设在浏览工具上。"""
    out = _run_candidates(monkeypatch, capsys,
                          [_cand('low1', 1), _cand('ok2', 2)], difficulty='1-2')
    assert 'MN-low1' in out, '显式 --difficulty 1-2 必须能看到 ★1'
    assert '入不了库' in out, '照出的同时必须警告它进不了库'


def test_candidates_upper_bound_still_works(monkeypatch, capsys):
    """抬下界不能顺手把上界弄坏。"""
    out = _run_candidates(monkeypatch, capsys,
                          [_cand('a2', 2), _cand('b5', 5)], difficulty='2-3')
    assert 'MN-a2' in out and 'MN-b5' not in out
