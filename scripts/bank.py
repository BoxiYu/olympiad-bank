#!/usr/bin/env python3
"""题库工具：lint / query / stats

用法：
  python3 scripts/bank.py lint
  python3 scripts/bank.py query [--difficulty 3] [--topic 韦达] [--contest IMO] [--category algebra] [--unverified]
  python3 scripts/bank.py stats
"""
import argparse, os, re, sys, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORIES = ['algebra', 'number-theory', 'combinatorics', 'geometry']
PREFIX = {'algebra': 'A', 'number-theory': 'N', 'combinatorics': 'C', 'geometry': 'G'}
REQUIRED = ['id', 'title', 'category', 'source_ref', 'difficulty', 'topics', 'verification', 'source_url']
SECTIONS = ['## 题面', '## 答案', '## 解法要点']


def load_all():
    problems = []
    for cat in CATEGORIES:
        d = os.path.join(ROOT, 'problems', cat)
        for name in sorted(os.listdir(d)):
            if not name.endswith('.md'):
                continue
            path = os.path.join(d, name)
            text = open(path, encoding='utf-8').read()
            m = re.match(r'---\n(.*?)\n---\n', text, re.S)
            fm = yaml.safe_load(m.group(1)) if m else None
            problems.append({'path': path, 'file': name, 'cat': cat, 'fm': fm, 'body': text})
    return problems


def lint(problems):
    errors = []
    seen = {}
    for p in problems:
        rel = os.path.relpath(p['path'], ROOT)
        fm = p['fm']
        if fm is None:
            errors.append(f'{rel}: frontmatter 缺失或无法解析')
            continue
        for k in REQUIRED:
            if k not in fm or fm[k] in (None, '', []):
                errors.append(f'{rel}: 缺少必填字段 {k}')
        pid = fm.get('id', '')
        if pid + '.md' != p['file']:
            errors.append(f'{rel}: id({pid}) 与文件名不一致')
        if not pid.startswith(PREFIX[p['cat']] + '-'):
            errors.append(f'{rel}: id 前缀与目录 {p["cat"]} 不匹配')
        if not isinstance(fm.get('difficulty'), int) or not 1 <= fm['difficulty'] <= 5:
            errors.append(f'{rel}: difficulty 必须是 1-5 的整数')
        url = str(fm.get('source_url', ''))
        if not url.startswith('http'):
            errors.append(f'{rel}: source_url 不是链接')
        if fm.get('verification') not in ('sourced', 'independent-derivation'):
            errors.append(f'{rel}: verification 取值非法')
        for s in SECTIONS:
            if s not in p['body']:
                errors.append(f'{rel}: 缺少小节 {s}')
        if fm.get('original_lang') == 'en' and '## 原文' not in p['body']:
            errors.append(f'{rel}: original_lang=en 但缺少「## 原文（English）」小节')
        seen.setdefault(p['cat'], []).append(pid)
    for cat, ids in seen.items():
        nums = sorted(int(i.split('-')[1]) for i in ids)
        expect = list(range(1, len(nums) + 1))
        if nums != expect:
            gaps = sorted(set(expect) - set(nums))
            errors.append(f'{cat}: 题号不连续，缺 {gaps}')
    if errors:
        print('\n'.join(errors))
        print(f'\nLINT FAILED: {len(errors)} 个问题')
        return 1
    warns = registry_report(problems)
    if warns:
        print(f'警告：{len(warns)} 处知识点未注册（不阻塞，请在 taxonomy/registry.yml 登记）：')
        for w in warns[:12]:
            print('  ' + w)
    print(f'LINT OK: {len(problems)} 题全部通过' + ('' if not warns else f'（{len(warns)} 条词表警告）'))
    return 0


def load_aliases():
    path = os.path.join(ROOT, 'taxonomy', 'aliases.yml')
    if not os.path.exists(path):
        return {}
    return yaml.safe_load(open(path, encoding='utf-8')) or {}


def topic_match(q, topics, title, aliases):
    ql = q.lower()
    if any(q in t for t in topics) or q in title:
        return True
    # 英文查询 → 命中英文别名的中文标准名出现在 topics 中
    for zh, ens in aliases.items():
        if any(ql in en.lower() for en in ens) and any(zh in t for t in topics):
            return True
    return False


def query(problems, args):
    aliases = load_aliases()
    rows = []
    for p in problems:
        fm = p['fm'] or {}
        if args.difficulty and fm.get('difficulty') != args.difficulty:
            continue
        if args.category and fm.get('category') != args.category:
            continue
        if args.contest and args.contest.lower() not in str(fm.get('contest', '')).lower():
            continue
        if args.topic and not topic_match(args.topic, fm.get('topics', []), fm.get('title', ''), aliases):
            continue
        if args.unverified and fm.get('verification') == 'sourced':
            continue
        rows.append(fm)
    for fm in rows:
        star = '★' * fm['difficulty']
        topics = ' / '.join(fm['topics'])
        print(f"{fm['id']}  {star:<5}  {fm.get('contest') or '?':<8} {fm.get('year') or '----'}  {fm['title']}  [{topics}]")
    print(f'\n共 {len(rows)} 题')


def stats(problems):
    diff = {}
    sys_dist = {}
    for p in problems:
        fm = p['fm'] or {}
        d, c = fm.get('difficulty'), p['cat']
        diff.setdefault(d, {}).setdefault(c, 0)
        diff[d][c] += 1
        s = fm.get('system') or '未归类'
        sys_dist.setdefault(s, {}).setdefault(c, 0)
        sys_dist[s][c] += 1
    header = f"{'':<12}" + ''.join(f'{c[:8]:>10}' for c in CATEGORIES) + f"{'合计':>8}"
    print('难度分布'); print(header)
    for d in sorted(diff):
        row = diff[d]
        total = sum(row.values())
        print(f"{'★' * d:<12}" + ''.join(f'{row.get(c, 0):>10}' for c in CATEGORIES) + f'{total:>8}')
    print(f"{'合计':<12}" + ''.join(f"{sum(diff[d].get(c, 0) for d in diff):>10}" for c in CATEGORIES) + f'{len(problems):>8}')
    print('\n体系分布'); print(header)
    for s, row in sorted(sys_dist.items()):
        print(f'{s:<12}' + ''.join(f'{row.get(c, 0):>10}' for c in CATEGORIES) + f'{sum(row.values()):>8}')


# 目标赛事 → 星级配比（权重，按比例取题）。锚点见 docs/赛事地图与官方题源.md
PLAN_PROFILES = {
    'AMC8':   {1: 6, 2: 4},
    'AMC10':  {2: 5, 3: 4, 4: 1},
    'AMC12':  {2: 4, 3: 5, 4: 1},
    'AIME':   {2: 1, 3: 6, 4: 3},
    '高联一试': {2: 5, 3: 5},
    '高联加试': {3: 4, 4: 6},
    'CMO':    {4: 6, 5: 4},
    'USAMO':  {4: 7, 5: 3},
    'IMO':    {4: 5, 5: 5},
}


def plan(problems, args):
    import random
    profile = PLAN_PROFILES.get(args.target)
    if not profile:
        print(f'未知目标赛事。可选：{"、".join(PLAN_PROFILES)}')
        sys.exit(2)
    rng = random.Random(args.seed)
    n = args.n
    total_w = sum(profile.values())
    # 每星级配额（至少凑满 n 题）
    quota = {d: max(1, round(n * w / total_w)) for d, w in profile.items()}
    pool = {}
    for p in problems:
        fm = p['fm'] or {}
        d = fm.get('difficulty')
        if d in quota:
            pool.setdefault(d, []).append(fm)
    picked = []
    for d, k in sorted(quota.items()):
        cands = pool.get(d, [])
        rng.shuffle(cands)
        # 尽量四板块轮转，避免全落在一个板块
        by_cat = {}
        for fm in cands:
            by_cat.setdefault(fm['category'], []).append(fm)
        order = sorted(by_cat)
        rng.shuffle(order)
        sel, i = [], 0
        while len(sel) < min(k, len(cands)):
            cat = order[i % len(order)]
            if by_cat[cat]:
                sel.append(by_cat[cat].pop(0))
            i += 1
            if i > 10 * len(cands) + 10:
                break
        picked += sel
    print(f'目标：{args.target}　共 {len(picked)} 题（seed={args.seed}，换一套用 --seed）\n')
    for fm in sorted(picked, key=lambda f: (f['difficulty'], f['id'])):
        star = '★' * fm['difficulty']
        print(f"{fm['id']}  {star:<5}  {fm.get('contest') or '?':<8} {fm['title']}  [{' / '.join(fm['topics'])}]")
    dist = {}
    for fm in picked:
        dist[fm['difficulty']] = dist.get(fm['difficulty'], 0) + 1
    print('\n难度构成：' + '，'.join(f'★{d}×{c}' for d, c in sorted(dist.items())))


# ---------------- 教练闭环：coach / log / review ----------------
ATTEMPTS = os.path.join(ROOT, 'data', 'attempts.jsonl')
INTERVALS = {'fail': 2, 'hard': 7, 'ok': 21}   # 间隔复习天数
TIME_LIMIT = {1: 15, 2: 25, 3: 40, 4: 80, 5: 120}  # 独立攻坚限时（分钟）


def load_attempts():
    import json
    if not os.path.exists(ATTEMPTS):
        return []
    return [json.loads(l) for l in open(ATTEMPTS, encoding='utf-8') if l.strip()]


def log_attempt(problems, args):
    import json, datetime
    ids = {p['fm']['id'] for p in problems if p['fm']}
    if args.id not in ids:
        print(f'未知题号 {args.id}')
        sys.exit(2)
    os.makedirs(os.path.dirname(ATTEMPTS), exist_ok=True)
    rec = {'id': args.id, 'result': args.result,
           'date': args.date or datetime.date.today().isoformat(),
           'hints': args.hints, 'note': args.note or ''}
    with open(ATTEMPTS, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    nxt = INTERVALS.get(args.result)
    print(f"已记录 {rec['id']}：{rec['result']}（用提示 {args.hints} 级）"
          + (f'，{nxt} 天后进入复习队列' if nxt else ''))


def review(problems, args=None):
    import datetime
    today = datetime.date.today()
    last = {}
    for r in load_attempts():
        last[r['id']] = r          # 追加式日志，后写覆盖前写
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    due = []
    for pid, r in last.items():
        gap = INTERVALS.get(r['result'])
        if gap is None or pid not in fmmap:
            continue
        due_date = datetime.date.fromisoformat(r['date']) + datetime.timedelta(days=gap)
        if due_date <= today:
            due.append((due_date, r, fmmap[pid]))
    if not due:
        print('复习队列为空。')
        return []
    due.sort()
    print(f'应复习 {len(due)} 题（按到期先后）：\n')
    for dd, r, fm in due:
        od = (today - dd).days
        tag = f'逾期 {od} 天' if od > 0 else '今日到期'
        print(f"{fm['id']}  {'★' * fm['difficulty']:<5} 上次 {r['result']:<4} {tag:<8} {fm['title']}")
    print('\n复习纪律：先不看任何提示重做；仍卡再按提示阶梯逐级解锁。做完 log。')
    return due


def _pick_rotating(cands, k, rng):
    """按板块轮转从候选中取 k 题，避免同板块扎堆。"""
    by_cat = {}
    for fm in cands:
        by_cat.setdefault(fm['category'], []).append(fm)
    order = sorted(by_cat)
    rng.shuffle(order)
    sel, i = [], 0
    while len(sel) < min(k, len(cands)):
        cat = order[i % len(order)]
        if by_cat[cat]:
            sel.append(by_cat[cat].pop(0))
        i += 1
        if i > 10 * len(cands) + 10:
            break
    return sel


def coach(problems, args):
    import random
    profile = PLAN_PROFILES.get(args.target)
    if not profile:
        print(f'未知目标赛事。可选：{"、".join(PLAN_PROFILES)}')
        sys.exit(2)
    rng = random.Random(args.seed)
    attempted = {r['id'] for r in load_attempts()}
    total_w = sum(profile.values())
    pool = {}
    for p in problems:
        fm = p['fm'] or {}
        if fm.get('id') in attempted:
            continue
        d = fm.get('difficulty')
        if d in profile:
            pool.setdefault(d, []).append(fm)
    for lst in pool.values():
        rng.shuffle(lst)
    print(f'=== 教练周计划 | 目标 {args.target} | {args.weeks} 周 × 每周 {args.n} 题 | seed={args.seed} ===\n')
    print('攻坚纪律：限时独立攻坚（' + '，'.join(f'★{d}≤{m}min' for d, m in sorted(TIME_LIMIT.items()) if d in profile)
          + '）；卡住按「提示阶梯」逐级解锁，每级之间再战 15 分钟；无论成败必须 log。\n')
    for w in range(1, args.weeks + 1):
        print(f'—— 第 {w} 周 ——')
        picked = []
        for d, wt in sorted(profile.items()):
            k = max(1, round(args.n * wt / total_w))
            sel = _pick_rotating(pool.get(d, []), k, rng)
            for fm in sel:
                pool[d].remove(fm)
            picked += sel
        for fm in sorted(picked, key=lambda f: (f['difficulty'], f['id'])):
            print(f"  {fm['id']}  {'★' * fm['difficulty']:<5} {fm.get('contest') or '?':<6} {fm['title']}")
        if not picked:
            print('  （题池已耗尽——先复习或扩池）')
        print()
    print('—— 复习检查 ——')
    review(problems)


# ---------------- 知识点注册表与指示图 ----------------
CAT_LABEL = {'algebra': '代数', 'number-theory': '数论', 'combinatorics': '组合', 'geometry': '几何'}


def load_registry():
    path = os.path.join(ROOT, 'taxonomy', 'registry.yml')
    if not os.path.exists(path):
        return None
    data = yaml.safe_load(open(path, encoding='utf-8')) or {}
    return {c: (v or {}) for c, v in data.items() if not str(c).startswith('_')}


def resolve_topic(reg, cat, t):
    nodes = (reg or {}).get(cat) or {}
    if t in nodes:
        return t
    for node, aliases in nodes.items():
        if t in (aliases or []):
            return node
    return None


def registry_report(problems):
    reg = load_registry()
    if reg is None:
        return ['taxonomy/registry.yml 缺失']
    bad = []
    for p in problems:
        fm = p['fm'] or {}
        for t in fm.get('topics', []) or []:
            if resolve_topic(reg, fm.get('category'), t) is None:
                bad.append(f"{fm.get('id')}: 「{t}」")
    return bad


def gen_map(problems):
    import json, datetime
    reg = load_registry()
    if reg is None:
        print('缺 taxonomy/registry.yml')
        sys.exit(2)
    cats, unresolved = [], []
    for cat in CATEGORIES:
        nodes = {n: {'name': n, 'stars': {str(i): 0 for i in range(1, 6)}, 'problems': []}
                 for n in (reg.get(cat) or {})}
        for p in problems:
            fm = p['fm'] or {}
            if fm.get('category') != cat:
                continue
            hit = set()
            for t in fm.get('topics', []) or []:
                node = resolve_topic(reg, cat, t)
                if node is None:
                    unresolved.append((fm.get('id'), t))
                else:
                    hit.add(node)
            for node in hit:
                d = fm.get('difficulty')
                nodes[node]['stars'][str(d)] += 1
                nodes[node]['problems'].append(
                    {'id': fm['id'], 'd': d, 't': fm.get('title', ''),
                     'c': fm.get('contest', ''), 'y': fm.get('year', '')})
        for nd in nodes.values():
            nd['problems'].sort(key=lambda x: (-x['d'], x['id']))
            nd['total'] = len(nd['problems'])
        cats.append({'cat': cat, 'label': CAT_LABEL[cat],
                     'nodes': [nodes[n] for n in (reg.get(cat) or {})]})
    data = {'generated': datetime.date.today().isoformat(), 'total': len(problems), 'cats': cats}
    os.makedirs(os.path.join(ROOT, 'maps'), exist_ok=True)
    with open(os.path.join(ROOT, 'maps', 'map_data.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tpl = open(os.path.join(ROOT, 'scripts', 'map_template.html'), encoding='utf-8').read()
    with open(os.path.join(ROOT, 'maps', '指示图.html'), 'w', encoding='utf-8') as f:
        f.write(tpl.replace('__MAP_DATA__', json.dumps(data, ensure_ascii=False)))
    n_nodes = sum(len(c['nodes']) for c in cats)
    print(f'maps/指示图.html 已生成：{len(problems)} 题 → {n_nodes} 个知识点节点')
    if unresolved:
        print(f'未注册 topic {len(unresolved)} 处（跑 lint 看明细）')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('lint')
    q = sub.add_parser('query')
    q.add_argument('--difficulty', type=int)
    q.add_argument('--topic')
    q.add_argument('--contest')
    q.add_argument('--category', choices=CATEGORIES)
    q.add_argument('--unverified', action='store_true')
    sub.add_parser('stats')
    pl = sub.add_parser('plan')
    pl.add_argument('--target', required=True)
    pl.add_argument('--n', type=int, default=12)
    pl.add_argument('--seed', type=int, default=1)
    co = sub.add_parser('coach')
    co.add_argument('--target', required=True)
    co.add_argument('--weeks', type=int, default=4)
    co.add_argument('--n', type=int, default=10)
    co.add_argument('--seed', type=int, default=1)
    lg = sub.add_parser('log')
    lg.add_argument('--id', required=True)
    lg.add_argument('--result', required=True, choices=['ok', 'hard', 'fail'])
    lg.add_argument('--hints', type=int, default=0, choices=[0, 1, 2, 3])
    lg.add_argument('--date')
    lg.add_argument('--note')
    sub.add_parser('review')
    sub.add_parser('map')
    args = ap.parse_args()
    problems = load_all()
    if args.cmd == 'lint':
        sys.exit(lint(problems))
    elif args.cmd == 'query':
        query(problems, args)
    elif args.cmd == 'plan':
        plan(problems, args)
    elif args.cmd == 'coach':
        coach(problems, args)
    elif args.cmd == 'log':
        log_attempt(problems, args)
    elif args.cmd == 'review':
        review(problems)
    elif args.cmd == 'map':
        gen_map(problems)
    else:
        stats(problems)


if __name__ == '__main__':
    main()
