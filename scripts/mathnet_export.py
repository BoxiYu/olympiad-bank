#!/usr/bin/env python3
"""MathNet 全量导出：把数据集全文摊成「板块 × 知识点」的 markdown 树，供人工检索选题。

用法：
  uv run --group mathnet python scripts/mathnet_export.py               # 全量导出（含配图）
  uv run --group mathnet python scripts/mathnet_export.py --no-images   # 只导文本，快 10 倍
  uv run --group mathnet python scripts/mathnet_export.py --out /tmp/x  # 导到别处

输入：HF 本地缓存的 ShadenA/MathNet（all config）+ candidates/mathnet.jsonl + taxonomy/registry.yml
输出：mathnet-full/（gitignore，可随时重建）——与 candidates/mathnet.jsonl 只存预览不同，这里是全文。
确定性：同一数据集快照 + 同版本候选池 + 同一入库/评审快照 → 原文树与索引逐字节一致；
已落盘的译文产物（index.en/zh.md 与 translation.json）在重导出时按 mathnet_id 原样保留。

与候选池的分工：candidates/mathnet.jsonl 是给管线用的索引（每题一行、只有 200 字预览），
本脚本是给人用的全文视图。分类不另立一套，直接复用候选池已判定的板块与知识点。

题面/解法/答案逐字照录 MathNet 原文，不做任何改写、补全或路径重写——包括原文里
`attached_image_N.png` 这种位置式插图引用。为了让这些引用原样可解析，每题独占一个目录、
配图作为 index.md 的兄弟文件落盘（详见 README 的「目录结构」）。
"""
import argparse
import atexit
import glob
import hashlib
import json
import os
import re
import shlex
import signal
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter

from mathnet_ingest import in_bank_snapshot
from mathnet_translation_assets import (
    EXPORT_RECOVERY_MARKER,
    TRANSLATION_RUN_DIRNAME,
    TRANSLATION_STASH_ARCHIVE_DIRNAME,
    TRANSLATION_STASH_PREFIX,
)
from mathnet_translate import update_index_line

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_PATH = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
REGISTRY_PATH = os.path.join(ROOT, 'taxonomy', 'registry.yml')
DEFAULT_OUT = os.path.join(ROOT, 'mathnet-full')
REPO_ID = 'ShadenA/MathNet'

STARS = {1: '★', 2: '★★', 3: '★★★', 4: '★★★★', 5: '★★★★★'}
UNCLASSIFIED = '_未分类'   # 候选池判定 out_of_scope、无板块的题
UNSPECIFIED = '_未细分'    # 有板块但候选池没判出知识点的题
VARIANT_LANGS = ('en', 'zh')
VARIANT_STATES = ('passthrough', 'translated', 'verified_identical', 'failed', 'missing')
INDEX_EXPORT_METADATA_FIELDS = ('topics_flat', 'difficulty_conf', 'in_bank')


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


TRANSLATION_ARTIFACTS = ('translation.json',) + tuple(f'index.{lang}.md' for lang in VARIANT_LANGS)


def translation_count(path):
    """统计暂存树中的 translation.json 数量，供恢复提示给出可核对的资产数。"""
    return sum('translation.json' in filenames for _root, _dirs, filenames in os.walk(path))


def translation_stash_roots(out):
    """同时发现新版内置暂存与旧版兄弟暂存，便于升级后主动认领历史现场。"""
    internal = [os.path.join(out, name) for name in os.listdir(out)
                if name.startswith(TRANSLATION_STASH_PREFIX)
                and name != TRANSLATION_STASH_ARCHIVE_DIRNAME
                and os.path.isdir(os.path.join(out, name))]
    legacy = [path for path in glob.glob(out + TRANSLATION_STASH_PREFIX + '*')
              if os.path.isdir(path)]
    return sorted(internal + legacy)


def describe_stashes(roots):
    details = []
    for root in roots:
        notes = []
        if os.path.isdir(os.path.join(root, TRANSLATION_RUN_DIRNAME)):
            notes.append('含翻译续跑账本')
        if os.path.isfile(os.path.join(root, EXPORT_RECOVERY_MARKER)):
            notes.append('含导出恢复标记')
        note = f'，{"、".join(notes)}' if notes else ''
        details.append(f'{root}（{translation_count(root)} 份 translation.json{note}）')
    return '；'.join(details)


def _prefer_live_command(out):
    return ('uv run --group mathnet python scripts/mathnet_export.py '
            f'--out {shlex.quote(out)} --prefer-live-translations')


def _archive_stashed_path(out, path, label):
    """显式采用当前产物前，先把旧暂存版本移进 checker 会跳过的永久备份区。"""
    archive_root = os.path.join(out, TRANSLATION_STASH_ARCHIVE_DIRNAME)
    os.makedirs(archive_root, exist_ok=True)
    backup = tempfile.mkdtemp(prefix=f'{safe(label)}-', dir=archive_root)
    archived = os.path.join(backup, 'stashed')
    os.replace(path, archived)
    return archived


def stash_translations(out, prefer_live_translations=False):
    """重导出前暂存译文，并主动认领上次中断留下的暂存目录。

    暂存目录刻意放在 ``out/.translations-*``：默认 out 是已被 .gitignore 覆盖的
    mathnet-full/，所以 git clean -fd 不会删它；清空原文树时也显式跳过它。这样即使
    SIGKILL 绕过 finally/atexit，付费译文仍在同盘、被忽略且可由下一次运行认领。
    """
    roots = translation_stash_roots(out)
    if len(roots) > 1:
        die(f'发现多个译文暂存目录，无法判断应认领哪一个：{describe_stashes(roots)}；'
            '未删除任何暂存，请先核对')

    stash_root = roots[0] if roots else None
    if stash_root is not None and os.path.dirname(stash_root) != out:
        suffix = os.path.basename(stash_root).split('.translations-', 1)[-1]
        protected_root = os.path.join(out, TRANSLATION_STASH_PREFIX + suffix)
        os.replace(stash_root, protected_root)
        print(f'  主动认领旧版译文暂存：{stash_root} -> {protected_root}')
        stash_root = protected_root

    live = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(out, 'by-topic')):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        present = [fn for fn in filenames if fn in TRANSLATION_ARTIFACTS]
        if not present:
            continue
        mid = os.path.basename(dirpath)
        live.append((mid, dirpath, present))

    live_run = os.path.join(out, TRANSLATION_RUN_DIRNAME)
    if stash_root is None:
        # 每次 destructive prepare 都先落持久恢复标记；即使最后一份译文已经 restore，
        # 新 index.jsonl 提交前的 SIGKILL 也仍能证明残留树是可恢复的本脚本产物。
        stash_root = tempfile.mkdtemp(prefix=TRANSLATION_STASH_PREFIX, dir=out)
    marker = os.path.join(stash_root, EXPORT_RECOVERY_MARKER)
    with open(marker, 'a', encoding='utf-8'):
        pass

    conflicting_mids = []
    run_conflict = False
    if stash_root is not None:
        for mid, _dirpath, present in live:
            dest = os.path.join(stash_root, mid)
            if any(os.path.exists(os.path.join(dest, fn)) for fn in present):
                conflicting_mids.append(mid)
        run_conflict = (os.path.isdir(live_run)
                        and os.path.exists(os.path.join(stash_root, TRANSLATION_RUN_DIRNAME)))

    if conflicting_mids or run_conflict:
        conflict_items = [f'{mid} 的译文产物' for mid in conflicting_mids]
        if run_conflict:
            conflict_items.append(TRANSLATION_RUN_DIRNAME)
        if not prefer_live_translations:
            die('译文暂存冲突：当前语料与中断恢复暂存同时含有 '
                f'{"、".join(conflict_items)}；未覆盖或删除任何文件。若这些当前产物是中断后补跑的'
                '新译文，请核对后执行：\n  '
                f'{_prefer_live_command(out)}\n'
                f'该开关会采用当前产物，并把旧暂存版本永久备份到 '
                f'{os.path.join(out, TRANSLATION_STASH_ARCHIVE_DIRNAME)}/')
        for mid in conflicting_mids:
            dest = os.path.join(stash_root, mid)
            archived = _archive_stashed_path(out, dest, mid)
            print(f'  已采用当前译文；旧暂存版本保留在 {archived}')
        if run_conflict:
            stashed_run = os.path.join(stash_root, TRANSLATION_RUN_DIRNAME)
            archived = _archive_stashed_path(out, stashed_run, 'translate-run')
            print(f'  已采用当前续跑账本；旧暂存版本保留在 {archived}')

    if stash_root is not None and os.path.isdir(live_run):
        os.replace(live_run, os.path.join(stash_root, TRANSLATION_RUN_DIRNAME))

    for mid, dirpath, present in live:
        dest = os.path.join(stash_root, mid)
        os.makedirs(dest, exist_ok=True)
        for fn in present:
            source = os.path.join(dirpath, fn)
            target = os.path.join(dest, fn)
            os.replace(source, target)

    stash = {}
    if stash_root is not None:
        for name in os.listdir(stash_root):
            path = os.path.join(stash_root, name)
            if name != TRANSLATION_RUN_DIRNAME and os.path.isdir(path):
                stash[name] = path
        print(f'  主动认领中断遗留暂存 {stash_root}：{translation_count(stash_root)} 份译文')
    return stash, stash_root


def restore_translation_run(out, stash_root):
    """清空派生树后立即放回续跑账本；SIGKILL 前若未走到这里，账本仍安全留在暂存。"""
    if stash_root is None:
        return
    source = os.path.join(stash_root, TRANSLATION_RUN_DIRNAME)
    if not os.path.isdir(source):
        return
    target = os.path.join(out, TRANSLATION_RUN_DIRNAME)
    os.replace(source, target)
    print(f'  已恢复翻译续跑账本 {target}')


def restore_translations(stash, mid, pdir):
    """把暂存的译文产物放回该题的新目录（分类挪了窝也跟着走，产物按 mathnet_id 归属）。"""
    src = stash.pop(mid, None)
    if src is None:
        return
    for fn in os.listdir(src):
        os.replace(os.path.join(src, fn), os.path.join(pdir, fn))
    os.rmdir(src)


def prepare_out(out, prefer_live_translations=False):
    """清空输出目录（译文产物先暂存后回填，不随树销毁）。只肯删自己产出的目录，
    避免 --out 指错把别人的东西删了。"""
    if not os.path.exists(out):
        os.makedirs(out)
    if not os.path.isdir(out):
        die(f'{out} 不是目录')

    roots = translation_stash_roots(out)
    if len(roots) > 1:
        die(f'发现多个译文暂存目录，拒绝清理输出：{describe_stashes(roots)}；'
            '未删除任何暂存，请先核对')
    entries = set(os.listdir(out))
    internal_roots = {os.path.basename(root) for root in roots if os.path.dirname(root) == out}
    protected_entries = internal_roots | {TRANSLATION_STASH_ARCHIVE_DIRNAME}
    ordinary_entries = entries - protected_entries
    recoverable = bool(roots and (
        translation_count(roots[0])
        or os.path.isdir(os.path.join(roots[0], TRANSLATION_RUN_DIRNAME))
        or os.path.isfile(os.path.join(roots[0], EXPORT_RECOVERY_MARKER))))
    if ordinary_entries and 'index.jsonl' not in ordinary_entries and not recoverable:
        recovery = describe_stashes(roots) if roots else '未发现译文暂存目录（0 份译文）'
        die(f'{out} 非空且不像本脚本的产物（没有 index.jsonl），拒绝删除；{recovery}；'
            '请换个 --out 或核对上述现场，切勿盲目清理')
    if ordinary_entries and 'index.jsonl' not in ordinary_entries:
        print(f'  检测到上次导出中断的残留输出；将认领 {describe_stashes(roots)} 并重建')

    stash, stash_root = stash_translations(
        out, prefer_live_translations=prefer_live_translations)
    # 不 rmtree(out)：内置暂存必须跨 SIGKILL 存活。只清理已验证的输出内容，明确跳过暂存根。
    for name in os.listdir(out):
        path = os.path.join(out, name)
        if ((stash_root is not None and os.path.abspath(path) == os.path.abspath(stash_root))
                or name == TRANSLATION_STASH_ARCHIVE_DIRNAME):
            continue
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    restore_translation_run(out, stash_root)
    return stash, stash_root


def report_recovery_stashes(out):
    """退出兜底：明确点名仍存活的暂存位置与译文数。"""
    roots = translation_stash_roots(out) if os.path.isdir(out) else []
    if roots:
        print(f'  ⚠️ 译文恢复暂存仍在：{describe_stashes(roots)}；下次重跑会主动认领',
              file=sys.stderr)


def finish_translation_stash(stash, stash_root, export_completed=True):
    """finally 收尾；未认领题继续留在持久暂存，空暂存才删除。"""
    if stash_root is None or not os.path.isdir(stash_root):
        return
    if stash:
        print(f'  ⚠️ {len(stash)} 题的译文产物尚未回填，保留在 {stash_root}（'
              f'{translation_count(stash_root)} 份译文）；下次重跑会主动认领', file=sys.stderr)
        return
    marker = os.path.join(stash_root, EXPORT_RECOVERY_MARKER)
    if not export_completed:
        print(f'  ⚠️ 导出尚未提交新索引；恢复标记保留在 {marker}，下次重跑会主动认领',
              file=sys.stderr)
        return
    try:
        if os.path.isfile(marker):
            os.unlink(marker)
        os.rmdir(stash_root)
    except OSError as exc:
        print(f'  ⚠️ 空暂存目录 {stash_root} 无法移除：{exc}', file=sys.stderr)


def install_interrupt_handlers():
    """把常见终止信号转成异常，让 export 的 finally 有机会运行；SIGKILL 由持久暂存兜底。"""
    previous = {}

    def interrupt(signum, _frame):
        raise KeyboardInterrupt(f'收到 {signal.Signals(signum).name}')

    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        signum = getattr(signal, name, None)
        if signum is not None:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
    return previous


def restore_interrupt_handlers(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def sha256_file(path):
    """流式计算文件摘要，避免全量题面一次性读进内存。"""
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def variant_status(row, lang):
    """兼容读取新旧索引；缺字段或未知值一律按 missing。"""
    variants = row.get('variants') or {}
    if not isinstance(variants, dict):
        return 'missing'
    status = variants.get(lang, 'missing')
    return status if status in VARIANT_STATES else 'missing'


def translation_projection(source_path):
    """把同目录 translation.json 投影成索引字段；无译文元数据时安全降级。"""
    fallback = {
        'source_lang': 'und',
        'variants': {lang: 'missing' for lang in VARIANT_LANGS},
        'translation_stale': False,
    }
    meta_path = os.path.join(os.path.dirname(source_path), 'translation.json')
    try:
        with open(meta_path, encoding='utf-8') as fh:
            meta = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return fallback
    if not isinstance(meta, dict):
        return fallback

    source_lang = meta.get('source_lang')
    if isinstance(source_lang, str):
        source_lang = source_lang.strip().lower()
    if not isinstance(source_lang, str) or not re.fullmatch(r'[a-z]{2}|und', source_lang):
        source_lang = 'und'
    variants_meta = meta.get('variants') or {}
    if not isinstance(variants_meta, dict):
        variants_meta = {}
    variants = {}
    for lang in VARIANT_LANGS:
        item = variants_meta.get(lang)
        mode = item.get('mode') if isinstance(item, dict) else item
        if mode == 'failed':
            variants[lang] = mode
            continue
        if mode not in ('passthrough', 'translated', 'verified_identical'):
            variants[lang] = 'missing'
            continue
        target_path = os.path.join(os.path.dirname(source_path), f'index.{lang}.md')
        expected_sha = item.get('sha256') if isinstance(item, dict) else None
        try:
            actual_sha = sha256_file(target_path)
        except OSError as exc:
            variants[lang] = 'missing'
            print(f'  ⚠️ {target_path} 缺失或不可读（translation.json 声称 {mode}）：{exc}；'
                  '索引写 missing', file=sys.stderr)
            continue
        if not isinstance(expected_sha, str) or actual_sha != expected_sha:
            variants[lang] = 'missing'
            print(f'  ⚠️ {target_path} sha256 与 translation.json 不一致；索引写 missing',
                  file=sys.stderr)
            continue
        variants[lang] = mode

    return {
        'source_lang': source_lang,
        'variants': variants,
        'translation_stale': meta.get('source_sha256') != sha256_file(source_path),
    }


def project_index_row(row, out):
    """返回带三语字段的新行，不修改调用方传入的旧索引行。"""
    projected = dict(row)
    projected.update(translation_projection(os.path.join(out, row['path'])))
    return projected


def write_index(out, index_rows):
    """原子改写索引，刷新中断时保留上一版可读文件。"""
    index_path = os.path.join(out, 'index.jsonl')
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=out, delete=False) as fh:
        tmp_path = fh.name
        for row in index_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    os.replace(tmp_path, index_path)


def write_readme(out, index_rows, n_topics):
    """统计口径全部现算自 index_rows，不手抄数字。"""
    cat, topics, diff = Counter(), Counter(), Counter()
    ctry, lang, ptype, status = Counter(), Counter(), Counter(), Counter()
    coverage = {code: Counter() for code in VARIANT_LANGS}
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
        for code in VARIANT_LANGS:
            coverage_state = variant_status(r, code)
            coverage[code][coverage_state] += 1
        if r['n_images']:
            n_img += r['n_images']
            n_img_prob += 1
    n_banked = sum(1 for r in index_rows if r.get('in_bank') not in (None, 'reviewed-skip'))
    n_skip = sum(1 for r in index_rows if r.get('in_bank') == 'reviewed-skip')

    def tbl(counter, limit=None):
        return '\n'.join(f'| {k} | {v:,} |' for k, v in counter.most_common(limit))

    diff_rows = '\n'.join(
        f"| {('★' * k + f'（{k}）') if isinstance(k, int) and k else '（未估）'} | {v:,} |"
        for k, v in sorted(diff.items(), key=lambda x: (x[0] is None, x[0])))

    coverage_rows = '\n'.join(
        f"| {code} | {coverage[code]['passthrough']:,} | {coverage[code]['translated']:,} | "
        f"{coverage[code]['verified_identical']:,} | "
        f"{coverage[code]['failed']:,} | {coverage[code]['missing']:,} |"
        for code in VARIANT_LANGS
    )

    md = f"""# MathNet 全量导出

由 `scripts/mathnet_export.py` 从本地 HF 缓存的 `{REPO_ID}` 生成，共 **{len(index_rows):,}** 道题全文。

> **这不是题库。** 仓库的正式题库是 `problems/`，每道题都有 `data/review/` 的评审凭证支撑。
> 本目录是**未经核验的原始素材**，只供检索选题，别和 `problems/` 混用。

- 已在 `.gitignore` 中排除，不进版本管理；删掉后重跑脚本即可重建。
- 题面、解法、最终答案**逐字照录** MathNet 原文，未做任何改写、补全或符号复原。
- 分类不另立一套：板块与知识点取自 `candidates/mathnet.jsonl`，
  知识点的板块归属查 `taxonomy/registry.yml`。
- 译文落盘后运行 `uv run python scripts/mathnet_export.py --refresh-index`，即可只刷新索引与本说明。
- 旧索引缺少导出元数据时运行 `--backfill-index-metadata`，只定点回填
  `topics_flat` / `difficulty_conf` / `in_bank`，无需重导全文。

## 目录结构

```
mathnet-full/
├── index.jsonl          全量索引，一行一题（分类/原始标签/难度及置信度/来源/答案/入库状态/路径）
├── by-topic/            主轴：板块 / 知识点 / <题号>/       ← 真实目录
│   └── <板块>/<知识点>/<题号>/index.md          原文
│                                index.en.md       英文版
│                                index.zh.md       中文版
│                                translation.json  译文元数据
│                                attached_image_1.png ...
└── by-contest/          次轴：国家 / 赛事 / <题号>          ← 符号链接
```

`index.md` 是原文且逐字照录；`index.zh.md` / `index.en.md` 是机器生成、未经人工核验的派生产物，
不得当作题源引用。译文产物与状态的语义正本见 `docs/译文契约-mathnet-full.md`。

**为什么每题一个目录而不是一个 .md**：MathNet 原文用 `attached_image_N.png` 这种位置式
相对引用插图（编号 1..N，全库 {n_img_prob:,} 道有图题零例外）。把配图放成 `index.md` 的兄弟
文件，原文引用一个字都不用改就能正确解析——这是为守住「逐字照录」付的结构代价。

一题可能挂多个知识点。**真实目录只放在主板块的那个知识点下**，其余知识点目录里是指向它的
相对符号链接，所以按知识点浏览不会漏题，但对全树 `find | wc -l` 会重复计数。
精确统计一律以 `index.jsonl` 为准。

## 分布

### 三语覆盖率

| 语言 | passthrough | translated | verified_identical | failed | missing |
| --- | ---: | ---: | ---: | ---: | ---: |
{coverage_rows}

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

按知识点 + 难度筛选，同时跳过已入库与评审弃用的题（选题的常规起点）：

```bash
python3 -c "
import json
for line in open('mathnet-full/index.jsonl'):
    r = json.loads(line)
    if '不等式' in r['topics'] and r['difficulty_est'] == 4 and not r.get('in_bank'):
        print(r['mathnet_id'], r['contest'], r['path'])
"
```

按目标语言与覆盖状态筛选（兼容尚无三语字段的旧索引）：

```bash
python3 -c "
import json
target_lang, target_status = 'zh', 'translated'
for line in open('mathnet-full/index.jsonl'):
    r = json.loads(line)
    status = (r.get('variants') or {{}}).get(target_lang, 'missing')
    if status == target_status:
        print(r['mathnet_id'], r['path'])
"
```

在某个板块里全文检索题面（`--include` 避免顺着符号链接重复命中）：

```bash
grep -rl --include=index.md "functional equation" mathnet-full/by-topic/algebra/
```

## 已知口径

- `topics_flat` 是 MathNet 原始英文标签路径，逐字照录；`topics` 才是本仓库的知识点判定。
- `difficulty_conf` 是候选池难度估级的置信度，档位正本见 `scripts/mathnet_ingest.py`。
- `in_bank` 是**导出时点快照**：题号（已入库，共 {n_banked:,} 道）＞ `reviewed-skip`
  （评审明确弃用，共 {n_skip:,} 道）＞ null（未经评审）。精确归属以 `problems/` 的
  frontmatter 与 `data/review/` 评审凭证为准，别拿快照当正本引用。
- 配图共 {n_img:,} 张，分布在 {n_img_prob:,} 道题上，其余题目无图。
- 依赖图形、无法用文字复原的题按铁律不可入库；这里保留原图只是便于判断。
- `status` 非 `ok` 的题在正文顶部有 ⚠️ 标记，共 {status.get('out_of_scope', 0):,} 道。
"""
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(md)


def refresh_index(out):
    """不访问 MathNet 语料，只从现有原文与 translation.json 刷新索引和 README。"""
    index_path = os.path.join(out, 'index.jsonl')
    if not os.path.exists(index_path):
        die(f'{index_path} 不存在；--refresh-index 只能用于已有导出目录')
    with open(index_path, encoding='utf-8') as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    rows = [project_index_row(row, out) for row in rows]
    write_index(out, rows)
    write_readme(out, rows, len({t for row in rows for t in row.get('topics', [])}))
    print(f'{os.path.relpath(out, ROOT)}/ 索引与 README 已刷新：{len(rows)} 题')
    return 0


def topics_flat_from_exported_source(out, row, line_number):
    """从旧导出 index.md 的元数据区还原 topics_flat；只读，不触碰原文文件。"""
    relative = row.get('path')
    if not isinstance(relative, str) or not relative:
        die(f'index.jsonl 第 {line_number} 行缺少 path')
    source_path = os.path.abspath(os.path.join(out, relative))
    try:
        if os.path.commonpath([os.path.abspath(out), source_path]) != os.path.abspath(out):
            die(f'index.jsonl 第 {line_number} 行 path 越出导出目录：{relative}')
    except ValueError:
        die(f'index.jsonl 第 {line_number} 行 path 非法：{relative}')
    try:
        with open(source_path, encoding='utf-8') as fh:
            header = fh.read().split('\n## 题面\n', 1)[0]
    except OSError as exc:
        die(f'无法读取原文 {source_path}：{exc}')
    prefix = '- MathNet 原始标签：'
    values = [line[len(prefix):] for line in header.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        die(f'{source_path} 的元数据区应恰有一行“MathNet 原始标签”，实际 {len(values)} 行')
    return [] if values[0] == '（无）' else values[0].split('; ')


def backfill_index_metadata(out):
    """只定点回填三个导出元数据键；不访问全文语料，也不重写该行其他字节。"""
    index_path = os.path.join(out, 'index.jsonl')
    if not os.path.exists(index_path):
        die(f'{index_path} 不存在；--backfill-index-metadata 只能用于已有导出目录')
    meta = load_pool()
    bank_marks = in_bank_snapshot()
    try:
        with open(index_path, 'rb') as fh:
            original = fh.read()
        text = original.decode('utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        die(f'无法读取 {index_path}：{exc}')

    output = []
    updated = 0
    for line_number, complete_line in enumerate(text.splitlines(keepends=True), 1):
        ending = complete_line[len(complete_line.rstrip('\r\n')):]
        line = complete_line[:-len(ending)] if ending else complete_line
        if not line.strip():
            output.append(complete_line)
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            die(f'{index_path} 第 {line_number} 行无法解析：{exc}')
        mid = row.get('mathnet_id') if isinstance(row, dict) else None
        if not isinstance(mid, str) or not mid:
            die(f'{index_path} 第 {line_number} 行缺少 mathnet_id')
        meta_row = meta.get(mid)
        if meta_row is None:
            die(f'{index_path} 第 {line_number} 行的 {mid} 在候选池中不存在；未写回任何内容')
        projection = {
            'topics_flat': topics_flat_from_exported_source(out, row, line_number),
            'difficulty_conf': meta_row.get('difficulty_conf'),
            'in_bank': bank_marks.get(mid),
        }
        output.append(update_index_line(
            line, projection, fields=INDEX_EXPORT_METADATA_FIELDS) + ending)
        updated += 1

    data = ''.join(output).encode('utf-8')
    with tempfile.NamedTemporaryFile('wb', dir=out, delete=False) as fh:
        tmp_path = fh.name
        fh.write(data)
    os.replace(tmp_path, index_path)
    print(f'{os.path.relpath(out, ROOT)}/ 索引元数据已定点回填：{updated} 题（仅 '
          f'{" / ".join(INDEX_EXPORT_METADATA_FIELDS)}）')
    return 0


def export_prepared(out, with_images, node_cat, meta, bank_marks, shards, stash):
    """在已清空且译文已暂存的 out 中生成新树；生命周期保护由 export 统一负责。"""
    import pyarrow.parquet as pq

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
            restore_translations(stash, mid, pdir)
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

            index_rows.append(project_index_row({
                'mathnet_id': mid,
                'path': os.path.relpath(os.path.join(pdir, 'index.md'), out),
                'category': meta_row.get('category'),
                'topics': meta_row.get('topics') or [],
                'topics_flat': rec.get('topics_flat') or [],
                'difficulty_est': meta_row.get('difficulty_est'),
                'difficulty_conf': meta_row.get('difficulty_conf'),
                'country': meta_row.get('country'),
                'contest': meta_row.get('contest_raw'),
                'year': meta_row.get('year'),
                'problem_type': rec.get('problem_type'),
                'language': rec.get('language'),
                'n_images': len(rec['images']),
                'n_solutions': len(rec.get('solutions_markdown') or []),
                'final_answer': rec.get('final_answer'),
                'status': meta_row.get('status'),
                'in_bank': bank_marks.get(mid),
            }, out))
        print(f'[{si}/{len(shards)}] {os.path.basename(shard)} → 累计 {stat["problems"]} 题', flush=True)

    write_index(out, index_rows)
    write_readme(out, index_rows, len({t for r in index_rows for t in r['topics']}))

    rel = os.path.relpath(out, ROOT)
    print(f'{rel}/ 已生成：{stat["problems"]} 题、{stat["images"]} 张配图')
    print(f'  符号链接: 知识点 {stat["links_topic"]}、赛事 {stat["links_contest"]}')
    if stat['missing_meta']:
        print(f'  ⚠️ 候选池里查不到的题 {stat["missing_meta"]} 道（候选池版本落后于数据集？先重跑 ingest）')
        return 1
    return 0


def export(out, with_images, prefer_live_translations=False):
    node_cat = load_node_category()
    meta = load_pool()
    bank_marks = in_bank_snapshot()   # 导出时点重扫，不沿用候选池建池时的旧快照
    shards = sorted(glob.glob(os.path.join(snapshot_dir(), 'data', 'all', '*.parquet')))
    if not shards:
        die('快照里没有 data/all/*.parquet，缓存可能不完整')

    previous_handlers = install_interrupt_handlers()
    stash, stash_root = {}, None
    export_completed = False
    exit_notice = lambda: report_recovery_stashes(out)
    atexit.register(exit_notice)
    try:
        # stash → 清理 → 逐题 restore 的整个窗口必须受 finally 保护；信号会转成异常走这里。
        stash, stash_root = prepare_out(
            out, prefer_live_translations=prefer_live_translations)
        result = export_prepared(out, with_images, node_cat, meta, bank_marks, shards, stash)
        export_completed = True
        return result
    finally:
        finish_translation_stash(stash, stash_root, export_completed=export_completed)
        report_recovery_stashes(out)
        atexit.unregister(exit_notice)
        restore_interrupt_handlers(previous_handlers)


def main():
    ap = argparse.ArgumentParser(description='把 MathNet 全量导出成板块 × 知识点的 markdown 树')
    ap.add_argument('--out', default=DEFAULT_OUT, help=f'输出目录（默认 {os.path.relpath(DEFAULT_OUT, ROOT)}/）')
    ap.add_argument('--no-images', action='store_true', help='不导配图，只出文本')
    ap.add_argument('--prefer-live-translations', action='store_true',
                    help=f'解决中断暂存冲突：采用当前补跑产物，旧版本移入 '
                         f'{TRANSLATION_STASH_ARCHIVE_DIRNAME}/')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--refresh-index', action='store_true',
                      help='不读数据集，只按已有 index.md / translation.json 刷新索引与 README')
    mode.add_argument('--backfill-index-metadata', action='store_true',
                      help='不重导全文，只定点回填 topics_flat / difficulty_conf / in_bank')
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    if args.refresh_index:
        result = refresh_index(out)
    elif args.backfill_index_metadata:
        result = backfill_index_metadata(out)
    else:
        result = export(out, with_images=not args.no_images,
                        prefer_live_translations=args.prefer_live_translations)
    sys.exit(result)


if __name__ == '__main__':
    main()
