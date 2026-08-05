#!/usr/bin/env python3
"""学生档案与能力图 —— scripts/bank.py 的辅助模块。

数据契约（能力图规则的唯一正本，SPEC §7 登记；docs/教练手册.md 只引用不抄录）：

- data/students/<学生id>/profile.yml：
  {id, name, grade, target, since, attempt_aliases, notes}
  attempt_aliases：data/attempts.jsonl 中 student 字段命中名单内任一值的训练记录归属此人
  （spar finish 目前固定写 student=self，第一个学生建档时用 --alias self 绑定）。
- data/students/<学生id>/assessments.jsonl：测评波次台账，一行一题：
  {wave, date, ref, category, topics, difficulty, score, note}
  ref = 库内题号（写入时快照 category/topics/difficulty，读取时若题仍在库则以现行
  frontmatter 为准——教练细分 registry 并 retag 后，历史证据自动落到更细的节点）
  或外部题标识（category/difficulty/topics 必须手填）；
  score ∈ [0,1]：对=1、半对=0.5、错=0（接受任意小数）。
- 证据折算：测评取 score；训练取 RESULT_VALUE[result]（四分结果 → 掌握值）。
- 节点掌握值 = Σ(值×难度) / Σ(难度)——难度加权、全程可手工复算；
  状态阈值：未测（无证据）｜薄弱 mastery<WEAK_MAX｜稳固 mastery≥SOLID_MIN 且
  证据数≥SOLID_MIN_N｜其余为进行中。「已证★n」= 存在满分证据（值≥0.999）的最高难度。
- 板块基础值：同一公式对「该板块该波次全部测评」加权，按波次成序列——这就是
  「若干波考试测基础值」的落盘形态；「全部证据累计」行是当前综合值。
- 细分建议（AI+教练分类迭代的触发信号）：同一节点证据 ≥SPLIT_MIN_N 且
  强证据（≥SOLID_MIN）、弱证据（≤WEAK_MAX）两侧各 ≥SPLIT_MIN_SIDE
  → 该节点对这名学生「过粗」，按案例在 taxonomy/registry.yml 细分（先登别名后立节点，
  流程见 docs/教练手册.md）。
"""
import datetime
import json
import os
import re
import sys

import yaml

from bank_constants import CATEGORIES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import spar_session as sp  # attempts v2 读取契约共用

STUDENTS_ROOT = os.path.join(ROOT, 'data', 'students')
CAT_LABEL = {'algebra': '代数', 'number-theory': '数论', 'combinatorics': '组合', 'geometry': '几何'}

# 证据折算与状态阈值（正本，勿在文档里抄数值）
RESULT_VALUE = {'independent_ok': 1.0, 'hinted_ok': 0.6, 'solution_reconstructed': 0.3, 'fail': 0.0}
SOLID_MIN, WEAK_MAX = 0.75, 0.40   # 稳固下限 / 薄弱上限（掌握值 0–1）
SOLID_MIN_N = 2                    # 稳固还需的最少证据数
SPLIT_MIN_N, SPLIT_MIN_SIDE = 6, 2  # 细分建议：总证据下限 / 强弱两侧各自下限
STATUS_ORDER = ('稳固', '进行中', '薄弱', '未测')
STATUS_ICON = {'稳固': '✅', '进行中': '🔶', '薄弱': '⚠️', '未测': '⬜'}

SID_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


# ---------------- 存取 ----------------
def _sdir(sid):
    return os.path.join(STUDENTS_ROOT, sid)


def load_student(sid):
    path = os.path.join(_sdir(sid), 'profile.yml')
    if not os.path.exists(path):
        return None
    prof = yaml.safe_load(open(path, encoding='utf-8')) or {}
    prof.setdefault('id', sid)
    prof.setdefault('attempt_aliases', [])
    return prof


def list_students():
    if not os.path.isdir(STUDENTS_ROOT):
        return []
    return sorted(d for d in os.listdir(STUDENTS_ROOT)
                  if os.path.exists(os.path.join(STUDENTS_ROOT, d, 'profile.yml')))


def save_student(prof):
    d = _sdir(prof['id'])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'profile.yml'), 'w', encoding='utf-8') as f:
        yaml.safe_dump(prof, f, allow_unicode=True, sort_keys=False)


def load_assessments(sid):
    path = os.path.join(_sdir(sid), 'assessments.jsonl')
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                print(f'警告：{sid}/assessments.jsonl 第 {i} 行不是合法 JSON，已跳过', file=sys.stderr)
    return out


def append_assessment(sid, rec):
    d = _sdir(sid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'assessments.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


# ---------------- 证据归一化 ----------------
def resolve_nodes(reg, resolve, category, topics):
    """topics → [(板块, 规范节点)]；跨界词落到它所属的板块。返回 (nodes, 未注册词)。"""
    nodes, unresolved = [], []
    for t in topics or []:
        n = resolve(reg, category, t)
        if n:
            hit = (category, n)
        else:
            hit = next(((c, m) for c in reg if (m := resolve(reg, c, t))), None)
        if hit is None:
            unresolved.append(t)
        elif hit not in nodes:
            nodes.append(hit)
    return nodes, unresolved


def evidence_from_assessment(rec, fmmap, reg, resolve):
    """一条测评记录 → 证据。库内题优先用现行 frontmatter（retag 后自动细化）。"""
    ref = str(rec.get('ref') or '')
    fm = fmmap.get(ref)
    cat = (fm or {}).get('category') or rec.get('category')
    topics = (fm or {}).get('topics') or rec.get('topics') or []
    diff = (fm or {}).get('difficulty') or rec.get('difficulty')
    nodes, unresolved = resolve_nodes(reg, resolve, cat, topics)
    return {'kind': 'assessment', 'ref': ref, 'wave': rec.get('wave'),
            'date': rec.get('date') or '', 'category': cat,
            'difficulty': int(diff or 1), 'value': float(rec.get('score') or 0.0),
            'nodes': nodes}, unresolved


def evidence_from_attempt(rec, fmmap, reg, resolve):
    """一条训练记录 → 证据；题号不在现库（如 legacy 清退）→ None。"""
    fm = fmmap.get(rec.get('id'))
    if fm is None or rec.get('result') not in RESULT_VALUE:
        return None, []
    nodes, unresolved = resolve_nodes(reg, resolve, fm['category'], fm.get('topics'))
    return {'kind': 'attempt', 'ref': fm['id'], 'wave': None,
            'date': rec.get('date') or '', 'category': fm['category'],
            'difficulty': int(fm.get('difficulty') or 1),
            'value': RESULT_VALUE[rec['result']], 'nodes': nodes}, unresolved


def build_evidence(prof, assessments, attempts, fmmap, reg, resolve):
    """全部证据 + 未注册词告警 + 被跳过的训练记录数（题号已不在库）。"""
    names = {prof['id'], *(prof.get('attempt_aliases') or [])}
    evidence, warns, skipped = [], [], 0
    for rec in assessments:
        ev, unresolved = evidence_from_assessment(rec, fmmap, reg, resolve)
        evidence.append(ev)
        warns += [f"测评 {ev['ref'] or '?'}：「{t}」未注册（taxonomy/registry.yml）" for t in unresolved]
    for rec in attempts:
        if rec.get('student') not in names:
            continue
        ev, unresolved = evidence_from_attempt(rec, fmmap, reg, resolve)
        if ev is None:
            skipped += 1
            continue
        evidence.append(ev)
        warns += [f"训练 {ev['ref']}：「{t}」未注册" for t in unresolved]
    evidence.sort(key=lambda e: (e['date'], e['ref']))
    return evidence, warns, skipped


# ---------------- 聚合 ----------------
def weighted(evs):
    """难度加权掌握值 Σ(v·d)/Σ(d)；无证据 → None。"""
    den = sum(e['difficulty'] for e in evs)
    return sum(e['value'] * e['difficulty'] for e in evs) / den if den else None


def status_of(mastery, n):
    if n == 0:
        return '未测'
    if mastery < WEAK_MAX:
        return '薄弱'
    if mastery >= SOLID_MIN and n >= SOLID_MIN_N:
        return '稳固'
    return '进行中'


def node_table(evidence, reg):
    """{(cat,node): {mastery,n,best,last,status,cases}}——registry 全节点，含未测。"""
    table = {(c, n): [] for c in reg for n in (reg.get(c) or {})}
    for ev in evidence:
        for key in ev['nodes']:
            table.setdefault(key, []).append(ev)
    out = {}
    for key, evs in table.items():
        m = weighted(evs)
        best = max((e['difficulty'] for e in evs if e['value'] >= 0.999), default=0)
        out[key] = {'mastery': m, 'n': len(evs), 'best': best,
                    'last': max((e['date'] for e in evs), default=''),
                    'status': status_of(m if m is not None else 0.0, len(evs)),
                    'cases': sorted(evs, key=lambda e: e['date'], reverse=True)}
    return out


def wave_rows(evidence):
    """测评证据按波次聚成基础值序列：[{wave, date, cats:{cat:{v,n}}}]，按首测日期排序。"""
    waves = {}
    for ev in evidence:
        if ev['kind'] != 'assessment':
            continue
        w = waves.setdefault(ev['wave'] or '?', {'wave': ev['wave'] or '?', 'date': ev['date'], 'evs': []})
        w['date'] = min(w['date'], ev['date']) if w['date'] and ev['date'] else (w['date'] or ev['date'])
        w['evs'].append(ev)
    rows = []
    for w in sorted(waves.values(), key=lambda x: (x['date'], x['wave'])):
        cats = {}
        for c in CATEGORIES:
            evs = [e for e in w['evs'] if e['category'] == c]
            if evs:
                cats[c] = {'v': weighted(evs), 'n': len(evs)}
        rows.append({'wave': w['wave'], 'date': w['date'], 'cats': cats})
    return rows


def current_by_cat(evidence):
    """全部证据（测评+训练）按板块的当前综合值。"""
    out = {}
    for c in CATEGORIES:
        evs = [e for e in evidence if e['category'] == c]
        if evs:
            out[c] = {'v': weighted(evs), 'n': len(evs)}
    return out


def split_suggestions(ntable):
    """强弱分化节点 → 分类过粗信号，供 AI+教练按案例细分。"""
    out = []
    for (cat, node), st in ntable.items():
        if st['n'] < SPLIT_MIN_N:
            continue
        strong = [e for e in st['cases'] if e['value'] >= SOLID_MIN]
        weak = [e for e in st['cases'] if e['value'] <= WEAK_MAX]
        if len(strong) >= SPLIT_MIN_SIDE and len(weak) >= SPLIT_MIN_SIDE:
            out.append({'cat': cat, 'node': node, 'n': st['n'],
                        'strong': [e['ref'] for e in strong],
                        'weak': [e['ref'] for e in weak]})
    return sorted(out, key=lambda s: (-s['n'], s['cat'], s['node']))


def _prereq_depth(prereq):
    """'cat/node' → 拓扑深度（无前置=0）。环由 doclint 挡住，这里对脏数据只保证不死循环。"""
    depth = {}

    def walk(key, trail):
        if key in depth:
            return depth[key]
        if key in trail:
            return 0
        pres = prereq.get(key) or []
        depth[key] = 1 + max((walk(p, trail | {key}) for p in pres), default=-1)
        return depth[key]

    for k in prereq:
        walk(k, frozenset())
    return depth


def gap_queue(ntable, reg, problems, resolve, seen_refs, prereq=None):
    """补齐队列：薄弱在前、未测在后；每个缺口给 ≤2 道库内未做过的题（★升序）。

    prereq 非 None（bank.load_prereq() 的结果）时：同状态桶内按拓扑深度升序——先补最
    上游的缺口；每项附 blocked_by = 该节点前置中当前为薄弱/未测的节点列表（教练话术：
    「先补 X 再打这里」）。
    """
    by_node = {}
    for p in problems:
        fm = p['fm'] or {}
        nodes, _ = resolve_nodes(reg, resolve, fm.get('category'), fm.get('topics'))
        for key in nodes:
            by_node.setdefault(key, []).append(fm)
    depth = _prereq_depth(prereq) if prereq else {}

    def weak(key):
        cat, _, node = key.partition('/')
        st = ntable.get((cat, node))
        return st is not None and st['status'] in ('薄弱', '未测')

    queue = []
    for cat in CATEGORIES:
        for status in ('薄弱', '未测'):
            bucket = []
            for node in (reg.get(cat) or {}):
                st = ntable.get((cat, node))
                if not st or st['status'] != status:
                    continue
                picks = sorted((fm for fm in by_node.get((cat, node), [])
                                if fm['id'] not in seen_refs),
                               key=lambda f: (f['difficulty'], f['id']))[:2]
                item = {'cat': cat, 'node': node, 'status': status,
                        'mastery': st['mastery'], 'n': st['n'],
                        'picks': [{'id': f['id'], 'd': f['difficulty']} for f in picks]}
                if prereq is not None:
                    item['blocked_by'] = [k for k in (prereq.get(f'{cat}/{node}') or [])
                                          if weak(k)]
                bucket.append(item)
            if prereq is not None:
                bucket.sort(key=lambda it: depth.get(f"{it['cat']}/{it['node']}", 0))
            queue.extend(bucket)
    return queue


def build_profile_data(prof, evidence, warns, skipped, reg, problems, resolve, prereq=None):
    """终端报告与 HTML 共用的数据装配（唯一装配点，便于测试）。"""
    ntable = node_table(evidence, reg)
    seen_refs = {e['ref'] for e in evidence}
    cats = []
    for cat in CATEGORIES:
        nodes = []
        for node in (reg.get(cat) or {}):
            st = ntable[(cat, node)]
            nodes.append({'name': node, 'status': st['status'], 'icon': STATUS_ICON[st['status']],
                          'mastery': st['mastery'], 'n': st['n'], 'best': st['best'],
                          'last': st['last'],
                          'cases': [{'ref': e['ref'], 'd': e['difficulty'], 'v': e['value'],
                                     'date': e['date'], 'kind': e['kind'], 'wave': e['wave']}
                                    for e in st['cases'][:10]]})
        tested = [n for n in nodes if n['status'] != '未测']
        cur = current_by_cat(evidence).get(cat)
        cats.append({'cat': cat, 'label': CAT_LABEL[cat], 'nodes': nodes,
                     'cur': cur, 'coverage': {'tested': len(tested), 'total': len(nodes),
                                              'solid': sum(1 for n in nodes if n['status'] == '稳固')}})
    n_assess = sum(1 for e in evidence if e['kind'] == 'assessment')
    return {'generated': datetime.date.today().isoformat(),
            'student': {k: prof.get(k) for k in ('id', 'name', 'grade', 'target', 'since')},
            'totals': {'assess': n_assess, 'attempts': len(evidence) - n_assess,
                       'waves': len(wave_rows(evidence)), 'skipped_attempts': skipped},
            'waves': wave_rows(evidence), 'current': current_by_cat(evidence),
            'cats': cats, 'gaps': gap_queue(ntable, reg, problems, resolve, seen_refs, prereq),
            'splits': split_suggestions(ntable), 'warns': warns}


# ---------------- 终端报告 ----------------
def _pct(v):
    # 四舍五入取上（与模板 JS 的 Math.round 一致，避免 62/63 双端不一）
    return f'{int(v * 100 + 0.5):>3d}' if v is not None else '  —'


def print_profile(data):
    s = data['student']
    head = f"=== 学生档案｜{s.get('name') or s['id']}（{s['id']}）"
    if s.get('target'):
        head += f"｜目标 {s['target']}"
    if s.get('since'):
        head += f"｜建档 {s['since']}"
    print(head + ' ===')
    t = data['totals']
    line = f"证据 {t['assess'] + t['attempts']} 条：测评 {t['assess']}（{t['waves']} 波）＋ 训练 {t['attempts']}"
    if t['skipped_attempts']:
        line += f"（另有 {t['skipped_attempts']} 条训练记录题号已不在现库，未计入）"
    print(line + '\n')

    print('—— 基础值走势（难度加权掌握 %，— = 该波未测该板块）——')
    hdr = f"{'波次':<22}" + ''.join(f'{CAT_LABEL[c]:>6}' for c in CATEGORIES)
    print(hdr)
    for row in data['waves']:
        label = f"{row['date']} {row['wave']}"[:20]
        print(f'{label:<22}' + ''.join(f"{_pct(row['cats'].get(c, {}).get('v')):>6}" for c in CATEGORIES))
    if not data['waves']:
        print('（尚无测评波次——用 assess 录入第一波基线）')
    print(f"{'─ 全部证据累计':<21}" + ''.join(f"{_pct(data['current'].get(c, {}).get('v')):>6}" for c in CATEGORIES))

    for c in data['cats']:
        cov = c['coverage']
        cur = _pct((c['cur'] or {}).get('v')).strip()
        print(f"\n—— {c['label']}（覆盖 {cov['tested']}/{cov['total']} 节点｜稳固 {cov['solid']}｜当前 {cur}%）——"
              if c['cur'] else
              f"\n—— {c['label']}（覆盖 {cov['tested']}/{cov['total']} 节点｜未测）——")
        untested = []
        for n in c['nodes']:
            if n['status'] == '未测':
                untested.append(n['name'])
                continue
            best = f"已证★{n['best']}" if n['best'] else '已证—'
            print(f"  {n['icon']} {n['name']:<14} {_pct(n['mastery'])}%  n={n['n']:<3} {best}  最近 {n['last'] or '?'}")
        if untested:
            print(f"  ⬜ 未测 {len(untested)}：{'、'.join(untested)}")

    gaps_with_picks = [g for g in data['gaps'] if g['picks']]
    print('\n—— 补齐队列（薄弱优先，未测在后；每缺口给 ≤2 道库内未做题）——')
    if gaps_with_picks:
        for g in gaps_with_picks:
            m = f"{_pct(g['mastery']).strip()}% n={g['n']}" if g['status'] == '薄弱' else '未测'
            picks = '，'.join(f"{p['id']}（★{p['d']}）" for p in g['picks'])
            blocked = '，'.join(k.split('/')[1] for k in g.get('blocked_by') or [])
            print(f"  {CAT_LABEL[g['cat']]}/{g['node']}：{m} → {picks}"
                  + (f"（先补：{blocked}）" if blocked else ''))
        n_dry = sum(1 for g in data['gaps'] if not g['picks'])
        if n_dry:
            print(f'  （另有 {n_dry} 个缺口节点库内暂无可用题——扩容看 candidates --gaps）')
    else:
        print('  （缺口节点库内暂无可用题——扩容看 candidates --gaps）' if data['gaps'] else '  （无缺口）')

    if data['splits']:
        print('\n—— 分类细分建议（该节点对此生强弱分化，AI+教练按案例迭代，流程见 docs/教练手册.md）——')
        for sug in data['splits']:
            print(f"  {CAT_LABEL[sug['cat']]}/{sug['node']}：n={sug['n']}"
                  f"｜强：{'、'.join(sug['strong'][:4])}｜弱：{'、'.join(sug['weak'][:4])}")
    if data['warns']:
        print(f"\n警告 {len(data['warns'])} 条（topics 未注册，不计入节点，只计入板块值）：")
        for w in data['warns'][:8]:
            print('  ' + w)


# ---------------- HTML 能力图 ----------------
def render_html(data):
    tpl = open(os.path.join(ROOT, 'scripts', 'profile_template.html'), encoding='utf-8').read()
    os.makedirs(os.path.join(ROOT, 'maps'), exist_ok=True)
    out = os.path.join(ROOT, 'maps', f"能力图-{data['student']['id']}.html")
    with open(out, 'w', encoding='utf-8') as f:
        f.write(tpl.replace('__PROFILE_DATA__', json.dumps(data, ensure_ascii=False)))
    return os.path.relpath(out, ROOT)


# ---------------- CLI ----------------
def cmd_student(problems, args):
    if args.action == 'list':
        sids = list_students()
        if not sids:
            print('尚无学生档案。建档：uv run python scripts/bank.py student add <id> --name 张三')
            return
        attempts = sp.load_attempts_v2()
        for sid in sids:
            prof = load_student(sid)
            names = {sid, *(prof.get('attempt_aliases') or [])}
            n_att = sum(1 for r in attempts if r.get('student') in names)
            assess = load_assessments(sid)
            waves = len({r.get('wave') for r in assess})
            print(f"{sid:<12} {prof.get('name') or '':<8} {prof.get('grade') or '—':<6} "
                  f"目标 {prof.get('target') or '—':<8} 建档 {prof.get('since') or '?'}  "
                  f"测评 {len(assess)} 条（{waves} 波）｜训练 {n_att} 条")
        return
    if args.action != 'add':
        print('用法：student add <id> [--name/--grade/--target/--alias/--note] | student list')
        sys.exit(2)
    sid = args.sid
    if not sid or not SID_RE.match(sid):
        print('学生 id 必须是小写字母/数字/连字符（如 zhang-san）')
        sys.exit(2)
    if load_student(sid):
        print(f'学生 {sid} 已存在（data/students/{sid}/profile.yml），不覆盖')
        sys.exit(2)
    prof = {'id': sid, 'name': args.name or sid, 'grade': args.grade,
            'target': args.target, 'since': datetime.date.today().isoformat(),
            'attempt_aliases': args.alias or [], 'notes': args.note or ''}
    save_student(prof)
    print(f"已建档 data/students/{sid}/profile.yml（{prof['name']}"
          + (f"，绑定训练别名 {prof['attempt_aliases']}" if prof['attempt_aliases'] else '') + '）')
    print(f'下一步：录基线波次 → uv run python scripts/bank.py assess {sid} --wave 基线-1 --id A-001 --score 1')


def cmd_assess(problems, reg, resolve, args):
    prof = load_student(args.sid)
    if prof is None:
        print(f'学生 {args.sid} 不存在。先建档：uv run python scripts/bank.py student add {args.sid}')
        sys.exit(2)
    if not 0.0 <= args.score <= 1.0:
        print('score 必须在 0–1 之间（对=1、半对=0.5、错=0）')
        sys.exit(2)
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    if args.id:
        fm = fmmap.get(args.id)
        if fm is None:
            print(f'未知题号 {args.id}（外部题请用 --source）')
            sys.exit(2)
        ref, cat, topics, diff = args.id, fm['category'], fm.get('topics') or [], fm['difficulty']
    else:
        if not (args.source and args.category and args.difficulty and args.topics):
            print('外部题必须给全：--source --category --difficulty --topics（逗号分隔）')
            sys.exit(2)
        ref, cat, diff = args.source, args.category, args.difficulty
        topics = [t.strip() for t in re.split('[,，、]', args.topics) if t.strip()]
    rec = {'wave': args.wave, 'date': args.date or datetime.date.today().isoformat(),
           'ref': ref, 'category': cat, 'topics': topics, 'difficulty': diff,
           'score': args.score, 'note': args.note or ''}
    append_assessment(args.sid, rec)
    nodes, unresolved = resolve_nodes(reg, resolve, cat, topics)
    node_s = '、'.join(f'{CAT_LABEL[c]}/{n}' for c, n in nodes) or '（无可解析节点）'
    print(f"已记录 {args.sid} · 波次 {rec['wave']} · {ref}（★{diff}，{CAT_LABEL.get(cat, cat)}）"
          f"score={args.score} → 计入节点：{node_s}")
    for t in unresolved:
        print(f'  警告：「{t}」未注册（taxonomy/registry.yml）——只计入板块值，不计入节点')


def cmd_profile(problems, reg, resolve, args, prereq=None):
    prof = load_student(args.sid)
    if prof is None:
        print(f'学生 {args.sid} 不存在。已有档案：{", ".join(list_students()) or "（无）"}')
        sys.exit(2)
    fmmap = {p['fm']['id']: p['fm'] for p in problems if p['fm']}
    evidence, warns, skipped = build_evidence(
        prof, load_assessments(args.sid), sp.load_attempts_v2(), fmmap, reg, resolve)
    data = build_profile_data(prof, evidence, warns, skipped, reg, problems, resolve, prereq)
    print_profile(data)
    if getattr(args, 'html', False):
        rel = render_html(data)
        print(f'\n已生成 {rel}')
