"""MathNet 评审批次 → 正式题库的转换用例（scripts/mathnet_import.py）：

1. q()：frontmatter 标量转义（引号/反斜杠/中文）必须是合法 YAML
2. existing_state()：编号推进「每板块最大号 +1」与已入库 mathnet_id 收集（幂等的基础）
3. needs_review()：四种触发条件各一条 + 全清白为 False
4. render()：凭证字段、就低不就高的难度、三节正文，以及与 spar_session 出卡的咬合（不泄答）
5. 准入线：recommend=skip / needs_review / 撞小节白名单 / 已入库 一律不得写盘（驱动 main()，
   数据集用假模块打桩，绝不触网、绝不读 HF 缓存）
6. 回归锚点：sol_chars 是「拼接后的字符数」而非「解答条数」

运行：uv run --group dev pytest -q
"""
import ast
import json
import os
import sys
import types

import pytest
import yaml

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
sys.path.insert(0, SCRIPTS)
import mathnet_import as mi  # noqa: E402
import spar_session as sp  # noqa: E402


# ---------------- 假数据（全部手写，不碰数据集） ----------------

def _row(**kw):
    """候选池一行（scripts/mathnet_ingest.py 的输出格式）。"""
    row = {'mathnet_id': 'm1', 'category': 'algebra', 'difficulty_est': 3,
           'topics': ['方程与设元'], 'contest_raw': 'IMO 2001', 'year': 2001,
           'final_answer': 'ANSWER-TOKEN 42', 'problem_type': 'final answer only'}
    row.update(kw)
    return row


def _verdict(**kw):
    """一条 Codex 评审结论（data/review/<batch>/verdicts.json 的元素）。"""
    v = {'mathnet_id': 'm1', 'short_title': 'A Nice Problem', 'difficulty_codex': 3,
         'difficulty_reason': '一步配方即可', 'topics_verdict': 'agree',
         'text_quality': 'clean', 'needs_figure': False, 'recommend': 'claim',
         'recommend_reason': '题面与解答均可靠'}
    v.update(kw)
    return v


_KEEP = object()   # 「不覆盖默认解答」的哨兵：sols=None 是被测的真实取值（数据集该列可为 null）


def _full(problem='STATEMENT-TOKEN 设 $x+y=3$，求 $(x+y)^2$。', sols=_KEEP):
    """数据集行里 render() 真正用到的两个字段。"""
    return {'problem_markdown': problem,
            'solutions_markdown': ['SOLUTION-TOKEN 完全平方直接代入。'] if sols is _KEEP else sols}


def _fm_of(text):
    """把渲染结果的 frontmatter 解析成 dict（顺带证明它是合法 YAML）。"""
    return yaml.safe_load(text.split('---\n', 2)[1])


# ---------------- 1. q()：frontmatter 标量转义 ----------------

class TestQuoting:
    """防：标题里的引号/反斜杠把 frontmatter 写坏，整题文件无法解析。"""

    @pytest.mark.parametrize('raw', [
        'He said "hi"',                      # 双引号必须转义
        "Euler's line",                      # 单引号在双引号标量里原样合法
        r'$\frac{1}{2}$ of a square',        # LaTeX 反斜杠必须转义成 \\
        '含冒号: 与 # 井号的标题',            # YAML 元字符靠加引号中和
        '二次剩余与原根',                     # 非 ASCII 不许被 \uXXXX 化
    ])
    def test_quoted_scalar_round_trips_through_yaml(self, raw):
        assert yaml.safe_load(f'title: {mi.q(raw)}') == {'title': raw}

    def test_non_ascii_stays_literal(self):
        """防：ensure_ascii 默认 True 把中文标题写成 \\uXXXX 转义序列。"""
        assert mi.q('二次剩余') == '"二次剩余"'

    def test_double_quote_is_backslash_escaped(self):
        """防：内嵌双引号直接落盘，提前闭合标量。"""
        assert mi.q('He said "hi"') == '"He said \\"hi\\""'

    def test_backslash_is_doubled(self):
        """防：LaTeX 单反斜杠被 YAML 当转义引导符（\\f 之类）吃掉。"""
        assert mi.q(r'\frac') == '"\\\\frac"'


# ---------------- 2. existing_state()：编号推进与幂等基础 ----------------

def _bank(tmp_path, files):
    """在 tmp_path 造一棵假 problems/ 树；files 形如 {'algebra/A-003.md': 正文}。"""
    for rel, text in files.items():
        p = tmp_path / 'problems' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    return tmp_path


def _stub(pid, mathnet_id=None, quote=True):
    fm = [f'id: {pid}', 'title: "T"']
    if mathnet_id is not None:
        fm.append(f'mathnet_id: "{mathnet_id}"' if quote else f'mathnet_id: {mathnet_id}')
    return '---\n' + '\n'.join(fm) + '\n---\n\n## 题面\n\n略\n'


class TestExistingState:
    def test_top_number_is_max_not_count(self, tmp_path, monkeypatch):
        """防：编号推进按「文件个数 +1」算，遇到号段断档（A-001/A-007）就撞号覆盖已有题。"""
        _bank(tmp_path, {'algebra/A-001.md': _stub('A-001'),
                         'algebra/A-007.md': _stub('A-007'),
                         'geometry/G-002.md': _stub('G-002')})
        (tmp_path / 'problems' / 'number-theory').mkdir(parents=True)
        monkeypatch.setattr(mi, 'ROOT', str(tmp_path))
        _, top = mi.existing_state()
        # combinatorics 目录根本不存在，也必须给出 0（下一题 C-001），不能抛 FileNotFoundError
        assert top == {'algebra': 7, 'number-theory': 0, 'combinatorics': 0, 'geometry': 2}

    def test_collects_mathnet_ids_with_and_without_quotes(self, tmp_path, monkeypatch):
        """防：幂等失效——已入库题的 mathnet_id 没收集全，同一题被二次导入。"""
        _bank(tmp_path, {'algebra/A-001.md': _stub('A-001', '0ach'),
                         'geometry/G-001.md': _stub('G-001', 'zz9', quote=False)})
        monkeypatch.setattr(mi, 'ROOT', str(tmp_path))
        ids, _ = mi.existing_state()
        assert ids == {'0ach': os.path.join('problems', 'algebra', 'A-001.md'),
                       'zz9': os.path.join('problems', 'geometry', 'G-001.md')}

    def test_ignores_non_problem_files(self, tmp_path, monkeypatch):
        """防：README.md / 草稿文件被当成题目，污染号段或 mathnet_id 台账。"""
        _bank(tmp_path, {'algebra/A-002.md': _stub('A-002', 'keep'),
                         'algebra/README.md': '# 板块说明\n无 frontmatter\n',
                         'algebra/A-999.txt': _stub('A-999', 'nope')})
        monkeypatch.setattr(mi, 'ROOT', str(tmp_path))
        ids, top = mi.existing_state()
        assert ids == {'keep': os.path.join('problems', 'algebra', 'A-002.md')}
        assert top['algebra'] == 2


# ---------------- 3. needs_review()：四种触发 + 全清白 ----------------

class TestNeedsReview:
    def test_difficulty_gap_two_triggers(self):
        """防：规则估级与 Codex 差 ≥2 档的题不经人工定夺直接入库。"""
        assert mi.needs_review(_verdict(difficulty_codex=5), 3) is True

    def test_difficulty_gap_one_does_not_trigger(self):
        """防：把阈值写成 >=1，一档正常分歧也被拦成人工待办。"""
        assert mi.needs_review(_verdict(difficulty_codex=4), 3) is False

    def test_topics_wrong_triggers(self):
        """防：标签被判 wrong 的题带着错知识点入库。"""
        assert mi.needs_review(_verdict(topics_verdict='wrong'), 3) is True

    def test_topics_partial_does_not_trigger(self):
        """防：partial 被当成 wrong，评审的三档语义塌成两档。"""
        assert mi.needs_review(_verdict(topics_verdict='partial'), 3) is False

    def test_text_broken_triggers(self):
        """防：转录残缺（broken）的题面入库，学生读到坏题。"""
        assert mi.needs_review(_verdict(text_quality='broken'), 3) is True

    def test_needs_figure_triggers(self):
        """防：依赖图形且无法用文字复原的题入库（题库铁律：此类不收）。"""
        assert mi.needs_review(_verdict(needs_figure=True), 3) is True

    def test_all_clean_is_false(self):
        """防：判定过严，清白题也进不了库（准入线形同虚设的反面）。"""
        assert mi.needs_review(_verdict(difficulty_codex=3), 3) is False


# ---------------- 4. render()：字段、难度、三节、与出卡咬合 ----------------

class TestRender:
    def test_frontmatter_carries_review_credentials(self):
        """防：入库题缺 mathnet_id / review_ref / verification，凭证链断裂（lint 铁律）。"""
        text, why = mi.render('A-004', 'A Nice Problem', _row(), _verdict(), _full(),
                              'data/review/t-01/verdicts.json')
        assert why is None
        fm = _fm_of(text)
        assert fm['mathnet_id'] == 'm1'
        assert fm['review_ref'] == 'data/review/t-01/verdicts.json'
        assert fm['verification'] == 'mathnet-reviewed'
        assert fm['source_ref'] == 'MathNet m1'
        assert fm['source_url'] == 'https://huggingface.co/datasets/ShadenA/MathNet'
        assert fm['id'] == 'A-004' and fm['category'] == 'algebra'
        assert fm['topics'] == ['方程与设元']

    @pytest.mark.parametrize('est,codex,expect', [(4, 2, 2), (2, 5, 2), (3, 3, 3)])
    def test_difficulty_takes_the_lower(self, est, codex, expect):
        """防：难度取 max 或直接采信某一方，把 ★2 的题挂成 ★5（就低不就高）。"""
        text, _ = mi.render('A-004', 'T', _row(difficulty_est=est),
                            _verdict(difficulty_codex=codex), _full(), 'ref.json')
        assert _fm_of(text)['difficulty'] == expect

    def test_difficulty_note_records_divergence(self):
        """防：就低取值后不留痕，日后无法复盘两侧估级差在哪。"""
        text, _ = mi.render('A-004', 'T', _row(difficulty_est=4),
                            _verdict(difficulty_codex=2, difficulty_reason='配方即可'),
                            _full(), 'ref.json')
        assert _fm_of(text)['difficulty_note'] == '配方即可（规则估★4/Codex★2，就低取★2）'

    def test_difficulty_note_clean_when_agreed(self):
        """防：两侧一致时也拼上分歧后缀，note 里出现自相矛盾的「就低取」。"""
        text, _ = mi.render('A-004', 'T', _row(difficulty_est=3),
                            _verdict(difficulty_codex=3, difficulty_reason='配方即可'),
                            _full(), 'ref.json')
        assert _fm_of(text)['difficulty_note'] == '配方即可'

    def test_year_none_renders_yaml_null(self):
        """防：year 缺失时写成 Python 的 None 字面量，YAML 解析出字符串 'None'。"""
        text, _ = mi.render('A-004', 'T', _row(year=None), _verdict(), _full(), 'ref.json')
        assert 'year: null\n' in text and _fm_of(text)['year'] is None

    def test_title_with_quotes_survives_frontmatter(self):
        """防：短标题含引号时 frontmatter 破损（q() 与 render() 的接缝）。"""
        text, _ = mi.render('A-004', 'The "Twin" Circles', _row(), _verdict(), _full(), 'ref.json')
        assert _fm_of(text)['title'] == 'The "Twin" Circles'

    def test_body_has_exactly_three_sections(self):
        """防：正文小节增删或改名，撞 spar_session 的小节白名单导致全库出不了卡。"""
        text, _ = mi.render('A-004', 'T', _row(), _verdict(), _full(), 'ref.json')
        assert list(sp.split_sections(text, 'A-004')) == ['题面', '答案', '解法要点']

    def test_rendered_problem_makes_a_card_without_leaking(self):
        """防：入库格式与出卡白名单脱钩——题卡漏出答案或解法（跨模块联测）。"""
        text, _ = mi.render('A-004', 'A Nice Problem', _row(), _verdict(), _full(), 'ref.json')
        secs = sp.split_sections(text, 'A-004')
        card = sp.build_card(_fm_of(text), secs, '20260804-A-004-1', 'fresh', 40)
        assert 'STATEMENT-TOKEN' in card
        for leak in ('ANSWER-TOKEN', 'SOLUTION-TOKEN', '## 答案', '## 解法要点'):
            assert leak not in card, f'题卡泄漏：{leak}'

    def test_proof_problem_gets_placeholder_answer(self):
        """防：证明题（无 final_answer）被判空而整批漏收。"""
        text, why = mi.render('A-004', 'T', _row(final_answer='', problem_type='proof'),
                              _verdict(), _full(), 'ref.json')
        assert why is None
        assert sp.split_sections(text, 'A-004')['答案'] == '证明题'

    def test_answer_type_without_final_answer_is_rejected(self):
        """防：problem_type 声称有答案却抓不到，入库后「答案」节是空的。"""
        text, why = mi.render('A-004', 'T', _row(final_answer='', problem_type='final answer only'),
                              _verdict(), _full(), 'ref.json')
        assert text is None
        assert why == 'problem_type 含 answer 但 final_answer 为空'

    @pytest.mark.parametrize('sols', [[], None, ['', '   ']])
    def test_empty_solutions_rejected(self, sols):
        """防：官方解为空（或只有空白串）的题入库，解法要点节空白。"""
        text, why = mi.render('A-004', 'T', _row(), _verdict(), _full(sols=sols), 'ref.json')
        assert (text, why) == (None, '题面或官方解为空')

    @pytest.mark.parametrize('kw', [
        {'problem': 'STATEMENT\n\n## Lemma 1\n\n设 $x=1$。'},
        {'sols': ['解答开头\n\n## Step 2\n\n于是结论成立。']},
    ])
    def test_hash_hash_line_in_source_rejected(self, kw):
        """防：原文自带 `## ` 行被照录，凭空多出一个白名单外小节（铁律 5：不改写、不收）。"""
        text, why = mi.render('A-004', 'T', _row(), _verdict(), _full(**kw), 'ref.json')
        assert text is None
        assert why == '原文含 `## ` 行，撞小节白名单（铁律 5：不改写、不收）'

    def test_out_of_range_difficulty_rejected(self):
        """防：候选池给出 0 或 6 档的脏数据被写进 frontmatter（difficulty 只许 1-5）。"""
        text, why = mi.render('A-004', 'T', _row(difficulty_est=0), _verdict(), _full(), 'ref.json')
        assert (text, why) == (None, '难度越界 0')


# ---------------- 5. 回归锚点：sol_chars 是字符数不是条数 ----------------

INGEST = os.path.join(SCRIPTS, 'mathnet_ingest.py')
_SOLS = [['a' * 300, 'b' * 400, 'c' * 339]]   # 一行数据集：三条解答，共 1039 字


def _eval_field(path, key):
    """取出源码里候选池行字典中某个键的取值表达式，喂假数据求值。

    该字段是 build() 里的行内表达式，而 build() 会 load_dataset；本仓库测试禁止触网，
    故只取表达式本身（不执行模块、不读数据集）求值。
    """
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    src = ast.unparse(v)
                    try:
                        return eval(src, {'cols': {'solutions_markdown': _SOLS}, 'i': 0})
                    except NameError as e:
                        pytest.fail(f'{key} 的取值表达式 {src!r} 引用了新名字（{e}）——'
                                    '若已抽成函数，请把本用例改成直接调用该函数')
    pytest.fail(f'mathnet_ingest 的候选池行里找不到 {key} 字段，用例需跟着改')


class TestSolutionCharCount:
    def test_ingest_sol_chars_counts_characters_not_solutions(self):
        """防回归：solutions_markdown 是解答**列表**，早期用 len() 误测成「解答条数」
        （22,585 行全显示 1）；正确口径是拼接后的字符数（中位数约 1039）。"""
        assert _eval_field(INGEST, 'sol_chars') == 1039

    def test_ingest_n_solutions_is_the_list_length(self):
        """防：修 sol_chars 时把 n_solutions 也改成字符数，两个字段语义混作一谈。"""
        assert _eval_field(INGEST, 'n_solutions') == 3

    def test_render_joins_all_solutions(self):
        """防：render 只取首条解答或误把列表当字符串——解法要点须含全部条目及分隔线。"""
        text, _ = mi.render('A-004', 'T', _row(),
                            _verdict(), _full(sols=['x' * 300, 'y' * 400, 'z' * 339]), 'ref.json')
        body = sp.split_sections(text, 'A-004')['解法要点']
        assert body == '\n\n---\n\n'.join(['x' * 300, 'y' * 400, 'z' * 339])
        assert len(body) == 1039 + 2 * len('\n\n---\n\n')


# ---------------- 6. 准入线：main() 的过滤（数据集打桩，不触网） ----------------

class _FakeDS:
    """只实现 mathnet_import.main() 用到的两种取值：ds['id'] 与 ds[i]。"""

    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, k):
        if isinstance(k, str):
            return [r[k] for r in self._rows]
        return self._rows[k]


def _run_import(tmp_path, monkeypatch, rows, verdicts, ds_rows, extra=()):
    """在 tmp_path 上跑一遍 main()：假 problems/ 树 + 假候选池 + 假 datasets 模块。"""
    for cat in mi.CATEGORIES:
        (tmp_path / 'problems' / cat).mkdir(parents=True, exist_ok=True)
    (tmp_path / 'problems' / 'algebra' / 'A-003.md').write_text(
        _stub('A-003', 'already-in-bank'), encoding='utf-8')
    pool = tmp_path / 'candidates' / 'mathnet.jsonl'
    pool.parent.mkdir(parents=True, exist_ok=True)
    pool.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    d = tmp_path / 'data' / 'review' / 't-01'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'batch.json').write_text(
        json.dumps([{'mathnet_id': r['mathnet_id']} for r in rows], ensure_ascii=False), encoding='utf-8')
    (d / 'verdicts.json').write_text(json.dumps(verdicts, ensure_ascii=False), encoding='utf-8')

    fake = types.ModuleType('datasets')
    fake.load_dataset = lambda *a, **k: {'train': _FakeDS(ds_rows)}
    monkeypatch.setitem(sys.modules, 'datasets', fake)
    monkeypatch.setattr(mi, 'ROOT', str(tmp_path))
    monkeypatch.setattr(mi, 'POOL', str(pool))
    monkeypatch.setattr(sys, 'argv', ['mathnet_import.py', '--dir', 'data/review/t-01', *extra])
    return mi.main()


def _ds_row(mid, problem='STATEMENT-TOKEN 题面。', sols=_KEEP):
    return {'id': mid, **_full(problem, sols)}


class TestAdmissionGate:
    def test_only_clean_claims_are_written(self, tmp_path, monkeypatch, capsys):
        """防：准入线失守——skip / needs_review / 撞白名单 / 已入库 的题混进 problems/。"""
        rows = [_row(mathnet_id='clean'),
                _row(mathnet_id='skipme'),
                _row(mathnet_id='gap', difficulty_est=5),
                _row(mathnet_id='figure'),
                _row(mathnet_id='hashline'),
                _row(mathnet_id='already-in-bank')]
        verdicts = [_verdict(mathnet_id='clean', short_title='Clean One'),
                    _verdict(mathnet_id='skipme', recommend='skip', recommend_reason='题意不自洽'),
                    _verdict(mathnet_id='gap', difficulty_codex=3),
                    _verdict(mathnet_id='figure', needs_figure=True),
                    _verdict(mathnet_id='hashline'),
                    _verdict(mathnet_id='already-in-bank')]
        ds_rows = [_ds_row('clean'), _ds_row('skipme'), _ds_row('gap'), _ds_row('figure'),
                   _ds_row('hashline', problem='STATEMENT\n\n## Lemma\n\n略'),
                   _ds_row('already-in-bank')]
        done = _run_import(tmp_path, monkeypatch, rows, verdicts, ds_rows)

        assert done == [('A-004', 'clean', 'algebra')]
        assert sorted(os.listdir(tmp_path / 'problems' / 'algebra')) == ['A-003.md', 'A-004.md']
        out = capsys.readouterr().out
        assert 'skipme: 评审 skip：题意不自洽' in out
        assert 'gap: needs_review（分歧/质量旗标），须人工定夺' in out
        assert 'figure: needs_review（分歧/质量旗标），须人工定夺' in out
        assert '已入库于 problems/algebra/A-003.md（幂等拒重）' in out

    def test_imported_file_points_at_its_verdicts(self, tmp_path, monkeypatch):
        """防：review_ref 写死或指错批次——凭证必须指向本次 verdicts.json（无凭证不入库）。"""
        _run_import(tmp_path, monkeypatch, [_row(mathnet_id='clean')],
                    [_verdict(mathnet_id='clean')], [_ds_row('clean')])
        fm = _fm_of((tmp_path / 'problems' / 'algebra' / 'A-004.md').read_text(encoding='utf-8'))
        assert fm['review_ref'] == os.path.join('data', 'review', 't-01', 'verdicts.json')
        assert fm['mathnet_id'] == 'clean'
        assert (tmp_path / fm['review_ref']).exists(), 'review_ref 指向的凭证文件必须真实存在'

    def test_per_category_quota_is_enforced(self, tmp_path, monkeypatch):
        """防：--per-category 失效，一次把整批同板块题灌进库（入库节奏靠它控）。"""
        rows = [_row(mathnet_id='c1'), _row(mathnet_id='c2'), _row(mathnet_id='c3')]
        verdicts = [_verdict(mathnet_id=m) for m in ('c1', 'c2', 'c3')]
        done = _run_import(tmp_path, monkeypatch, rows, verdicts,
                           [_ds_row(m) for m in ('c1', 'c2', 'c3')], extra=('--per-category', '2'))
        assert [p for p, _, _ in done] == ['A-004', 'A-005']
        assert not (tmp_path / 'problems' / 'algebra' / 'A-006.md').exists()

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        """防：--dry-run 仍然写盘，预演变成真入库。"""
        done = _run_import(tmp_path, monkeypatch, [_row(mathnet_id='clean')],
                           [_verdict(mathnet_id='clean')], [_ds_row('clean')], extra=('--dry-run',))
        assert done == [('A-004', 'clean', 'algebra')]
        assert os.listdir(tmp_path / 'problems' / 'algebra') == ['A-003.md']

    def test_missing_verdicts_aborts(self, tmp_path, monkeypatch):
        """防：无 verdicts.json 也能入库——凭证纪律的最后一道闸（数据集声称 ≠ 已核验）。"""
        d = tmp_path / 'data' / 'review' / 't-02'
        d.mkdir(parents=True)
        (d / 'batch.json').write_text('[]', encoding='utf-8')
        monkeypatch.setattr(mi, 'ROOT', str(tmp_path))
        monkeypatch.setattr(sys, 'argv', ['mathnet_import.py', '--dir', 'data/review/t-02'])
        with pytest.raises(SystemExit) as e:
            mi.main()
        assert e.value.code == 2
