#!/usr/bin/env python3
"""MathNet 全量导出：把数据集全文摊成「板块 × 知识点」的 markdown 树，供人工检索选题。

用法：
  uv run --group mathnet python scripts/mathnet_export.py               # 全量导出（含配图）
  uv run --group mathnet python scripts/mathnet_export.py --no-images   # 只导文本，快 10 倍
  uv run --group mathnet python scripts/mathnet_export.py --out /tmp/x  # 导到别处

输入：HF 本地缓存的 ShadenA/MathNet（all config）+ candidates/mathnet.jsonl + taxonomy/registry.yml
输出：mathnet-full/（gitignore，可随时重建）——与 candidates/mathnet.jsonl 只存预览不同，这里是全文。
确定性：同一数据集快照 + 同版本候选池 → 输出逐字节一致。

与候选池的分工：candidates/mathnet.jsonl 是给管线用的索引（每题一行、只有 200 字预览），
本脚本是给人用的全文视图。分类不另立一套，直接复用候选池已判定的板块与知识点。

题面/解法/答案逐字照录 MathNet 原文，不做任何改写、补全或路径重写——包括原文里
`attached_image_N.png` 这种位置式插图引用。为了让这些引用原样可解析，每题独占一个目录、
配图作为 index.md 的兄弟文件落盘（详见 README 的「目录结构」）。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_PATH = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
REGISTRY_PATH = os.path.join(ROOT, 'taxonomy', 'registry.yml')
DEFAULT_OUT = os.path.join(ROOT, 'mathnet-full')
REPO_ID = 'ShadenA/MathNet'

STARS = {1: '★', 2: '★★', 3: '★★★', 4: '★★★★', 5: '★★★★★'}
UNCLASSIFIED = '_未分类'   # 候选池判定 out_of_scope、无板块的题
UNSPECIFIED = '_未细分'    # 有板块但候选池没判出知识点的题


def die(msg):
    """按 WORKFLOW.md：数据缺失是合法阻塞，如实上报，绝不凭记忆或联网重建题目数据。"""
    print(f'mathnet_export: {msg}', file=sys.stderr)
    sys.exit(1)


def snapshot_dir():
    """定位 HF 本地缓存里的数据集快照；只读本地，不触发下载。"""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        die('缺少 huggingface_hub，请用 uv run --group mathnet python ... 运行')
    try:
        return snapshot_download(REPO_ID, repo_type='dataset', local_files_only=True)
    except Exception as exc:
        die(f'本地 HF 缓存里没有 {REPO_ID}：{exc}\n'
            f'  先执行：uv run --group mathnet python -c '
            f'"from huggingface_hub import snapshot_download; '
            f"snapshot_download('{REPO_ID}', repo_type='dataset')\"")


def load_node_category():
    """知识点 → 所属板块。正本是 taxonomy/registry.yml（category → 节点 → 别名）。"""
    import yaml
    with open(REGISTRY_PATH, encoding='utf-8') as fh:
        reg = yaml.safe_load(fh)
    return {node: cat for cat, nodes in (reg or {}).items() for node in (nodes or {})}


def load_pool():
    if not os.path.exists(POOL_PATH):
        die('candidates/mathnet.jsonl 不存在（gitignore，需重建）：\n'
            '  uv run --group mathnet python scripts/mathnet_ingest.py')
    meta = {}
    with open(POOL_PATH, encoding='utf-8') as fh:
        for line in fh:
            row = json.loads(line)
            meta[row['mathnet_id']] = row
    return meta


def safe(name, fallback='_未知'):
    """把任意标签变成安全目录名：换掉路径分隔符、去控制字符、压空格、截断。"""
    if not name:
        return fallback
    s = unicodedata.normalize('NFC', str(name)).strip()
    s = s.replace('/', '／').replace('\\', '＼').replace(':', '：')
    s = re.sub(r'[\x00-\x1f]', '', s)
    s = re.sub(r'\s+', ' ', s).strip(' .')
    return s[:80] or fallback


def topic_dirs(meta_row, node_cat):
    """该题应出现的 (板块, 知识点) 列表；第一个是真实目录，其余落符号链接。

    知识点挂在它自己所属的板块下（查 registry.yml），而不是一律挂在该题的主板块下——
    否则 geometry/ 底下会冒出「不定方程」这类根本不属于几何的节点。
    """
    cat = meta_row.get('category')
    if not cat:
        return [(UNCLASSIFIED, UNCLASSIFIED)]
    topics = meta_row.get('topics') or []
    if not topics:
        return [(cat, UNSPECIFIED)]
    places = [(node_cat.get(t, cat), t) for t in topics]
    # 真实目录要待在该题的主板块下，所以把主板块的知识点提到首位
    for i, (c, _) in enumerate(places):
        if c == cat:
            places.insert(0, places.pop(i))
            break
    return places


def render(rec, meta_row):
    """渲染单题 markdown：元数据区可加工，题面/解法/答案逐字照录。"""
    diff = meta_row.get('difficulty_est')
    src = ' / '.join(str(x) for x in
                     [meta_row.get('contest_raw'), meta_row.get('country'), meta_row.get('year')] if x)
    n_img = len(rec.get('images') or [])
    tags = '; '.join(rec.get('topics_flat') or [])

    lines = [f"# {rec['id']}", '']
    lines += [
        f"- 板块：{meta_row.get('category') or '（未分类）'}",
        f"- 知识点：{'、'.join(meta_row.get('topics') or []) or '（未细分）'}",
        f"- 难度估计：{STARS.get(diff, '？')}（{diff}，置信度 {meta_row.get('difficulty_conf')}）",
        f"- 来源：{src or '（未标注）'}",
        f"- 题型：{rec.get('problem_type') or '（未标注）'}",
        f"- 语言：{rec.get('language') or '（未标注）'}",
        f'- 配图：{n_img} 张（同目录 attached_image_N.png）' if n_img else '- 配图：无',
        f"- MathNet 原始标签：{tags or '（无）'}",
    ]
    if meta_row.get('status') != 'ok':
        lines.append(f"- ⚠️ 候选池标记：{meta_row.get('status')}（{meta_row.get('excluded_reason')}）")

    lines += ['', '## 题面', '', rec.get('problem_markdown') or '（原文为空）', '']
    sols = rec.get('solutions_markdown') or []
    for i, sol in enumerate(sols, 1):
        lines += [f'## 解法 {i}', '', sol or '（原文为空）', '']
    if not sols:
        lines += ['## 解法', '', '（数据集未提供）', '']
    fa = rec.get('final_answer')
    lines += ['## 最终答案', '', fa if fa else '（数据集未提供 / 证明题）', '']
    return '\n'.join(lines)


def prepare_out(out):
    """清空输出目录。只肯删自己产出的目录，避免 --out 指错把别人的东西删了。"""
    if not os.path.exists(out):
        os.makedirs(out)
        return
    if not os.path.isdir(out):
        die(f'{out} 不是目录')
    entries = set(os.listdir(out))
    if entries and 'index.jsonl' not in entries:
        die(f'{out} 非空且不像本脚本的产物（没有 index.jsonl），拒绝删除；请换个 --out 或手动清理')
    shutil.rmtree(out)
    os.makedirs(out)


def write_readme(out, index_rows, n_topics):
    """统计口径全部现算自 index_rows，不手抄数字。"""
    cat, topics, diff = Counter(), Counter(), Counter()
    ctry, lang, ptype, status = Counter(), Counter(), Counter(), Counter()
    n_img = n_img_prob = 0
    for r in index_rows:
        cat[r['category'] or '（未分类）'] += 1
        for t in r['topics']:
            topics[t] += 1
        diff[r['difficulty_est']] += 1
        ctry[r['country'] or '（未标注）'] += 1
        lang[r['language'] or '（未标注）'] += 1
        ptype[r['problem_type'] or '（未标注）'] += 1
        status[r['status']] += 1
        if r['n_images']:
            n_img += r['n_images']
            n_img_prob += 1

    def tbl(counter, limit=None):
        return '\n'.join(f'| {k} | {v:,} |' for k, v in counter.most_common(limit))

    diff_rows = '\n'.join(
        f"| {('★' * k + f'（{k}）') if isinstance(k, int) and k else '（未估）'} | {v:,} |"
        for k, v in sorted(diff.items(), key=lambda x: (x[0] is None, x[0])))

    md = f"""# MathNet 全量导出

由 `scripts/mathnet_export.py` 从本地 HF 缓存的 `{REPO_ID}` 生成，共 **{len(index_rows):,}** 道题全文。

> **这不是题库。** 仓库的正式题库是 `problems/`，每道题都有 `data/review/` 的评审凭证支撑。
> 本目录是**未经核验的原始素材**，只供检索选题，别和 `problems/` 混用。

- 已在 `.gitignore` 中排除，不进版本管理；删掉后重跑脚本即可重建。
- 题面、解法、最终答案**逐字照录** MathNet 原文，未做任何改写、补全或符号复原。
- 分类不另立一套：板块与知识点取自 `candidates/mathnet.jsonl`，
  知识点的板块归属查 `taxonomy/registry.yml`。

## 目录结构

```
mathnet-full/
├── index.jsonl          全量索引，一行一题（分类/难度/来源/答案/路径）
├── by-topic/            主轴：板块 / 知识点 / <题号>/       ← 真实目录
│   └── <板块>/<知识点>/<题号>/index.md
│                                attached_image_1.png ...
└── by-contest/          次轴：国家 / 赛事 / <题号>          ← 符号链接
```

**为什么每题一个目录而不是一个 .md**：MathNet 原文用 `attached_image_N.png` 这种位置式
相对引用插图（编号 1..N，全库 {n_img_prob:,} 道有图题零例外）。把配图放成 `index.md` 的兄弟
文件，原文引用一个字都不用改就能正确解析——这是为守住「逐字照录」付的结构代价。

一题可能挂多个知识点。**真实目录只放在主板块的那个知识点下**，其余知识点目录里是指向它的
相对符号链接，所以按知识点浏览不会漏题，但对全树 `find | wc -l` 会重复计数。
精确统计一律以 `index.jsonl` 为准。

## 分布

### 板块

| 板块 | 题数 |
| --- | --- |
{tbl(cat)}

### 难度估计

候选池的启发式判定，不是人工标注；入库前仍须按 `docs/入库SOP-MathNet.md` 走评审。

| 难度 | 题数 |
| --- | --- |
{diff_rows}

### 知识点（共 {n_topics} 个）

| 知识点 | 题数 |
| --- | --- |
{tbl(topics)}

### 题型

| 题型 | 题数 |
| --- | --- |
{tbl(ptype)}

### 来源（前 25）

| 国家 / 赛事 | 题数 |
| --- | --- |
{tbl(ctry, 25)}

### 语言（前 10）

| 语言 | 题数 |
| --- | --- |
{tbl(lang, 10)}

## 常用检索

按知识点 + 难度筛选：

```bash
python3 -c "
import json
for line in open('mathnet-full/index.jsonl'):
    r = json.loads(line)
    if '不等式' in r['topics'] and r['difficulty_est'] == 4:
        print(r['mathnet_id'], r['contest'], r['path'])
"
```

在某个板块里全文检索题面（`--include` 避免顺着符号链接重复命中）：

```bash
grep -rl --include=index.md "functional equation" mathnet-full/by-topic/algebra/
```

## 已知口径

- 配图共 {n_img:,} 张，分布在 {n_img_prob:,} 道题上，其余题目无图。
- 依赖图形、无法用文字复原的题按铁律不可入库；这里保留原图只是便于判断。
- `status` 非 `ok` 的题在正文顶部有 ⚠️ 标记，共 {status.get('out_of_scope', 0):,} 道。
"""
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(md)


def export(out, with_images):
    import pyarrow.parquet as pq

    node_cat = load_node_category()
    meta = load_pool()
    shards = sorted(glob.glob(os.path.join(snapshot_dir(), 'data', 'all', '*.parquet')))
    if not shards:
        die('快照里没有 data/all/*.parquet，缓存可能不完整')

    prepare_out(out)
    cols = ['id', 'problem_markdown', 'solutions_markdown', 'country', 'competition',
            'topics_flat', 'language', 'problem_type', 'final_answer']
    stat = Counter()
    index_rows = []

    for si, shard in enumerate(shards, 1):
        pf = pq.ParquetFile(shard)
        recs = pf.read(columns=cols).to_pylist()
        images = (pf.read(columns=['images']).column('images').to_pylist()
                  if with_images else [None] * len(recs))

        for rec, ims in zip(recs, images):
            mid = rec['id']
            meta_row = meta.get(mid)
            if meta_row is None:
                stat['missing_meta'] += 1
                meta_row = {'category': None, 'topics': [], 'status': 'unknown'}
            rec['images'] = ims or []

            places = topic_dirs(meta_row, node_cat)
            cat, topic = places[0]
            pdir = os.path.join(out, 'by-topic', safe(cat), safe(topic), mid)
            os.makedirs(pdir, exist_ok=True)
            with open(os.path.join(pdir, 'index.md'), 'w', encoding='utf-8') as fh:
                fh.write(render(rec, meta_row))
            stat['problems'] += 1

            # 配图按原文的位置式引用命名，作为 index.md 的兄弟文件
            for k, im in enumerate(rec['images'], 1):
                blob = im.get('bytes')
                if blob:
                    with open(os.path.join(pdir, f'attached_image_{k}.png'), 'wb') as fh:
                        fh.write(blob)
                    stat['images'] += 1

            for cat2, topic2 in places[1:]:
                link_dir = os.path.join(out, 'by-topic', safe(cat2), safe(topic2))
                os.makedirs(link_dir, exist_ok=True)
                link = os.path.join(link_dir, mid)
                if not os.path.lexists(link):
                    os.symlink(os.path.relpath(pdir, link_dir), link)
                    stat['links_topic'] += 1

            link_dir = os.path.join(out, 'by-contest', safe(meta_row.get('country'), '_未知国家'),
                                    safe(meta_row.get('contest_raw') or rec.get('competition'), '_未知赛事'))
            os.makedirs(link_dir, exist_ok=True)
            link = os.path.join(link_dir, mid)
            if not os.path.lexists(link):
                os.symlink(os.path.relpath(pdir, link_dir), link)
                stat['links_contest'] += 1

            index_rows.append({
                'mathnet_id': mid,
                'path': os.path.relpath(os.path.join(pdir, 'index.md'), out),
                'category': meta_row.get('category'),
                'topics': meta_row.get('topics') or [],
                'difficulty_est': meta_row.get('difficulty_est'),
                'country': meta_row.get('country'),
                'contest': meta_row.get('contest_raw'),
                'year': meta_row.get('year'),
                'problem_type': rec.get('problem_type'),
                'language': rec.get('language'),
                'n_images': len(rec['images']),
                'n_solutions': len(rec.get('solutions_markdown') or []),
                'final_answer': rec.get('final_answer'),
                'status': meta_row.get('status'),
            })
        print(f'[{si}/{len(shards)}] {os.path.basename(shard)} → 累计 {stat["problems"]} 题', flush=True)

    with open(os.path.join(out, 'index.jsonl'), 'w', encoding='utf-8') as fh:
        for row in index_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    write_readme(out, index_rows, len({t for r in index_rows for t in r['topics']}))

    rel = os.path.relpath(out, ROOT)
    print(f'{rel}/ 已生成：{stat["problems"]} 题、{stat["images"]} 张配图')
    print(f'  符号链接: 知识点 {stat["links_topic"]}、赛事 {stat["links_contest"]}')
    if stat['missing_meta']:
        print(f'  ⚠️ 候选池里查不到的题 {stat["missing_meta"]} 道（候选池版本落后于数据集？先重跑 ingest）')
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description='把 MathNet 全量导出成板块 × 知识点的 markdown 树')
    ap.add_argument('--out', default=DEFAULT_OUT, help=f'输出目录（默认 {os.path.relpath(DEFAULT_OUT, ROOT)}/）')
    ap.add_argument('--no-images', action='store_true', help='不导配图，只出文本')
    args = ap.parse_args()
    sys.exit(export(os.path.abspath(args.out), with_images=not args.no_images))


if __name__ == '__main__':
    main()
