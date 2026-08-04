#!/usr/bin/env python3
"""把候选题批量派给本地 Codex 逐题评审。**评审即正式核验档（mathnet-reviewed）**：
本脚本产出的 verdicts.json 是入库题 `verification: mathnet-reviewed` 的评审凭证，
归档于 data/review/<batch>/ 并随仓库提交，入库题 frontmatter 的 review_ref 必须指向它。
凭证纪律：数据集声称 ≠ 已核验，无 verdicts.json 不入库（端到端流程见 docs/入库SOP-MathNet.md）。

用法：
  # 1) 出批次（从候选池按条件抽题，附题面与官方解摘录）
  uv run --group mathnet python scripts/mathnet_review.py batch \
      --category geometry --difficulty 2-3 --node 共线共点定理 --n 12 --out data/review/geo-01

  # 2) 派给 Codex（需本机 codex-cli 已登录）
  uv run python scripts/mathnet_review.py dispatch --dir data/review/geo-01

  # 3) 合并回填候选池 + 出分歧报告
  uv run python scripts/mathnet_review.py merge --dir data/review/geo-01

Codex 同时给出难度的**独立第二意见**：它读题面，规则层只看赛事名。
两者分歧 ≥2 档的题自动标 needs_review，交人工定夺——这是难度估级唯一的可证伪校验。
本脚本只写 data/review/ 与候选池回填，不改动 problems/ 里任何正式题目。
"""
import argparse, glob, json, os, re, subprocess, sys

from bank import MIN_DIFFICULTY  # 学段下界唯一常量正本，勿在此另设阈值

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
COMPANION_GLOB = '~/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs'


def find_companion():
    """glob 发现 codex-companion，插件版本目录取最高版本（不再硬编码具体版本路径）。"""
    def ver_key(path):
        seg = path.split(os.sep)[-3]   # …/codex/<版本>/scripts/codex-companion.mjs
        return [int(x) if x.isdigit() else -1 for x in re.split(r'[.\-+]', seg)]
    hits = glob.glob(os.path.expanduser(COMPANION_GLOB))
    return max(hits, key=ver_key) if hits else None

PROMPT = """你是奥数题库的候选题评审员。读同目录 batch.json（{n} 道 MathNet 候选题，含题面、
部分官方解摘录、我方规则层给出的板块/知识点/难度估级），逐题独立评估，把结果写成 verdicts.json。

难度标尺（★1–5，按解法所需思维跨度定级，不按赛事名气；有疑义就低不就高）：
★1 单步套用定义/公式，2–5 分钟（AMC 8 前中段）
★2 两三步常规组合，需选对工具（AMC 10/12、各国初轮）
★3 需要一个非显然的想法或引理（AIME、国家二轮）
★4 需构造/多引理串联，突破口不常见（IMO P1/P4、USAMO、CMO）
★5 需深刻洞察或长链论证（IMO P3/P6）

每题一个对象，字段严格如下：
{{"mathnet_id":"...",
  "short_title":"≤8 词英文短标题：名词短语概括题目核心对象与性质，入库时直接用作 title",
  "difficulty_codex":1-5 整数,
  "difficulty_reason":"一句话：关键突破口与思维跨度",
  "topics_verdict":"agree|partial|wrong", "topics_comment":"标签是否贴切；不贴切说明该往哪个方向",
  "text_quality":"clean|minor_issues|broken", "text_comment":"转录质量：LaTeX 完整性、OCR 残缺、题意自洽性",
  "needs_figure":true|false, "figure_comment":"是否依赖图形且无法用文字复原（题库铁律：此类不收录）",
  "recommend":"claim|skip", "recommend_reason":"一句话结论"}}

要求：
- 只输出这 {n} 个对象组成的 JSON 数组，写入 verdicts.json，不要创建其他文件。
- 独立判断：不要因为 est 字段就顺从我方估级——分歧正是本次评审的价值所在。
- 题面/解答分别评价：MathNet 的题面通常干净，解答常有 OCR 与 LLM 转写瑕疵，如实分别记录。
- **`solution_head` 是按字符数截断的摘录，不是完整解答**（入库时读的是数据集原文全文）。
  因此「解答在此处戛然而止」不构成质量缺陷，不要据此判 minor_issues——只评摘录里**已出现**的内容
  有没有 OCR 残缺、符号丢失、论证错误。题面 `problem` 同样可能被截断，同理处理。
"""


def cmd_batch(args):
    from datasets import load_dataset
    rows = [json.loads(l) for l in open(POOL, encoding='utf-8') if l.strip()]
    pool = [r for r in rows if r['status'] == 'ok' and not r['has_images']]
    dropped_low = len(pool)
    pool = [r for r in pool if r['difficulty_est'] >= MIN_DIFFICULTY]  # 学段下界，SPEC §4
    dropped_low -= len(pool)
    if dropped_low:
        print(f'学段下界：跳过 est★<{MIN_DIFFICULTY} 的候选 {dropped_low} 条（不送评审，SPEC §4）')
    if args.category:
        pool = [r for r in pool if r['category'] == args.category]
    if args.difficulty:
        a, _, b = args.difficulty.partition('-')
        lo, hi = int(a), int(b or a)
        pool = [r for r in pool if lo <= r['difficulty_est'] <= hi]
    if args.node:
        pool = [r for r in pool if any(args.node in t for t in r['topics'])]
    if args.conf:
        rank = {'high': 2, 'mid': 1, 'low': 0}
        pool = [r for r in pool if rank[r['difficulty_conf']] >= rank[args.conf]]
    if not pool:
        print('无匹配候选'); sys.exit(2)
    import random
    random.Random(args.seed).shuffle(pool)
    pick = pool[:args.n]
    ds = load_dataset('ShadenA/MathNet', 'all')['train']
    idx = {mid: i for i, mid in enumerate(ds['id'])}
    batch = []
    for r in pick:
        i = idx[r['mathnet_id']]
        batch.append({'mathnet_id': r['mathnet_id'], 'est': r['difficulty_est'],
                      'conf': r['difficulty_conf'], 'category': r['category'],
                      'topics': r['topics'], 'contest': r['contest_raw'],
                      'problem': ds[i]['problem_markdown'][:args.chars],
                      'solution_head': '\n\n---\n\n'.join(ds[i]['solutions_markdown'] or [])[:args.chars // 2]})
    d = os.path.join(ROOT, args.out)
    os.makedirs(d, exist_ok=True)
    json.dump(batch, open(os.path.join(d, 'batch.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    open(os.path.join(d, 'task.md'), 'w', encoding='utf-8').write(PROMPT.format(n=len(batch)))
    print(f'批次已出：{len(batch)} 题 → {args.out}/batch.json')
    print(f'下一步：uv run python scripts/mathnet_review.py dispatch --dir {args.out}')


def cmd_dispatch(args):
    d = os.path.join(ROOT, args.dir)
    if not os.path.exists(os.path.join(d, 'batch.json')):
        print(f'{args.dir}/batch.json 不存在，先跑 batch'); sys.exit(2)
    companion = find_companion()
    if not companion:
        print(f'找不到 codex-companion（找遍 {COMPANION_GLOB}）\n先确认 Codex 插件已安装'); sys.exit(2)
    print('派给 Codex（大批次可能数分钟）…')
    p = subprocess.run(['node', companion, 'task', '--prompt-file', os.path.join(d, 'task.md'),
                        '--cwd', d, '--write', '--json'], capture_output=True, text=True)
    print(p.stdout[-600:] or p.stderr[-600:])
    if not os.path.exists(os.path.join(d, 'verdicts.json')):
        print('Codex 未产出 verdicts.json'); sys.exit(1)
    print(f'完成。下一步：uv run python scripts/mathnet_review.py merge --dir {args.dir}')


def cmd_merge(args):
    d = os.path.join(ROOT, args.dir)
    verdicts = {v['mathnet_id']: v for v in json.load(open(os.path.join(d, 'verdicts.json'), encoding='utf-8'))}
    batch = {b['mathnet_id']: b for b in json.load(open(os.path.join(d, 'batch.json'), encoding='utf-8'))}
    rows = [json.loads(l) for l in open(POOL, encoding='utf-8') if l.strip()]
    stat = {'agree': 0, 'gap1': 0, 'gap2': 0, 'skip': 0, 'figure': 0, 'text': 0, 'topic': 0}
    cur_est = {}   # 候选池现值（批次出题后表可能已修，一律以现值为准）
    for r in rows:
        v = verdicts.get(r['mathnet_id'])
        if not v:
            continue
        cur_est[r['mathnet_id']] = r['difficulty_est']
        gap = abs(v['difficulty_codex'] - r['difficulty_est'])
        r['difficulty_codex'] = v['difficulty_codex']
        r['codex_recommend'] = v['recommend']
        r['needs_review'] = bool(gap >= 2 or v['topics_verdict'] == 'wrong'
                                 or v['text_quality'] == 'broken' or v['needs_figure'])
        stat['agree' if gap == 0 else ('gap1' if gap == 1 else 'gap2')] += 1
        stat['skip'] += v['recommend'] == 'skip'
        stat['figure'] += v['needs_figure']
        stat['text'] += v['text_quality'] != 'clean'
        stat['topic'] += v['topics_verdict'] != 'agree'
    with open(POOL, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    n = len(verdicts)
    print(f'已回填 {n} 题评审到候选池\n')
    print(f"难度：完全一致 {stat['agree']}，差 1 档 {stat['gap1']}，差 ≥2 档 {stat['gap2']}（→ needs_review）")
    print(f"标签有异议 {stat['topic']}／转录非 clean {stat['text']}／依赖图形 {stat['figure']}／建议 skip {stat['skip']}")
    print('\n分歧明细（人工定夺）：')
    flagged = 0
    for mid, v in verdicts.items():
        est = cur_est.get(mid, batch[mid]['est'])
        drift = f"（批次时★{batch[mid]['est']}，表已修）" if est != batch[mid]['est'] else ''
        if abs(v['difficulty_codex'] - est) >= 2 or v['recommend'] == 'skip' or v['needs_figure']:
            flagged += 1
            print(f"  {mid} 规则★{est}{drift} → Codex★{v['difficulty_codex']}"
                  f" | {v['recommend']} | {v['recommend_reason'][:80]}")
    if not flagged:
        print('  （无：难度分歧均 ≤1 档，无 skip、无图形依赖）')
    print('\n注：Codex 评审是第二意见，不是定稿。正式 difficulty 仍由教练在入库时按 SPEC 第 4 节裁定。')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('batch')
    b.add_argument('--category'); b.add_argument('--difficulty'); b.add_argument('--node')
    b.add_argument('--conf', choices=['high', 'mid', 'low'], default='mid')
    b.add_argument('--n', type=int, default=12); b.add_argument('--seed', type=int, default=7)
    b.add_argument('--chars', type=int, default=2200)
    b.add_argument('--out', default='data/review/batch-01')
    for name in ('dispatch', 'merge'):
        s = sub.add_parser(name)
        s.add_argument('--dir', required=True)
    args = ap.parse_args()
    {'batch': cmd_batch, 'dispatch': cmd_dispatch, 'merge': cmd_merge}[args.cmd](args)


if __name__ == '__main__':
    main()
