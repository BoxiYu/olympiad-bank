#!/usr/bin/env python3
"""为题库补三级提示阶梯（方向 / 关键结构 / 临门一脚）。

提示只能从本题已入库的《解法要点》推出——不引入外部思路、不透出最终答案数值，
这样提示与题目同源，不构成新的核验负担（SPEC §5 铁律一：不编造）。

用法：
  uv run python scripts/hint_backfill.py batch --out data/hints/round-01   # 出批次（默认全部缺阶梯的题）
  uv run python scripts/hint_backfill.py dispatch --dir data/hints/round-01 # 派本地 Codex
  uv run python scripts/hint_backfill.py apply --dir data/hints/round-01    # 校验后写回题文件
apply 会拒绝含答案字面量的提示，并逐题打印以便人工过目。
"""
import argparse, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spar_session as sp
import bank

ROOT = sp.ROOT
LADDER_HEAD = '## 提示阶梯'

PROMPT = """你是奥数教练，为 {n} 道题各写一条三级提示阶梯。批次目录：{dir}/
（你可能被置于仓库根目录运行；读 {dir}/batch.json，结果必须写到 {dir}/hints.json，不要碰其它目录。）

三级的分工（这是学生卡住时逐级解锁的，每级之间他会再战 15 分钟）：
1. **方向**：指出该往哪个方向想（切入视角、该关注什么量），不给具体手法。
2. **关键结构**：点破核心的结构/引理/变换是什么，但不代入本题的具体数值推演。
3. **临门一脚**：给出关键的那一步怎么走，走完学生应能自己收尾。

每题一个对象：
{{"id":"A-001","hints":["方向级提示","关键结构级提示","临门一脚级提示"]}}

硬要求：
- 只能从该题《解法要点》里已有的信息推出，**不得引入解法之外的思路或外部定理名**；
- **任何一级都不得出现最终答案的数值或最终结论本身**（第 3 级也不行——它只给关键步骤，不给答案）；
- 每级 1–2 句，中文，措辞克制；三级之间必须有真实的信息梯度，不许三级说同一件事；
- 题面是英文，提示写中文（术语可保留英文）；
- 只输出这 {n} 个对象组成的 JSON 数组写入 hints.json，不要创建其他文件。
"""


def load_problems():
    return bank.load_all()


def cmd_batch(args):
    probs = load_problems()
    items = []
    for p in probs:
        fm = p['fm'] or {}
        if LADDER_HEAD in p['body']:
            continue
        if args.ids and fm.get('id') not in args.ids:
            continue
        secs = sp.split_sections(p['body'], fm['id'])
        items.append({'id': fm['id'], 'title': fm.get('title', ''),
                      'difficulty': fm.get('difficulty'), 'topics': fm.get('topics', []),
                      'problem': secs.get('题面', '').strip(),
                      'answer': secs.get('答案', '').strip(),
                      'solution': secs.get('解法要点', '').strip()})
    if not items:
        print('没有缺提示阶梯的题'); sys.exit(0)
    d = os.path.join(ROOT, args.out)
    os.makedirs(d, exist_ok=True)
    json.dump(items, open(os.path.join(d, 'batch.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    open(os.path.join(d, 'task.md'), 'w', encoding='utf-8').write(
        PROMPT.format(n=len(items), dir=args.out.rstrip('/')))
    print(f'批次已出：{len(items)} 题 → {args.out}/batch.json')
    print(f'下一步：uv run python scripts/hint_backfill.py dispatch --dir {args.out}')


def cmd_dispatch(args):
    d = os.path.join(ROOT, args.dir)
    if not os.path.exists(os.path.join(d, 'batch.json')):
        print(f'{args.dir}/batch.json 不存在，先跑 batch'); sys.exit(2)
    # 自愈：派单前按现行模板重render task.md（companion 会把 --cwd 归一到仓库根，
    # 提示词必须内含批次目录显式路径，机制同 mathnet_review.cmd_dispatch）。
    items = json.load(open(os.path.join(d, 'batch.json'), encoding='utf-8'))
    open(os.path.join(d, 'task.md'), 'w', encoding='utf-8').write(
        PROMPT.format(n=len(items), dir=os.path.relpath(d, ROOT)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mathnet_review import find_companion
    companion = find_companion()
    if not companion:
        print('找不到 codex-companion，确认 Codex 插件已安装'); sys.exit(2)
    print('派给 Codex（数分钟）…')
    p = subprocess.run(['node', companion, 'task', '--prompt-file', os.path.join(d, 'task.md'),
                        '--cwd', d, '--write', '--json'], capture_output=True, text=True)
    print((p.stdout or p.stderr)[-500:])
    if not os.path.exists(os.path.join(d, 'hints.json')):
        print('Codex 未产出 hints.json'); sys.exit(1)
    print(f'完成。下一步：uv run python scripts/hint_backfill.py apply --dir {args.dir}')


def _answer_tokens(ans, statement=''):
    """答案里的数值 token，用于检测提示是否剧透。

    题面里已出现的数字不算剧透——它们是题目给定量（如 `x_{2011}=x_0` 的 2011、
    `n ≥ 100` 的 10），提示复述它们不泄露任何答案信息。只有答案独有的数值才是剧透。
    """
    given = set(re.findall(r'\d+(?:\.\d+)?', statement))
    return {t for t in re.findall(r'\d+(?:\.\d+)?', ans) if t not in given}


def cmd_apply(args):
    d = os.path.join(ROOT, args.dir)
    hints = {h['id']: h['hints'] for h in json.load(open(os.path.join(d, 'hints.json'), encoding='utf-8'))}
    probs = {(p['fm'] or {}).get('id'): p for p in load_problems()}
    applied, rejected = [], []
    for pid, levels in hints.items():
        p = probs.get(pid)
        if not p:
            rejected.append((pid, '题号不存在')); continue
        if LADDER_HEAD in p['body']:
            rejected.append((pid, '已有提示阶梯')); continue
        if len(levels) != 3 or not all(str(x).strip() for x in levels):
            rejected.append((pid, '不是三级或有空级')); continue
        secs = sp.split_sections(p['body'], pid)
        ans = secs.get('答案', '')
        toks = _answer_tokens(ans, secs.get('题面', ''))
        leak = [t for t in toks if len(t) >= 2 and any(t in lv for lv in levels)]
        if leak and not args.force:
            rejected.append((pid, f'提示疑似含答案数值 {leak}（--force 可强收）')); continue
        block = f'\n{LADDER_HEAD}\n\n' + '\n'.join(
            f'{i}. {str(lv).strip()}' for i, lv in enumerate(levels, 1)) + '\n'
        body = p['body'].rstrip('\n') + '\n' + block
        open(p['path'], 'w', encoding='utf-8').write(body)
        applied.append(pid)
        print(f'\n【{pid}】{(p["fm"] or {}).get("title", "")}')
        for i, lv in enumerate(levels, 1):
            print(f'  {i}. {str(lv).strip()}')
    print(f'\n写回 {len(applied)} 题：{" ".join(applied)}')
    if rejected:
        print(f'拒收 {len(rejected)} 题：')
        for pid, why in rejected:
            print(f'  {pid}：{why}')
    print('\n提示由 AI 从本题《解法要点》导出，请过目；不满意直接改题文件的「## 提示阶梯」小节。')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('batch')
    b.add_argument('--out', default='data/hints/round-01')
    b.add_argument('--ids', nargs='*')
    for name in ('dispatch', 'apply'):
        s = sub.add_parser(name)
        s.add_argument('--dir', required=True)
        if name == 'apply':
            s.add_argument('--force', action='store_true', help='忽略疑似剧透警告强行写回')
    args = ap.parse_args()
    {'batch': cmd_batch, 'dispatch': cmd_dispatch, 'apply': cmd_apply}[args.cmd](args)


if __name__ == '__main__':
    main()
