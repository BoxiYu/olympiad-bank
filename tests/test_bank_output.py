"""bank.py 的终端输出回归用例。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402


def _problem(pid, difficulty, category='algebra'):
    return {
        'cat': category,
        'fm': {
            'id': pid,
            'title': f'测试题 {pid}',
            'category': category,
            'contest': 'Test Contest',
            'difficulty': difficulty,
            'topics': ['不等式'],
        },
    }


def _plan_args(target, n=10, category=None):
    return argparse.Namespace(target=target, n=n, seed=1, category=category)


def test_plan_warns_for_each_difficulty_inventory_shortfall(capsys):
    problems = [
        *[_problem(f'A-{i:03d}', 4) for i in range(1, 4)],
        _problem('A-004', 5),
        *[_problem(f'N-{i:03d}', 4, 'number-theory') for i in range(1, 4)],
        *[_problem(f'N-{i:03d}', 5, 'number-theory') for i in range(4, 7)],
    ]

    bank.plan(problems, _plan_args('CMO', category='algebra'))
    out = capsys.readouterr().out

    assert '目标：CMO　板块：algebra　共 4 题' in out
    assert '⚠ 库存缺口：★★★★ 需要 6 道，库存 3 道，缺 3 道' in out
    assert '⚠ 库存缺口：★★★★★ 需要 4 道，库存 1 道，缺 3 道' in out


def test_plan_with_sufficient_inventory_has_no_warning(capsys):
    problems = [
        *[_problem(f'A-{i:03d}', 2) for i in range(1, 6)],
        *[_problem(f'N-{i:03d}', 3, 'number-theory') for i in range(1, 5)],
        _problem('G-001', 4, 'geometry'),
    ]

    bank.plan(problems, _plan_args('AMC10'))
    out = capsys.readouterr().out

    assert '库存缺口' not in out
    assert '目标：AMC10　共 10 题' in out
    assert '难度构成：★2×5，★3×4，★4×1' in out


def test_stats_only_prints_difficulty_distribution(capsys):
    problems = [
        _problem('A-001', 2),
        _problem('N-001', 3, 'number-theory'),
    ]
    problems[0]['fm']['system'] = 'legacy-system'

    bank.stats(problems)
    out = capsys.readouterr().out

    assert '难度分布' in out
    assert '体系分布' not in out
    assert 'legacy-system' not in out
    assert '未归类' not in out
