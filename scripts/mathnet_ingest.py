#!/usr/bin/env python3
"""MathNet 候选池构建：把 ShadenA/MathNet 全量加工成 candidates/mathnet.jsonl

用法：
  uv run --group mathnet python scripts/mathnet_ingest.py              # 全量构建
  uv run --group mathnet python scripts/mathnet_ingest.py --selfcheck  # 只校验两张表自洽

输入：HF 本地缓存的 ShadenA/MathNet（all config）+ taxonomy/mathnet_map.yml + taxonomy/contest_tiers.yml
输出：candidates/mathnet.jsonl（gitignore，可随时重建）——只含元数据与题面前 80 字预览，不含全文。
确定性：同一数据集快照 + 同版本两张表 + 同一入库/评审快照（in_bank_snapshot 的输入）→ 输出逐字节一致。
"""
import argparse, glob, json, os, re, sys, unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(ROOT, 'taxonomy', 'mathnet_map.yml')
TIER_PATH = os.path.join(ROOT, 'taxonomy', 'contest_tiers.yml')
OUT_PATH = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
PROBLEMS_GLOB = os.path.join(ROOT, 'problems', '*', '*.md')
VERDICTS_GLOB = os.path.join(ROOT, 'data', 'review', '*', 'verdicts.json')

# 板块平票裁决优先序（特异性从高到低；Algebra 标签常为工具位）
TIE_ORDER = ['number-theory', 'geometry', 'combinatorics', 'algebra']
# problem_type 封顶（就低不就高：只降不升）
PTYPE_CAP = {'MCQ': 2, 'final answer only': 3}
KEYWORD_SEARCH_IN = {'problem', 'solutions', 'both'}


def load_yaml(path):
    import yaml
    return yaml.safe_load(open(path, encoding='utf-8'))


def generic_norm(s):
    """标签路径的通用归一化：NFC、弯引号转直、压空格、统一 ' > ' 分隔。"""
    s = unicodedata.normalize('NFC', s.strip())
    s = s.replace('’', "'").replace('‘', "'")
    s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\s*>\s*', ' > ', s)
    return s


def make_path_normalizer(norm_rules):
    """normalize 段按序链式应用（第 5→6 条是链式：先并空格版、再并短版）。"""
    pairs = [(generic_norm(r['from']), generic_norm(r['to'])) for r in norm_rules]

    def norm(raw):
        cur = generic_norm(raw)
        for f, t in pairs:
            if cur == f:
                cur = t
        return cur
    return norm


def norm_comp(c):
    """赛名归一化：与 contest_tiers.yml 表头约定一致（小写、去年份/序数/罗马数字/标点）。"""
    if not c:
        return None
    c = unicodedata.normalize('NFC', c).lower().strip()
    c = re.sub(r'\b(19|20)\d{2}\b', ' ', c)
    c = re.sub(r'\b\d{1,3}(st|nd|rd|th)\b', ' ', c)
    c = re.sub(r'\b[ivxl]{1,6}\b', ' ', c)
    c = re.sub(r'[\-–—_/,\.]', ' ', c)
    c = re.sub(r'\s+', ' ', c).strip()
    return c or None


# 届数序数（英文拼写）——只剥赛名开头的，句中的 "third round" 是轮次信息必须保留
_ORD_WORDS = (r'first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|'
              r'thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|'
              r'thirtieth|fortieth|fiftieth|sixtieth')
_LEAD_ORD = re.compile(rf'^(the\s+)?({_ORD_WORDS})\s+(?!round|stage|selection|day|problem)')


def comp_variants(cn):
    """赛名的回退写法：原名 → 去开头 the → 去开头届数序数。用于 tier 表精确匹配的重试。"""
    seen, out = set(), []
    for v in (cn, re.sub(r'^the\s+', '', cn or ''), _LEAD_ORD.sub('', cn or '')):
        v = (v or '').strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def key_form(s):
    """表键与输入的双向归一：去开头 the 与届数序数，用于精确匹配的查找键。"""
    return _LEAD_ORD.sub('', re.sub(r'^the\s+', '', (s or '').strip())).strip()


# 家族回退的整词子串匹配下限：短于此的键只允许显式白名单（知名缩写），防止泛词误配
FAMILY_MIN_LEN = 8
FAMILY_SHORT_OK = {'hmmt', 'imo', 'egmo', 'apmo', 'jbmo', 'rmm', 'usamo', 'usajmo', 'aime', 'cmo', 'tst'}


def family_match(cn, tier_keys_by_len):
    """家族回退：知名赛事的长尾写法（如 'hmmt invitational competition'）按最长的表内整词子串归档。"""
    for key in tier_keys_by_len:
        if len(key) < FAMILY_MIN_LEN and key not in FAMILY_SHORT_OK:
            continue
        if re.search(rf'(?<![a-z]){re.escape(key)}(?![a-z])', cn):
            return key
    return None


def extract_year(c):
    m = re.search(r'\b(19|20)\d{2}\b', c or '')
    return int(m.group(0)) if m else None


def compile_modifiers(mods):
    """把 contest_tiers.yml 的 fallback_modifiers 编译成 (regex, kind, value, low_conf)。
    effect 语法：「直接定为N」（命中即终止）/「+1（上限5）」/「-1（下限1）」/「-0（…low-conf）」。
    """
    out = []
    for m in mods:
        eff = m['effect']
        rx = re.compile(m['pattern'])
        if eff.startswith('直接定为'):
            out.append((rx, 'set', int(eff[4:5]), False))
        elif eff.startswith('+'):
            out.append((rx, 'delta', int(eff[1]), False))
        elif eff.startswith('-0'):
            out.append((rx, 'noop', 0, True))
        elif eff.startswith('-'):
            out.append((rx, 'delta', -int(eff[1]), False))
        else:
            raise ValueError(f'无法解析 modifier effect: {eff!r}')
    return out


def grade(comp_norm, ptype, tiers, modifiers, default, tier_keys_by_len=()):
    """难度估级：tier 表命中 → high；表外走 modifiers → mid；全未命中 → default/low。
    modifiers 语义（见表注）：按序求值，set 规则命中即终止；delta 规则累加；
    TST 类 +1 在同名含 junior/初轮词时不加（表注的优先关系）。最后 problem_type 封顶。
    """
    conf = None
    hit = None
    if comp_norm:
        for v in comp_variants(comp_norm):
            if v in tiers:
                hit = v
                break
            kf = key_form(v)
            if kf in tiers.get('_by_keyform', {}):
                hit = tiers['_by_keyform'][kf]
                break
    fam = family_match(comp_norm, tier_keys_by_len) if (comp_norm and not hit) else None
    if hit:
        est, conf = tiers[hit]['base'], 'high'
    elif fam:
        est, conf = tiers[fam]['base'], 'mid'   # 家族回退：档位可信但写法未审定，降一级置信
    elif comp_norm:
        est = default['base']
        matched = [(rx, kind, val, lc) for rx, kind, val, lc in modifiers if rx.search(comp_norm)]
        junior_hit = any('junior' in rx.pattern for rx, k, v, lc in matched)
        first_hit = any('first round' in rx.pattern for rx, k, v, lc in matched)
        low_conf_only = False
        for rx, kind, val, _lc in matched:
            if kind == 'set':
                est, conf = val, 'mid'
                break
            if kind == 'noop':
                low_conf_only = True
                continue
            if kind == 'delta':
                if val > 0 and 'tst' in rx.pattern and (junior_hit or first_hit):
                    continue  # TST +1 让位于低龄/初轮修正
                est = max(1, min(5, est + val))
                conf = 'mid'
        if conf is None:
            conf = 'low' if not matched or low_conf_only else 'mid'
    else:
        est, conf = default['base'], default['conf']
    cap = PTYPE_CAP.get(ptype or '')
    if cap is not None and est > cap:
        est, conf = cap, conf if conf == 'high' else 'mid'
    return est, conf


def load_tables():
    mp = load_yaml(MAP_PATH)
    tr = load_yaml(TIER_PATH)
    return mp, tr


_FM_ID = re.compile(r'^id:\s*"?([^"\n]+?)"?\s*$', re.M)
_FM_MATHNET_ID = re.compile(r'^mathnet_id:\s*"?([^"\n]+?)"?\s*$', re.M)


def in_bank_snapshot():
    """mathnet_id → 入库标记的时点快照：题号（如 "G-035"）＞ 'reviewed-skip'（评审明确弃用）＞ 无记录。

    正本是 problems/ 的 frontmatter 与 data/review/*/verdicts.json 评审凭证，这里只做一次投影；
    先记评审弃用、再让已入库题号覆盖，保证「已入库」优先。ingest 与 export 共用本函数。
    """
    marks = {}
    for path in sorted(glob.glob(VERDICTS_GLOB)):
        with open(path, encoding='utf-8') as fh:
            for v in json.load(fh):
                if v.get('recommend') == 'skip' and v.get('mathnet_id'):
                    marks[v['mathnet_id']] = 'reviewed-skip'
    for path in sorted(glob.glob(PROBLEMS_GLOB)):
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        fm = text.split('\n---', 1)[0] if text.startswith('---') else ''
        pid, mid = _FM_ID.search(fm), _FM_MATHNET_ID.search(fm)
        if pid and mid:
            marks[mid.group(1)] = pid.group(1)
    return marks


def compile_keyword_rules(rules):
    """编译关键词规则；search_in 缺省 problem，保持旧版题面召回口径。"""
    return [(re.compile(r['pattern']), r['category'], r['node'], r.get('search_in', 'problem'))
            for r in rules]


def keyword_rule_matches(rule, row):
    """在合成/数据集行的指定侧匹配关键词；解答允许列表、字符串或空值。"""
    rx, _category, _node, search_in = rule
    if search_in in ('problem', 'both') and rx.search(row.get('problem_markdown') or ''):
        return True
    if search_in not in ('solutions', 'both'):
        return False
    solutions = row.get('solutions_markdown') or []
    if isinstance(solutions, str):
        solutions = [solutions]
    return any(isinstance(solution, str) and rx.search(solution) for solution in solutions)


def selfcheck(mp, tr):
    """表自洽校验（不读数据集）：节点合法、路径无交叠、tier 取值合法。CI 可跑。"""
    import yaml
    reg = yaml.safe_load(open(os.path.join(ROOT, 'taxonomy', 'registry.yml'), encoding='utf-8'))
    reg = {c: (v or {}) for c, v in reg.items() if not str(c).startswith('_')}
    errs = []
    sections = [set(mp.get(k) or {}) for k in ('map', 'board_only', 'ignore')]
    for a, b in [(0, 1), (0, 2), (1, 2)]:
        if sections[a] & sections[b]:
            errs.append(f'路径同时出现在多段: {sections[a] & sections[b]}')
    for p, d in (mp.get('map') or {}).items():
        if d['category'] not in reg:
            errs.append(f'map 非法板块 {p}: {d["category"]}')
        elif d['node'] not in reg[d['category']]:
            errs.append(f'map 非法节点 {p}: {d["category"]}/{d["node"]}')
    for p, d in (mp.get('board_only') or {}).items():
        if d['category'] not in reg:
            errs.append(f'board_only 非法板块 {p}')
    for i, rule in enumerate(mp.get('keyword_rules') or [], 1):
        search_in = rule.get('search_in', 'problem')
        if search_in not in KEYWORD_SEARCH_IN:
            errs.append(f'keyword_rules[{i}] 非法 search_in: {search_in!r}')
    for name, d in (tr.get('tiers') or {}).items():
        b = d.get('base')   # bool 是 int 子类：base: yes 会读成 True，不排除就当 ★1 混过校验
        if isinstance(b, bool) or not (isinstance(b, int) and 1 <= b <= 5):
            errs.append(f'tier 非法 base: {name}')
    compile_modifiers(tr.get('fallback_modifiers') or [])  # 语法可解析
    return errs


def build(mp, tr):
    from datasets import load_dataset
    import pyarrow.compute as pc
    ds = load_dataset('ShadenA/MathNet', 'all')['train']
    n = len(ds)
    n_imgs = pc.list_value_length(ds.data.column('images')).to_pylist()
    cols = {k: ds[k] for k in ('id', 'problem_markdown', 'solutions_markdown', 'country',
                               'competition', 'topics_flat', 'language', 'problem_type', 'final_answer')}

    norm = make_path_normalizer(mp.get('normalize') or [])
    kw_rules = compile_keyword_rules(mp.get('keyword_rules') or [])
    sec_map, sec_board, sec_ign = mp.get('map') or {}, mp.get('board_only') or {}, mp.get('ignore') or {}
    tiers = tr.get('tiers') or {}
    tier_keys_by_len = sorted([k for k in tiers if not k.startswith("_")], key=len, reverse=True)
    # 表键侧也做一次归一（去 the/届数序数），供输入侧回退查找
    tiers['_by_keyform'] = {key_form(k): k for k in tier_keys_by_len if key_form(k) != k}
    modifiers = compile_modifiers(tr.get('fallback_modifiers') or [])
    default = tr.get('default_unknown') or {'base': 2, 'conf': 'low'}

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    bank_marks = in_bank_snapshot()
    stat = Counter()
    unmapped = Counter()
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        for i in range(n):
            tags = [norm(t) for t in cols['topics_flat'][i] if t.strip()]
            votes, topics, weak_topics, ign_reasons = Counter(), [], [], []
            for t in tags:
                if t in sec_map:
                    d = sec_map[t]
                    votes[d['category']] += 1
                    (weak_topics if d.get('weak') else topics).append(d['node'])
                elif t in sec_board:
                    votes[sec_board[t]['category']] += 1
                elif t in sec_ign:
                    ign_reasons.append(sec_ign[t]['reason'])
                else:
                    unmapped[t] += 1
            topics = list(dict.fromkeys(topics))
            weak_only = not topics
            if weak_only:
                topics = list(dict.fromkeys(weak_topics))
            # 全文关键词召回（仅限已投该板块票的行，控误报）
            keyword_row = {
                'problem_markdown': cols['problem_markdown'][i],
                'solutions_markdown': cols['solutions_markdown'][i],
            }
            for rule in kw_rules:
                _rx, kcat, knode, _search_in = rule
                if kcat in votes and knode not in topics and keyword_rule_matches(rule, keyword_row):
                    topics.append(knode)
                    weak_only = False
            if votes:
                top = max(votes.values())
                cat = min((c for c, v in votes.items() if v == top), key=TIE_ORDER.index)
                status, reason = 'ok', None
            else:
                cat = None
                status = 'out_of_scope'
                reason = '；'.join(dict.fromkeys(ign_reasons)) or '无标签'
            comp_raw = cols['competition'][i]
            cn = norm_comp(comp_raw)
            est, conf = grade(cn, cols['problem_type'][i], tiers, modifiers, default, tier_keys_by_len)
            row = {
                'mathnet_id': cols['id'][i],
                'status': status,
                'category': cat,
                'category_all': [c for c, _ in votes.most_common()],
                'topics': topics,
                'topics_weak_only': weak_only if topics else None,
                'difficulty_est': est,
                'difficulty_conf': conf,
                'contest_raw': comp_raw,
                'comp_norm': cn,
                'year': extract_year(comp_raw),
                'country': cols['country'][i],
                'language': cols['language'][i],
                'problem_type': cols['problem_type'][i],
                'has_images': (n_imgs[i] or 0) > 0,
                'n_images': n_imgs[i] or 0,
                'n_solutions': len(cols['solutions_markdown'][i] or []),
                'sol_chars': sum(len(s) for s in (cols['solutions_markdown'][i] or [])),
                'final_answer': cols['final_answer'][i],
                'excluded_reason': reason,
                'head': re.sub(r'\s+', ' ', cols['problem_markdown'][i] or '')[:200],
                'dup_group': None,
                'in_bank': bank_marks.get(cols['id'][i]),
            }
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            stat['total'] += 1
            stat[f'status:{status}'] += 1
            if cat:
                stat[f'cat:{cat}'] += 1
            stat[f'conf:{conf}'] += 1
    print(f'candidates/mathnet.jsonl 已生成：{stat["total"]} 行')
    print('  在库口径 ok:', stat['status:ok'], ' 域外 out_of_scope:', stat['status:out_of_scope'])
    print('  板块:', {c: stat[f'cat:{c}'] for c in TIE_ORDER})
    print('  置信度:', {c: stat[f'conf:{c}'] for c in ('high', 'mid', 'low')})
    if unmapped:
        print(f'  ⚠️ 未映射路径 {len(unmapped)} 条（数据集升级？先补 taxonomy/mathnet_map.yml）:')
        for t, c in unmapped.most_common(10):
            print(f'    {c}× {t}')
        return 1
    print('  未映射路径: 0 ✅')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selfcheck', action='store_true', help='只校验两张表自洽，不读数据集')
    args = ap.parse_args()
    mp, tr = load_tables()
    errs = selfcheck(mp, tr)
    if errs:
        print('\n'.join(errs))
        print(f'SELFCHECK FAILED: {len(errs)} 个问题')
        sys.exit(1)
    print('表自洽校验通过（map/board_only/ignore 无交叠，节点合法，tier 合法）')
    if args.selfcheck:
        return
    sys.exit(build(mp, tr))


if __name__ == '__main__':
    main()
