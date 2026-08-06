#!/usr/bin/env python3
"""题库工具：lint / doclint / query / mathnet-search / stats / plan / coach / spar / review / similar / web / student / assess / profile / gaps / doctor

用法：
  uv run python scripts/bank.py web        # 浏览器训练台（学生推荐入口，scripts/web_app.py）
  uv run python scripts/bank.py lint
  uv run python scripts/bank.py doclint    # 全仓 md：死链 / 禁词 / taxonomy 树 / 训练契约一致性
  uv run python scripts/bank.py query [--difficulty 3] [--topic 韦达] [--contest IMO] [--category algebra] [--unverified]
  uv run python scripts/bank.py mathnet-search "关键词" --lang zh --topic 不等式
  uv run python scripts/bank.py stats
  uv run python scripts/bank.py coach --target IMO --save     # 周计划 → data/plan.json
  uv run python scripts/bank.py spar next                     # 开卡（复习到期 > 周计划）
  uv run python scripts/bank.py spar hint / reveal / finish   # 提示 / 看解 / 落账
  uv run python scripts/bank.py similar A-037                 # 相似候选 + 确认边
  uv run python scripts/bank.py student add <id> --name 张三  # 学生建档（student list 看名单）
  uv run python scripts/bank.py assess <id> --wave 基线-1 --id A-001 --score 1   # 测评波次录入
  uv run python scripts/bank.py profile <id> [--html]         # 能力图：基础值走势 + 节点状态 + 补齐队列
  uv run python scripts/bank.py gaps [--student self]         # 统一缺口台账 → maps/gaps.json（coach --from-gaps 消费）
  uv run python scripts/bank.py doctor     # 生成产物新鲜度自检（maps/ 与 simindex；只读，绝不代跑重建）
"""
import argparse, os, re, sys, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from bank_constants import CATEGORIES
import spar_session as sp  # 会话流程 + v2 日志契约（间隔/毕业/归一化），三方共用
import student_profile as stp  # 学生档案 + 能力图（证据折算/状态阈值的正本在该模块 docstring）
PREFIX = {'algebra': 'A', 'number-theory': 'N', 'combinatorics': 'C', 'geometry': 'G'}
REQUIRED = ['id', 'title', 'category', 'source_ref', 'difficulty', 'topics', 'verification', 'source_url']
ALLOWED = REQUIRED + ['contest', 'year', 'difficulty_note', 'mathnet_id', 'review_ref', 'machine_check_ref']
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


_MACHINE_CACHE = {}


def _machine_status(ref):
    """machine_check_ref（仓库相对路径）→ {题号: status}；缺失/不可解析 → None。
    机制正本在 scripts/checks/run_checks.py 头注；lint 只查凭证覆盖，重跑核验归 CI。"""
    if ref not in _MACHINE_CACHE:
        import json
        try:
            rows = json.load(open(os.path.join(ROOT, ref), encoding='utf-8'))
            _MACHINE_CACHE[ref] = {str(r.get('id')): r.get('status')
                                   for r in rows if isinstance(r, dict)}
        except (OSError, ValueError):
            _MACHINE_CACHE[ref] = None
    return _MACHINE_CACHE[ref]


def lint(problems):
    errors = []
    seen = {}
    for p in problems:
        rel = os.path.relpath(p['path'], ROOT)
        fm = p['fm']
        if fm is None:
            errors.append(f'{rel}: frontmatter 缺失或无法解析')
            continue
        unknown = sorted(set(fm) - set(ALLOWED))
        if unknown:
            errors.append(f'{rel}: frontmatter 含未知字段 {", ".join(unknown)}')
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
        tv = fm.get('topics')
        if isinstance(tv, list) and len(tv) > 4:
            errors.append(f'{rel}: topics 有 {len(tv)} 个，超出上限 4（SPEC §2：1–4 个规范节点，只留解题主线）')
        elif tv is not None and not isinstance(tv, list):
            errors.append(f'{rel}: topics 必须是列表（SPEC §2）')
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
        # 机器核验凭证（可选字段）：挂了 machine_check_ref 的题，台账必须真实覆盖且为 pass。
        # 凭证是否「仍真」由 CI 重跑 scripts/checks/run_checks.py 保证——lint 不执行核验代码。
        mref = str(fm.get('machine_check_ref') or '')
        if mref:
            mst = _machine_status(mref)
            if mst is None:
                errors.append(f'{rel}: machine_check_ref 指向的核验台账不存在或无法解析：{mref}')
            elif mst.get(pid) != 'pass':
                errors.append(f'{rel}: 核验台账 {mref} 未覆盖 {pid} 或状态非 pass——裸声明不被信任')
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
    # 唯一硬门槛也执行训练契约检查；完整文档一致性仍由 doclint 负责。
    errors.extend(_training_contract_errors())
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


MATHNET_REBUILD_CMD = 'uv run --group mathnet python scripts/mathnet_export.py'
TRANSLATION_EXPORT_CMD = 'uv run python scripts/mathnet_translate.py export --out translations.todo.jsonl'
MATHNET_VARIANT_STATES = ('passthrough', 'translated', 'failed', 'missing')


def _mathnet_variant_path(source_path, lang):
    """index.jsonl 的原文路径 → 所选语言正文路径。"""
    if lang == 'orig':
        return source_path
    parent, _ = os.path.split(source_path)
    return os.path.join(parent, f'index.{lang}.md')


def _mathnet_coverage(row, lang):
    """兼容新索引字符串投影和 translation.json 风格的嵌套 mode。"""
    if lang == 'orig':
        return 'passthrough'
    variants = row.get('variants')
    value = variants.get(lang, 'missing') if isinstance(variants, dict) else 'missing'
    if isinstance(value, dict):
        value = value.get('mode', 'missing')
    value = str(value or 'missing')
    return value if value in MATHNET_VARIANT_STATES else 'missing'


def _mathnet_row_matches(row, args):
    if args.category and row.get('category') != args.category:
        return False
    if args.difficulty is not None and row.get('difficulty_est') != args.difficulty:
        return False
    if args.country and args.country.casefold() not in str(row.get('country') or '').casefold():
        return False
    if args.topic:
        needle = args.topic.casefold()
        if not any(needle in str(topic).casefold() for topic in (row.get('topics') or [])):
            return False
    if args.coverage == 'stale':
        return bool(row.get('translation_stale', False))
    if args.coverage and _mathnet_coverage(row, args.lang) != args.coverage:
        return False
    return True


def _mathnet_snippet(text, keyword, width=120):
    """命中位置两侧的单行片段；无关键词时取正文开头。"""
    flat = re.sub(r'\s+', ' ', text).strip()
    if not flat:
        return '（正文为空）'
    pos = flat.casefold().find((keyword or '').casefold()) if keyword else 0
    if pos < 0:
        return None
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    snippet = flat[start:end]
    return ('…' if start else '') + snippet + ('…' if end < len(flat) else '')


def mathnet_search(args):
    """以 index.jsonl 为清单检索全量语料；不递归目录，也不跟随符号链接。"""
    import json

    corpus_root = os.path.abspath(args.root)
    default_root = os.path.join(ROOT, 'mathnet-full')
    display_root = ('mathnet-full' if corpus_root == default_root
                    else (args.root.rstrip(os.sep) or args.root))
    index_path = os.path.join(corpus_root, 'index.jsonl')
    if not os.path.isdir(corpus_root):
        print(f'全量语料目录不存在：{display_root}/')
        print(f'请重建：{MATHNET_REBUILD_CMD}')
        return 0
    if not os.path.isfile(index_path):
        print(f'全量索引不存在：{os.path.join(display_root, "index.jsonl")}')
        print(f'请重建：{MATHNET_REBUILD_CMD}')
        return 0

    matches = []
    visited_ids = set()
    missing_language = 0
    failed_language = 0
    invalid_rows = 0
    with open(index_path, encoding='utf-8') as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            if not isinstance(row, dict):
                invalid_rows += 1
                continue
            mathnet_id = str(row.get('mathnet_id') or '')
            rel_source = row.get('path')
            if not mathnet_id or not isinstance(rel_source, str) or not _mathnet_row_matches(row, args):
                continue
            # index.jsonl 契约是一题一行；即使索引损坏出现重复，也绝不读取第二个挂载路径。
            if mathnet_id in visited_ids:
                continue
            visited_ids.add(mathnet_id)

            rel_variant = _mathnet_variant_path(rel_source, args.lang)
            variant_path = os.path.abspath(os.path.join(corpus_root, rel_variant))
            coverage_state = _mathnet_coverage(row, args.lang)
            try:
                inside_corpus = os.path.commonpath([corpus_root, variant_path]) == corpus_root
            except ValueError:
                inside_corpus = False
            if not inside_corpus or os.path.realpath(variant_path) != variant_path:
                invalid_rows += 1
                continue
            if coverage_state == 'failed':
                # failed 是 translation.json/index.jsonl 的权威状态；即使旧版本曾留下
                # variant 文件，也不能把过期译文作为失败档结果展示。
                failed_language += 1
                # 没有关键词时，missing/failed 均可作为状态清单使用；两档不可互相代替。
                if args.keyword or args.coverage != 'failed':
                    continue
                snippet = f'（{args.lang} 译文校验失败）'
            elif not os.path.isfile(variant_path):
                missing_language += 1
                if args.keyword or args.coverage != 'missing':
                    continue
                snippet = f'（{args.lang} 版本缺失）'
            else:
                try:
                    with open(variant_path, encoding='utf-8') as variant_fh:
                        body = variant_fh.read()
                except OSError:
                    missing_language += 1
                    continue
                snippet = _mathnet_snippet(body, args.keyword)
                if snippet is None:
                    continue

            shown_path = os.path.join(display_root, rel_variant)
            matches.append((mathnet_id, snippet, shown_path))

    shown = matches[:args.limit]
    for mathnet_id, snippet, path in shown:
        print(f'{mathnet_id}  {path}')
        print(f'  {snippet}')

    truncated = len(matches) - len(shown)
    if truncated:
        print(f'\n匹配 {len(matches)} 题，显示 {len(shown)} 题；已截断 {truncated} 题（用 --limit 调整）')
    else:
        print(f'\n共 {len(matches)} 题')
    if missing_language:
        label = {'orig': '原文', 'en': '英文', 'zh': '中文'}[args.lang]
        print(f'注意：筛选范围内有 {missing_language} 题缺少{label}版本，未作为全文命中。')
        if args.lang == 'orig':
            print(f'请重建：{MATHNET_REBUILD_CMD}')
        else:
            print(f'请生成：{TRANSLATION_EXPORT_CMD}')
    if failed_language:
        label = {'orig': '原文', 'en': '英文', 'zh': '中文'}[args.lang]
        print(f'注意：筛选范围内有 {failed_language} 题{label}译文校验失败，未作为全文命中。')
    if invalid_rows:
        print(f'注意：index.jsonl 有 {invalid_rows} 行无效或路径越界；请重建：{MATHNET_REBUILD_CMD}')
    return 0


def stats(problems):
    diff = {}
    for p in problems:
        fm = p['fm'] or {}
        d, c = fm.get('difficulty'), p['cat']
        diff.setdefault(d, {}).setdefault(c, 0)
        diff[d][c] += 1
    header = f"{'':<12}" + ''.join(f'{c[:8]:>10}' for c in CATEGORIES) + f"{'合计':>8}"
    print('难度分布'); print(header)
    for d in sorted(diff):
        row = diff[d]
        total = sum(row.values())
        print(f"{'★' * d:<12}" + ''.join(f'{row.get(c, 0):>10}' for c in CATEGORIES) + f'{total:>8}')
    print(f"{'合计':<12}" + ''.join(f"{sum(diff[d].get(c, 0) for d in diff):>10}" for c in CATEGORIES) + f'{len(problems):>8}')


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


def _plan_quota(profile, n):
    """按赛事权重计算各星级需求；与实际抽题使用同一份配额。"""
    total_w = sum(profile.values())
    return {d: max(1, round(n * w / total_w)) for d, w in profile.items()}


def plan(problems, args):
    import random
    profile = PLAN_PROFILES.get(args.target)
    if not profile:
        print(f'未知目标赛事。可选：{"、".join(PLAN_PROFILES)}')
        sys.exit(2)
    rng = random.Random(args.seed)
    n = args.n
    category = getattr(args, 'category', None)
    # 每星级配额（至少凑满 n 题）
    quota = _plan_quota(profile, n)
    pool = {}
    for p in problems:
        if category and p['cat'] != category:
            continue
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
    scope = f'　板块：{category}' if category else ''
    print(f'目标：{args.target}{scope}　共 {len(picked)} 题（seed={args.seed}，换一套用 --seed）\n')
    shortfalls = [(d, k, len(pool.get(d, []))) for d, k in sorted(quota.items())
                  if len(pool.get(d, [])) < k]
    for d, needed, available in shortfalls:
        print(f'⚠ 库存缺口：{"★" * d} 需要 {needed} 道，库存 {available} 道，缺 {needed - available} 道')
    if shortfalls:
        print()
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


GAP_SHARE = 0.35          # coach --from-gaps：缺口名额占比（任务档 30–40% 取中值，round 后落在档内）
GAPS_MIN_EVIDENCE = 10    # 证据下限：attempts+assessments 合计不足则能力图立不住，整体降级回纯配比


def load_gap_picks(problems, profile, excluded):
    """maps/gaps.json 缺口队列 → 有序候选 fm 列表（薄弱优先、队列序即拓扑序，跨周就地消耗）。

    单一正本：只读 gaps 台账落盘的状态与选题，不 import student_profile 重算掌握值。
    台账缺失/不可解析/证据不足 → 打印原因并返回空表（coach 整体降级为纯配比轮转）。
    """
    import json
    path = os.path.join(ROOT, 'maps', 'gaps.json')
    try:
        data = json.load(open(path, encoding='utf-8'))
    except OSError:
        print('（--from-gaps）maps/gaps.json 不存在——先跑 uv run python scripts/bank.py gaps；'
              '本次整体降级为纯配比轮转\n')
        return []
    except ValueError:
        print('（--from-gaps）maps/gaps.json 无法解析——重建：uv run python scripts/bank.py gaps；'
              '本次整体降级为纯配比轮转\n')
        return []
    total = data.get('evidence_total') or 0
    if total < GAPS_MIN_EVIDENCE:
        print(f'（--from-gaps）证据不足：attempts+assessments 共 {total} 条（<{GAPS_MIN_EVIDENCE}），'
              f'能力图还立不住——本次整体降级为纯配比轮转\n')
        return []
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    entries = [e for e in data.get('nodes') or [] if e.get('queue_rank') is not None]
    # 台账队列序本身已按前置拓扑排（上游在前，blocked_by 只作标注）；这里再全局提薄弱
    entries.sort(key=lambda e: (0 if e.get('status') == '薄弱' else 1, e['queue_rank']))
    picked, seen = [], set()
    for e in entries:
        for pk in e.get('picks') or []:
            fm = fmmap.get(pk.get('id'))
            if fm is None or fm['id'] in seen or fm['id'] in excluded:
                continue
            if fm.get('difficulty') not in profile:   # 星级配比约束：目标赛事外的档位不进周计划
                continue
            seen.add(fm['id'])
            picked.append(fm)
    return picked


def pick_week(pool, profile, n, rng, gap_fms=None):
    """从候选池按星级配比取一周题目（就地消耗 pool），按（难度, 题号）排序返回。

    gap_fms 非空时先从缺口队列头部取约 GAP_SHARE 名额（就地消耗，占用对应星级配额），
    剩余名额仍走板块轮转——整周星级构成与纯配比完全一致。
    """
    total_w = sum(profile.values())
    quota = {d: max(1, round(n * wt / total_w)) for d, wt in profile.items()}
    picked = []
    if gap_fms:
        # 先剔除已不在池里的队列项：上周经轮转进过计划的题仍留在 gap_fms，
        # 不剔除会被缺口循环跨周再排一次（pool 去重那步拦不住已出池的题）
        gap_fms[:] = [f for f in gap_fms if f in pool.get(f['difficulty'], [])]
    if gap_fms:
        k_gap = max(1, round(n * GAP_SHARE))
        while gap_fms and len(picked) < k_gap:
            fm = next((f for f in gap_fms if quota.get(f['difficulty'], 0) > 0), None)
            if fm is None:
                break
            gap_fms.remove(fm)
            quota[fm['difficulty']] -= 1
            if fm in pool.get(fm['difficulty'], []):
                pool[fm['difficulty']].remove(fm)
            picked.append(fm)
    for d, k in sorted(quota.items()):
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
    gap_fms = load_gap_picks(problems, profile, excluded) if getattr(args, 'from_gaps', False) else []
    print(f'=== 教练周计划 | 目标 {args.target} | {args.weeks} 周 × 每周 {args.n} 题 | seed={args.seed} ===\n')
    if gap_fms:
        print(f'缺口名额：每周约 {round(GAP_SHARE * 100)}% 出自 maps/gaps.json 队列（薄弱优先、上游在前，'
              f'标〔缺口〕），其余按配比轮转。\n')
    print('攻坚纪律：限时独立攻坚（' + '，'.join(f'★{d}≤{m}min' for d, m in sorted(TIME_LIMIT.items()) if d in profile)
          + '）；卡住按「提示阶梯」逐级解锁，每级之间再战 15 分钟；无论成败必须 spar finish 落账。\n')
    week1 = []
    for w in range(1, args.weeks + 1):
        print(f'—— 第 {w} 周 ——')
        gap_before = {fm['id'] for fm in gap_fms}
        picked = pick_week(pool, profile, args.n, rng, gap_fms)
        gap_used = gap_before - {fm['id'] for fm in gap_fms}
        for fm in picked:
            mark = '〔缺口〕' if fm['id'] in gap_used else ''
            print(f"  {fm['id']}  {'★' * fm['difficulty']:<5} {fm.get('contest') or '?':<6} {fm['title']}{mark}"
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


def load_prereq():
    """taxonomy/prereq.yml → {'板块/节点': ['板块/节点', ...]}；文件缺失返回 None（与 load_registry 同款约定）。"""
    path = os.path.join(ROOT, 'taxonomy', 'prereq.yml')
    if not os.path.exists(path):
        return None
    data = yaml.safe_load(open(path, encoding='utf-8')) or {}
    return {str(k): [str(v) for v in (vs or [])]
            for k, vs in (data.get('prereq') or {}).items()}


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


MAP_STUDENT = 'self'  # 掌握层固定读 self（spar finish 的落账学生；多学生场景看 profile <id> --html）


def _map_mastery(problems, reg):
    """指示图掌握层：复用 student_profile 的证据装配与状态阈值（不复制折算逻辑）。

    学生无档案或零证据 → None（前端隐藏开关，指示图退回纯供给视图）。
    """
    prof = stp.load_student(MAP_STUDENT)
    if prof is None:
        return None
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    evidence, _, _ = stp.build_evidence(
        prof, stp.load_assessments(MAP_STUDENT), sp.load_attempts_v2(), fmmap, reg, resolve_topic)
    if not evidence:
        return None
    return {'student': MAP_STUDENT, 'evidence': len(evidence),
            'nodes': {f'{c}/{n}': {'status': st['status'], 'mastery': st['mastery'], 'n': st['n']}
                      for (c, n), st in stp.node_table(evidence, reg).items()}}


def gen_map(problems):
    import json, datetime
    reg = load_registry()
    if reg is None:
        print('缺 taxonomy/registry.yml')
        sys.exit(2)
    # 跨界题的他板块标签按「节点归属板块」落位（SPEC §1 允许并列第二板块节点），
    # 判定口径与 lint/registry_report 一致：本板块解析不到就全表找，找不到才算未注册。
    all_nodes = {cat: {n: {'name': n, 'key': f'{cat}/{n}',
                           'stars': {str(i): 0 for i in range(1, 6)}, 'problems': []}
                       for n in (reg.get(cat) or {})} for cat in CATEGORIES}
    unresolved = []
    for p in problems:
        fm = p['fm'] or {}
        cat = fm.get('category')
        hit = set()
        for t in fm.get('topics', []) or []:
            node = resolve_topic(reg, cat, t)
            owner = cat if node else next((c for c in CATEGORIES if resolve_topic(reg, c, t)), None)
            if owner is None:
                unresolved.append((fm.get('id'), t))
            else:
                hit.add((owner, node or resolve_topic(reg, owner, t)))
        for owner, node in hit:
            d = fm.get('difficulty')
            nd = all_nodes[owner][node]
            nd['stars'][str(d)] += 1
            nd['problems'].append({'id': fm['id'], 'd': d, 't': fm.get('title', ''),
                                   'c': fm.get('contest', ''), 'y': fm.get('year', '')})
    # 供给层：候选池按 节点×估级 聚合（计数正本 gap_counts，与 candidates --gaps / gaps 台账同源）；
    # 池子缺失（clone 后常态）→ 节点不带 supply，前端按 has_supply 在页头提示
    rows = load_candidates() if os.path.exists(_candidates_path()) else None
    cand_map = gap_counts(problems, rows, reg)[1] if rows is not None else {}
    cats = []
    for cat in CATEGORIES:
        for node, nd in all_nodes[cat].items():
            nd['problems'].sort(key=lambda x: (-x['d'], x['id']))
            nd['total'] = len(nd['problems'])
            if rows is not None:
                nd['supply'] = {str(d): v for d, v in sorted(cand_map.get((cat, node), {}).items())}
        cats.append({'cat': cat, 'label': CAT_LABEL[cat],
                     'nodes': [all_nodes[cat][n] for n in (reg.get(cat) or {})]})
    # 前置依赖边（正本 taxonomy/prereq.yml，校验在 doclint——map 只消费，端点不认识就跳过）
    known = {f'{cat}/{n}' for cat in CATEGORIES for n in (reg.get(cat) or {})}
    edges = [{'from': v, 'to': k} for k, vs in (load_prereq() or {}).items()
             for v in vs if k in known and v in known]
    data = {'generated': datetime.date.today().isoformat(), 'total': len(problems),
            'cats': cats, 'edges': edges,
            'has_supply': rows is not None, 'mastery': _map_mastery(problems, reg)}
    os.makedirs(os.path.join(ROOT, 'maps'), exist_ok=True)
    with open(os.path.join(ROOT, 'maps', 'map_data.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tpl = open(os.path.join(ROOT, 'scripts', 'map_template.html'), encoding='utf-8').read()
    with open(os.path.join(ROOT, 'maps', '指示图.html'), 'w', encoding='utf-8') as f:
        f.write(tpl.replace('__MAP_DATA__', json.dumps(data, ensure_ascii=False)))
    n_nodes = sum(len(c['nodes']) for c in cats)
    print(f'maps/指示图.html 已生成：{len(problems)} 题 → {n_nodes} 个知识点节点')
    if rows is None:
        print(f'候选池不存在——供给层已省略；重建：{CANDIDATES_REBUILD_CMD}')
    m = data['mastery']
    print(f"掌握层：学生 {m['student']} 证据 {m['evidence']} 条" if m else
          '掌握层：无学生证据，已省略（student add + assess/spar 后重跑 map）')
    if unresolved:
        print(f'未注册 topic {len(unresolved)} 处（跑 lint 看明细）')


# ---------------- 文档一致性校验 doclint ----------------
# 扫描范围：全仓 .md（跳过隐藏目录、依赖目录与 gitignore 派生语料 mathnet-full/）；
# docs/archive/ 为历史存档白名单（免禁词检查）。
DOCLINT_FORBIDDEN = ['origin/main', 'scripts/symphony-start.sh', 'docs/sources/']
DOCLINT_EXCLUDED_DIRS = {'node_modules', 'mathnet-full'}
TAXONOMY_BOARDS = {'algebra': 'algebra.md', 'combinatorics': 'combinatorics.md',
                   'geometry': 'geometry.md', 'number-theory': 'number-theory.md'}
TRAINING_CONTRACT_DOCS = ('docs/学生手册.md', 'docs/教练手册.md')
TRAINING_CONTRACT_SECTIONS = ('intervals', 'time-limits', 'hint-cooldown', 'graduate-streak')


def _walk_md():
    # mathnet-full/ 是 mathnet_export.py 的本地导出产物（gitignore，2.7 万题目目录）：
    # 数据集原文不受本仓文档规范约束，扫进来会把 doclint 从 ~300 文件拖到 2.8 万
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames
                            if not d.startswith('.') and d not in DOCLINT_EXCLUDED_DIRS)
        for fn in sorted(filenames):
            if fn.endswith('.md'):
                yield os.path.join(dirpath, fn)


def _marked_contract_block(text, rel, section, errors):
    """提取手册中唯一的训练契约标记块；缺失/重复/未闭合均显式报错。"""
    start = f'<!-- training-contract:{section}:start -->'
    end = f'<!-- training-contract:{section}:end -->'
    if text.count(start) != 1 or text.count(end) != 1:
        errors.append(f'{rel}: 训练契约标记 {section} 必须且只能出现一对')
        return None
    start_at = text.index(start) + len(start)
    end_at = text.index(end)
    if end_at < start_at:
        errors.append(f'{rel}: 训练契约标记 {section} 顺序错误')
        return None
    return text[start_at:end_at]


def _parse_interval_contract(block):
    """读取标记块内 Markdown 表格的首列结果与末列天数，允许中间保留读者说明列。"""
    values = {}
    for line in block.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 2:
            continue
        key_match = re.fullmatch(r'`([^`]+)`', cells[0])
        value_match = re.fullmatch(r'(\d+)\s*天', cells[-1])
        if key_match and value_match:
            values[key_match.group(1)] = int(value_match.group(1))
    return values


def _parse_graduate_contract(block):
    matches = re.findall(r'连续\s*(\d+)\s*次\s*`independent_ok`', block)
    return {'independent_ok': int(matches[-1])} if matches else {}


def _parse_hint_cooldown_contract(block):
    matches = re.findall(r'再独立奋战\s*(\d+)\s*分钟', block)
    return {'每级': int(matches[-1])} if matches else {}


def _training_contract_errors():
    """两手册的刻意数值抄录必须与 spar_session 契约常量逐项一致。"""
    errors = []
    parsers = {
        'intervals': _parse_interval_contract,
        'time-limits': lambda block: {
            int(m.group('key')): int(m.group('value'))
            for m in re.finditer(r'★(?P<key>[1-5])\s*≤\s*(?P<value>\d+)\s*(?:min)?', block)
        },
        'hint-cooldown': _parse_hint_cooldown_contract,
        'graduate-streak': _parse_graduate_contract,
    }
    expected = {
        'intervals': sp.INTERVALS,
        'time-limits': sp.TIME_LIMIT,
        'hint-cooldown': {'每级': sp.HINT_COOLDOWN_MIN},
        'graduate-streak': {'independent_ok': sp.GRADUATE_STREAK},
    }
    labels = {
        'intervals': '复习间隔',
        'time-limits': '攻坚限时',
        'hint-cooldown': '提示冷却',
        'graduate-streak': '毕业连击数',
    }
    units = {'intervals': '天', 'time-limits': '分钟',
             'hint-cooldown': '分钟', 'graduate-streak': '次'}

    for rel in TRAINING_CONTRACT_DOCS:
        path = os.path.join(ROOT, *rel.split('/'))
        try:
            text = open(path, encoding='utf-8').read()
        except OSError:
            errors.append(f'{rel}: 训练契约手册缺失')
            continue
        for section in TRAINING_CONTRACT_SECTIONS:
            block = _marked_contract_block(text, rel, section, errors)
            if block is None:
                continue
            actual = parsers[section](block)
            wanted = expected[section]
            for key in wanted.keys() - actual.keys():
                errors.append(f'{rel}: {labels[section]}缺少「{key}」')
            for key in actual.keys() - wanted.keys():
                errors.append(f'{rel}: {labels[section]}多出未知项「{key}」')
            for key in wanted.keys() & actual.keys():
                if actual[key] != wanted[key]:
                    errors.append(f'{rel}: {labels[section]}「{key}」应为 {wanted[key]}{units[section]}，'
                                  f'手册写为 {actual[key]}{units[section]}')
    return errors


def _strip_math(text):
    """挖掉 $$…$$ 与行内 $…$ 数学环境：公式里的 [..](..) 是数学记号不是链接
    （实例：A-047 官方解的 c[(x+r)-(x+r-d-1)](x+r-1)），照录内容不可改，只能让扫描避开。"""
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.S)
    return re.sub(r'\$[^$\n]+\$', '', text)


def doclint():
    errors = []
    link_re = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
    n_files = 0
    for path in _walk_md():
        n_files += 1
        rel = os.path.relpath(path, ROOT)
        text = open(path, encoding='utf-8').read()
        # a) 死链：相对路径引用的文件必须存在（跳过 URL/锚点/绝对路径；数学环境不参与）
        for m in link_re.finditer(_strip_math(text)):
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
    # d) 前置依赖图：端点必须是 registry 规范节点（<板块>/<节点名>，不走别名解析），且全图无环
    pre = load_prereq()
    if pre is None:
        errors.append('taxonomy/prereq.yml 缺失')
    elif reg is not None:
        def _known(key):
            cat, _, node = key.partition('/')
            return cat in CATEGORIES and node in (reg.get(cat) or {})
        nodes = set(pre)
        for k, vs in pre.items():
            nodes.update(vs)
            for ep in dict.fromkeys([k, *vs]):
                if not _known(ep):
                    errors.append(f'taxonomy/prereq.yml: 端点「{ep}」不是 registry 规范节点'
                                  f'（须写 <板块>/<节点名>，别名不合法）')
            if k in vs:
                errors.append(f'taxonomy/prereq.yml: 「{k}」把自己列为前置（自环）')
        # 正反两遍 Kahn 拓扑排序取残留交集＝真正的环上节点（单向残留会把环的下游也算进去）
        def _kahn_leftover(edges):
            indeg = {n: 0 for n in nodes}
            succ = {n: [] for n in nodes}
            for a, b in edges:
                succ[a].append(b)
                indeg[b] += 1
            queue = [n for n in nodes if indeg[n] == 0]
            while queue:
                for m in succ[queue.pop()]:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        queue.append(m)
            return {n for n in nodes if indeg[n] > 0}
        fwd = [(v, k) for k, vs in pre.items() for v in vs if v != k]
        cyc = sorted(_kahn_leftover(fwd) & _kahn_leftover([(b, a) for a, b in fwd]))
        if cyc:
            errors.append('taxonomy/prereq.yml: 依赖图有环，环上节点：' + '、'.join(cyc))
    # e) 两手册完整抄录的训练契约数值必须与 spar_session 唯一正本一致
    errors.extend(_training_contract_errors())
    if errors:
        print('\n'.join(errors))
        print(f'\nDOCLINT FAILED: {len(errors)} 个问题')
        return 1
    print(f'DOCLINT OK: {n_files} 个 md 文件（死链/禁词/树一致性/依赖图/训练契约）全部通过')
    return 0


# ---------------- 外链检查 linkcheck ----------------
# 刻意不并入 lint/doclint：联网慢且外站抖动不该挡入库。CI 按月跑（.github/workflows/linkcheck.yml），
# 处置纪律的正本在 docs/入库SOP-MathNet.md 凭证纪律节（修复或换 archive.org 快照，不得静默删引用）。


def _fetch_status(url):
    """HEAD 优先（403/405/网络错时降级 GET）→ HTTP 状态码；彻底失败 → None。"""
    import urllib.error
    import urllib.request
    for method in ('HEAD', 'GET'):
        req = urllib.request.Request(url, method=method,
                                     headers={'User-Agent': 'olympiad-bank-linkcheck/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            if method == 'HEAD' and e.code in (403, 405):
                continue
            return e.code
        except OSError:
            if method == 'HEAD':
                continue
            return None
    return None


def collect_external_links():
    """全仓 md 正文的 http(s) 外链 → {url: [出现位置]}。docs/archive/ 豁免（史料链接允许腐）。"""
    link_re = re.compile(r'\[[^\]]*\]\((https?://[^)\s]+)\)')
    links = {}
    for path in _walk_md():
        rel = os.path.relpath(path, ROOT)
        if rel.startswith(os.path.join('docs', 'archive') + os.sep):
            continue
        for i, line in enumerate(open(path, encoding='utf-8').read().splitlines(), 1):
            for m in link_re.finditer(line):
                links.setdefault(m.group(1), []).append(f'{rel}:{i}')
    return links


def linkcheck(fetch=None):
    fetch = fetch or _fetch_status
    links = collect_external_links()
    dead = []
    for url in sorted(links):
        status = fetch(url)
        ok = status is not None and status < 400
        print(f'{"ok  " if ok else "DEAD"} {status if status is not None else "ERR "}  {url}')
        if not ok:
            dead.append(url)
    if dead:
        print(f'\nLINKCHECK FAILED: {len(dead)} 个死链（处置纪律见 docs/入库SOP-MathNet.md 凭证纪律节）')
        for url in dead:
            for loc in links[url]:
                print(f'  {loc}: {url}')
        return 1
    print(f'LINKCHECK OK: {len(links)} 个外链全部可达')
    return 0


# ---------------- MathNet 候选池 ----------------
CANDIDATES_REBUILD_CMD = 'uv run --group mathnet python scripts/mathnet_ingest.py'


def _candidates_path():
    return os.path.join(ROOT, 'candidates', 'mathnet.jsonl')


def load_candidates():
    import json
    if not os.path.exists(_candidates_path()):
        print(f'候选池不存在。先构建：{CANDIDATES_REBUILD_CMD}')
        sys.exit(2)
    return [json.loads(l) for l in open(_candidates_path(), encoding='utf-8') if l.strip()]


def apply_grade_floor(rows):
    """滤掉 est 低于学段下界的候选（SPEC §4）→ (保留行, 被滤条数)。

    评审池与采购单共用：★1 送评审是白烧预算，计进采购单是虚报可补量。
    浏览用的 candidates 不走这里——它允许显式点名低档查看，硬闸在入库路径。
    """
    kept = [r for r in rows if r['difficulty_est'] >= MIN_DIFFICULTY]
    return kept, len(rows) - len(kept)


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
    # 学段下界（SPEC §4）只作默认值：★1 进不了库，不该默认占选题视野。
    # 但 candidates 是给人看的浏览工具——显式点名低档时照出并警告，否则赛名表校准这类
    # 需要翻低档候选的活就没法做了。硬闸在入库路径（mathnet_import.below_floor），不在这里。
    lo, hi = _parse_diff(args.difficulty) if args.difficulty else (MIN_DIFFICULTY, 5)
    if lo < MIN_DIFFICULTY:
        print(f'注：est ★<{MIN_DIFFICULTY} 的候选入不了库（学段下界，SPEC §4）；'
              f'本次按你显式指定的下限 ★{lo} 照出，仅供查看与校准')
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


def gap_counts(problems, rows, reg):
    """（板块,节点）→ 库内 {题号: 难度} 与候选 {估级: 题数}。candidates --gaps 与 gaps 台账共用的计数正本。

    rows=None（候选池缺失）时候选侧返回空表——调用方自行决定按 0 打印还是置 null。
    """
    bank = {}
    for p in problems:
        fm = p['fm'] or {}
        for t in fm.get('topics', []) or []:
            node = resolve_topic(reg, fm.get('category'), t)
            if node:
                bank.setdefault((fm['category'], node), {})[fm['id']] = fm.get('difficulty')
    cand = {}
    # 学段下界（SPEC §4）：★1 进不了库，计进采购单会虚报可补量
    ok, _ = apply_grade_floor([r for r in rows or [] if r['status'] == 'ok'])
    for r in ok:
        for node in r['topics']:
            k = (r['category'], node)
            by_star = cand.setdefault(k, {})
            by_star[r['difficulty_est']] = by_star.get(r['difficulty_est'], 0) + 1
    return bank, cand


def candidates_gaps(problems, rows):
    """45 节点采购单：库内现有 vs 候选可补（含中低星细分）。"""
    reg = load_registry()
    bank, cand = gap_counts(problems, rows, reg)
    print(f"{'板块':<6} {'知识点':<14} {'库内':>4} {'候选':>6} {'候选★≤3':>7}")
    for cat in CATEGORIES:
        for node in (reg.get(cat) or {}):
            k = (cat, node)
            b = len(bank.get(k, ()))
            c_all = sum(cand.get(k, {}).values())
            c_low = sum(v for d, v in cand.get(k, {}).items() if d <= 3)
            mark = ' ←缺' if b <= 2 else ''
            print(f'{CAT_LABEL[cat]:<6} {node:<14} {b:>4} {c_all:>6} {c_low:>7}{mark}')
    print('\n「←缺」= 库内 ≤2 题的薄弱节点；候选数为标签流估计，入库前须官方源核验。')


def cmd_gaps(problems, args):
    """统一缺口台账 → maps/gaps.json：学生缺口队列（student_profile.gap_queue 的落盘）
    × 库内/候选供给（candidates --gaps 同源计数），键空间统一为（板块, 节点）。"""
    import datetime, json
    reg = load_registry()
    if reg is None:
        print('缺 taxonomy/registry.yml')
        sys.exit(2)
    rows = None
    if os.path.exists(_candidates_path()):
        rows = load_candidates()
    else:
        print(f'候选池不存在（candidates/mathnet.jsonl）——cand 计数置 null；重建：{CANDIDATES_REBUILD_CMD}')
    bank_map, cand_map = gap_counts(problems, rows, reg)

    sid = args.student
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    prof = stp.load_student(sid)
    if prof is None:
        print(f'学生 {sid} 无档案——按零证据产出（节点状态多为未测）；'
              f'建档：uv run python scripts/bank.py student add {sid}')
        evidence = []
    else:
        evidence, _, _ = stp.build_evidence(
            prof, stp.load_assessments(sid), sp.load_attempts_v2(), fmmap, reg, resolve_topic)
    ntable = stp.node_table(evidence, reg)
    seen_refs = {e['ref'] for e in evidence}
    queue = stp.gap_queue(ntable, reg, problems, resolve_topic, seen_refs, load_prereq())
    rank = {(it['cat'], it['node']): (i, it) for i, it in enumerate(queue)}

    nodes = []
    for cat in CATEGORIES:
        for node in (reg.get(cat) or {}):
            k = (cat, node)
            st = ntable[k]
            bank_stars = {}
            for d in bank_map.get(k, {}).values():
                bank_stars[str(d)] = bank_stars.get(str(d), 0) + 1
            in_q = rank.get(k)
            nodes.append({
                'cat': cat, 'node': node,
                'status': st['status'], 'mastery': st['mastery'], 'n': st['n'],
                'bank': {'total': len(bank_map.get(k, {})),
                         'by_star': dict(sorted(bank_stars.items()))},
                'cand': None if rows is None else
                        {'total': sum(cand_map.get(k, {}).values()),
                         'by_star': {str(d): v for d, v in sorted(cand_map.get(k, {}).items())}},
                'queue_rank': in_q[0] if in_q else None,
                'picks': in_q[1]['picks'] if in_q else [],
                'blocked_by': (in_q[1].get('blocked_by') or []) if in_q else [],
            })
    data = {
        'generated': datetime.datetime.now().isoformat(timespec='seconds'),
        '_note': '生成产物，勿手改；重建：uv run python scripts/bank.py gaps（--student 换人）',
        'student': sid,
        'evidence_total': len(evidence),
        'nodes': nodes,
    }
    os.makedirs(os.path.join(ROOT, 'maps'), exist_ok=True)
    with open(os.path.join(ROOT, 'maps', 'gaps.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    n_weak = sum(1 for e in nodes if e['status'] == '薄弱')
    n_untested = sum(1 for e in nodes if e['status'] == '未测')
    print(f'maps/gaps.json 已生成：{len(nodes)} 节点（薄弱 {n_weak}｜未测 {n_untested}｜队列 {len(queue)} 项）'
          f'｜学生 {sid} 证据 {len(evidence)} 条')


# ---------------- 生成产物新鲜度自检 doctor ----------------
# 刻意不进 lint.sh：maps/ 与 candidates/ 是 gitignore 的本地缓存，clone 后缺失是常态，
# 不该挡提交。doctor 只读比对，绝不代跑重建（重建命令都在输出里）。
# simindex 的判据与重建命令正本在 similar_index.freshness_issues，这里只持有 maps/ 的。
MAP_REBUILD_CMD = 'uv run python scripts/bank.py map'


def doctor(problems):
    """生成产物新鲜度自检：逐项比对内嵌规模与现库题数。任一缺失/陈旧 → exit 1，全新鲜 → 0。"""
    import glob, json
    n_bank = len(problems)
    n_issue = 0

    def _load_json(path):
        try:
            data = json.load(open(path, encoding='utf-8'))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # a) maps/map_data.json：total vs 现库题数
    map_path = os.path.join(ROOT, 'maps', 'map_data.json')
    if not os.path.exists(map_path):
        print(f'maps/map_data.json：缺失（clone 后正常）——重建：{MAP_REBUILD_CMD}')
        n_issue += 1
    else:
        data = _load_json(map_path)
        if data is None:
            print(f'maps/map_data.json：无法解析——重建：{MAP_REBUILD_CMD}')
            n_issue += 1
        elif data.get('total') != n_bank:
            print(f"maps/map_data.json：陈旧（生成于 {data.get('generated') or '?'}，记 {data.get('total')} 题，"
                  f'现库 {n_bank} 题）——重建：{MAP_REBUILD_CMD}')
            n_issue += 1
        else:
            print(f"maps/map_data.json：新鲜（{n_bank} 题，生成于 {data.get('generated') or '?'}）")

    # b) candidates/simindex/：判据正本在 similar_index.freshness_issues（doctor 汇报计数，
    #    similar 查询守卫拒绝出结果，两处消费同一份判据）
    import similar_index
    issues, cfg = similar_index.freshness_issues(ROOT)
    for msg in issues:
        print(f'candidates/simindex/{msg}')
    n_issue += len(issues)
    if not issues:
        print(f"candidates/simindex/config.json：新鲜（bank_n={cfg.get('bank_n')}，"
              f"cand_n={cfg.get('cand_n')}，构建于 {cfg.get('built') or '?'}）")

    # c) maps/能力图-*.html：内嵌 __PROFILE_DATA__ 只有生成日期、无库内题数字段
    #    （见 scripts/profile_template.html），没有可靠的新鲜度判据——注明跳过，不计入问题数
    for path in sorted(glob.glob(os.path.join(ROOT, 'maps', '能力图-*.html'))):
        rel = os.path.relpath(path, ROOT)
        m = re.search(r'"generated":\s*"([^"]+)"', open(path, encoding='utf-8').read())
        print(f"{rel}：跳过（内嵌数据只有生成日期 {m.group(1) if m else '?'}、无题数字段可比对；"
              f'重建：uv run python scripts/bank.py profile <学生id> --html）')

    if n_issue:
        print(f'\nDOCTOR: {n_issue} 项缺失或陈旧（重建命令见上，doctor 不代跑）')
        return 1
    print('\nDOCTOR OK: 生成产物全部新鲜')
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('lint')
    sub.add_parser('doclint', help='全仓 md 文档校验：死链 / 禁词 / taxonomy 树 / 前置依赖图 / 训练契约')
    sub.add_parser('linkcheck', help='联网检查文档外链（不属于 lint；CI 按月跑）')
    q = sub.add_parser('query')
    q.add_argument('--difficulty', type=int)
    q.add_argument('--topic')
    q.add_argument('--contest')
    q.add_argument('--category', choices=CATEGORIES)
    q.add_argument('--unverified', action='store_true')
    ms = sub.add_parser('mathnet-search', help='按 index.jsonl 去重检索 MathNet 全量三语正文')
    ms.add_argument('keyword', nargs='?', help='全文关键词（不填时列出符合过滤条件的题）')
    ms.add_argument('--lang', default='orig', choices=['zh', 'en', 'orig'], help='检索版本（默认 orig 原文）')
    ms.add_argument('--topic', help='知识点子串')
    ms.add_argument('--category', choices=CATEGORIES)
    ms.add_argument('--difficulty', type=int, choices=[1, 2, 3, 4, 5], help='difficulty_est')
    ms.add_argument('--country', help='国家/地区子串')
    ms.add_argument('--coverage', choices=[*MATHNET_VARIANT_STATES, 'stale'])
    ms.add_argument('--limit', type=int, default=20, help='最多显示多少题（默认 20）')
    ms.add_argument('--root', default=os.path.join(ROOT, 'mathnet-full'), help=argparse.SUPPRESS)
    sub.add_parser('stats')
    pl = sub.add_parser('plan')
    pl.add_argument('--target', required=True)
    pl.add_argument('--category', choices=CATEGORIES)
    pl.add_argument('--n', type=int, default=12)
    pl.add_argument('--seed', type=int, default=1)
    co = sub.add_parser('coach')
    co.add_argument('--target', required=True)
    co.add_argument('--weeks', type=int, default=4)
    co.add_argument('--n', type=int, default=10)
    co.add_argument('--seed', type=int, default=1)
    co.add_argument('--save', action='store_true', help='把第 1 周选题写入 data/plan.json（spar next 按此出题）')
    co.add_argument('--from-gaps', dest='from_gaps', action='store_true',
                    help=f'约 {round(GAP_SHARE * 100)}%% 名额从 maps/gaps.json 缺口队列取（薄弱优先；'
                         f'证据不足 {GAPS_MIN_EVIDENCE} 条自动降级纯配比）')
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
    si.add_argument('--evidence', help='--confirm 必填：确认依据自由文本，写明出处与判断方式，'
                                       '如「AI双评审2026-08-06：均为根轴+圆幂引理链」')
    lg = sub.add_parser('log')
    lg.add_argument('--id', required=True)
    lg.add_argument('--result', required=True, choices=['ok', 'hard', 'fail'])
    lg.add_argument('--hints', type=int, default=0, choices=[0, 1, 2, 3])
    lg.add_argument('--date')
    lg.add_argument('--note')
    sub.add_parser('review')
    sub.add_parser('map')
    stu = sub.add_parser('student', help='学生档案：add <id> / list')
    stu.add_argument('action', help='add|list')
    stu.add_argument('sid', nargs='?', help='学生 id（小写字母/数字/连字符）')
    stu.add_argument('--name')
    stu.add_argument('--grade')
    stu.add_argument('--target')
    stu.add_argument('--alias', action='append', help='绑定 attempts.jsonl 的 student 名（可多次；首个学生用 --alias self）')
    stu.add_argument('--note')
    asx = sub.add_parser('assess', help='测评波次录入（基础值来源）')
    asx.add_argument('sid', help='学生 id')
    asx.add_argument('--wave', required=True, help='波次名，如 基线-1')
    asx.add_argument('--score', required=True, type=float, help='0–1：对=1、半对=0.5、错=0')
    asx.add_argument('--id', help='库内题号（自动补板块/知识点/难度）')
    asx.add_argument('--source', help='外部题标识，如 "AMC10 2023 P15"（须配 --category/--difficulty/--topics）')
    asx.add_argument('--category', choices=CATEGORIES)
    asx.add_argument('--difficulty', type=int, choices=[1, 2, 3, 4, 5])
    asx.add_argument('--topics', help='知识点，逗号分隔（registry 节点名或别名）')
    asx.add_argument('--date')
    asx.add_argument('--note')
    pf = sub.add_parser('profile', help='能力图：基础值走势 + 节点状态 + 补齐队列 + 细分建议')
    pf.add_argument('sid', help='学生 id')
    pf.add_argument('--html', action='store_true', help='另生成 maps/能力图-<id>.html')
    gp = sub.add_parser('gaps', help='统一缺口台账：学生缺口队列 × 库内/候选供给 → maps/gaps.json')
    gp.add_argument('--student', default='self', help='学生 id（默认 self）')
    sub.add_parser('doctor', help='生成产物新鲜度自检（maps/ 与 simindex；只读，任一缺失/陈旧 exit 1）')
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
    if args.cmd == 'linkcheck':
        sys.exit(linkcheck())
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
    if args.cmd == 'mathnet-search':
        sys.exit(mathnet_search(args))
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
    elif args.cmd == 'student':
        stp.cmd_student(problems, args)
    elif args.cmd == 'assess':
        stp.cmd_assess(problems, load_registry(), resolve_topic, args)
    elif args.cmd == 'profile':
        stp.cmd_profile(problems, load_registry(), resolve_topic, args, load_prereq())
    elif args.cmd == 'gaps':
        cmd_gaps(problems, args)
    elif args.cmd == 'doctor':
        sys.exit(doctor(problems))
    elif args.cmd == 'candidates':
        candidates_cmd(problems, args)
    else:
        stats(problems)


if __name__ == '__main__':
    main()
