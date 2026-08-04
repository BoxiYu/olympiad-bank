#!/usr/bin/env python3
"""浏览器训练台（本地 web UI）—— 启动：uv run python scripts/bank.py web

设计约束（与 CLI 同一正本，零逻辑复制）：
- 会话流程与判定全部调用 spar_session 的核心函数（start_session_core / unlock_hint_core /
  reveal_core / commit_finish / finish_suggestion），契约常量（TIME_LIMIT / INTERVALS /
  HINT_COOLDOWN_MIN / STUCK_CHOICES / GRADUATE_STREAK）一律引用不复制；
- 防泄答在服务端强制：页面渲染只经白名单通道——题面走 CARD_SECTIONS 语义（只渲染「题面」），
  提示只渲染 meta 中已解锁的级别，答案/解法要点只在 reveal_core 落盘 revealed_at 之后出现。
  未解锁内容不出服务端（tests/test_web.py 锁定）。
"""
import datetime
import html
import os
import re
import sys
from urllib.parse import quote

from fastapi import FastAPI, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bank  # noqa: E402
import spar_session as sp  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title='奥数训练台')
app.mount('/static', StaticFiles(directory=os.path.join(BASE, 'web_static')), name='static')
templates = Jinja2Templates(directory=os.path.join(BASE, 'web_templates'))

# 收卷摘要的进程内暂存（本地单人应用；POST /finish 写入，GET /?done= 取走）
LAST_SUMMARY = {}

# 判定与卡点的界面文案。语义正本：docs/学生手册.md §2/§3（此处只是展示用抄件，间隔天数不抄，引 INTERVALS）
RESULT_TEXT = {
    'independent_ok': ('独立完成', '没看答案，提示 ≤1 级，限时内独立完成'),
    'hinted_ok': ('提示后完成', '没看答案做出来了，但用到第 2/3 级提示，或超时'),
    'solution_reconstructed': ('看解后复述通过', '看了答案，随后合卷复述出完整证明'),
    'fail': ('未通过', '看了答案，复述也没通过（或没做复述）'),
}
STUCK_TEXT = {
    '建模': '题读了几遍仍不知从何下手，翻不成数学对象和条件',
    '识别结构': '模型建起来了，但没认出该用的结构/方法（看提示 1 秒懂）',
    '关键引理': '方向对了，卡在中间某个关键命题绕不过去',
    '技术执行': '思路完整，但计算、放缩、构造在细节处执行不动或出错',
    '证明表达': '心里「会了」，落笔写不成严谨证明',
}


def load_problems():
    """每次请求现读题库（19 题量级，随改随生效）；测试可整体替换。"""
    return bank.load_all()


def md_to_html(text):
    """极简 markdown → HTML：段落 / 有序列表 / 无序列表，正文一律转义。
    数学公式（$…$ / $$…$$）原样保留在文本节点里，交给前端 KaTeX auto-render。"""
    if not text or not text.strip():
        return ''
    out = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = [l.rstrip() for l in block.splitlines() if l.strip()]
        # 注：正则不可内联进 f-string——f-string 里的反斜杠是 Python 3.12+ 语法，
        # 而本项目 requires-python >=3.11（ruff 的 invalid-syntax 抓到过这处）。
        if all(re.match(r'\s*[-*]\s+', l) for l in lines):
            stripped = (re.sub(r'^\s*[-*]\s+', '', l) for l in lines)
            items = ''.join(f'<li>{html.escape(s)}</li>' for s in stripped)
            out.append(f'<ul>{items}</ul>')
        elif len(lines) > 1 and all(re.match(r'\s*\d+[.、]\s+', l) for l in lines):
            stripped = (re.sub(r'^\s*\d+[.、]\s+', '', l) for l in lines)
            items = ''.join(f'<li>{html.escape(s)}</li>' for s in stripped)
            out.append(f'<ol>{items}</ol>')
        else:
            # 段内换行按 markdown 软换行处理（拼回同一文本节点）：
            # $$…$$ 多行公式块必须留在一个文本节点里，KaTeX auto-render 才能识别
            out.append('<p>' + '\n'.join(html.escape(l) for l in lines) + '</p>')
    return '\n'.join(out)


def _fm_view(fm):
    contest = fm.get('contest') or '?'
    year = fm.get('year') or ''
    if year and str(year) in str(contest):
        year = ''  # 赛名里已带年份，不重复显示
    return {'id': fm['id'], 'title': fm.get('title', ''), 'difficulty': fm.get('difficulty'),
            'stars': '★' * (fm.get('difficulty') or 0),
            'contest': contest, 'year': year,
            'topics': fm.get('topics') or [],
            'limit': sp.TIME_LIMIT.get(fm.get('difficulty'))}


def build_queue(problems):
    """今日队列：复习到期（含逾期标签）在前，周计划未完成在后。顺序契约同 sp.pick_next。"""
    fmmap = sp._fmmap(problems)
    hist = sp.history_by_id(sp.load_attempts_v2())
    today = datetime.date.today()
    due_items = []
    for dd, pid in sp.review_due_list(fmmap, hist, today):
        od = (today - dd).days
        v = _fm_view(fmmap[pid]['fm'])
        v.update(kind='review', mode='review', tag=f'逾期 {od} 天' if od > 0 else '今日到期')
        due_items.append(v)
    plan = sp.load_plan()
    plan_items = []
    for pid, mode in sp.plan_remaining(fmmap, hist, plan):
        v = _fm_view(fmmap[pid]['fm'])
        v.update(kind='plan', mode=mode, tag='重做' if mode == 'review' else '新题')
        plan_items.append(v)
    return due_items, plan_items, plan


def _redirect(url, **flash):
    """303 重定向（POST 后转 GET），可携带 err/msg 闪现参数。"""
    qs = '&'.join(f'{k}={quote(str(v))}' for k, v in flash.items() if v)
    if qs:
        url += ('&' if '?' in url else '?') + qs
    return RedirectResponse(url, status_code=303)


def _elapsed_s(meta):
    start = datetime.datetime.fromisoformat(meta['started_at'])
    return max(0, int((sp._now() - start).total_seconds()))


def _cooldown_remaining_s(meta):
    """距下一级提示的建议再战秒数（可为 0）；参照物同 unlock_hint_core（上次解锁或开卡）。"""
    hints = meta.get('hints', [])
    ref = datetime.datetime.fromisoformat(hints[-1]['at'] if hints else meta['started_at'])
    passed = (sp._now() - ref).total_seconds()
    return max(0, int(sp.HINT_COOLDOWN_MIN * 60 - passed))


@app.get('/')
def index(request: Request, done: str = '', err: str = '', msg: str = ''):
    problems = load_problems()
    due_items, plan_items, plan = build_queue(problems)
    sess = sp.find_open_session()
    open_view = None
    if sess:
        sid, meta = sess
        open_view = {'sid': sid, 'pid': meta['id'], 'title': meta.get('title', ''),
                     'elapsed_min': _elapsed_s(meta) // 60, 'limit': meta.get('time_limit_min')}
    attempts = sp.load_attempts_v2()
    week_cnt, streak = sp.week_and_streak(attempts, datetime.date.today())
    summary = LAST_SUMMARY.pop(done, None) if done else None
    this_week = sp.iso_week_str(datetime.date.today())
    return templates.TemplateResponse(request, 'index.html', {
        'open': open_view, 'due_items': due_items, 'plan_items': plan_items,
        'plan': plan, 'plan_stale': bool(plan) and plan.get('week') != this_week,
        'first_pid': (due_items + plan_items)[0]['id'] if (due_items or plan_items) else None,
        'week_count': week_cnt, 'streak': streak, 'n_attempts': len(attempts),
        'summary': summary, 'err': err, 'msg': msg,
        'targets': list(bank.PLAN_PROFILES), 'result_text': RESULT_TEXT,
        'graduate_streak': sp.GRADUATE_STREAK,
    })


@app.post('/start')
def start(pid: str = Form(...), mode: str = Form(''), abandon: str = Form('')):
    try:
        sp.start_session_core(load_problems(), pid.strip(),
                              mode=mode or None, abandon=bool(abandon))
    except sp.SparError as e:
        return _redirect('/', err=str(e))
    return _redirect('/session')


@app.post('/abandon')
def abandon():
    info = sp.abandon_open_session()
    if not info:
        return _redirect('/', err='没有进行中的会话')
    return _redirect('/', msg=f'已放弃会话 {info[0]}（{info[1]}，不写训练记录）')


@app.get('/session')
def session(request: Request, err: str = ''):
    sess = sp.find_open_session()
    if not sess:
        return _redirect('/', err='没有进行中的会话——先开卡')
    sid, meta = sess
    problems = load_problems()
    fmmap = sp._fmmap(problems)
    pid = meta['id']
    if pid not in fmmap:
        return _redirect('/', err=f'会话 {sid} 指向未知题号 {pid}')
    try:
        sections = sp.split_sections(fmmap[pid]['body'], pid)
    except ValueError as e:
        return _redirect('/', err=str(e))
    ladder = sp.parse_hint_ladder(sections)
    used = meta.get('hints', [])
    hints_view = [{'level': h['level'], 'early': h.get('early'),
                   'html': md_to_html(ladder[h['level'] - 1]) if h['level'] <= len(ladder) else ''}
                  for h in used]
    revealed = bool(meta.get('revealed_at'))
    solution = None
    if revealed:
        # revealed_at 已落盘的会话才走到这里；渲染与 reveal_core 同源（答案 + 解法要点）
        solution = {'answer': md_to_html(sections.get('答案', '')),
                    'keypoints': md_to_html(sections.get('解法要点', ''))}
    fm = fmmap[pid]['fm']
    return templates.TemplateResponse(request, 'session.html', {
        'sid': sid, 'p': _fm_view(fm), 'mode': meta.get('mode'),
        'statement_html': md_to_html(sections['题面']),
        'elapsed_s': _elapsed_s(meta), 'limit_min': meta.get('time_limit_min'),
        'hints': hints_view, 'ladder_total': len(ladder),
        'can_hint': len(used) < len(ladder),
        'cooldown_s': _cooldown_remaining_s(meta), 'cooldown_min': sp.HINT_COOLDOWN_MIN,
        'revealed': revealed, 'solution': solution, 'err': err,
    })


@app.post('/session/hint')
def session_hint():
    try:
        info = sp.unlock_hint_core(load_problems())
    except sp.SparError as e:
        return _redirect('/', err=str(e))
    if info['status'] == 'no_ladder':
        return _redirect('/session', err='本题暂无提示阶梯')
    if info['status'] == 'exhausted':
        return _redirect('/session', err=f"提示已全部解锁（共 {info['total']} 级）——再战一会儿，或看解法/收卷")
    return _redirect(f"/session#hint-{info['level']}")


@app.post('/session/reveal')
def session_reveal():
    try:
        sp.reveal_core(load_problems())
    except sp.SparError as e:
        return _redirect('/', err=str(e))
    return _redirect('/session#solution')


@app.get('/session/finish')
def finish_form(request: Request, err: str = ''):
    sess = sp.find_open_session()
    if not sess:
        return _redirect('/', err='没有进行中的会话')
    sid, meta = sess
    time_min = sp.session_time_min(meta)
    revealed = bool(meta.get('revealed_at'))
    suggested, why = (None, None) if revealed else sp.finish_suggestion(meta, time_min)
    # 已看答案时建议由「合卷复述」单选驱动（页面 JS 按契约映射：通过→solution_reconstructed，否→fail）
    n_hints = len(meta.get('hints', []))
    return templates.TemplateResponse(request, 'finish.html', {
        'sid': sid, 'pid': meta['id'], 'title': meta.get('title', ''),
        'time_min': time_min, 'limit': meta.get('time_limit_min'),
        'n_hints': n_hints, 'revealed': revealed,
        'suggested': suggested, 'why': why,
        'results': [(r,) + RESULT_TEXT[r] + (sp.INTERVALS[r],) for r in sp.RESULTS],
        'stucks': [(c, STUCK_TEXT[c]) for c in sp.STUCK_CHOICES],
        'err': err,
    })


@app.post('/session/finish')
def finish_submit(result: str = Form(...), stuck: str = Form(''), note: str = Form('')):
    sess = sp.find_open_session()
    if not sess:
        return _redirect('/', err='没有进行中的会话')
    sid, meta = sess
    try:
        summary = sp.commit_finish(sid, meta, result, stuck=stuck or None, note=note.strip())
    except sp.SparError as e:
        return _redirect('/session/finish', err=str(e))
    label = RESULT_TEXT.get(result, (result,))[0]
    LAST_SUMMARY[sid] = {'pid': meta['id'], 'title': meta.get('title', ''),
                         'result': result, 'label': label, **summary}
    return _redirect('/', done=sid)


@app.post('/plan')
def make_plan(target: str = Form(...), n: str = Form('10'), seed: str = Form('')):
    profile = bank.PLAN_PROFILES.get(target)
    if not profile:
        return _redirect('/', err=f'未知目标赛事 {target}')
    import random
    n = max(3, min(20, int(n) if str(n).strip().isdigit() else 10))
    seed = int(seed) if str(seed).strip().lstrip('-').isdigit() else 0
    seed = seed or datetime.date.today().toordinal()  # 不填 seed 则按日期换一套
    rng = random.Random(seed)
    hist = sp.history_by_id(sp.load_attempts_v2())
    graduated = {pid for pid, recs in hist.items() if sp.is_graduated(recs)}
    excluded = set(hist) | graduated   # 同 coach：做过的走复习队列；毕业题永久不进周计划
    pool = bank.build_coach_pool(load_problems(), profile, excluded, rng)
    week1 = bank.pick_week(pool, profile, n, rng)
    if not week1:
        return _redirect('/', err='题池为空（都做过或难度不匹配），未生成周计划')
    week = sp.iso_week_str(datetime.date.today())
    sp.save_plan(week, target, seed, [fm['id'] for fm in week1])
    return _redirect('/', msg=f'已生成本周计划：{target}，{len(week1)} 题（seed={seed}）')
