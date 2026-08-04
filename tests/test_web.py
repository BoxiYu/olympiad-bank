"""浏览器训练台（scripts/web_app.py）端到端用例。

锁定四件事：
1. 防泄答是服务端强制：reveal 前任何页面不得出现答案/解法要点/未解锁提示；
2. web 收卷写出的 attempts.jsonl 记录满足日志 v2 契约（与 CLI 同一 commit_finish 正本）；
3. 网页上不出现任何命令行指令（spar_session 的 CLI 文案经 _web_err 改写）；
4. 四个写操作都带会话号校验——按浏览器后退重提交，不得把成绩记到另一道题上。

运行：uv run --group dev pytest -q
"""
import os
import sys
from urllib.parse import unquote

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402
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

FM2 = dict(FM, id='A-002', title='Second Problem', difficulty=2)
BODY2 = BODY.replace('A-001', 'A-002').replace('SECRET', 'SECOND')

FAKE_PROBLEMS = [{'path': '/fake/A-001.md', 'file': 'A-001.md', 'cat': 'algebra',
                  'fm': FM, 'body': BODY},
                 {'path': '/fake/A-002.md', 'file': 'A-002.md', 'cat': 'algebra',
                  'fm': FM2, 'body': BODY2}]

LEAKS = ('SECRET-ANSWER', 'SECRET-SOLUTION', 'SECRET-HINT')
# 网页上绝不允许出现的命令行痕迹（spar_session 的 CLI 文案必须经 _web_err 改写）
CLI_MARKS = ('uv run', 'spar finish', 'spar start', 'spar next', '--abandon',
             'bank.py', 'KNOWN_SECTIONS', 'VALID_VERIFICATION', 'revealed_at')


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
        assert '/?info=' in r.headers['location']   # 正常流程提示，不是红色告警
        r = client.post('/start', data={'pid': 'A-001', 'abandon': '1'}, follow_redirects=False)
        assert r.headers['location'] == '/session'

    def test_switch_problem_available_while_session_open(self, client):
        # 有未完成会话时，首页仍须给出换题的出口（否则学生无路可走）
        _start(client)
        text = client.get('/').text
        assert '换成这道' in text
        r = client.post('/start', data={'pid': 'A-002', 'abandon': '1'}, follow_redirects=False)
        assert r.headers['location'] == '/session'
        _, meta = sp.find_open_session()
        assert meta['id'] == 'A-002'
        assert sp.load_attempts_v2() == []   # 放弃的那卷不写记录

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
        assert '已登记' in client.get(f'/?done={done_sid}').text

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
        assert plan['target'] == 'AIME' and sorted(plan['items']) == ['A-001', 'A-002']
        text = client.get('/').text
        assert 'A-001' in text and '本周题单' in text

    def test_plan_shortfall_is_explained(self, client):
        # 题库凑不满时必须说清楚为什么，别静默给一个缩水的题单
        r = client.post('/plan', data={'target': 'AIME', 'n': '20'}, follow_redirects=False)
        loc = unquote(r.headers['location'])
        assert '你要的是 20 题' in loc and '挑得出' in loc


class TestNoCliLeak:
    """网页上一条命令行指令都不许出现——学生在浏览器里敲不了命令。"""

    def _assert_clean(self, text, where):
        for m in CLI_MARKS:
            assert m not in text, f'{where} 泄漏命令行痕迹：{m}'

    def test_pages_have_no_cli_marks(self, client):
        _start(client)
        client.post('/session/hint')
        client.post('/session/reveal')
        for url in ('/', '/help', '/session', '/session/finish'):
            self._assert_clean(client.get(url).text, url)

    def test_open_session_conflict_message_is_plain_chinese(self, client):
        _start(client)
        r = client.post('/start', data={'pid': 'A-002'}, follow_redirects=False)
        self._assert_clean(unquote(r.headers['location']), 'POST /start 冲突提示')
        self._assert_clean(client.get('/', follow_redirects=True).text, '首页')

    def test_no_open_session_message_is_plain_chinese(self, client):
        r = client.post('/session/hint', follow_redirects=False)
        self._assert_clean(unquote(r.headers['location']), 'POST /session/hint 无会话')
        r = client.get('/session', follow_redirects=False)
        self._assert_clean(unquote(r.headers['location']), 'GET /session 无会话')

    def test_verification_iron_rule_message_is_plain_chinese(self, client, monkeypatch):
        bad = dict(FM, id='A-003', verification='待核验')  # 不在 sp.VALID_VERIFICATION 白名单
        monkeypatch.setattr(web_app, 'load_problems',
                            lambda: FAKE_PROBLEMS + [{'path': '/f/A-003.md', 'file': 'A-003.md',
                                                      'cat': 'algebra', 'fm': bad, 'body': BODY}])
        r = client.post('/start', data={'pid': 'A-003'}, follow_redirects=False)
        loc = unquote(r.headers['location'])
        self._assert_clean(loc, '铁律拒绝出卡')
        assert '还没通过入库检查' in loc

    def test_broken_problem_file_message_is_plain_chinese(self, client, monkeypatch):
        broken = BODY.replace('## 提示阶梯', '## 出题人吐槽')
        monkeypatch.setattr(web_app, 'load_problems',
                            lambda: [{'path': '/f/A-001.md', 'file': 'A-001.md', 'cat': 'algebra',
                                      'fm': FM, 'body': broken}])
        r = client.post('/start', data={'pid': 'A-001'}, follow_redirects=False)
        loc = unquote(r.headers['location'])
        self._assert_clean(loc, '题目文件损坏')
        assert '格式有问题' in loc


class TestSessionGuard:
    """按浏览器后退重提交，绝不能把成绩记到另一道题上（实测复现过的数据完整性 bug）。"""

    def test_stale_finish_form_does_not_grade_the_new_problem(self, client):
        _start(client)
        sid1, _ = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid1, 'result': 'independent_ok'})
        client.post('/start', data={'pid': 'A-002'})
        sid2, _ = sp.find_open_session()
        assert sid2 != sid1
        # 学生按后退，把给 A-001 填的那张表单又交了一次
        r = client.post('/session/finish',
                        data={'sid': sid1, 'result': 'fail', 'note': '这是给 A-001 填的'},
                        follow_redirects=False)
        recs = sp.load_attempts_v2()
        assert len(recs) == 1, '重复提交不得写第二条记录'
        assert recs[0]['id'] == 'A-001' and recs[0]['result'] == 'independent_ok'
        assert sp.find_open_session()[0] == sid2, 'A-002 这一卷必须原样开着'
        assert '已经交过了' in unquote(r.headers['location'])

    def test_stale_abandon_does_not_kill_the_current_session(self, client):
        # 四个写操作里 abandon 曾漏掉校验：过期的「不做了」会作废学生正在做的那一卷
        _start(client)
        sid1, _ = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid1, 'result': 'independent_ok'})
        client.post('/start', data={'pid': 'A-002'})
        sid2, _ = sp.find_open_session()
        client.post('/session/hint', data={'sid': sid2})
        client.post('/abandon', data={'sid': sid1})       # 学生按后退，点了旧页面上的「不做了」
        sess = sp.find_open_session()
        assert sess is not None, 'A-002 这一卷不得被过期表单作废'
        assert sess[0] == sid2 and len(sess[1]['hints']) == 1

    def test_abandon_with_matching_sid_still_works(self, client):
        _start(client)
        sid, _ = sp.find_open_session()
        client.post('/abandon', data={'sid': sid})
        assert sp.find_open_session() is None
        assert sp.load_attempts_v2() == []

    def test_stale_finish_form_within_same_session_is_rejected(self, client):
        # 打开收卷页 → 回去看了答案 → 用旧表单提交，不得按「独立完成」落账
        _start(client)
        sid, meta = sp.find_open_session()
        stale_state = web_app._state_token(meta)          # 此刻：0 条提示、未看答案
        client.post('/session/reveal', data={'sid': sid})
        r = client.post('/session/finish',
                        data={'sid': sid, 'state': stale_state, 'result': 'independent_ok'},
                        follow_redirects=False)
        assert '/session/finish?err=' in r.headers['location']
        assert sp.load_attempts_v2() == [], '过期表单不得落账'
        # 重新打开收卷页拿到新指纹后可正常提交
        _, meta2 = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid, 'state': web_app._state_token(meta2),
                                             'result': 'solution_reconstructed', 'stuck': '建模'})
        assert sp.load_attempts_v2()[0]['result'] == 'solution_reconstructed'

    def test_stale_hint_and_reveal_are_ignored(self, client):
        _start(client)
        sid1, _ = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid1, 'result': 'independent_ok'})
        client.post('/start', data={'pid': 'A-002'})
        client.post('/session/hint', data={'sid': sid1})
        client.post('/session/reveal', data={'sid': sid1})
        _, meta = sp.find_open_session()
        assert meta['hints'] == [], '过期表单不得给新题解锁提示'
        assert meta['revealed_at'] is None, '过期表单不得给新题亮答案'


class TestNoJsFallback:
    """禁用 JS 时训练闭环仍须走得通（表单是原生 POST）。"""

    def test_retell_drives_result_server_side(self, client):
        # 无 JS 时 result 不会被前端预选，服务端必须按「合上答案重写一遍」的回答兑现判定
        _start(client)
        client.post('/session/reveal')
        sid, _ = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid, 'retell': 'yes', 'result': ''})
        assert sp.load_attempts_v2()[0]['result'] == 'solution_reconstructed'

    def test_retell_no_maps_to_fail(self, client):
        _start(client)
        client.post('/session/reveal')
        sid, _ = sp.find_open_session()
        client.post('/session/finish', data={'sid': sid, 'retell': 'no', 'result': ''})
        assert sp.load_attempts_v2()[0]['result'] == 'fail'


class TestFirstRun:
    def test_zero_state_shows_setup_before_queue(self, client):
        text = client.get('/').text
        assert '第一次来' in text
        assert '第一步：排出这周要做的题' in text
        # 引导块必须排在题目列表之前，别让学生以为要等教练配
        assert text.index('第一步：排出这周要做的题') < text.index('今天要做的题</h2>')

    def test_target_options_show_availability(self, client):
        # 选 IMO 只能排出 3 题这种坑，要在下拉里就看得见
        text = client.get('/').text
        assert '本库可排' in text and '（推荐）' in text

    def test_availability_is_actual_yield_not_pool_size(self, client):
        # 「可排 N 题」必须是按配比真排得出的题数：池子总数会虚高，那正是它要防的坑
        import random as _r
        opts, _ = web_app.plan_target_options(FAKE_PROBLEMS, {}, want=10)
        for o in opts:
            prof = bank.PLAN_PROFILES[o['name']]
            pool = bank.build_coach_pool(FAKE_PROBLEMS, prof, set(), _r.Random(0))
            actual = len(bank.pick_week(pool, prof, 10, _r.Random(0)))
            assert o['avail'] == actual, f"{o['name']} 显示 {o['avail']} 实排 {actual}"


class TestErrorPages:
    def test_404_renders_chinese_page_not_raw_json(self, client):
        r = client.get('/nonexistent')
        assert r.status_code == 404
        assert '这个网址不存在' in r.text
        assert 'Not Found' not in r.text or '<html' in r.text

    def test_plan_rejects_junk_numbers_without_500(self, client):
        for bad in ('abc', '１０', '9' * 400, ''):
            r = client.post('/plan', data={'target': 'AIME', 'n': bad, 'seed': bad},
                            follow_redirects=False)
            assert r.status_code == 303, f'n={bad!r} 不该 500'

    def test_help_page_renders(self, client):
        text = client.get('/help').text
        assert '这个系统怎么练' in text
        assert str(sp.HINT_COOLDOWN_MIN) in text and str(sp.GRADUATE_STREAK) in text
