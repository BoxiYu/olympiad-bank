"""学段下界（SPEC §4：本库只收初中+高中，★1 不入库）在各准入点的回归用例。

锁定的契约是「下界必须在写盘之前拦住，而不只在最终 lint 拦住」：
lint 是最后一道门，但入库脚本若放行 ★1，题号与板块配额已被消耗，
事后修复要动重号，成本远高于在准入路径直接跳过。

运行：uv run --group dev pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402
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
