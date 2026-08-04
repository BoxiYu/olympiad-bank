#!/usr/bin/env python3
"""浏览器训练台（本地 web UI）—— 启动：uv run python scripts/bank.py web

设计约束（与 CLI 同一正本，零逻辑复制）：
- 会话流程与判定全部调用 spar_session 的核心函数（start_session_core / unlock_hint_core /
  reveal_core / commit_finish / finish_suggestion），契约常量（TIME_LIMIT / INTERVALS /
  HINT_COOLDOWN_MIN / STUCK_CHOICES / GRADUATE_STREAK）一律引用不复制；
- 防泄答在服务端强制：页面渲染只经白名单通道——题面走 CARD_SECTIONS 语义（只渲染「题面」），
  提示只渲染 meta 中已解锁的级别，答案/解法要点只在 reveal_core 落盘 revealed_at 之后出现。
  未解锁内容不出服务端（tests/test_web.py 锁定）。
- 界面文字一律说人话：术语正本（开卡/收卷/落账/攻坚/判定）留在 spar_session.py 与 docs/ 两手册，
  网页上换成学生看得懂的说法。**任何命令行指令都不得出现在网页上**——CLI 文案经 _web_err 改写。
"""
import datetime
import html
import os
import random
import re
import sys
import traceback
from urllib.parse import quote

from fastapi import FastAPI, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
# 404/405 由 Starlette 抛它自己的 HTTPException，注册在 FastAPI 的那个类上不会生效
from starlette.exceptions import HTTPException as StarletteHTTPException

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
    'independent_ok': ('独立完成', '没看答案，最多用 1 条提示，在限时内自己做出来了'),
    'hinted_ok': ('提示后完成', '没看答案，自己做出来了，但用到了第 2 条以后的提示，或者超时了'),
    'solution_reconstructed': ('看解后复述通过', '看了答案，然后合上答案自己写出了完整证明'),
    'fail': ('未通过', '看了答案，合上以后也没写出来（或者没写）'),
}
STUCK_TEXT = {
    '建模': '题读了几遍仍不知从何下手，翻不成数学对象和条件',
    '识别结构': '模型建起来了，但没认出该用的结构或方法（看了提示一秒就懂）',
    '关键引理': '方向对了，卡在中间某个关键命题绕不过去',
    '技术执行': '思路完整，但计算、放缩、构造在细节处执行不动或出错',
    '证明表达': '心里「会了」，落笔写不成严谨证明',
}
# 目标赛事的一句话定位（纯展示；星级配比正本在 bank.PLAN_PROFILES）
TARGET_HINT = {
    'AMC8': '初中入门', 'AMC10': '高中入门', 'AMC12': '高中进阶', 'AIME': '美国邀请赛',
    '高联一试': '全国联赛一试', '高联加试': '全国联赛加试',
    'CMO': '中国奥赛', 'USAMO': '美国奥赛', 'IMO': '国际奥赛',
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


# ---------------- 错误改写：CLI 文案 → 网页文案 ----------------
# spar_session 的错误文案是给终端看的，里面拼了 `uv run python scripts/bank.py spar …` 这类指令。
# 网页用户既敲不了命令，也不该看见命令。此处按前缀识别改写，**不改 spar_session 正本**（CLI 仍用原文）。
def _web_err(e, pid=None):
    """SparError/ValueError → (给学生看的话, flash 档位)。档位 err=红色告警，info=中性提示。"""
    s = str(e)
    if s.startswith('已有进行中的会话'):
        return ('你还有一道题没做完。可以接着做，或者在列表里点「换成这道」。', 'info')
    if s.startswith('没有进行中的会话'):
        return ('这道题的计时已经结束了——可能在另一个标签页交过了，或者被放弃了。回首页挑下一道吧。', 'info')
    if s.startswith('铁律：'):
        return (f'这道题（{pid or "该题"}）还没通过入库检查，暂时不能做。换一道题，或者把题号告诉教练。', 'err')
    if '存在白名单外小节' in s or '缺少「## 题面」' in s:
        return (f'这道题（{pid or "该题"}）的题目文件格式有问题，暂时打不开。先换一道题，把题号告诉教练。', 'err')
    if s.startswith('未知题号') or '指向未知题号' in s:
        return (f'题库里找不到这道题（{pid or "该题"}），可能被改名或移走了。把题号告诉教练。', 'err')
    if s.startswith('非法判定') or s.startswith('非法卡点'):
        return ('提交的内容不完整，请重新选一次。', 'err')
    return (f'操作没能完成：{s}', 'err')


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
        v.update(kind='review', mode='review', tag=f'逾期 {od} 天' if od > 0 else '今天到期')
        due_items.append(v)
    plan = sp.load_plan()
    plan_items = []
    for pid, mode in sp.plan_remaining(fmmap, hist, plan):
        v = _fm_view(fmmap[pid]['fm'])
        v.update(kind='plan', mode=mode, tag='重做' if mode == 'review' else '新题')
        plan_items.append(v)
    return due_items, plan_items, plan


PLAN_DEFAULT_N = 10  # 下拉里「可排 N 题」按这个题量干跑；与表单默认值一致


def plan_target_options(problems, hist, want=PLAN_DEFAULT_N):
    """每个目标赛事**按配比真能排出几题**（完整干跑 pick_week，不是候选池总数）。
    选 IMO 只出 3 题这种坑要在下拉里就看见——所以这里必须是实际产出量，
    池子总数会虚高（如 AMC8 池 9 题但按配比只出得了 5 题），那本身就是它想防的坑。"""
    excluded = set(hist)
    opts = []
    for t, prof in bank.PLAN_PROFILES.items():
        pool = bank.build_coach_pool(problems, prof, excluded, random.Random(0))
        avail = len(bank.pick_week(pool, prof, want, random.Random(0)))  # pick_week 会消耗 pool，用完即弃
        stars = sorted(prof)
        opts.append({'name': t, 'avail': avail, 'hint': TARGET_HINT.get(t, ''),
                     'stars': f'★{stars[0]}–★{stars[-1]}' if stars[0] != stars[-1] else f'★{stars[0]}'})
    # 推荐：能凑满的里面星级最低的那个，别让默认值落在只排得出 5 题的 AMC8
    full = [o for o in opts if o['avail'] >= want]
    rec = full[0]['name'] if full else (max(opts, key=lambda o: o['avail'])['name'] if opts else None)
    for o in opts:
        o['recommended'] = (o['name'] == rec)
    return opts, rec


def _redirect(url, **flash):
    """303 重定向（POST 后转 GET），可携带 err/msg/info 闪现参数。"""
    qs = '&'.join(f'{k}={quote(str(v))}' for k, v in flash.items() if v)
    if qs:
        url += ('&' if '?' in url else '?') + qs
    return RedirectResponse(url, status_code=303)


def _flash(msg_level, url='/'):
    text, level = msg_level
    return _redirect(url, **{level: text})


def _elapsed_s(meta):
    start = datetime.datetime.fromisoformat(meta['started_at'])
    return max(0, int((sp._now() - start).total_seconds()))


def _cooldown_remaining_s(meta):
    """距下一级提示的建议再战秒数（可为 0）；参照物同 unlock_hint_core（上次解锁或开卡）。"""
    hints = meta.get('hints', [])
    ref = datetime.datetime.fromisoformat(hints[-1]['at'] if hints else meta['started_at'])
    passed = (sp._now() - ref).total_seconds()
    return max(0, int(sp.HINT_COOLDOWN_MIN * 60 - passed))


def _require_session(sid_from_form):
    """取当前进行中的会话，并校验表单提交的是**同一卷**。
    返回 (sid, meta, None) 或 (None, None, 出错响应)。
    防的是：学生按浏览器后退，把给上一道题填的表单又交了一次——不校验就会记到新题头上。"""
    sess = sp.find_open_session()
    if not sess:
        return None, None, None  # 交由各路由按语境处理（多半是「刚才已经交过了」）
    sid, meta = sess
    if sid_from_form and sid_from_form != sid:
        return None, None, 'mismatch'
    return sid, meta, None


def _state_token(meta):
    """会话进度指纹：已解锁提示条数 + 是否已看答案。
    收卷表单把渲染那一刻的指纹带回来，与当前比对——防的是：学生打开收卷页后又回去
    解锁提示/看了答案，再用后退交回那张旧表单，把「独立完成」记到一次已看答案的攻坚上。"""
    return f"{len(meta.get('hints', []))}:{1 if meta.get('revealed_at') else 0}"


def _already_finished(sid_from_form):
    """从训练日志反查某一卷的结果，用于「你刚才已经交过了」的友好提示。"""
    if not sid_from_form:
        return None
    for r in reversed(sp.load_attempts_v2()):
        if r.get('session') == sid_from_form:
            label = RESULT_TEXT.get(r.get('result'), (r.get('result'),))[0]
            return {'pid': r.get('id'), 'label': label}
    return None


# ---------------- 页面 ----------------
@app.get('/')
def index(request: Request, done: str = '', err: str = '', msg: str = '', info: str = ''):
    problems = load_problems()
    due_items, plan_items, plan = build_queue(problems)
    sess = sp.find_open_session()
    open_view = None
    if sess:
        sid, meta = sess
        open_view = {'sid': sid, 'pid': meta['id'], 'title': meta.get('title', ''),
                     'elapsed_min': _elapsed_s(meta) // 60, 'limit': meta.get('time_limit_min')}
    attempts = sp.load_attempts_v2()
    hist = sp.history_by_id(attempts)
    week_cnt, streak = sp.week_and_streak(attempts, datetime.date.today())
    summary = LAST_SUMMARY.get(done) if done else None
    this_week = sp.iso_week_str(datetime.date.today())
    targets, recommended = plan_target_options(problems, hist)
    return templates.TemplateResponse(request, 'index.html', {
        'open': open_view, 'due_items': due_items, 'plan_items': plan_items,
        'plan': plan, 'plan_stale': bool(plan) and plan.get('week') != this_week,
        'first': (due_items + plan_items)[0] if (due_items or plan_items) else None,
        'week_count': week_cnt, 'streak': streak, 'n_attempts': len(attempts),
        'first_run': (not attempts and not plan),
        'summary': summary, 'err': err, 'msg': msg, 'info': info,
        'targets': targets, 'recommended': recommended,
    })


@app.get('/help')
def help_page(request: Request):
    return templates.TemplateResponse(request, 'help.html', {
        'results': [(RESULT_TEXT[r][0], RESULT_TEXT[r][1], sp.INTERVALS[r]) for r in sp.RESULTS],
        'limits': sorted(sp.TIME_LIMIT.items()),
        'cooldown': sp.HINT_COOLDOWN_MIN, 'graduate': sp.GRADUATE_STREAK,
    })


@app.post('/start')
def start(pid: str = Form(...), mode: str = Form(''), abandon: str = Form('')):
    pid = pid.strip()
    try:
        sp.start_session_core(load_problems(), pid, mode=mode or None, abandon=bool(abandon))
    except sp.SparError as e:
        return _flash(_web_err(e, pid))
    return _redirect('/session')


@app.post('/abandon')
def abandon(sid: str = Form('')):
    # 必须同卷校验：过期页面上的「不做了」若不校验，作废掉的是学生此刻正在做的那一卷
    cur, _, bad = _require_session(sid)
    if bad or (sid and not cur):
        return _redirect('/', info='那一卷已经结束了，不用再放弃一次。')
    info = sp.abandon_open_session()
    if not info:
        return _redirect('/', info='你现在没有正在做的题，不用放弃。')
    return _redirect('/', msg=f'已经放弃 {info[1]}，这次不算，没有写进训练记录，这道题以后还会正常出现。')


@app.get('/session')
def session(request: Request, err: str = '', info: str = ''):
    sess = sp.find_open_session()
    if not sess:
        return _redirect('/', info='你现在没有正在做的题。回首页挑一道开始吧。')
    sid, meta = sess
    problems = load_problems()
    fmmap = sp._fmmap(problems)
    pid = meta['id']
    if pid not in fmmap:
        return _flash(_web_err(sp.SparError(f'会话 {sid} 指向未知题号 {pid}'), pid))
    try:
        sections = sp.split_sections(fmmap[pid]['body'], pid)
    except ValueError as e:
        return _flash(_web_err(e, pid))
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
    limit = meta.get('time_limit_min')
    elapsed = _elapsed_s(meta)
    deadline = None
    remain_s = 0
    if limit:
        deadline = (datetime.datetime.fromisoformat(meta['started_at'])
                    + datetime.timedelta(minutes=limit)).strftime('%H:%M')
        remain_s = limit * 60 - elapsed
    # 无 JS 时这就是学生看到的数字，语义须与 app.js 接管后一致（有限时→剩余，无限时→已用）
    shown = remain_s if limit else elapsed
    timer_text = f'{"-" if shown < 0 else ""}{abs(shown) // 60}:{abs(shown) % 60:02d}'
    hints_meta = meta.get('hints', [])
    return templates.TemplateResponse(request, 'session.html', {
        'sid': sid, 'p': _fm_view(fm), 'mode': meta.get('mode'),
        'statement_html': md_to_html(sections['题面']),
        'elapsed_s': elapsed, 'elapsed_min': elapsed // 60,
        'limit_min': limit, 'deadline': deadline,
        'remain_s': remain_s, 'timer_text': timer_text,
        'hints': hints_view, 'ladder_total': len(ladder),
        'can_hint': len(used) < len(ladder),
        'cooldown_s': _cooldown_remaining_s(meta), 'cooldown_min': sp.HINT_COOLDOWN_MIN,
        # 第 1 条提示的冷却参照物是「开始做这道题」，之后才是「上一条提示」
        'cooldown_from': '上一条提示' if hints_meta else '开始做这道题',
        'revealed': revealed, 'solution': solution, 'err': err, 'info': info,
    })


@app.post('/session/hint')
def session_hint(sid: str = Form('')):
    cur, meta, bad = _require_session(sid)
    if bad or (sid and not cur):
        return _redirect('/', info='这道题的计时已经结束了，提示不用看了。回首页挑下一道吧。')
    try:
        got = sp.unlock_hint_core(load_problems())
    except sp.SparError as e:
        return _flash(_web_err(e, meta['id'] if meta else None))
    except ValueError as e:
        return _flash(_web_err(e, meta['id'] if meta else None))
    if got['status'] == 'no_ladder':
        return _redirect('/session', info='这道题没有配提示。自己再想想，或者看答案。')
    if got['status'] == 'exhausted':
        return _redirect('/session', info=f"提示已经全部用完了（共 {got['total']} 条）——再自己想想，或者看答案，或者去登记结果。")
    return _redirect(f"/session#hint-{got['level']}")


@app.post('/session/reveal')
def session_reveal(sid: str = Form('')):
    cur, meta, bad = _require_session(sid)
    if bad or (sid and not cur):
        return _redirect('/', info='这道题的计时已经结束了。回首页挑下一道吧。')
    try:
        sp.reveal_core(load_problems())
    except sp.SparError as e:
        return _flash(_web_err(e, meta['id'] if meta else None))
    except ValueError as e:
        return _flash(_web_err(e, meta['id'] if meta else None))
    return _redirect('/session#solution')


@app.get('/session/finish')
def finish_form(request: Request, err: str = ''):
    sess = sp.find_open_session()
    if not sess:
        return _redirect('/', info='你现在没有正在做的题。回首页挑一道开始吧。')
    sid, meta = sess
    now = sp._now()
    time_min = sp.session_time_min(meta, now)
    revealed = bool(meta.get('revealed_at'))
    # 判定建议取 finish_suggestion（契约正本）；解释文字由此处自己组织，
    # 免得把 CLI 口吻的「提示 N 级」端到页面上（页头写的是「用了 N 条提示」）
    suggested = None if revealed else sp.finish_suggestion(meta, time_min)[0]
    limit = meta.get('time_limit_min')
    n_hints = len(meta.get('hints', []))
    over = bool(limit) and time_min > limit
    why = (f'没看答案，用了 {n_hints} 条提示，用时 {time_min} 分钟'
           + ('，超过了限时' if over else '，没有超时')) if suggested else None
    return templates.TemplateResponse(request, 'finish.html', {
        'sid': sid, 'pid': meta['id'], 'title': meta.get('title', ''),
        'time_min': time_min, 'limit': limit, 'over_time': over,
        'stopped_at': sp.iso(now), 'state': _state_token(meta),
        'n_hints': n_hints, 'revealed': revealed,
        'suggested': suggested,
        'suggested_label': RESULT_TEXT[suggested][0] if suggested else None,
        'why': why,
        'results': [(r,) + RESULT_TEXT[r] + (sp.INTERVALS[r],) for r in sp.RESULTS],
        'stucks': [(c, STUCK_TEXT[c]) for c in sp.STUCK_CHOICES],
        'err': err,
    })


@app.post('/session/finish')
def finish_submit(sid: str = Form(''), result: str = Form(''), retell: str = Form(''),
                  stuck: str = Form(''), note: str = Form(''), stopped_at: str = Form(''),
                  state: str = Form('')):
    cur, meta, bad = _require_session(sid)
    if bad or not cur:
        # 学生按了后退又交了一次上一道题的表单——绝不能把成绩记到当前这道题头上
        prev = _already_finished(sid)
        tail = f'你现在正在做的是 {cur["id"]}。' if cur else ''
        if prev:
            return _redirect('/', msg=f"{prev['pid']} 刚才已经交过了，成绩是「{prev['label']}」，不用再交一次。{tail}")
        return _redirect('/', info=f'那一卷已经结束了，不用再交一次。{tail}')
    sid, meta = cur, meta
    # 同一卷内的过期表单：打开收卷页后又回去解锁提示/看了答案，再交回旧表单——
    # 那张表单上的判定建议已经与事实不符，退回重填，不落账
    if state and state != _state_token(meta):
        return _redirect('/session/finish',
                         err='刚才你又看了提示或答案，这一页的选项已经跟着变了，请重新选一次再提交。')
    # 收卷时刻：以学生点开「登记结果」那一刻为准（页面上看到的用时就是记进日志的用时）
    end = sp._now()
    if stopped_at:
        try:
            t = datetime.datetime.fromisoformat(stopped_at)
            start = datetime.datetime.fromisoformat(meta['started_at'])
            if start <= t <= end:
                end = t
        except (ValueError, TypeError):   # 不带时区的时间串比较会抛 TypeError，别让整次收卷 500
            pass
    if not result:
        # 无 JS 时「合上答案重写一遍」的回答由服务端兑现成判定（JS 只是即时预选，正本在这里）
        rt = {'yes': True, 'no': False}.get(retell)
        result = sp.finish_suggestion(meta, sp.session_time_min(meta, end), retell=rt)[0]
    if not result:
        return _redirect('/session/finish', err='请先选一下这道题做得怎么样。')
    try:
        summary = sp.commit_finish(sid, meta, result, stuck=stuck or None, note=note.strip(), end=end)
    except sp.SparError as e:
        return _flash(_web_err(e, meta['id']), '/session/finish')
    except OSError:
        return _redirect('/session/finish',
                         err='成绩没能写进日志（可能是 data 目录不可写）。先别关页面，把这句话告诉教练。')
    label = RESULT_TEXT.get(result, (result,))[0]
    LAST_SUMMARY[sid] = {'pid': meta['id'], 'title': meta.get('title', ''),
                         'result': result, 'label': label, **summary}
    return _redirect('/', done=sid)


@app.post('/plan')
def make_plan(target: str = Form(...), n: str = Form('10'), seed: str = Form('')):
    profile = bank.PLAN_PROFILES.get(target)
    if not profile:
        return _redirect('/', err=f'没有「{target}」这个目标赛事，请重新选一个。')
    def _int(s, default=0):
        # isdigit() 对全角数字为真但 int() 会抛，且超长数字串也要挡住——一律 try
        try:
            return int(str(s).strip()[:9])
        except (ValueError, TypeError):
            return default
    want = max(3, min(20, _int(n, PLAN_DEFAULT_N) or PLAN_DEFAULT_N))
    seed = _int(seed) or datetime.date.today().toordinal()  # 不填则按日期换一套
    rng = random.Random(seed)
    hist = sp.history_by_id(sp.load_attempts_v2())
    graduated = {pid for pid, recs in hist.items() if sp.is_graduated(recs)}
    excluded = set(hist) | graduated   # 同 coach：做过的走复习队列；毕业题永久不进周计划
    pool = bank.build_coach_pool(load_problems(), profile, excluded, rng)
    week1 = bank.pick_week(pool, profile, want, rng)
    if not week1:
        return _redirect('/', err=f'按「{target}」的难度配比挑不出题了（剩下的要么做过、要么星级不匹配）。'
                                  '换个目标赛事，或者把题量调小一点再试。')
    week = sp.iso_week_str(datetime.date.today())
    sp.save_plan(week, target, seed, [fm['id'] for fm in week1])
    note = f'本周题单已排好：{target}，共 {len(week1)} 题。'
    if len(week1) < want:
        stars = sorted({fm['difficulty'] for fm in week1})
        note += (f'你要的是 {want} 题，但题库里按「{target}」的难度配比只挑得出这 {len(week1)} 题'
                 f'（都是 ★{"、★".join(str(s) for s in stars)}）。'
                 '想要满额的一周，换一个难度低一些的目标赛事。')
    return _redirect('/', msg=note)


# ---------------- 兜底：任何未预期的异常都不许把 traceback 摔给学生 ----------------
@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, 'error.html', {
            'headline': '这个网址不存在', 'detail': '', 'status': 404}, status_code=404)
    return templates.TemplateResponse(request, 'error.html', {
        'headline': '这个操作没能完成', 'detail': str(exc.detail), 'status': exc.status_code},
        status_code=exc.status_code)


@app.exception_handler(Exception)
def unhandled_error(request: Request, exc: Exception):
    traceback.print_exc()  # 完整堆栈只进终端
    return templates.TemplateResponse(request, 'error.html', {
        'headline': '训练台遇到了问题，不是你操作错了',
        'detail': f'{type(exc).__name__}: {exc}', 'status': 500}, status_code=500)
