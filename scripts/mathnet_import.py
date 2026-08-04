#!/usr/bin/env python3
"""MathNet 评审批次 → 正式题库（入库 SOP 第 6 步，格式正本见 SPEC.md）。

用法：
  uv run --group mathnet python scripts/mathnet_import.py --dir data/review/import-01 \
      [--per-category 5] [--dry-run]

准入线（SOP 第 4 步）：verdicts 中 recommend=claim 且非 needs_review（难度分歧 ≥2 档、
topics_verdict=wrong、text_quality=broken、needs_figure 任一命中即拒）。
逐字纪律（铁律 1）：题面/答案/解法全部从 HF 本地缓存的数据集行原文照录，本脚本零改写；
含 `## ` 行的原文会撞小节解析白名单，直接拒收（宁缺勿滥）。
幂等：已入库同 mathnet_id 的题拒绝重复导入。
字段映射正本在 docs/入库SOP-MathNet.md 第 6 步表；难度按「就低不就高」取
min(候选池 difficulty_est, 评审 difficulty_codex)，依据写入 difficulty_note。
"""
import argparse, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
CATEGORIES = ['algebra', 'number-theory', 'combinatorics', 'geometry']
PREFIX = {'algebra': 'A', 'number-theory': 'N', 'combinatorics': 'C', 'geometry': 'G'}
SOURCE_URL = 'https://huggingface.co/datasets/ShadenA/MathNet'


def q(s):
    """YAML 安全的双引号标量（JSON 字符串是合法 YAML）。"""
    return json.dumps(s, ensure_ascii=False)


def existing_state():
    """扫描 problems/：已入库 mathnet_id 集合 + 各板块当前最大号。"""
    ids, top = {}, {c: 0 for c in CATEGORIES}
    for cat in CATEGORIES:
        d = os.path.join(ROOT, 'problems', cat)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith('.md'):
                continue
            text = open(os.path.join(d, name), encoding='utf-8').read()
            m = re.match(r'---\n(.*?)\n---\n', text, re.S)
            if m:
                mm = re.search(r'^mathnet_id:\s*["\']?([^"\'\n]+)', m.group(1), re.M)
                if mm:
                    ids[mm.group(1).strip()] = os.path.join('problems', cat, name)
            n = re.match(r'[ANCG]-(\d+)\.md$', name)
            if n:
                top[cat] = max(top[cat], int(n.group(1)))
    return ids, top


def needs_review(v, est):
    """merge 同款判定（正本：mathnet_review.cmd_merge / SOP 第 4 步）。"""
    return bool(abs(v['difficulty_codex'] - est) >= 2 or v['topics_verdict'] == 'wrong'
                or v['text_quality'] == 'broken' or v['needs_figure'])


def render(pid, title, row, v, full, review_ref):
    """按 SPEC §2/§3 拼一题；不可入库时返回 (None, 原因)。"""
    prob = (full['problem_markdown'] or '').strip()
    sols = [s.strip() for s in (full['solutions_markdown'] or []) if s and s.strip()]
    if not prob or not sols:
        return None, '题面或官方解为空'
    answer = (row.get('final_answer') or '').strip()
    if not answer:
        if 'answer' in (row.get('problem_type') or ''):
            return None, 'problem_type 含 answer 但 final_answer 为空'
        answer = '证明题'
    joined = '\n\n---\n\n'.join(sols)
    if any(ln.lstrip().startswith('## ') for ln in (prob + '\n' + joined).splitlines()):
        return None, '原文含 `## ` 行，撞小节白名单（铁律 5：不改写、不收）'
    est = row['difficulty_est']
    diff = min(est, v['difficulty_codex'])
    if not 1 <= diff <= 5:
        return None, f'难度越界 {diff}'
    note = v['difficulty_reason'].strip()
    if est != v['difficulty_codex']:
        note += f'（规则估★{est}/Codex★{v["difficulty_codex"]}，就低取★{diff}）'
    year = row.get('year')
    fm = [
        '---',
        f'id: {pid}',
        f'title: {q(title)}',
        f'category: {row["category"]}',
        f'contest: {q(row.get("contest_raw") or "?")}',
        f'year: {year if year is not None else "null"}',
        f'source_ref: MathNet {row["mathnet_id"]}',
        f'difficulty: {diff}',
        f'difficulty_note: {q(note)}',
        f'topics: [{", ".join(q(t) for t in row["topics"])}]',
        'verification: mathnet-reviewed',
        f'mathnet_id: {q(row["mathnet_id"])}',
        f'review_ref: {review_ref}',
        f'source_url: {SOURCE_URL}',
        '---',
    ]
    body = (f'\n# {pid}｜{title}\n\n'
            f'## 题面\n\n{prob}\n\n'
            f'## 答案\n\n{answer}\n\n'
            f'## 解法要点\n\n{joined}\n')
    return '\n'.join(fm) + '\n' + body, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', required=True, help='评审批次目录，如 data/review/import-01')
    ap.add_argument('--per-category', type=int, default=5, help='每板块最多入库题数（默认 5）')
    ap.add_argument('--dry-run', action='store_true', help='只报告不写盘')
    # 人工裁定要在入库前生效：lint 强制题号连号，事后删题就得重排编号，极易出错。
    ap.add_argument('--exclude', nargs='*', default=[], metavar='MATHNET_ID',
                    help='入库者裁定放弃的题（如题面非英文、官方解为空指针），入库前剔除')
    ap.add_argument('--only', nargs='*', default=None, metavar='MATHNET_ID',
                    help='只入库这些题（与 --exclude 互补，用于挑选式入库）')
    args = ap.parse_args()
    d = os.path.join(ROOT, args.dir)
    review_ref = os.path.relpath(os.path.join(d, 'verdicts.json'), ROOT)
    for f in ('batch.json', 'verdicts.json'):
        if not os.path.exists(os.path.join(d, f)):
            print(f'{args.dir}/{f} 不存在——先跑 batch/dispatch/merge'); sys.exit(2)
    batch = json.load(open(os.path.join(d, 'batch.json'), encoding='utf-8'))
    verdicts = {v['mathnet_id']: v for v in json.load(open(os.path.join(d, 'verdicts.json'), encoding='utf-8'))}
    rows = {r['mathnet_id']: r for r in
            (json.loads(l) for l in open(POOL, encoding='utf-8') if l.strip())}
    have, top = existing_state()

    from datasets import load_dataset   # 依赖 --group mathnet；读 HF 本地缓存
    ds = load_dataset('ShadenA/MathNet', 'all')['train']
    idx = {mid: i for i, mid in enumerate(ds['id'])}

    taken = {c: 0 for c in CATEGORIES}
    done, skipped = [], []
    for b in batch:                     # 按批次顺序 = 确定性入库顺序
        mid = b['mathnet_id']
        if mid in set(args.exclude):
            skipped.append((mid, '入库者裁定放弃（--exclude）')); continue
        if args.only is not None and mid not in set(args.only):
            skipped.append((mid, '不在 --only 名单')); continue
        v, row = verdicts.get(mid), rows.get(mid)
        if not v or not row:
            skipped.append((mid, '缺 verdict 或候选池行')); continue
        cat = row['category']
        if taken[cat] >= args.per_category:
            skipped.append((mid, f'{cat} 配额已满')); continue
        if v['recommend'] != 'claim':
            skipped.append((mid, f'评审 skip：{v.get("recommend_reason", "")[:60]}')); continue
        if needs_review(v, row['difficulty_est']):
            skipped.append((mid, 'needs_review（分歧/质量旗标），须人工定夺')); continue
        if mid in have:
            skipped.append((mid, f'已入库于 {have[mid]}（幂等拒重）')); continue
        if mid not in idx:
            skipped.append((mid, '数据集缓存中找不到该行')); continue
        title = (v.get('short_title') or '').strip()
        if not title:
            skipped.append((mid, 'verdict 缺 short_title')); continue
        pid = f'{PREFIX[cat]}-{top[cat] + 1:03d}'
        text, why = render(pid, title, row, v, ds[idx[mid]], review_ref)
        if text is None:
            skipped.append((mid, why)); continue
        path = os.path.join(ROOT, 'problems', cat, pid + '.md')
        if not args.dry_run:
            open(path, 'w', encoding='utf-8').write(text)
        top[cat] += 1
        taken[cat] += 1
        done.append((pid, mid, cat))
        print(f'{"[dry] " if args.dry_run else ""}入库 {pid} ← MathNet {mid}（{cat}，★{min(row["difficulty_est"], v["difficulty_codex"])}）')
    if skipped:
        print('\n未入库：')
        for mid, why in skipped:
            print(f'  {mid}: {why}')
    print(f'\n共入库 {len(done)} 题（' + '，'.join(f'{c} {taken[c]}' for c in CATEGORIES) + '）'
          + ('；dry-run 未写盘' if args.dry_run else '。下一步：bash scripts/lint.sh'))
    return done


if __name__ == '__main__':
    main()
