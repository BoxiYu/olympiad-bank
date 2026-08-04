#!/usr/bin/env python3
"""题库工具：lint / doclint / query / stats / plan / coach / spar / review / similar / web

用法：
  uv run python scripts/bank.py web        # 浏览器训练台（学生推荐入口，scripts/web_app.py）
  uv run python scripts/bank.py lint
  uv run python scripts/bank.py doclint    # 全仓 md：死链 / 禁词 / taxonomy 树一致性
  uv run python scripts/bank.py query [--difficulty 3] [--topic 韦达] [--contest IMO] [--category algebra] [--unverified]
  uv run python scripts/bank.py stats
  uv run python scripts/bank.py coach --target IMO --save     # 周计划 → data/plan.json
  uv run python scripts/bank.py spar next                     # 开卡（复习到期 > 周计划）
  uv run python scripts/bank.py spar hint / reveal / finish   # 提示 / 看解 / 落账
  uv run python scripts/bank.py similar A-037                 # 相似候选 + 确认边
"""
import argparse, os, re, sys, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import spar_session as sp  # 会话流程 + v2 日志契约（间隔/毕业/归一化），三方共用
CATEGORIES = ['algebra', 'number-theory', 'combinatorics', 'geometry']
PREFIX = {'algebra': 'A', 'number-theory': 'N', 'combinatorics': 'C', 'geometry': 'G'}
REQUIRED = ['id', 'title', 'category', 'source_ref', 'difficulty', 'topics', 'verification', 'source_url']
SECTIONS = ['## 题面', '## 答案', '## 解法要点']
MIN_DIFFICULTY = 2  # 学段下界：本库只收初中+高中，★1 为小学/低龄档不入库（语义正本 SPEC §4）


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


_VERDICT_CACHE = {}


def _verdict_ids(ref):
    """review_ref（仓库相对路径）→ 该评审凭证覆盖的 mathnet_id 集合；缺失/不可解析 → None。"""
    if ref not in _VERDICT_CACHE:
        import json
        try:
            rows = json.load(open(os.path.join(ROOT, ref), encoding='utf-8'))
            _VERDICT_CACHE[ref] = {str(r.get('mathnet_id')) for r in rows if isinstance(r, dict)}
        except (OSError, ValueError):
            _VERDICT_CACHE[ref] = None
    return _VERDICT_CACHE[ref]


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
        # bool 是 int 的子类：YAML 的 difficulty: true/yes 会读成 True，不排除就被静默当成 ★1
        dv = fm.get('difficulty')
        if isinstance(dv, bool) or not isinstance(dv, int) or not 1 <= dv <= 5:
            errors.append(f'{rel}: difficulty 必须是 1-5 的整数')
        elif fm['difficulty'] < MIN_DIFFICULTY:  # 学段下界正本：SPEC §4（初中+高中，★1 是小学档）
            errors.append(f'{rel}: difficulty {fm["difficulty"]} 低于学段下界 ★{MIN_DIFFICULTY}'
                          f'——本库只收初中与高中，★1 不入库（SPEC §4）')
        url = str(fm.get('source_url', ''))
        if not url.startswith('http'):
            errors.append(f'{rel}: source_url 不是链接')
        if fm.get('verification') not in sp.VALID_VERIFICATION:  # 枚举正本在 spar_session.VALID_VERIFICATION
            errors.append(f'{rel}: verification 取值非法（合法：{"/".join(sp.VALID_VERIFICATION)}）')
        if fm.get('verification') == 'mathnet-reviewed':
            # 铁律 3/4：凭证必须落盘且真实覆盖本题——裸声明字段不被信任
            mid = str(fm.get('mathnet_id') or '')
            ref = str(fm.get('review_ref') or '')
            if not mid:
                errors.append(f'{rel}: verification=mathnet-reviewed 缺少必填溯源字段 mathnet_id')
            if not ref:
                errors.append(f'{rel}: verification=mathnet-reviewed 缺少评审凭证字段 review_ref')
            else:
                ids = _verdict_ids(ref)
                if ids is None:
                    errors.append(f'{rel}: review_ref 指向的评审凭证不存在或无法解析：{ref}')
                elif mid and mid not in ids:
                    errors.append(f'{rel}: 评审凭证 {ref} 未覆盖 mathnet_id={mid}——「数据集声称≠已核验」')
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


def topic_match(q, topics, title, reg):
    ql = q.lower()
    if any(q in t for t in topics) or q in title:
        return True
    # 别名查询（中英同源 registry）：查询词命中某节点的节点名/任一别名 → 该节点写法出现在 topics 中
    for nodes in (reg or {}).values():
        for node, aliases in (nodes or {}).items():
            names = [node] + [str(a) for a in (aliases or [])]
            if any(ql in n.lower() for n in names) and any(n in t for n in names for t in topics):
                return True
    return False


def query(problems, args):
    reg = load_registry()
    rows = []
    for p in problems:
        fm = p['fm'] or {}
        if args.difficulty and fm.get('difficulty') != args.difficulty:
            continue
        if args.category and fm.get('category') != args.category:
            continue
        if args.contest and args.contest.lower() not in str(fm.get('contest', '')).lower():
            continue
        if args.topic and not topic_match(args.topic, fm.get('topics', []), fm.get('title', ''), reg):
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


# 目标赛事 → 星级配比（权重，按比例取题）。锚点见 docs/archive/赛事地图与官方题源.md
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
        print(f"{fm['id']}  {star:<5}  {fm.get('contest') or '?':<8} {fm['title']}  [{' / '.join(fm['topics'])}]"
              f"  → uv run python scripts/bank.py spar {fm['id']}")
    dist = {}
    for fm in picked:
        dist[fm['difficulty']] = dist.get(fm['difficulty'], 0) + 1
    print('\n难度构成：' + '，'.join(f'★{d}×{c}' for d, c in sorted(dist.items())))


# ---------------- 教练闭环：coach / log / review / spar ----------------
ATTEMPTS = sp.ATTEMPTS_PATH
TIME_LIMIT = sp.TIME_LIMIT


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
    norm = sp.normalize_attempt(rec)['result']
    nxt = sp.INTERVALS.get(norm)
    print(f"已记录 {rec['id']}：{rec['result']}（读取时归一化为 {norm}，提示 {args.hints} 级）"
          + (f'，{nxt} 天后进入复习队列' if nxt else ''))
    print(f'建议改用 spar 流程（全程留痕、自动判定）：uv run python scripts/bank.py spar {args.id}')


def review(problems, args=None):
    import datetime
    today = datetime.date.today()
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    hist = sp.history_by_id(sp.load_attempts_v2())   # 全历史扫描（非只看最后一条）
    grads, due = [], []
    for pid, recs in hist.items():
        if pid not in fmmap:
            continue
        if sp.is_graduated(recs):
            grads.append(fmmap[pid])
            continue
        dd = sp.due_date_of(recs)
        if dd is not None and dd <= today:
            due.append((dd, recs[-1], fmmap[pid]))
    if grads:
        print(f'🎓 本次毕业（连续 {sp.GRADUATE_STREAK} 次 independent_ok，永久退出复习队列）：')
        for fm in sorted(grads, key=lambda f: f['id']):
            print(f"  {fm['id']}  {'★' * fm['difficulty']:<5} {fm['title']}")
        print()
    if not due:
        print('复习队列为空。')
        return []
    due.sort(key=lambda t: (t[0], t[2]['id']))
    print(f'应复习 {len(due)} 题（按到期先后）：\n')
    for dd, r, fm in due:
        od = (today - dd).days
        tag = f'逾期 {od} 天' if od > 0 else '今日到期'
        print(f"{fm['id']}  {'★' * fm['difficulty']:<5} 上次 {r['result']:<22} {tag:<8} {fm['title']}")
    print('\n复习纪律：先不看任何提示重做。开卡：uv run python scripts/bank.py spar next（自动优先到期复习）')
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


def build_coach_pool(problems, profile, excluded, rng):
    """周计划候选池：{难度: [fm]}（排除已做/毕业题，随机洗牌）。coach 与 web 训练台共用。"""
    pool = {}
    for p in problems:
        fm = p['fm'] or {}
        if fm.get('id') in excluded:
            continue
        d = fm.get('difficulty')
        if d in profile:
            pool.setdefault(d, []).append(fm)
    for lst in pool.values():
        rng.shuffle(lst)
    return pool


def pick_week(pool, profile, n, rng):
    """从候选池按星级配比取一周题目（就地消耗 pool），按（难度, 题号）排序返回。"""
    total_w = sum(profile.values())
    picked = []
    for d, wt in sorted(profile.items()):
        k = max(1, round(n * wt / total_w))
        sel = _pick_rotating(pool.get(d, []), k, rng)
        for fm in sel:
            pool[d].remove(fm)
        picked += sel
    picked.sort(key=lambda f: (f['difficulty'], f['id']))
    return picked


def coach(problems, args):
    import datetime, random
    profile = PLAN_PROFILES.get(args.target)
    if not profile:
        print(f'未知目标赛事。可选：{"、".join(PLAN_PROFILES)}')
        sys.exit(2)
    rng = random.Random(args.seed)
    hist = sp.history_by_id(sp.load_attempts_v2())
    graduated = {pid for pid, recs in hist.items() if sp.is_graduated(recs)}
    excluded = set(hist) | graduated   # 做过的走复习队列；毕业题永久不进周计划
    pool = build_coach_pool(problems, profile, excluded, rng)
    print(f'=== 教练周计划 | 目标 {args.target} | {args.weeks} 周 × 每周 {args.n} 题 | seed={args.seed} ===\n')
    print('攻坚纪律：限时独立攻坚（' + '，'.join(f'★{d}≤{m}min' for d, m in sorted(TIME_LIMIT.items()) if d in profile)
          + '）；卡住按「提示阶梯」逐级解锁，每级之间再战 15 分钟；无论成败必须 spar finish 落账。\n')
    week1 = []
    for w in range(1, args.weeks + 1):
        print(f'—— 第 {w} 周 ——')
        picked = pick_week(pool, profile, args.n, rng)
        for fm in picked:
            print(f"  {fm['id']}  {'★' * fm['difficulty']:<5} {fm.get('contest') or '?':<6} {fm['title']}"
                  f"  → uv run python scripts/bank.py spar {fm['id']}")
        if not picked:
            print('  （题池已耗尽——先复习或扩池）')
        if w == 1:
            week1 = [fm['id'] for fm in picked]
        print()
    if getattr(args, 'save', False):
        if week1:
            week = sp.iso_week_str(datetime.date.today())
            sp.save_plan(week, args.target, args.seed, week1)
            print(f'已写入 data/plan.json（{week}，第 1 周 {len(week1)} 题）——spar next 会按此计划出题\n')
        else:
            print('题池为空，未写 data/plan.json\n')
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
            if resolve_topic(reg, fm.get('category'), t) is not None:
                continue
            # 跨界题允许并列他板块的规范节点名（SPEC §1）——任一板块能解析即视为已注册
            if any(resolve_topic(reg, c, t) for c in reg):
                continue
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


# ---------------- 文档一致性校验 doclint ----------------
# 扫描范围：全仓 .md（跳过 . 开头的隐藏目录与 node_modules）；docs/archive/ 为历史存档白名单（免禁词检查）。
DOCLINT_FORBIDDEN = ['origin/main', 'scripts/symphony-start.sh', 'docs/sources/']
TAXONOMY_BOARDS = {'algebra': 'algebra.md', 'combinatorics': 'combinatorics.md',
                   'geometry': 'geometry.md', 'number-theory': 'number-theory.md'}


def _walk_md():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d != 'node_modules')
        for fn in sorted(filenames):
            if fn.endswith('.md'):
                yield os.path.join(dirpath, fn)


def doclint():
    errors = []
    link_re = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
    n_files = 0
    for path in _walk_md():
        n_files += 1
        rel = os.path.relpath(path, ROOT)
        text = open(path, encoding='utf-8').read()
        # a) 死链：相对路径引用的文件必须存在（跳过 URL/锚点/绝对路径）
        for m in link_re.finditer(text):
            tgt = m.group(1)
            if tgt.startswith(('http://', 'https://', 'mailto:', '#', '/')) or '://' in tgt:
                continue
            tgt = tgt.split('#')[0]
            if tgt and not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), tgt))):
                errors.append(f'{rel}: 死链 {m.group(1)}')
        # b) 禁词：已废弃指针不得再出现（历史存档 docs/archive/ 豁免）
        if not rel.startswith(os.path.join('docs', 'archive') + os.sep):
            for i, line in enumerate(text.splitlines(), 1):
                for word in DOCLINT_FORBIDDEN:
                    if word in line:
                        errors.append(f'{rel}:{i}: 禁词「{word}」')
    # c) taxonomy 四板块 .md 的 ### 标题集合 == registry 对应板块节点集合
    reg = load_registry()
    if reg is None:
        errors.append('taxonomy/registry.yml 缺失')
    else:
        head_re = re.compile(r'###\s+(?:\d+[.、．]\s*)?(.+?)\s*$')
        for cat, fname in TAXONOMY_BOARDS.items():
            path = os.path.join(ROOT, 'taxonomy', fname)
            if not os.path.exists(path):
                errors.append(f'taxonomy/{fname} 缺失')
                continue
            heads = {m.group(1) for line in open(path, encoding='utf-8')
                     if (m := head_re.match(line.strip()))}
            nodes = set(reg.get(cat) or {})
            for n in sorted(nodes - heads):
                errors.append(f'taxonomy/{fname}: 缺少 registry 节点小节「### {n}」')
            for h in sorted(heads - nodes):
                errors.append(f'taxonomy/{fname}: 小节「{h}」不是 registry 的 {cat} 节点（树漂移）')
    if errors:
        print('\n'.join(errors))
        print(f'\nDOCLINT FAILED: {len(errors)} 个问题')
        return 1
    print(f'DOCLINT OK: {n_files} 个 md 文件（死链/禁词/树一致性）全部通过')
    return 0


# ---------------- MathNet 候选池 ----------------
CANDIDATES = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')


def load_candidates():
    import json
    if not os.path.exists(CANDIDATES):
        print('候选池不存在。先构建：uv run --group mathnet python scripts/mathnet_ingest.py')
        sys.exit(2)
    return [json.loads(l) for l in open(CANDIDATES, encoding='utf-8') if l.strip()]


def _parse_diff(s):
    """'3' → (3,3)；'2-3' → (2,3)。"""
    a, _, b = s.partition('-')
    lo, hi = int(a), int(b or a)
    return lo, hi


CONF_RANK = {'high': 2, 'mid': 1, 'low': 0}


def candidates_cmd(problems, args):
    rows = load_candidates()
    if args.gaps:
        return candidates_gaps(problems, rows)
    pool = [r for r in rows if r['status'] == 'ok']
    if not args.with_images:
        pool = [r for r in pool if not r['has_images']]
    min_conf = CONF_RANK.get(args.conf, 1)
    pool = [r for r in pool if CONF_RANK[r['difficulty_conf']] >= min_conf]
    if args.category:
        pool = [r for r in pool if r['category'] == args.category]
    lo, hi = _parse_diff(args.difficulty) if args.difficulty else (MIN_DIFFICULTY, 5)
    if lo < MIN_DIFFICULTY:  # 学段下界，SPEC §4：★1 无论如何都进不了库，不让它占浏览视野
        print(f'注：--difficulty 下限 {lo} 已抬到学段下界 ★{MIN_DIFFICULTY}（本库只收初中+高中，SPEC §4）')
        lo = MIN_DIFFICULTY
    pool = [r for r in pool if lo <= r['difficulty_est'] <= hi]
    if args.node:
        pool = [r for r in pool if any(args.node in t for t in r['topics'])]
    if args.contest:
        q = args.contest.lower()
        pool = [r for r in pool if q in (r['contest_raw'] or '').lower() or q in (r['comp_norm'] or '')]
    if args.lang:
        q = args.lang.lower()
        pool = [r for r in pool if q in (r['language'] or '').lower()]
    if args.grep:
        rx = re.compile(args.grep, re.I)
        pool = [r for r in pool if rx.search(r['head'])]
    if args.stats:
        diff = {}
        for r in pool:
            diff.setdefault(r['difficulty_est'], {}).setdefault(r['category'], 0)
            diff[r['difficulty_est']][r['category']] += 1
        header = f"{'':<12}" + ''.join(f'{c[:8]:>10}' for c in CATEGORIES) + f"{'合计':>8}"
        print(f'候选池分布（当前筛选条件下，共 {len(pool)} 题）'); print(header)
        for d in sorted(diff):
            row = diff[d]
            print(f"{'★' * d:<12}" + ''.join(f'{row.get(c, 0):>10}' for c in CATEGORIES) + f'{sum(row.values()):>8}')
        return
    pool.sort(key=lambda r: (r['difficulty_est'], -CONF_RANK[r['difficulty_conf']], r['comp_norm'] or '~', r['mathnet_id']))
    shown = pool[:args.limit]
    for r in shown:
        star = '★' * r['difficulty_est']
        topics = ' / '.join(r['topics'][:4]) or '（仅板块）'
        weak = '‹弱›' if r.get('topics_weak_only') else ''
        img = '图' if r['has_images'] else ''
        print(f"MN-{r['mathnet_id']}  {star:<5}({r['difficulty_conf'][0]})  {(r['contest_raw'] or '?')[:34]:<34} {r['year'] or '----'} {img:<2} [{topics}]{weak}")
        print(f"       {r['head'][:76]}")
    print(f'\n匹配 {len(pool)} 题，显示前 {len(shown)}（--limit 调整；est 为估级非定级，入库时按 SPEC 定稿）')


def candidates_gaps(problems, rows):
    """45 节点采购单：库内现有 vs 候选可补（含中低星细分）。"""
    reg = load_registry()
    bank = {}
    for p in problems:
        fm = p['fm'] or {}
        for t in fm.get('topics', []) or []:
            node = resolve_topic(reg, fm.get('category'), t)
            if node:
                bank.setdefault((fm['category'], node), set()).add(fm['id'])
    cand, cand_low = {}, {}
    for r in rows:
        # 学段下界（SPEC §4）：★1 进不了库，计进采购单会虚报可补量
        if r['status'] != 'ok' or r['difficulty_est'] < MIN_DIFFICULTY:
            continue
        for node in r['topics']:
            k = (r['category'], node)
            cand[k] = cand.get(k, 0) + 1
            if r['difficulty_est'] <= 3:
                cand_low[k] = cand_low.get(k, 0) + 1
    print(f"{'板块':<6} {'知识点':<14} {'库内':>4} {'候选':>6} {'候选★≤3':>7}")
    for cat in CATEGORIES:
        for node in (reg.get(cat) or {}):
            k = (cat, node)
            b = len(bank.get(k, ()))
            mark = ' ←缺' if b <= 2 else ''
            print(f'{CAT_LABEL[cat]:<6} {node:<14} {b:>4} {cand.get(k, 0):>6} {cand_low.get(k, 0):>7}{mark}')
    print('\n「←缺」= 库内 ≤2 题的薄弱节点；候选数为标签流估计，入库前须官方源核验。')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('lint')
    sub.add_parser('doclint', help='全仓 md 文档校验：死链 / 禁词 / taxonomy 树一致性')
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
    co.add_argument('--save', action='store_true', help='把第 1 周选题写入 data/plan.json（spar next 按此出题）')
    spar = sub.add_parser('spar', help='攻坚会话：start <ID> / next / hint / reveal / finish')
    spar.add_argument('action', help='start|next|hint|reveal|finish，或直接给题号（= start）')
    spar.add_argument('target', nargs='?', help='start 的题号')
    spar.add_argument('--print', dest='print_card', action='store_true', help='start：题卡纯文本输出到 stdout（可打印）')
    spar.add_argument('--abandon', action='store_true', help='start：放弃当前未关会话再开新卡')
    spar.add_argument('--mode', choices=['fresh', 'review', 'variant'], help='start：手动指定模式')
    spar.add_argument('--result', choices=list(sp.RESULTS), help='finish：直接给判定（跳过交互）')
    spar.add_argument('--retell', choices=['yes', 'no'], help='finish：已看答案时的复述结果（跳过交互）')
    spar.add_argument('--stuck', choices=list(sp.STUCK_CHOICES), help='finish：卡点五选一')
    spar.add_argument('--note', help='finish：备注')
    si = sub.add_parser('similar', help='相似候选（委托 similar_index.py）+ 确认边台账')
    si.add_argument('id')
    si.add_argument('--top', type=int, default=20)
    si.add_argument('--confirm', metavar='DST', help='确认与 DST 的关系，写入 data/similar/edges.jsonl')
    si.add_argument('--relation', choices=list(sp.RELATIONS))
    si.add_argument('--confidence', type=float, default=1.0)
    si.add_argument('--evidence', default='manual', choices=['text', 'formula', 'solution', 'manual'])
    lg = sub.add_parser('log')
    lg.add_argument('--id', required=True)
    lg.add_argument('--result', required=True, choices=['ok', 'hard', 'fail'])
    lg.add_argument('--hints', type=int, default=0, choices=[0, 1, 2, 3])
    lg.add_argument('--date')
    lg.add_argument('--note')
    sub.add_parser('review')
    sub.add_parser('map')
    wb = sub.add_parser('web', help='浏览器训练台（学生推荐入口，本地服务）')
    wb.add_argument('--host', default='127.0.0.1')
    wb.add_argument('--port', type=int, default=8642)
    wb.add_argument('--no-open', action='store_true', help='启动后不自动打开浏览器')
    ca = sub.add_parser('candidates')
    ca.add_argument('--category', choices=CATEGORIES)
    ca.add_argument('--difficulty', help='单值 3 或区间 2-3（估级）')
    ca.add_argument('--node', help='知识点（registry 规范节点名，子串匹配）')
    ca.add_argument('--contest', help='赛事名子串')
    ca.add_argument('--lang', help='语言子串，如 english')
    ca.add_argument('--grep', help='题面预览正则（Ramsey 类标签盖不住的召回旁路）')
    ca.add_argument('--conf', default='mid', choices=['high', 'mid', 'low'], help='最低置信度，默认 mid')
    ca.add_argument('--with-images', action='store_true', help='包含带图题（默认排除）')
    ca.add_argument('--limit', type=int, default=20)
    ca.add_argument('--stats', action='store_true', help='只看 难度×板块 分布')
    ca.add_argument('--gaps', action='store_true', help='45 节点缺口采购单：库内 vs 候选')
    args = ap.parse_args()
    if args.cmd == 'doclint':
        sys.exit(doclint())
    if args.cmd == 'web':
        import threading, webbrowser
        import uvicorn
        from web_app import app as web_application
        url = f'http://{args.host}:{args.port}'
        print(f'训练台已启动：{url}（关闭：终端里按 Ctrl+C）')
        if not args.no_open:
            threading.Timer(0.8, webbrowser.open, args=(url,)).start()
        uvicorn.run(web_application, host=args.host, port=args.port, log_level='warning')
        return
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
    elif args.cmd == 'spar':
        sp.cmd_spar(problems, args)
    elif args.cmd == 'similar':
        sp.cmd_similar(problems, args)
    elif args.cmd == 'review':
        review(problems)
    elif args.cmd == 'map':
        gen_map(problems)
    elif args.cmd == 'candidates':
        candidates_cmd(problems, args)
    else:
        stats(problems)


if __name__ == '__main__':
    main()
