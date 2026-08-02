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


def query(problems, args):
    rows = []
    for p in problems:
        fm = p['fm'] or {}
        if args.difficulty and fm.get('difficulty') != args.difficulty:
            continue
        if args.category and fm.get('category') != args.category:
            continue
        if args.contest and args.contest.lower() not in str(fm.get('contest', '')).lower():
            continue
        if args.topic and not any(args.topic in t for t in fm.get('topics', [])) \
           and args.topic not in fm.get('title', ''):
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
    args = ap.parse_args()
    problems = load_all()
    if args.cmd == 'lint':
        sys.exit(lint(problems))
    elif args.cmd == 'query':
        query(problems, args)
    else:
        stats(problems)


if __name__ == '__main__':
    main()
