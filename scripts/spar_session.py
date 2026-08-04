#!/usr/bin/env python3
"""spar 会话流程与训练日志 v2 —— scripts/bank.py 的辅助模块。

三方数据契约（已定稿，勿自行更改）：
- data/attempts.jsonl v2 记录 + 旧格式归一化：load_attempts_v2 / normalize_attempt
  旧格式（result: ok|hard|fail，hints 为整数）读取时归一化：
  ok且hints≤1→independent_ok；ok且hints≥2→hinted_ok；hard→hinted_ok；fail→fail
- 复习间隔 INTERVALS；同一题连续 2 次 independent_ok → 毕业（is_graduated）
- 会话目录 data/sessions/<session_id>/：statement.md（绝不含答案/要点/提示）、
  meta.json、hints/hint-N.md、solution.md
- data/plan.json：{"week","target","seed","items"}
- data/similar/edges.jsonl：确认边台账（不存 cluster_id，关系类型由人确认）
"""
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTEMPTS_PATH = os.path.join(ROOT, 'data', 'attempts.jsonl')
SESSIONS_ROOT = os.path.join(ROOT, 'data', 'sessions')
PLAN_PATH = os.path.join(ROOT, 'data', 'plan.json')
EDGES_PATH = os.path.join(ROOT, 'data', 'similar', 'edges.jsonl')
SIMILAR_INDEX = os.path.join(ROOT, 'scripts', 'similar_index.py')
CAND_PATH = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')

INTERVALS = {'independent_ok': 21, 'hinted_ok': 7, 'solution_reconstructed': 3, 'fail': 2}
TIME_LIMIT = {1: 15, 2: 25, 3: 40, 4: 80, 5: 120}  # 独立攻坚限时（分钟）
RESULTS = ('independent_ok', 'hinted_ok', 'solution_reconstructed', 'fail')
STUCK_CHOICES = ('建模', '识别结构', '关键引理', '技术执行', '证明表达')
RELATIONS = ('duplicate', 'near_isomorphic', 'same_method', 'related')
HINT_COOLDOWN_MIN = 15
GRADUATE_STREAK = 2
VALID_VERIFICATION = ('sourced', 'independent-derivation', 'mathnet-reviewed')  # 枚举唯一正本，bank.py lint 亦用此

# 题文件小节白名单：出现名单外小节即拒绝解析（防未来新小节把答案泄进题卡）
KNOWN_SECTIONS = ('题面', '答案', '解法要点', '核验', '提示阶梯')
CARD_SECTIONS = ('题面',)  # statement.md 只允许题面（legacy「原文（English）」节已随旧库清退收回）

CLI = 'uv run python scripts/bank.py'


# ---------------- 时间 ----------------
def _now():
    return datetime.datetime.now().astimezone()


def iso(dt):
    return dt.isoformat(timespec='seconds')


def iso_week_str(d):
    y, w, _ = d.isocalendar()
    return f'{y}-W{w:02d}'


def _week_monday(week_str):
    m = re.match(r'^(\d{4})-W(\d{1,2})$', str(week_str or ''))
    if not m:
        return None
    try:
        return datetime.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


# ---------------- attempts.jsonl v2 ----------------
def normalize_attempt(rec):
    """旧格式记录归一化为 v2；v2 记录原样补默认字段。"""
    r = dict(rec)
    hints = r.get('hints', [])
    if isinstance(hints, int):
        hints = [{'level': i} for i in range(1, hints + 1)]
    elif not isinstance(hints, list):
        hints = []
    r['hints'] = hints
    res = r.get('result')
    if res == 'ok':
        res = 'independent_ok' if len(hints) <= 1 else 'hinted_ok'
    elif res == 'hard':
        res = 'hinted_ok'
    r['result'] = res  # fail 新旧同义；v2 四值原样通过
    r.setdefault('revealed_at', None)
    r.setdefault('mode', 'fresh')
    r.setdefault('stuck', None)
    r.setdefault('student', 'self')
    return r


def load_attempts_v2(path=ATTEMPTS_PATH):
    """读取全部训练记录（含旧格式归一化），供 review / coach / spar 共用。"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                print(f'警告：attempts.jsonl 第 {i} 行不是合法 JSON，已跳过', file=sys.stderr)
                continue
            out.append(normalize_attempt(rec))
    return out


def append_attempt(rec, path=ATTEMPTS_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def history_by_id(attempts):
    """按题分组、按（date，文件顺序）排序的全历史。"""
    grouped = {}
    for i, r in enumerate(attempts):
        if r.get('id'):
            grouped.setdefault(r['id'], []).append((str(r.get('date', '')), i, r))
    return {pid: [r for _, _, r in sorted(rows)] for pid, rows in grouped.items()}


def is_graduated(recs):
    """连续 GRADUATE_STREAK 次 independent_ok（全历史任意位置）→ 永久毕业。"""
    streak = 0
    for r in recs:
        if r.get('result') == 'independent_ok':
            streak += 1
            if streak >= GRADUATE_STREAK:
                return True
        else:
            streak = 0
    return False


def due_date_of(recs):
    """未毕业题的下次复习日；无法判定返回 None。"""
    last = recs[-1]
    gap = INTERVALS.get(last.get('result'))
    if gap is None:
        return None
    try:
        return datetime.date.fromisoformat(str(last.get('date'))) + datetime.timedelta(days=gap)
    except ValueError:
        return None


# ---------------- data/plan.json ----------------
def load_plan(path=PLAN_PATH):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding='utf-8'))
    except ValueError:
        print('警告：data/plan.json 无法解析，忽略', file=sys.stderr)
        return None


def save_plan(week, target, seed, items, path=PLAN_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {'week': week, 'target': target, 'seed': seed, 'items': items}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')


# ---------------- 题文件解析（白名单制）----------------
_FM_RE = re.compile(r'\A---\n.*?\n---\n', re.S)
_H2_RE = re.compile(r'^##\s+(.+?)\s*$', re.M)


def split_sections(body, ident='?'):
    """H2 小节切分；白名单外小节 / 缺题面 → ValueError（拒绝出卡）。"""
    text = _FM_RE.sub('', body, count=1)
    heads = list(_H2_RE.finditer(text))
    sections = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        sections[m.group(1).strip()] = text[m.end():end].strip('\n')
    unknown = [n for n in sections if n not in KNOWN_SECTIONS]
    if unknown:
        raise ValueError(f'{ident}: 存在白名单外小节 {unknown}——为防答案泄进题卡，拒绝解析'
                         f'（白名单：{"、".join(KNOWN_SECTIONS)}；请先更新 spar_session.KNOWN_SECTIONS）')
    if not sections.get('题面', '').strip():
        raise ValueError(f'{ident}: 缺少「## 题面」内容，无法出卡')
    return sections


def parse_hint_ladder(sections):
    src = sections.get('提示阶梯') or ''
    chunks = re.split(r'^\s*\d+[.、]\s+', src, flags=re.M)
    return [c.strip() for c in chunks[1:] if c.strip()]


# ---------------- 会话目录 ----------------
def _session_dir(sid):
    return os.path.join(SESSIONS_ROOT, sid)


def load_meta(sid):
    return json.load(open(os.path.join(_session_dir(sid), 'meta.json'), encoding='utf-8'))


def save_meta(sid, meta):
    with open(os.path.join(_session_dir(sid), 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
        f.write('\n')


def find_open_session():
    if not os.path.isdir(SESSIONS_ROOT):
        return None
    opens = []
    for d in sorted(os.listdir(SESSIONS_ROOT)):
        mp = os.path.join(SESSIONS_ROOT, d, 'meta.json')
        if not os.path.isfile(mp):
            continue
        try:
            meta = json.load(open(mp, encoding='utf-8'))
        except ValueError:
            continue
        if meta.get('status') == 'open':
            opens.append((str(meta.get('started_at', '')), d, meta))
    if not opens:
        return None
    opens.sort()
    if len(opens) > 1:
        print(f'警告：发现 {len(opens)} 个未关会话，操作最新的一个', file=sys.stderr)
    _, sid, meta = opens[-1]
    return sid, meta


def _require_open_session():
    sess = find_open_session()
    if not sess:
        sys.exit(f'没有进行中的会话——先开卡：{CLI} spar start <ID>（或 spar next）')
    return sess


def new_session_id(pid, start):
    prefix = f"{start.strftime('%Y%m%d')}-{pid}-"
    n = 0
    if os.path.isdir(SESSIONS_ROOT):
        for d in os.listdir(SESSIONS_ROOT):
            if d.startswith(prefix) and d[len(prefix):].isdigit():
                n = max(n, int(d[len(prefix):]))
    return f'{prefix}{n + 1}'


def _rel(path):
    return os.path.relpath(path, ROOT)


# ---------------- spar 四动作 ----------------
def cmd_spar(problems, args):
    action = args.action
    if action == 'hint':
        return spar_hint(problems, args)
    if action == 'reveal':
        return spar_reveal(problems, args)
    if action == 'finish':
        return spar_finish(problems, args)
    if action == 'next':
        return spar_start(problems, args, pid=None)
    if action == 'start':
        if not args.target:
            sys.exit('用法：spar start <ID>（或 spar next 自动选题）')
        return spar_start(problems, args, pid=args.target)
    return spar_start(problems, args, pid=action)  # 糖：spar A-037 = spar start A-037


def _fmmap(problems):
    return {p['fm']['id']: p for p in problems if p['fm'] and p['fm'].get('id')}


def _verification_ok(fm):
    return fm.get('verification') in VALID_VERIFICATION


def pick_next(fmmap, hist):
    """选题顺序：复习到期 > plan.json 未完成项 > 提示先跑 coach --save。"""
    today = datetime.date.today()
    due = []
    for pid, recs in hist.items():
        if pid not in fmmap or is_graduated(recs):
            continue
        dd = due_date_of(recs)
        if dd is not None and dd <= today:
            due.append((dd, pid))
    for _, pid in sorted(due):
        if _verification_ok(fmmap[pid]['fm']):
            return pid, 'review'
        print(f'跳过复习到期但未过核验铁律的 {pid}', file=sys.stderr)
    plan = load_plan()
    if not plan:
        sys.exit(f'复习队列为空，且无 data/plan.json——先生成周计划：{CLI} coach --target <赛事> --save')
    monday = _week_monday(plan.get('week'))
    for pid in plan.get('items', []):
        if pid not in fmmap:
            continue
        recs = hist.get(pid, [])
        if is_graduated(recs):
            continue
        done = (any(str(r.get('date', '')) >= monday.isoformat() for r in recs)
                if monday else bool(recs))
        if done:
            continue
        if not _verification_ok(fmmap[pid]['fm']):
            print(f'跳过未过核验铁律的计划题 {pid}', file=sys.stderr)
            continue
        return pid, ('review' if recs else 'fresh')
    sys.exit(f'复习队列为空，周计划（{plan.get("week")}）已全部完成 🎉——'
             f'换一批：{CLI} coach --target {plan.get("target", "<赛事>")} --save')


def build_card(fm, sections, sid, mode, limit):
    d = fm.get('difficulty')
    src = ' '.join(str(x) for x in (fm.get('contest'), fm.get('year')) if x)
    lines = [f"# {fm['id']}｜{fm.get('title', '')}", '',
             f"- 来源：{src or '?'}（{fm.get('source_ref', '')}）",
             f"- 难度：{'★' * (d or 0)}　限时 ≤ {limit} 分钟",
             f'- 会话：{sid}　模式：{mode}',
             '- 纪律：限时独立攻坚；卡住先再战 15 分钟再逐级解锁提示；无论成败 spar finish 落账',
             '']
    for name in CARD_SECTIONS:
        if sections.get(name, '').strip():
            lines += [f'## {name}', '', sections[name].strip(), '']
    return '\n'.join(lines).rstrip() + '\n'


def spar_start(problems, args, pid):
    fmmap = _fmmap(problems)
    hist = history_by_id(load_attempts_v2())
    mode = None
    if pid is None:
        pid, mode = pick_next(fmmap, hist)
    if pid not in fmmap:
        sys.exit(f'未知题号 {pid}')
    fm = fmmap[pid]['fm']
    # 铁律：verification 必须在 VALID_VERIFICATION 白名单（含 mathnet-reviewed），否则拒绝出卡
    if not _verification_ok(fm):
        sys.exit(f"铁律：{pid} 的 verification={fm.get('verification')!r} "
                 f'不在 {VALID_VERIFICATION}，拒绝出卡——先走评审入库流程（docs/入库SOP-MathNet.md）')
    sess = find_open_session()
    if sess:
        sid0, meta0 = sess
        if getattr(args, 'abandon', False):
            meta0['status'] = 'abandoned'
            meta0['ended_at'] = iso(_now())
            save_meta(sid0, meta0)
            print(f"已放弃会话 {sid0}（{meta0.get('id')}，不写训练记录）")
        else:
            sys.exit(f"已有进行中的会话 {sid0}（{meta0.get('id')}）——"
                     f'先 spar finish 落账，或 spar start {pid} --abandon 放弃重开')
    try:
        sections = split_sections(fmmap[pid]['body'], pid)
    except ValueError as e:
        sys.exit(str(e))
    mode = getattr(args, 'mode', None) or mode or ('review' if hist.get(pid) else 'fresh')
    if is_graduated(hist.get(pid, [])):
        print(f'注意：{pid} 已毕业（连续 {GRADUATE_STREAK} 次 independent_ok），本次为自选加练')
    start = _now()
    sid = new_session_id(pid, start)
    sdir = _session_dir(sid)
    os.makedirs(sdir, exist_ok=True)
    d = fm.get('difficulty')
    limit = TIME_LIMIT.get(d)
    card = build_card(fm, sections, sid, mode, limit)
    card_path = os.path.join(sdir, 'statement.md')
    with open(card_path, 'w', encoding='utf-8') as f:
        f.write(card)
    meta = {'session': sid, 'id': pid, 'mode': mode, 'status': 'open',
            'started_at': iso(start), 'hints': [], 'revealed_at': None,
            'difficulty': d, 'time_limit_min': limit, 'title': fm.get('title', '')}
    save_meta(sid, meta)
    n_hints = len(parse_hint_ladder(sections))
    ladder_note = f'提示阶梯 {n_hints} 级' if n_hints else '本题暂无提示阶梯'
    print(f'会话已开：{sid}（{mode}）')
    print(f'题卡：{_rel(card_path)}')
    print(f'限时：★{d} ≤ {limit} 分钟（超时不拦，finish 时计入判定）；{ladder_note}')
    print(f'卡住：{CLI} spar hint　看解：spar reveal　落账：spar finish')
    if getattr(args, 'print_card', False):
        print('\n' + card)


def spar_hint(problems, args):
    sid, meta = _require_open_session()
    fmmap = _fmmap(problems)
    pid = meta['id']
    if pid not in fmmap:
        sys.exit(f'会话 {sid} 指向未知题号 {pid}')
    try:
        sections = split_sections(fmmap[pid]['body'], pid)
    except ValueError as e:
        sys.exit(str(e))
    ladder = parse_hint_ladder(sections)
    if not ladder:
        print('本题暂无阶梯')
        return
    used = len(meta.get('hints', []))
    if used >= len(ladder):
        print(f'提示已全部解锁（共 {len(ladder)} 级）——再战一会儿，或 spar reveal / spar finish')
        return
    now = _now()
    if used:
        ref = datetime.datetime.fromisoformat(meta['hints'][-1]['at'])
        ref_name = '上次解锁'
    else:
        ref = datetime.datetime.fromisoformat(meta['started_at'])
        ref_name = '开卡'
    mins = (now - ref).total_seconds() / 60
    early = mins < HINT_COOLDOWN_MIN
    if early:
        print(f'⚠️ 距{ref_name}仅 {mins:.0f} 分钟（纪律：每级之间再战 {HINT_COOLDOWN_MIN} 分钟）'
              '——照常解锁，已记 early')
    level = used + 1
    text = ladder[level - 1]
    hints_dir = os.path.join(_session_dir(sid), 'hints')
    os.makedirs(hints_dir, exist_ok=True)
    hint_path = os.path.join(hints_dir, f'hint-{level}.md')
    with open(hint_path, 'w', encoding='utf-8') as f:
        f.write(f'# {pid} 提示 {level}/{len(ladder)}\n\n{text}\n')
    meta.setdefault('hints', []).append({'level': level, 'at': iso(now), 'early': early})
    save_meta(sid, meta)
    print(f'—— 提示 {level}/{len(ladder)}（{_rel(hint_path)}）——\n{text}')


def spar_reveal(problems, args):
    sid, meta = _require_open_session()
    fmmap = _fmmap(problems)
    pid = meta['id']
    if pid not in fmmap:
        sys.exit(f'会话 {sid} 指向未知题号 {pid}')
    try:
        sections = split_sections(fmmap[pid]['body'], pid)
    except ValueError as e:
        sys.exit(str(e))
    sol = (f'# {pid} 答案与解法要点\n\n## 答案\n\n{sections.get("答案", "").strip()}\n\n'
           f'## 解法要点\n\n{sections.get("解法要点", "").strip()}\n')
    sol_path = os.path.join(_session_dir(sid), 'solution.md')
    with open(sol_path, 'w', encoding='utf-8') as f:
        f.write(sol)
    if not meta.get('revealed_at'):
        meta['revealed_at'] = iso(_now())
        save_meta(sid, meta)
    print(sol)
    print(f'已记 revealed_at（{_rel(sol_path)}）。合卷凭记忆复述一遍，再 {CLI} spar finish——会询问复述结果')


def _ask(prompt, default=''):
    try:
        s = input(prompt)
    except EOFError:
        return default
    return s.strip() or default


def _suggest_result(meta, time_min):
    """契约：revealed→问复述；未看答案且提示≤1级且未超限→independent_ok；其余→hinted_ok。"""
    nh = len(meta.get('hints', []))
    limit = meta.get('time_limit_min')
    over = bool(limit) and time_min > limit
    if nh <= 1 and not over:
        return 'independent_ok', f'未看答案，提示 {nh} 级，用时 {time_min} 分钟未超限'
    why = f'未看答案，提示 {nh} 级' + (f'，超限（{time_min} > {limit} 分钟）' if over else '')
    return 'hinted_ok', why


def spar_finish(problems, args):
    sid, meta = _require_open_session()
    pid = meta['id']
    end = _now()
    start = datetime.datetime.fromisoformat(meta['started_at'])
    time_min = max(1, round((end - start).total_seconds() / 60))
    if meta.get('revealed_at'):
        retell = getattr(args, 'retell', None)
        if retell:
            ok = retell == 'yes'
        else:
            ok = _ask('已看答案——合卷复述通过了吗？[y/N]：').lower() in ('y', 'yes', '是', '通过')
        suggested = 'solution_reconstructed' if ok else 'fail'
        why = '已看答案，复述' + ('通过' if ok else '未通过')
    else:
        suggested, why = _suggest_result(meta, time_min)
    result = getattr(args, 'result', None)
    if not result:
        num = {str(i + 1): r for i, r in enumerate(RESULTS)}
        s = _ask(f'判定建议：{suggested}（{why}）\n'
                 '回车接受，或改判 [1]independent_ok [2]hinted_ok '
                 '[3]solution_reconstructed [4]fail：')
        result = num.get(s, s if s in RESULTS else suggested)
    stuck = getattr(args, 'stuck', None)
    if result in ('fail', 'solution_reconstructed') and not stuck:
        num = {str(i + 1): c for i, c in enumerate(STUCK_CHOICES)}
        s = _ask('卡在哪一步？[1]建模 [2]识别结构 [3]关键引理 [4]技术执行 [5]证明表达（回车跳过）：')
        stuck = num.get(s, s if s in STUCK_CHOICES else None)
    note = getattr(args, 'note', None)
    if note is None:
        note = _ask('备注（回车跳过）：')
    rec = {'id': pid, 'result': result, 'date': end.date().isoformat(),
           'session': sid, 'mode': meta.get('mode', 'fresh'),
           'started_at': meta['started_at'], 'ended_at': iso(end), 'time_min': time_min,
           'hints': meta.get('hints', []), 'revealed_at': meta.get('revealed_at'),
           'stuck': stuck, 'note': note or '', 'student': 'self'}
    append_attempt(rec)
    meta.update(status='finished', ended_at=iso(end), result=result)
    save_meta(sid, meta)
    print(f'已记录 {pid}：{result}（用时 {time_min} 分钟，提示 {len(rec["hints"])} 级）→ data/attempts.jsonl')
    attempts = load_attempts_v2()
    recs = history_by_id(attempts).get(pid, [])
    if is_graduated(recs):
        if is_graduated(recs[:-1]):
            print(f'{pid} 已毕业，本次为加练，不再排复习。')
        else:
            print(f'🎓 连续 {GRADUATE_STREAK} 次 independent_ok——{pid} 毕业，永久退出复习队列！')
    else:
        nxt = end.date() + datetime.timedelta(days=INTERVALS[result])
        print(f'下次复习：{nxt.isoformat()}（{INTERVALS[result]} 天后）')
    y, w, _ = end.date().isocalendar()
    week_cnt = 0
    days = set()
    for r in attempts:
        try:
            d = datetime.date.fromisoformat(str(r.get('date')))
        except ValueError:
            continue
        days.add(d)
        if d.isocalendar()[:2] == (y, w):
            week_cnt += 1
    streak, d = 0, end.date()
    while d in days:
        streak += 1
        d -= datetime.timedelta(days=1)
    print(f'本周已完成 {week_cnt} 次攻坚；连击 {streak} 天' + ('　🔥' if streak >= 3 else ''))


# ---------------- similar 薄壳 ----------------
def _load_edges():
    if not os.path.exists(EDGES_PATH):
        return []
    out = []
    with open(EDGES_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _mn_in_pool(mathnet_id, path=CAND_PATH):
    """MN-<mathnet_id> 是否在候选池中；候选池文件缺失返回 None（无法校验）。"""
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        for line in f:
            if mathnet_id not in line:  # 快速预筛，命中再解析确认
                continue
            try:
                if json.loads(line).get('mathnet_id') == mathnet_id:
                    return True
            except ValueError:
                continue
    return False


def _check_confirm_dst(fmmap, src, dst):
    """--confirm 写台账前的校验：拒自环、dst 必须是库内 id 或候选池 MN- id。"""
    if dst == src:
        sys.exit(f'--confirm 拒绝自环：src 与 dst 同为 {src}')
    if dst in fmmap:
        return
    if dst.startswith('MN-'):
        found = _mn_in_pool(dst[3:])
        if found is None:
            sys.exit(f'无法校验 {dst}：候选池 candidates/mathnet.jsonl 不存在（gitignore，可重建）'
                     '——先重建候选池再登记，避免错误 id 永久写入台账')
        if not found:
            sys.exit(f'候选池中不存在 {dst}（candidates/mathnet.jsonl 无 mathnet_id='
                     f'{dst[3:]}），拒绝写入台账——检查是否敲错')
        return
    sys.exit(f'未知 dst {dst}：既不是库内题号，也不是 MN-<mathnet_id> 候选 id，拒绝写入台账')


def cmd_similar(problems, args):
    fmmap = _fmmap(problems)
    if args.id not in fmmap:
        sys.exit(f'未知题号 {args.id}')
    if args.confirm:
        dst = args.confirm.strip()
        if not args.relation:
            sys.exit(f'--confirm 需要同时给 --relation（{"|".join(RELATIONS)}）')
        if not 0.0 <= args.confidence <= 1.0:
            sys.exit(f'--confidence 必须在 0.0–1.0（契约区间），当前为 {args.confidence}')
        _check_confirm_dst(fmmap, args.id, dst)
        os.makedirs(os.path.dirname(EDGES_PATH), exist_ok=True)
        edge = {'src': args.id, 'dst': dst, 'relation': args.relation,
                'confidence': args.confidence, 'evidence': args.evidence,
                'confirmed': True, 'date': datetime.date.today().isoformat()}
        with open(EDGES_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(edge, ensure_ascii=False) + '\n')
        print(f'已登记确认边：{args.id} —{args.relation}→ {dst}（{_rel(EDGES_PATH)}）')
        return
    confirmed = {}
    for e in _load_edges():
        if not e.get('confirmed'):
            continue
        if e.get('src') == args.id:
            confirmed[e['dst']] = e.get('relation', '?')
        elif e.get('dst') == args.id:
            confirmed[e['src']] = f"{e.get('relation', '?')}（反向）"
    if not os.path.exists(SIMILAR_INDEX):
        if confirmed:
            print('已确认边（data/similar/edges.jsonl）：')
            for d, rel in sorted(confirmed.items()):
                print(f'  {d}  ✅ {rel}')
        print('scripts/similar_index.py 尚未就位（由相似检索模块提供）——索引就绪后重试；'
              f'确认边可先手动登记：{CLI} similar {args.id} --confirm <DST> --relation <r>')
        sys.exit(2)
    proc = subprocess.run([sys.executable, SIMILAR_INDEX, 'query', args.id,
                           '--top', str(args.top)],
                          capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        print('similar_index 查询失败（索引可能未建）：')
        print((proc.stderr or proc.stdout).strip())
        print(f'建好索引后重试；确认边可先手动登记：{CLI} similar {args.id} --confirm <DST> --relation <r>')
        sys.exit(2)
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    print(f'{args.id} 的相似候选（similar_index，top {args.top}）：')
    seen = set()
    for r in rows:
        dst = r.get('dst') or r.get('id') or r.get('target') or '?'
        score = r.get('score', r.get('similarity', r.get('cos', '')))
        try:
            score_s = f'{float(score):.3f}'
        except (TypeError, ValueError):
            score_s = str(score)
        desc = str(r.get('title') or r.get('head') or r.get('text') or '')[:60]
        mark = f'  ✅ {confirmed[dst]}' if dst in confirmed else ''
        print(f'  {dst:<12} {score_s:<7} {desc}{mark}')
        seen.add(dst)
    if not rows:
        print('  （similar_index 无候选输出）')
    rest = {d: rel for d, rel in confirmed.items() if d not in seen}
    if rest:
        print('已确认边（不在本次候选中）：')
        for d, rel in sorted(rest.items()):
            print(f'  {d}  ✅ {rel}')
    print(f'确认关系：{CLI} similar {args.id} --confirm <DST> --relation <{"|".join(RELATIONS)}>')
