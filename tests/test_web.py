"""浏览器训练台（scripts/web_app.py）端到端用例。

锁定两件事：
1. 防泄答是服务端强制：reveal 前任何页面不得出现答案/解法要点/未解锁提示；
2. web 收卷写出的 attempts.jsonl 记录满足日志 v2 契约（与 CLI 同一 commit_finish 正本）。

运行：uv run --group dev pytest -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import spar_session as sp  # noqa: E402
import web_app  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

BODY = """---
id: A-001
title: Test Problem
---

# A-001｜Test Problem

## 题面

Find all $x$ with $x+1=2$.

## 答案

SECRET-ANSWER $x=1$

## 解法要点

SECRET-SOLUTION Subtract one.

## 提示阶梯

1. SECRET-HINT-1 Move the constant.
2. SECRET-HINT-2 Subtract $1$ on both sides.
"""

FM = {'id': 'A-001', 'title': 'Test Problem', 'category': 'algebra',
      'contest': 'TEST', 'year': 2026, 'source_ref': 'MathNet test',
      'difficulty': 3, 'topics': ['方程与设元'], 'verification': 'mathnet-reviewed',
      'source_url': 'https://example.com'}

FAKE_PROBLEMS = [{'path': '/fake/A-001.md', 'file': 'A-001.md', 'cat': 'algebra',
                  'fm': FM, 'body': BODY}]

LEAKS = ('SECRET-ANSWER', 'SECRET-SOLUTION', 'SECRET-HINT')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, 'ATTEMPTS_PATH', str(tmp_path / 'attempts.jsonl'))
    monkeypatch.setattr(sp, 'SESSIONS_ROOT', str(tmp_path / 'sessions'))
    monkeypatch.setattr(sp, 'PLAN_PATH', str(tmp_path / 'plan.json'))
    monkeypatch.setattr(web_app, 'load_problems', lambda: FAKE_PROBLEMS)
    web_app.LAST_SUMMARY.clear()
    return TestClient(web_app.app)


def _start(client):
    r = client.post('/start', data={'pid': 'A-001'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/session'


class TestMdToHtml:
    def test_display_math_block_stays_in_one_text_node(self):
        # $$…$$ 多行公式块不能被 <br> 拆开，否则 KaTeX auto-render 识别不了
        h = web_app.md_to_html('defined by\n$$\ny_n = \\frac{x_1}{1^b}\n$$\nis convergent.')
        assert '<br' not in h
        assert '$$\ny_n' in h

    def test_html_is_escaped(self):
        assert '<script>' not in web_app.md_to_html('evil <script>alert(1)</script>')


class TestNoLeak:
    def test_home_and_session_have_no_answer_material(self, client):
        _start(client)
        for url in ('/', '/session', '/session/finish'):
            text = client.get(url).text
            for leak in LEAKS:
                assert leak not in text, f'{url} 泄漏 {leak}'

    def test_statement_is_rendered(self, client):
        _start(client)
        assert 'Find all' in client.get('/session').text

    def test_only_unlocked_hints_appear(self, client):
        _start(client)
        client.post('/session/hint')
        text = client.get('/session').text
        assert 'SECRET-HINT-1' in text
        assert 'SECRET-HINT-2' not in text
        assert 'SECRET-ANSWER' not in text

    def test_solution_only_after_reveal(self, client):
        _start(client)
        assert 'SECRET-ANSWER' not in client.get('/session').text
        client.post('/session/reveal')
        text = client.get('/session').text
        assert 'SECRET-ANSWER' in text and 'SECRET-SOLUTION' in text


class TestFlow:
    def test_start_records_open_session_and_card(self, client):
        _start(client)
        sess = sp.find_open_session()
        assert sess is not None
        sid, meta = sess
        assert meta['id'] == 'A-001' and meta['status'] == 'open'
        assert meta['time_limit_min'] == sp.TIME_LIMIT[3]
        card = open(os.path.join(sp.SESSIONS_ROOT, sid, 'statement.md'), encoding='utf-8').read()
        for leak in LEAKS:
            assert leak not in card

    def test_second_start_rejected_without_abandon(self, client):
        _start(client)
        r = client.post('/start', data={'pid': 'A-001'}, follow_redirects=False)
        assert '/?err=' in r.headers['location']
        r = client.post('/start', data={'pid': 'A-001', 'abandon': '1'}, follow_redirects=False)
        assert r.headers['location'] == '/session'

    def test_hint_marks_early_and_meta(self, client):
        _start(client)
        client.post('/session/hint')
        _, meta = sp.find_open_session()
        assert len(meta['hints']) == 1
        assert meta['hints'][0]['level'] == 1
        assert meta['hints'][0]['early'] is True  # 刚开卡就解锁，必然在冷却期内

    def test_finish_writes_v2_record(self, client):
        _start(client)
        client.post('/session/hint')
        client.post('/session/reveal')
        r = client.post('/session/finish',
                        data={'result': 'solution_reconstructed', 'stuck': '建模', 'note': 'web 测试'},
                        follow_redirects=False)
        assert '/?done=' in r.headers['location']
        recs = sp.load_attempts_v2()
        assert len(recs) == 1
        rec = recs[0]
        assert rec['id'] == 'A-001'
        assert rec['result'] == 'solution_reconstructed'
        assert rec['stuck'] == '建模'
        assert rec['mode'] == 'fresh'
        assert rec['student'] == 'self'
        assert rec['revealed_at'] is not None
        assert [h['level'] for h in rec['hints']] == [1]
        assert sp.find_open_session() is None
        # 摘要页闪现
        done_sid = r.headers['location'].split('done=')[1]
        assert '已落账' in client.get(f'/?done={done_sid}').text

    def test_finish_rejects_illegal_result(self, client):
        _start(client)
        r = client.post('/session/finish', data={'result': 'nonsense'}, follow_redirects=False)
        assert '/session/finish?err=' in r.headers['location']
        assert sp.load_attempts_v2() == []

    def test_abandon_writes_no_record(self, client):
        _start(client)
        client.post('/abandon')
        assert sp.find_open_session() is None
        assert sp.load_attempts_v2() == []

    def test_plan_generation_and_queue(self, client):
        r = client.post('/plan', data={'target': 'AIME', 'n': '5', 'seed': '7'},
                        follow_redirects=False)
        assert '/?msg=' in r.headers['location']
        plan = sp.load_plan()
        assert plan['target'] == 'AIME' and plan['items'] == ['A-001']
        text = client.get('/').text
        assert 'A-001' in text and '本周计划' in text
