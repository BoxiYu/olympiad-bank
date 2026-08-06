#!/usr/bin/env python3
"""相似度设施：题库 / MathNet 候选池的 embedding + 公式指纹索引与查询（库 + CLI 双用）

用法：
  # 只建库内题（快速可用档，不需要 HF 缓存）：
  uv run --group similar python scripts/similar_index.py build --bank-only
  # 建库内 + 候选池前 N 条 status=ok（验证用；读 HF 缓存需叠加 mathnet 组）：
  uv run --group similar --group mathnet python scripts/similar_index.py build --limit 500
  # 全量（27k 候选，耗时长）：
  uv run --group similar --group mathnet python scripts/similar_index.py build

  # 查询（纯 numpy 读索引，不加载模型，秒回）：
  uv run --group similar python scripts/similar_index.py query G-035 --top 20
  uv run --group similar python scripts/similar_index.py query MN-009s --top 20

  # 全量候选重复分组（默认只读；--write 才写 duplicates.jsonl）：
  uv run --group similar python scripts/similar_index.py dupes
  uv run --group similar python scripts/similar_index.py dupes --write
  uv run --group similar python scripts/similar_index.py dupes \
    --root /data/mathnet-full --index-dir /data/simindex

输出（query，JSONL 一行一条）：
  {"dst","score","score_text","score_formula","score_solution","same_node",
   "contest","difficulty","head"}

输出（dupes）：mathnet-full/duplicates.jsonl 一行一个候选重复组。index.jsonl 永不投影；
index.md 只读题面做词面复核，原文件完全不写。

融合规则（评审定稿）：三路分数独立给出，主排序 score = max(score_text, score_solution)；
公式重合（score_formula，overlap 系数 = |交| / min(|A|,|B|)）与同节点（same_node）
只作加分项展示，不折叠进单一权重——判定归人。

索引落盘 candidates/simindex/（gitignore，可重建）：
  bank.npz / bank_meta.jsonl     库内题：题面、解法要点两路 embedding 分开存
  cand.npz / cand_meta.jsonl     候选池 status=ok 行（dst 记作 MN-<mathnet_id>）
  config.json                    模型名 / 维度 / 构建时间与规模

模型：paraphrase-multilingual-MiniLM-L12-v2（CPU；首次运行自动下载权重约 470MB）。
128 token 截断的缓解：长文本按段落切 2-3 段，取段落 embedding 的均值再归一化。
"""
import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections import Counter

from bank_constants import CATEGORIES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAND_PATH = os.path.join(ROOT, 'candidates', 'mathnet.jsonl')
INDEX_DIR = os.path.join(ROOT, 'candidates', 'simindex')
MATHNET_FULL = os.path.join(ROOT, 'mathnet-full')
MODEL_NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
HEAD_LEN = 120

# 真实 27,817 题语料的阈值扫描中，0.92 会形成 7,060 题巨组；0.995 时最大组降至 4。
# cosine 只负责召回，最终连边还必须过 0.99 词面近似；三路分数仍分开输出、判定归人。
DEFAULT_DUPES_THRESHOLD = 0.995
DEFAULT_DUPES_LEXICAL_THRESHOLD = 0.99
DEFAULT_DUPES_BLOCK_SIZE = 256
# 正常默认阈值实测最大组为 4；100 题已属异常，限制公式比对在 4,950 对以内。
DEFAULT_FORMULA_GROUP_LIMIT = 100

FM_RE = re.compile(r'^---\n(.*?)\n---\n', re.S)


def info(*a):
    print(*a, file=sys.stderr, flush=True)


# ───────────────────────── 公式指纹 ─────────────────────────

ENV_RE = re.compile(
    r'\\begin\{(align\*?|aligned|alignat\*?|flalign\*?|gather\*?|gathered|'
    r'eqnarray\*?|multline\*?|cases|split)\}(.*?)\\end\{\1\}', re.S)
DISPLAY_RES = [
    re.compile(r'\$\$(.{1,3000}?)\$\$', re.S),
    re.compile(r'\\\[(.{1,3000}?)\\\]', re.S),
    re.compile(r'\\\((.{1,1000}?)\\\)', re.S),
]
INLINE_RE = re.compile(r'\$([^$\n]{1,600}?)\$')


def norm_formula(s):
    """单条公式归一化：NFC、剥排版噪声宏、统一别名、去空白、去首尾标点。"""
    s = unicodedata.normalize('NFC', s)
    s = re.sub(r'\\(?:left|right|[bB]igg?[lr]?|limits|displaystyle|textstyle|'
               r'quad|qquad|mathrm|mathbf|mathit|mathcal|text|operatorname)\b', '', s)
    s = re.sub(r'\\[,;!:]', '', s)
    s = s.replace('\\dfrac', '\\frac').replace('\\tfrac', '\\frac')
    s = re.sub(r'\\le\b', r'\\leq', s)
    s = re.sub(r'\\ge\b', r'\\geq', s)
    s = re.sub(r'\s+', '', s)
    return s.strip('.,;:：；，。')


def extract_formulas(text):
    """抽取全部数学块 → 归一化指纹集合。覆盖 $...$、$$...$$、\\(\\)、\\[\\]、align 族环境。
    align 族按行（\\\\）拆条并去掉对齐符 &；长度 <3 的碎片（单变量/数字）丢弃。"""
    out = set()
    if not text:
        return out

    def grab(content):
        for piece in re.split(r'\\\\', content):
            f = norm_formula(piece.replace('&', ''))
            if len(f) >= 3:
                out.add(f)

    def eat(m):
        grab(m.group(m.lastindex))  # ENV_RE 内容在 group2，其余在 group1
        return ' '

    t = ENV_RE.sub(eat, text)
    for rx in DISPLAY_RES:
        t = rx.sub(eat, t)
    INLINE_RE.sub(eat, t)
    return out


def formula_score(a, b):
    """overlap 系数：小集合中有多大比例的公式在对方也出现。展示用加分项。"""
    if not a or not b:
        return 0.0
    a, b = set(a), set(b)
    return len(a & b) / min(len(a), len(b))


# ───────────────────────── 语料装载 ─────────────────────────

def sections_of(text):
    secs = {}
    heads = list(re.finditer(r'^## (.+?)\s*$', text, re.M))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        secs[h.group(1).strip()] = text[h.end():end].strip()
    return secs


def load_bank():
    """problems/ 全部题：题面（含原文小节）与解法要点两路特征分开。"""
    import yaml
    items = []
    for cat in CATEGORIES:
        d = os.path.join(ROOT, 'problems', cat)
        for name in sorted(os.listdir(d)):
            if not name.endswith('.md'):
                continue
            text = open(os.path.join(d, name), encoding='utf-8').read()
            m = FM_RE.match(text)
            fm = (yaml.safe_load(m.group(1)) if m else None) or {}
            secs = sections_of(text)
            stmt = secs.get('题面', '')
            orig = next((v for k, v in secs.items() if k.startswith('原文')), '')
            sol = secs.get('解法要点', '')
            contest = ' '.join(str(x) for x in (fm.get('contest'), fm.get('year')) if x)
            items.append({
                'id': fm.get('id') or name[:-3],
                'text': stmt + ('\n\n' + orig if orig else ''),
                'sol': sol,
                'topics': fm.get('topics') or [],
                'contest': contest or None,
                'difficulty': fm.get('difficulty'),
                'head': re.sub(r'\s+', ' ', stmt)[:HEAD_LEN],
                'formulas': sorted(extract_formulas(stmt) | extract_formulas(orig)
                                   | extract_formulas(sol)),
            })
    return items


def load_cand_rows(limit=None):
    """candidates/mathnet.jsonl 的 status=ok 行（保持文件顺序；--limit 取前 N）。"""
    rows = []
    with open(CAND_PATH, encoding='utf-8') as f:
        for line in f:
            r = json.loads(line)
            if r.get('status') != 'ok':
                continue
            rows.append(r)
            if limit and len(rows) >= limit:
                break
    return rows


def load_cand_fulltext(ids):
    """从 HF 本地缓存取题面全文与解法全文（solutions_markdown 是列表，拼接）。"""
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit('候选池 build 需要 datasets：请用 uv run --group similar --group mathnet 运行')
    info('读取 HF 缓存 ShadenA/MathNet ...')
    ds = load_dataset('ShadenA/MathNet', 'all')['train']
    pos = {mid: i for i, mid in enumerate(ds['id'])}
    probs, sols = ds['problem_markdown'], ds['solutions_markdown']
    texts, soltexts = [], []
    for mid in ids:
        i = pos.get(mid)
        if i is None:
            texts.append('')
            soltexts.append('')
            continue
        texts.append(probs[i] or '')
        soltexts.append('\n\n'.join(sols[i] or []))
    return texts, soltexts


# ───────────────────────── 切段与编码 ─────────────────────────

def chunk_text(text, target=300, max_chunks=3):
    """128 token 截断缓解：长文本按段落（无段落则按句）切 2-3 段，供均值池化。"""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) <= target:
        return [re.sub(r'\s+', ' ', text)]
    units = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(units) == 1:
        units = [s for s in re.split(r'(?<=[。．！？!?；;])', units[0]) if s.strip()]
    n = min(max_chunks, max(2, -(-len(text) // target)))
    per = -(-sum(len(u) for u in units) // n)
    chunks, cur = [], ''
    for u in units:
        if cur and len(cur) + len(u) > per and len(chunks) < n - 1:
            chunks.append(cur)
            cur = u
        else:
            cur = cur + '\n' + u if cur else u
    if cur:
        chunks.append(cur)
    return [re.sub(r'\s+', ' ', c) for c in chunks]


def get_model():
    from sentence_transformers import SentenceTransformer
    info(f'加载模型 {MODEL_NAME}（CPU；首次运行会自动下载权重，约 470MB）...')
    return SentenceTransformer(MODEL_NAME, device='cpu')


def _dim(model):
    """embedding 维度（兼容 sentence-transformers 3.x/5.x 的方法改名）。"""
    fn = getattr(model, 'get_embedding_dimension', None) or model.get_sentence_embedding_dimension
    return fn()


def encode_features(model, texts, batch_size=64, label=''):
    """每条文本切段编码后取均值再归一化。返回 (N×dim float32, 有效 mask)。"""
    import numpy as np
    chunks, owners = [], []
    for i, t in enumerate(texts):
        for c in chunk_text(t):
            chunks.append(c)
            owners.append(i)
    dim = _dim(model)
    out = np.zeros((len(texts), dim), dtype='float32')
    if chunks:
        info(f'  编码{label}：{len(texts)} 条 → {len(chunks)} 段')
        emb = model.encode(chunks, batch_size=batch_size,
                           show_progress_bar=True, normalize_embeddings=True)
        np.add.at(out, owners, emb.astype('float32'))
    counts = np.bincount(owners, minlength=len(texts)) if owners else np.zeros(len(texts), int)
    mask = counts > 0
    norms = np.linalg.norm(out, axis=1)
    nz = norms > 0
    out[nz] /= norms[nz, None]
    return out, mask


# ───────────────────────── build ─────────────────────────

def _save_corpus(name, ids, text_emb, text_mask, sol_emb, sol_mask, meta):
    import numpy as np
    np.savez(os.path.join(INDEX_DIR, f'{name}.npz'),
             ids=np.array(ids), text=text_emb, text_mask=text_mask,
             sol=sol_emb, sol_mask=sol_mask)
    with open(os.path.join(INDEX_DIR, f'{name}_meta.jsonl'), 'w', encoding='utf-8') as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')


def build_index(bank_only=False, limit=None, batch_size=64):
    t0 = time.time()
    os.makedirs(INDEX_DIR, exist_ok=True)
    # 开建先清场：config.json 删掉，建到一半挂了守卫会拦住半成品；--bank-only 时
    # 残留的旧 cand.npz 会被 load_index 安静合并进语料，必须一并删除
    stale = ['config.json'] + (['cand.npz', 'cand_meta.jsonl'] if bank_only else [])
    for name in stale:
        p = os.path.join(INDEX_DIR, name)
        if os.path.exists(p):
            os.remove(p)
            info(f'清除旧产物 {name}')
    model = get_model()
    dim = _dim(model)

    bank = load_bank()
    info(f'库内 {len(bank)} 题')
    b_text, b_tmask = encode_features(model, [p['text'] for p in bank], batch_size, '题面')
    b_sol, b_smask = encode_features(model, [p['sol'] for p in bank], batch_size, '解法要点')
    meta = [{'id': p['id'], 'contest': p['contest'], 'difficulty': p['difficulty'],
             'topics': p['topics'], 'head': p['head'], 'formulas': p['formulas']}
            for p in bank]
    _save_corpus('bank', [p['id'] for p in bank], b_text, b_tmask, b_sol, b_smask, meta)
    info(f'bank 索引已写入（{time.time() - t0:.0f}s）')

    n_cand = 0
    if not bank_only:
        rows = load_cand_rows(limit)
        info(f'候选池 status=ok 取 {len(rows)} 行' + (f'（--limit {limit}）' if limit else ''))
        mids = [r['mathnet_id'] for r in rows]
        texts, soltexts = load_cand_fulltext(mids)
        c_text, c_tmask = encode_features(model, texts, batch_size, '候选题面')
        c_sol, c_smask = encode_features(model, soltexts, batch_size, '候选解法')
        cmeta = []
        for r, t, s in zip(rows, texts, soltexts):
            cmeta.append({
                'id': 'MN-' + r['mathnet_id'],
                'contest': r.get('contest_raw'),
                'difficulty': r.get('difficulty_est'),
                'topics': r.get('topics') or [],
                'head': re.sub(r'\s+', ' ', t)[:HEAD_LEN] or r.get('head', '')[:HEAD_LEN],
                'formulas': sorted(extract_formulas(t) | extract_formulas(s)),
            })
        _save_corpus('cand', [m['id'] for m in cmeta], c_text, c_tmask, c_sol, c_smask, cmeta)
        n_cand = len(rows)

    with open(os.path.join(INDEX_DIR, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'model': MODEL_NAME, 'dim': dim,
                   'built': time.strftime('%Y-%m-%dT%H:%M:%S'),
                   'bank_n': len(bank), 'cand_n': n_cand,
                   'cand_limit': limit, 'bank_only': bank_only}, f, ensure_ascii=False)
    info(f'BUILD OK：bank {len(bank)} 题，cand {n_cand} 条，共 {time.time() - t0:.0f}s')


# ───────────────────────── query ─────────────────────────

REBUILD_CMD = 'uv run --group similar python scripts/similar_index.py build --bank-only'
FULL_REBUILD_CMD = 'uv run --group similar --group mathnet python scripts/similar_index.py build'


def count_bank_problems(root=None):
    """problems/ 实际题数（只数文件，不读内容）——新鲜度判据统一用这个题数口径。"""
    root = root or ROOT
    n = 0
    for cat in CATEGORIES:
        d = os.path.join(root, 'problems', cat)
        if os.path.isdir(d):
            n += sum(1 for name in os.listdir(d) if name.endswith('.md'))
    return n


def freshness_issues(root=None):
    """simindex 新鲜度判据唯一正本：config 缺失/不可解析、bank_n 与现库题数不符、
    bank_only 构建下残留 cand.npz。返回 (问题描述列表, config 字典或 None)；
    bank.py doctor 逐条汇报计数，查询守卫拒绝出结果，两处消费同一份判据。"""
    root = root or ROOT
    index_dir = os.path.join(root, 'candidates', 'simindex')
    cfg_path = os.path.join(index_dir, 'config.json')
    if not os.path.exists(cfg_path):
        return [f'config.json：缺失（clone 后正常）——重建：{REBUILD_CMD}'], None
    try:
        with open(cfg_path, encoding='utf-8') as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError('config.json 顶层必须是对象')
    except (OSError, ValueError):
        return [f'config.json：无法解析——重建：{REBUILD_CMD}'], None
    issues = []
    n_bank = count_bank_problems(root)
    if cfg.get('bank_n') != n_bank:
        issues.append(f"config.json：陈旧（bank_n={cfg.get('bank_n')}，现库 {n_bank} 题，"
                      f"构建于 {cfg.get('built') or '?'}）——重建：{REBUILD_CMD}")
    if cfg.get('bank_only') and os.path.exists(os.path.join(index_dir, 'cand.npz')):
        issues.append(f'：残留（config 记 bank_only 构建，但候选索引 cand.npz 仍在，'
                      f'similar 查询会混入陈旧候选）——重建：{FULL_REBUILD_CMD}')
    return issues, cfg


def ensure_index_fresh():
    """查询前的廉价新鲜度守卫（判据正本在 freshness_issues，这里只负责拒绝）：
    索引建自旧库或混有残留 cand 时安静返回错误语料比报错更糟，一律拒绝出结果。"""
    issues, _cfg = freshness_issues()
    if issues:
        sys.exit('索引不可用，拒绝出结果：\n'
                 + '\n'.join(f'  candidates/simindex/{msg}' for msg in issues))


def load_index():
    """合并 bank + cand（若已建）为单一语料。返回 dict 或 None。"""
    ensure_index_fresh()
    try:
        import numpy as np
    except ImportError:
        sys.exit('query 需要 numpy：请用 uv run --group similar 运行（首次需先 build 建索引）')
    ids, texts, tmasks, sols, smasks, meta = [], [], [], [], [], []
    for name in ('bank', 'cand'):
        npz_path = os.path.join(INDEX_DIR, f'{name}.npz')
        if not os.path.exists(npz_path):
            continue
        z = np.load(npz_path)
        ids += list(z['ids'])
        texts.append(z['text'])
        tmasks.append(z['text_mask'])
        sols.append(z['sol'])
        smasks.append(z['sol_mask'])
        with open(os.path.join(INDEX_DIR, f'{name}_meta.jsonl'), encoding='utf-8') as f:
            meta += [json.loads(line) for line in f]
    if not ids:
        return None
    return {
        'ids': ids,
        'pos': {i: k for k, i in enumerate(ids)},
        'text': np.vstack(texts), 'text_mask': np.concatenate(tmasks),
        'sol': np.vstack(sols), 'sol_mask': np.concatenate(smasks),
        'meta': meta,
    }


def query_similar(qid, top=20, index=None):
    """按库内题号（G-035）或候选号（MN-009s / 009s）查 top-k 相似。返回 list[dict]。"""
    idx = index or load_index()
    if idx is None:
        sys.exit('索引不存在：先跑 build（如 --bank-only）')
    pos = idx['pos'].get(qid)
    if pos is None and not qid.startswith('MN-'):
        pos = idx['pos'].get('MN-' + qid)
    if pos is None:
        sys.exit(f'索引中找不到 {qid}（候选池条目需先 build 进索引，形如 MN-009s）')
    q_meta = idx['meta'][pos]
    q_form = set(q_meta['formulas'])
    q_topics = set(q_meta.get('topics') or [])

    st = idx['text'] @ idx['text'][pos] if idx['text_mask'][pos] else None
    ss = idx['sol'] @ idx['sol'][pos] if idx['sol_mask'][pos] else None

    rows = []
    for i, m in enumerate(idx['meta']):
        if i == pos:
            continue
        s_text = float(st[i]) if st is not None and idx['text_mask'][i] else None
        s_sol = float(ss[i]) if ss is not None and idx['sol_mask'][i] else None
        cands = [s for s in (s_text, s_sol) if s is not None]
        if not cands:
            continue
        rows.append({
            'dst': str(idx['ids'][i]),
            'score': round(max(cands), 4),
            'score_text': round(s_text, 4) if s_text is not None else None,
            'score_formula': round(formula_score(q_form, m['formulas']), 4),
            'score_solution': round(s_sol, 4) if s_sol is not None else None,
            'same_node': bool(q_topics & set(m.get('topics') or [])),
            'contest': m.get('contest'),
            'difficulty': m.get('difficulty'),
            'head': m.get('head'),
        })
    rows.sort(key=lambda r: -r['score'])
    return rows[:top]


# ───────────────────────── dupes ─────────────────────────

def _jsonl_rows(path):
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'{path} 第 {line_no} 行不是合法 JSON: {exc}') from exc
            if not isinstance(row, dict):
                raise ValueError(f'{path} 第 {line_no} 行必须是 JSON 对象')
            rows.append(row)
    return rows


def _atomic_jsonl(path, rows):
    """同目录原子替换 duplicates.jsonl；它是重复组唯一真相源。"""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=parent,
                                         delete=False) as fh:
            tmp_path = fh.name
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _id_text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8')
    return str(value)


def _peak_rss_mib():
    """resource.ru_maxrss 在 macOS 是 bytes，在 Linux/BSD 是 KiB。"""
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, ValueError):
        return None
    return rss / (1024 * 1024) if sys.platform == 'darwin' else rss / 1024


def _cosine_block(left, right):
    """单独封装，测试可锁定乘法左侧永远只是一个行块。"""
    return left @ right.T


def _normalise_lexical(text):
    """复现实测口径：题面 Unicode 归一化、小写、去掉全部空白。"""
    text = unicodedata.normalize('NFC', text or '').lower()
    return re.sub(r'\s+', '', text)


def _lexical_scorer(corpus_root, index_rows, ids):
    """按需只读 index.md 题面；返回 pair scorer 与无法读取的候选 id 集合。"""
    root = os.path.realpath(corpus_root)
    cache = {}
    unavailable = set()

    def statement(pos):
        if pos in cache:
            return cache[pos]
        row = index_rows[pos]
        relative = row.get('path')
        value = None
        if isinstance(relative, str) and relative:
            path = os.path.realpath(os.path.join(root, relative))
            try:
                within_root = os.path.commonpath((root, path)) == root
            except ValueError:
                within_root = False
            if within_root and os.path.basename(path) == 'index.md':
                try:
                    with open(path, encoding='utf-8') as fh:
                        value = _normalise_lexical(sections_of(fh.read()).get('题面', ''))
                except OSError:
                    value = None
        if not value:
            unavailable.add(ids[pos])
            value = None
        cache[pos] = value
        return value

    def score(left, right):
        a, b = statement(left), statement(right)
        if a is None or b is None:
            return None
        return difflib.SequenceMatcher(None, a, b).ratio()

    return score, unavailable


def _candidate_components(text, text_mask, sol, sol_mask, threshold, lexical_threshold,
                          lexical_score, block_size):
    """cosine 分块召回，再以词面近似过滤连边；不存 N×N 矩阵或候选边全集。"""
    import numpy as np

    n = len(text)
    parent = list(range(n))
    sizes = [1] * n
    text_max = [None] * n
    solution_max = [None] * n
    lexical_max = [None] * n
    candidate_pairs = 0
    accepted_pairs = 0
    peak_block_bytes = 0
    columns = np.arange(n)[None, :]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def evidence_max(*values):
        present = [value for value in values if value is not None]
        return max(present) if present else None

    def union(i, j, score_text, score_solution, score_lexical):
        left, right = find(i), find(j)
        merged_text = evidence_max(text_max[left], text_max[right], score_text)
        merged_solution = evidence_max(
            solution_max[left], solution_max[right], score_solution)
        merged_lexical = evidence_max(
            lexical_max[left], lexical_max[right], score_lexical)
        if left == right:
            text_max[left] = merged_text
            solution_max[left] = merged_solution
            lexical_max[left] = merged_lexical
            return
        if sizes[left] < sizes[right]:
            left, right = right, left
        parent[right] = left
        sizes[left] += sizes[right]
        text_max[left] = merged_text
        solution_max[left] = merged_solution
        lexical_max[left] = merged_lexical

    for lo in range(0, n, block_size):
        hi = min(lo + block_size, n)
        score_text = _cosine_block(text[lo:hi], text)
        score_solution = _cosine_block(sol[lo:hi], sol)
        peak_block_bytes = max(
            peak_block_bytes, score_text.nbytes + score_solution.nbytes)

        valid_text = text_mask[lo:hi, None] & text_mask[None, :]
        valid_solution = sol_mask[lo:hi, None] & sol_mask[None, :]
        selected = ((score_text > threshold) & valid_text)
        selected |= ((score_solution > threshold) & valid_solution)
        # 只保留严格上三角；“高于阈值”按 > 实现，恰等于阈值的对不入选。
        selected &= columns > np.arange(lo, hi)[:, None]

        for row, col in np.argwhere(selected):
            i, j = lo + int(row), int(col)
            candidate_pairs += 1
            score_lexical = lexical_score(i, j)
            if score_lexical is None or score_lexical < lexical_threshold:
                continue
            accepted_pairs += 1
            union(
                i, j,
                float(score_text[row, col]) if valid_text[row, col] else None,
                float(score_solution[row, col]) if valid_solution[row, col] else None,
                score_lexical,
            )
    groups = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)
    components = [
        (members, text_max[root], solution_max[root], lexical_max[root])
        for root, members in groups.items()
        if len(members) > 1
    ]
    return components, peak_block_bytes, candidate_pairs, accepted_pairs


def _member_preview(ids, ordered, limit=10):
    shown = ', '.join(ids[pos] for pos in ordered[:limit])
    remaining = len(ordered) - limit
    return shown + (f'，另 {remaining} 题' if remaining > 0 else '')


def _group_rows(ids, meta, components, formula_group_limit):
    prepared = []
    for members, text_max, solution_max, lexical_max in components:
        # mathnet_id 总存在且稳定；字典序 canonical 不依赖缺失/格式不一的年份，结果可复现。
        ordered = sorted(members, key=lambda i: ids[i])
        canonical = ids[ordered[0]]
        formula_max = None
        if len(ordered) > formula_group_limit:
            pair_count = len(ordered) * (len(ordered) - 1) // 2
            info(f'公式比对保护：{canonical} 组共 {len(ordered)} 题，超过上限 '
                 f'{formula_group_limit}；跳过 {pair_count} 对组内公式比对（成员：'
                 f'{_member_preview(ids, ordered)}）')
        else:
            formula_max = 0.0
            for offset, left in enumerate(ordered):
                for right in ordered[offset + 1:]:
                    formula_max = max(
                        formula_max,
                        formula_score(meta[left].get('formulas') or [],
                                      meta[right].get('formulas') or []),
                    )
        topic_sets = [set(meta[i].get('topics') or []) for i in ordered]
        shared_topics = set.intersection(*topic_sets) if topic_sets else set()
        prepared.append({
            'canonical': canonical,
            'members': [ids[i] for i in ordered],
            # 组分数是构成该连通分量的候选边上的各路最大证据；不做加权融合。
            'score_text': round(text_max, 4) if text_max is not None else None,
            'score_solution': round(solution_max, 4) if solution_max is not None else None,
            'score_formula': round(formula_max, 4) if formula_max is not None else None,
            'score_lexical': round(lexical_max, 4),
            # true 表示全组共享至少一个知识点；仅部分成员重合仍为 false。
            'same_topic': bool(shared_topics),
        })
    prepared.sort(key=lambda row: row['canonical'])
    for serial, row in enumerate(prepared, 1):
        row['group_id'] = f'DG-{serial:05d}'
        # 固定字段顺序，便于人工 diff；duplicates.jsonl 才是唯一真相源。
        row = {
            'group_id': row['group_id'],
            'canonical': row['canonical'],
            'members': row['members'],
            'score_text': row['score_text'],
            'score_solution': row['score_solution'],
            'score_formula': row['score_formula'],
            'score_lexical': row['score_lexical'],
            'same_topic': row['same_topic'],
        }
        prepared[serial - 1] = row
    return prepared


def find_duplicate_groups(threshold=DEFAULT_DUPES_THRESHOLD, limit=None, write=False,
                          block_size=DEFAULT_DUPES_BLOCK_SIZE, index_dir=INDEX_DIR,
                          corpus_root=MATHNET_FULL,
                          lexical_threshold=DEFAULT_DUPES_LEXICAL_THRESHOLD,
                          formula_group_limit=DEFAULT_FORMULA_GROUP_LIMIT):
    """从 cand 索引产生候选重复组；默认只读，写盘也只写真相源 duplicates.jsonl。"""
    import numpy as np

    if not 0.0 <= threshold <= 1.0:
        raise ValueError('--threshold 必须在 0 到 1 之间')
    if not 0.0 <= lexical_threshold <= 1.0:
        raise ValueError('--lexical-threshold 必须在 0 到 1 之间')
    if limit is not None and limit <= 0:
        raise ValueError('--limit 必须是正整数')
    if write and limit is not None:
        raise ValueError('--write 不允许与验证参数 --limit 同用，拒绝用局部结果覆盖 duplicates.jsonl')
    if block_size <= 0:
        raise ValueError('--block-size 必须是正整数')
    if formula_group_limit <= 0:
        raise ValueError('--formula-group-limit 必须是正整数')

    t0 = time.perf_counter()
    index_path = os.path.join(corpus_root, 'index.jsonl')
    npz_path = os.path.join(index_dir, 'cand.npz')
    meta_path = os.path.join(index_dir, 'cand_meta.jsonl')
    index_rows = _jsonl_rows(index_path)
    cand_meta = _jsonl_rows(meta_path)

    unique_index_ids = []
    index_by_id = {}
    seen = set()
    invalid_index_rows = duplicate_index_rows = 0
    for row in index_rows:
        mid = row.get('mathnet_id')
        if not isinstance(mid, str) or not mid:
            invalid_index_rows += 1
            continue
        if mid in seen:
            duplicate_index_rows += 1
            continue
        seen.add(mid)
        unique_index_ids.append(mid)
        index_by_id[mid] = row
    total_unique = len(unique_index_ids)
    selected_ids = unique_index_ids[:limit] if limit is not None else unique_index_ids

    with np.load(npz_path) as archive:
        required = ('ids', 'text', 'text_mask', 'sol', 'sol_mask')
        missing = [name for name in required if name not in archive.files]
        if missing:
            raise ValueError(f'cand.npz 缺少字段：{", ".join(missing)}')
        cand_ids = [_id_text(value) for value in archive['ids']]
        arrays = {name: archive[name] for name in required if name != 'ids'}
    if len(cand_meta) != len(cand_ids):
        raise ValueError(
            f'cand_meta.jsonl 与 cand.npz 未对齐：{len(cand_meta)} != {len(cand_ids)}')
    for name, array in arrays.items():
        if len(array) != len(cand_ids):
            raise ValueError(f'cand.npz 的 {name} 行数与 ids 未对齐')
    for pos, (cid, item) in enumerate(zip(cand_ids, cand_meta)):
        if item.get('id') != cid:
            raise ValueError(f'cand_meta.jsonl 第 {pos + 1} 行 id 与 cand.npz 未对齐')

    cand_pos = {}
    duplicate_cand_ids = 0
    for pos, cid in enumerate(cand_ids):
        mid = cid[3:] if cid.startswith('MN-') else cid
        if mid in cand_pos:
            duplicate_cand_ids += 1
            continue
        cand_pos[mid] = pos

    matched_ids = [mid for mid in selected_ids if mid in cand_pos]
    missing_embeddings = [mid for mid in selected_ids if mid not in cand_pos]
    positions = [cand_pos[mid] for mid in matched_ids]
    text = np.asarray(arrays['text'][positions], dtype='float32')
    text_mask = np.asarray(arrays['text_mask'][positions], dtype=bool)
    sol = np.asarray(arrays['sol'][positions], dtype='float32')
    sol_mask = np.asarray(arrays['sol_mask'][positions], dtype=bool)
    meta = [cand_meta[pos] for pos in positions]
    if text.ndim != 2 or sol.ndim != 2:
        raise ValueError('cand.npz 的 text / sol 必须是二维矩阵')

    matched_rows = [index_by_id[mid] for mid in matched_ids]
    lexical_score, unavailable_statements = _lexical_scorer(
        corpus_root, matched_rows, matched_ids)
    components, peak_block_bytes, candidate_pairs, accepted_pairs = _candidate_components(
        text, text_mask, sol, sol_mask, threshold, lexical_threshold,
        lexical_score, block_size)
    groups = _group_rows(matched_ids, meta, components, formula_group_limit)

    if write:
        _atomic_jsonl(os.path.join(corpus_root, 'duplicates.jsonl'), groups)

    elapsed = time.perf_counter() - t0
    flagged = sum(len(group['members']) for group in groups)
    sizes = Counter(len(group['members']) for group in groups)
    size_summary = ', '.join(f'{size}题×{count}组' for size, count in sorted(sizes.items())) or '无'
    rss = _peak_rss_mib()
    info(f'候选重复：{len(groups)} 组，{flagged} 题；cosine 召回 {candidate_pairs} 条边，'
         f'词面通过 {accepted_pairs} 条；'
         f'组大小分布：{size_summary}')
    info(f'参数：threshold={threshold:.4f}，lexical_threshold={lexical_threshold:.4f}，'
         f'block_size={block_size}，formula_group_limit={formula_group_limit}，'
         f'耗时={elapsed:.3f}s')
    indexed_coverage = sum(mid in cand_pos for mid in unique_index_ids)
    coverage_pct = (100 * indexed_coverage / total_unique) if total_unique else 100.0
    info(f'索引覆盖率：cand.npz 覆盖 {indexed_coverage} / {total_unique} '
         f'({coverage_pct:.1f}%)，缺口 {total_unique - indexed_coverage}；'
         f'本次实际比较 {len(matched_ids)} 题')
    if limit is not None and total_unique > limit:
        info(f'覆盖已截断：--limit {limit}，跳过 index.jsonl 其余 {total_unique - limit} 题')
    if missing_embeddings:
        info(f'覆盖已跳过：{len(missing_embeddings)} 题在 cand.npz 中无 embedding')
    if unavailable_statements:
        info(f'词面复核已跳过：{len(unavailable_statements)} 个候选端点无可读题面（'
             f'{", ".join(sorted(unavailable_statements)[:10])}'
             f'{"…" if len(unavailable_statements) > 10 else ""}）')
    if invalid_index_rows:
        info(f'覆盖已跳过：index.jsonl 有 {invalid_index_rows} 行缺少有效 mathnet_id')
    if duplicate_index_rows:
        info(f'去重统计：index.jsonl 有 {duplicate_index_rows} 个重复挂载行，按 mathnet_id 仅计一次')
    if duplicate_cand_ids:
        info(f'去重统计：cand.npz 有 {duplicate_cand_ids} 个重复 id，仅采用首次出现')
    selected_id_set = set(selected_ids)
    outside = sum(1 for mid in cand_pos if mid not in selected_id_set)
    if outside:
        info(f'覆盖已跳过：cand.npz 有 {outside} 题不在本次 index.jsonl 范围内')
    block_mib = peak_block_bytes / (1024 * 1024)
    rss_text = f'{rss:.1f} MiB' if rss is not None else '平台不支持采集'
    info(f'内存：峰值 RSS {rss_text}；相似度浮点块上界 {block_mib:.1f} MiB（非 N×N）')
    info('DRY RUN：未写任何文件' if not write
         else '已写 duplicates.jsonl；index.jsonl / index.md 未改动')
    return groups


# ───────────────────────── CLI ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='构建 embedding + 公式指纹索引')
    b.add_argument('--bank-only', action='store_true', help='只建库内题（不读 HF 缓存）')
    b.add_argument('--limit', type=int, default=None, help='候选池只取前 N 条 status=ok（验证用）')
    b.add_argument('--batch-size', type=int, default=64)
    q = sub.add_parser('query', help='查 top-k 相似（读索引，不加载模型）')
    q.add_argument('id', help='库内题号（G-035）或候选号（MN-009s）')
    q.add_argument('--top', type=int, default=20)
    d = sub.add_parser('dupes', help='全量分块聚类候选重复题（只读已有 cand 索引）')
    d.add_argument('--threshold', type=float, default=DEFAULT_DUPES_THRESHOLD,
                   help=f'max(题面, 解法) cosine 召回严格下界（默认 {DEFAULT_DUPES_THRESHOLD}）')
    d.add_argument('--lexical-threshold', type=float, default=DEFAULT_DUPES_LEXICAL_THRESHOLD,
                   help=f'题面词面近似必要条件（默认 {DEFAULT_DUPES_LEXICAL_THRESHOLD}）')
    d.add_argument('--limit', type=int, default=None,
                   help='只比较 index.jsonl 前 N 个唯一 id（验证用；会显式报告截断）')
    d.add_argument('--write', action='store_true',
                   help='写入 root/duplicates.jsonl；默认 dry-run，不写任何文件')
    d.add_argument('--root', default=MATHNET_FULL,
                   help=f'mathnet-full 语料根目录（默认 {MATHNET_FULL}）')
    d.add_argument('--index-dir', default=INDEX_DIR,
                   help=f'已有 cand.npz/cand_meta.jsonl 的目录（默认 {INDEX_DIR}）')
    d.add_argument('--block-size', type=int, default=DEFAULT_DUPES_BLOCK_SIZE,
                   help=f'矩阵乘左侧行块大小（默认 {DEFAULT_DUPES_BLOCK_SIZE}）')
    d.add_argument('--formula-group-limit', type=int, default=DEFAULT_FORMULA_GROUP_LIMIT,
                   help=f'组内公式 O(k²) 比对的组大小上限（默认 {DEFAULT_FORMULA_GROUP_LIMIT}）')
    args = ap.parse_args()

    if args.cmd == 'build':
        build_index(bank_only=args.bank_only, limit=args.limit, batch_size=args.batch_size)
    elif args.cmd == 'query':
        for r in query_similar(args.id, args.top):
            print(json.dumps(r, ensure_ascii=False))
    else:
        try:
            find_duplicate_groups(
                threshold=args.threshold, limit=args.limit, write=args.write,
                block_size=args.block_size, index_dir=args.index_dir,
                corpus_root=args.root, lexical_threshold=args.lexical_threshold,
                formula_group_limit=args.formula_group_limit)
        except (FileNotFoundError, OSError, ValueError) as exc:
            sys.exit(f'dupes: {exc}')


if __name__ == '__main__':
    main()
