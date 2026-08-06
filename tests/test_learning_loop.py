"""训练闭环关键函数级用例（纯 stdlib + pytest）：

1. 旧格式记录归一化（normalize_attempt）
2. 毕业连击（is_graduated）
3. result 自动建议（_suggest_result）
4. 题卡解析不含答案（split_sections / build_card 白名单制）
5. 确认边台账必填出处（cmd_similar --confirm 的 --evidence 契约）

运行：uv run --group dev pytest -q
"""
import argparse
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import spar_session as sp  # noqa: E402


# ---------------- 1. 旧记录归一化 ----------------

def _old(result, hints):
    return sp.normalize_attempt(
        {'id': 'X-001', 'result': result, 'date': '2026-07-01', 'hints': hints})


class TestNormalizeAttempt:
    @pytest.mark.parametrize('result,hints,expect', [
        ('ok', 0, 'independent_ok'),    # ok 且 hints≤1 → independent_ok
        ('ok', 1, 'independent_ok'),
        ('ok', 2, 'hinted_ok'),         # ok 且 hints≥2 → hinted_ok
        ('ok', 3, 'hinted_ok'),
        ('hard', 0, 'hinted_ok'),       # hard → hinted_ok（与提示数无关）
        ('hard', 3, 'hinted_ok'),
        ('fail', 1, 'fail'),            # fail 新旧同义
    ])
    def test_old_result_mapping(self, result, hints, expect):
        assert _old(result, hints)['result'] == expect

    def test_int_hints_synthesized_as_level_list(self):
        assert _old('ok', 3)['hints'] == [{'level': 1}, {'level': 2}, {'level': 3}]
        assert _old('ok', 0)['hints'] == []

    def test_v2_record_passthrough_with_defaults(self):
        hints = [{'level': 1, 'at': '2026-08-03T10:00:00+01:00', 'early': True}]
        r = sp.normalize_attempt({'id': 'A-001', 'result': 'solution_reconstructed',
                                  'date': '2026-08-03', 'hints': hints})
        assert r['result'] == 'solution_reconstructed'   # v2 四值原样通过
        assert r['hints'] == hints
        assert r['revealed_at'] is None
        assert r['mode'] == 'fresh'
        assert r['stuck'] is None
        assert r['student'] == 'self'


# ---------------- 2. 毕业连击 ----------------

def _recs(*results):
    return [{'result': r} for r in results]


class TestGraduation:
    def test_two_consecutive_independent_ok_graduates(self):
        assert sp.is_graduated(_recs('independent_ok', 'independent_ok'))

    def test_single_independent_ok_not_enough(self):
        assert not sp.is_graduated(_recs('independent_ok'))

    def test_broken_streak_does_not_graduate(self):
        assert not sp.is_graduated(
            _recs('independent_ok', 'hinted_ok', 'independent_ok'))

    def test_streak_anywhere_in_history_is_permanent(self):
        # 毕业后再 fail 也不撤销（永久退出复习队列）
        assert sp.is_graduated(
            _recs('fail', 'independent_ok', 'independent_ok', 'fail'))

    def test_contract_constants(self):
        assert sp.GRADUATE_STREAK == 2
        assert sp.INTERVALS == {'independent_ok': 21, 'hinted_ok': 7,
                                'solution_reconstructed': 3, 'fail': 2}


# ---------------- 3. result 自动建议 ----------------

def _suggest(n_hints, time_min, limit=40):
    meta = {'hints': [{'level': i + 1} for i in range(n_hints)],
            'time_limit_min': limit}
    return sp._suggest_result(meta, time_min)[0]


class TestSuggestResult:
    def test_no_hints_within_limit_is_independent(self):
        assert _suggest(0, 30) == 'independent_ok'

    def test_one_hint_within_limit_is_independent(self):
        assert _suggest(1, 39) == 'independent_ok'

    def test_two_hints_is_hinted(self):
        assert _suggest(2, 30) == 'hinted_ok'

    def test_over_time_limit_is_hinted(self):
        assert _suggest(0, 41) == 'hinted_ok'

    def test_at_exact_limit_still_independent(self):
        assert _suggest(1, 40) == 'independent_ok'


# ---------------- 4. 题卡解析不含答案 ----------------

BODY = """---
id: T-001
title: 测试题
---

# T-001｜测试题

## 题面

设 $x+y=3$，求 $(x+y)^2$。

## 答案

SECRET-ANSWER $9$

## 解法要点

SECRET-SOLUTION 完全平方直接代入。

## 提示阶梯

1. SECRET-HINT-1 想想完全平方。
2. SECRET-HINT-2 直接代入。
"""


class TestCardNoLeak:
    def test_card_contains_statement_but_no_answer_material(self):
        secs = sp.split_sections(BODY, 'T-001')
        fm = {'id': 'T-001', 'title': '测试题', 'difficulty': 3,
              'contest': 'TEST', 'year': 2026, 'source_ref': 'ref'}
        card = sp.build_card(fm, secs, '20260803-T-001-1', 'fresh', 40)
        assert '设 $x+y=3$' in card                 # 题面在
        for leak in ('SECRET-ANSWER', 'SECRET-SOLUTION', 'SECRET-HINT',
                     '## 答案', '## 解法要点', '## 提示阶梯'):
            assert leak not in card, f'题卡泄漏：{leak}'

    def test_hint_ladder_parsed_but_not_in_card(self):
        secs = sp.split_sections(BODY, 'T-001')
        ladder = sp.parse_hint_ladder(secs)
        assert len(ladder) == 2
        assert 'SECRET-HINT-1' in ladder[0]

    def test_unknown_section_rejected(self):
        with pytest.raises(ValueError, match='白名单'):
            sp.split_sections(BODY.replace('## 提示阶梯', '## 出题人吐槽'), 'T-001')

    def test_legacy_english_section_rejected(self):
        # legacy「原文（English）」节已随旧库清退从白名单收回（SPEC §3）
        legacy = BODY.replace('## 答案', '## 原文（English）\n\nGiven.\n\n## 答案')
        with pytest.raises(ValueError, match='白名单'):
            sp.split_sections(legacy, 'T-001')

    def test_missing_statement_rejected(self):
        with pytest.raises(ValueError, match='题面'):
            sp.split_sections('## 答案\n\nSECRET\n', 'T-001')


# ---------------- 5. 确认边台账必填出处 ----------------

EDGE_PROBLEMS = [{'fm': {'id': 'A-001'}}, {'fm': {'id': 'B-001'}}]


def _confirm_args(**kw):
    base = {'id': 'A-001', 'top': 20, 'confirm': 'B-001',
            'relation': 'same_method', 'confidence': 1.0, 'evidence': None}
    base.update(kw)
    return argparse.Namespace(**base)


class TestConfirmEdgeEvidence:
    @pytest.fixture(autouse=True)
    def _edges_in_tmp(self, tmp_path, monkeypatch):
        self.edges = tmp_path / 'edges.jsonl'
        monkeypatch.setattr(sp, 'EDGES_PATH', str(self.edges))

    def test_missing_evidence_rejected(self):
        with pytest.raises(SystemExit) as e:
            sp.cmd_similar(EDGE_PROBLEMS, _confirm_args())
        assert '--evidence' in str(e.value.code)
        assert not self.edges.exists()

    def test_blank_evidence_rejected(self):
        with pytest.raises(SystemExit) as e:
            sp.cmd_similar(EDGE_PROBLEMS, _confirm_args(evidence='  \t '))
        assert '--evidence' in str(e.value.code)
        assert not self.edges.exists()

    def test_evidence_written_verbatim_after_strip(self, capsys):
        raw = '  AI双评审2026-08-06：均为根轴+圆幂引理链 '
        sp.cmd_similar(EDGE_PROBLEMS, _confirm_args(evidence=raw))
        assert '已登记确认边' in capsys.readouterr().out
        lines = self.edges.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 1
        edge = json.loads(lines[0])
        assert edge['evidence'] == raw.strip()   # 自由文本逐字落盘（仅去首尾空白）
        assert edge['src'] == 'A-001' and edge['dst'] == 'B-001'
        assert edge['relation'] == 'same_method' and edge['confirmed'] is True
