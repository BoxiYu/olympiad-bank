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
    print(f'LINT OK: {len(problems)} 题全部通过')
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
    args = ap.parse_args()
    problems = load_all()
    if args.cmd == 'lint':
        sys.exit(lint(problems))
    elif args.cmd == 'query':
        query(problems, args)
    elif args.cmd == 'plan':
        plan(problems, args)
    else:
        stats(problems)


if __name__ == '__main__':
    main()
