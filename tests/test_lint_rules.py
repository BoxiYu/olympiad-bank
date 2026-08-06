"""「铁律的机器执行」回归用例：bank.lint / _verdict_ids / registry_report / doclint。

全部在 tmp_path 造的假仓库上跑（monkeypatch bank.ROOT），不读网络、不碰真实
problems/ data/ taxonomy/。核心防的是旧题库那次审计的教训：83% 的 sourced 题是裸声明、
2 道实质错题 —— 由此立下「无 verdicts.json 不入库」，本文件逐条把它钉成机器可执行的检查。

运行：uv run --group dev pytest -q
"""
import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402
import spar_session as sp  # noqa: E402
import student_profile as stp  # noqa: E402

# ---------------- 假仓库脚手架 ----------------

_DROP = object()  # make_problem(title=_DROP) → 从 frontmatter 里删掉该键

REGISTRY = {
    'algebra': {'不等式': ['AM-GM'], '函数方程': None},
    'number-theory': {'同余': ['modular arithmetic']},
    'combinatorics': {'计数': None},
    'geometry': {'圆幂': ['power of a point']},
}

BODY_OK = """
# 题目

## 题面

袋中有 25 只鸟。

## 答案

17 与 8。

## 解法要点

从末态逆推。
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def make_problem(root, pid='A-001', cat='algebra', body=BODY_OK, filename=None, **over):
    """在假仓库里落一道题；over 覆盖 frontmatter 字段，值为 _DROP 表示删除该字段。"""
    fm = {'id': pid, 'title': '测试题', 'category': cat, 'source_ref': 'MathNet test',
          'difficulty': 3, 'topics': ['不等式'], 'verification': 'sourced',
          'source_url': 'https://example.org/p'}
    fm.update(over)
    fm = {k: v for k, v in fm.items() if v is not _DROP}
    text = '---\n' + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + '---\n' + body
    path = os.path.join(root, 'problems', cat, filename or (pid + '.md'))
    _write(path, text)
    return path


VERDICTS_REL = 'data/review/batch-01/verdicts.json'   # review_ref 一律用仓库相对的正斜杠路径


def make_verdicts(root, rel=VERDICTS_REL, ids=('0ach',), raw=None):
    """落一份评审凭证；raw 非 None 时写入原始字符串（用于造不可解析的 JSON）。"""
    payload = raw if raw is not None else json.dumps(
        [{'mathnet_id': i, 'recommend': 'claim'} for i in ids], ensure_ascii=False)
    _write(os.path.join(root, *rel.split('/')), payload)
    return rel


def write_registry(root, reg=REGISTRY):
    _write(os.path.join(root, 'taxonomy', 'registry.yml'),
           yaml.safe_dump(reg, allow_unicode=True, sort_keys=False))


def write_boards(root, reg=REGISTRY):
    """taxonomy/<板块>.md 的 ### 标题集合与 registry 节点集合保持一致（doclint c 项的基线）。"""
    for cat, fname in bank.TAXONOMY_BOARDS.items():
        heads = ''.join(f'### {n}\n\n占位说明。\n\n' for n in (reg.get(cat) or {}))
        _write(os.path.join(root, 'taxonomy', fname), f'# {cat}\n\n{heads}')


# 基线依赖图：一条板块内边 + 一条跨板块边，合法 DAG（doclint d 项的基线）
PREREQ = {'algebra/函数方程': ['algebra/不等式'],
          'geometry/圆幂': ['number-theory/同余']}


def write_prereq(root, pre=PREREQ):
    _write(os.path.join(root, 'taxonomy', 'prereq.yml'),
           yaml.safe_dump({'prereq': pre}, allow_unicode=True, sort_keys=False))


def write_training_contract_docs(root):
    """两手册的最小契约片段；显式标记让 doclint 不会误扫其它数字。"""
    limits = '，'.join(f'★{difficulty}≤{minutes}min'
                      for difficulty, minutes in sp.TIME_LIMIT.items())
    for rel in bank.TRAINING_CONTRACT_DOCS:
        if rel == 'docs/学生手册.md':
            interval_head = '| 最近结果 | 判定标准 | 间隔 |\n| --- | --- | --- |'
            intervals = '\n'.join(
                f'| `{result}` | 测试说明 | {days} 天 |' for result, days in sp.INTERVALS.items())
        else:
            interval_head = '| 最近结果 | 间隔 |\n| --- | --- |'
            intervals = '\n'.join(
                f'| `{result}` | {days} 天 |' for result, days in sp.INTERVALS.items())
        text = f'''# 手册

<!-- training-contract:intervals:start -->
{interval_head}
{intervals}
<!-- training-contract:intervals:end -->

<!-- training-contract:time-limits:start -->
限时独立攻坚：{limits}。
<!-- training-contract:time-limits:end -->

<!-- training-contract:hint-cooldown:start -->
每解一级提示，再独立奋战 {sp.HINT_COOLDOWN_MIN} 分钟。
<!-- training-contract:hint-cooldown:end -->

<!-- training-contract:graduate-streak:start -->
连续 {sp.GRADUATE_STREAK} 次 `independent_ok` 即毕业。
<!-- training-contract:graduate-streak:end -->
'''
        _write(os.path.join(root, *rel.split('/')), text)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """一个 lint/doclint 基线全绿的空仓库：四个题目目录 + registry + 四张 taxonomy 板块表。"""
    root = str(tmp_path)
    for cat in bank.CATEGORIES:
        os.makedirs(os.path.join(root, 'problems', cat))
    write_registry(root)
    write_boards(root)
    write_prereq(root)
    write_training_contract_docs(root)
    monkeypatch.setattr(bank, 'ROOT', root)
    # gen_map 的掌握层会读学生与训练数据：路径一并重定向进 tmp，防止吸到真实 data/
    monkeypatch.setattr(stp, 'STUDENTS_ROOT', os.path.join(root, 'data', 'students'))
    monkeypatch.setattr(sp, 'ATTEMPTS_PATH', os.path.join(root, 'data', 'attempts.jsonl'))
    bank._VERDICT_CACHE.clear()   # 模块级缓存按 ref 相对路径存，换 ROOT 必须清，否则串味
    bank._MACHINE_CACHE.clear()
    yield root
    bank._VERDICT_CACHE.clear()
    bank._MACHINE_CACHE.clear()


def run_lint(capsys):
    rc = bank.lint(bank.load_all())
    return rc, capsys.readouterr().out


def run_doclint(capsys):
    rc = bank.doclint()
    return rc, capsys.readouterr().out


# ---------------- 1. 凭证四连（回归锚点③：无 verdicts.json 不入库） ----------------

class TestVerdictIronLaw:
    def test_reviewed_without_mathnet_id_is_rejected(self, repo, capsys):
        """防回归：mathnet-reviewed 却没 mathnet_id —— 无从核对，必须报缺字段而不是放行。"""
        ref = make_verdicts(repo)
        make_problem(repo, verification='mathnet-reviewed', review_ref=ref)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: verification=mathnet-reviewed 缺少必填溯源字段 mathnet_id' in out

    def test_reviewed_without_review_ref_is_rejected(self, repo, capsys):
        """防回归：只写 mathnet_id 不给 review_ref —— 这正是「裸声明」，必须报缺凭证字段。"""
        make_problem(repo, verification='mathnet-reviewed', mathnet_id='0ach')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'verification=mathnet-reviewed 缺少评审凭证字段 review_ref' in out
        assert '缺少必填溯源字段 mathnet_id' not in out   # mathnet_id 齐全时不得误报

    @pytest.mark.parametrize('case,raw', [
        ('missing', None),          # 文件根本不存在
        ('corrupt', '{不是 JSON'),   # 文件在但解析不了
    ])
    def test_review_ref_unreadable_is_rejected(self, repo, capsys, case, raw):
        """防回归：review_ref 指向不存在或坏掉的凭证文件时，不能被静默当成「已核验」。"""
        ref = VERDICTS_REL
        if case == 'corrupt':
            make_verdicts(repo, raw=raw)
        make_problem(repo, verification='mathnet-reviewed', mathnet_id='0ach', review_ref=ref)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert f'review_ref 指向的评审凭证不存在或无法解析：{ref}' in out

    def test_verdicts_not_covering_this_problem_is_rejected(self, repo, capsys):
        """防回归（最核心）：凭证文件存在但不含本题 mathnet_id —— 「数据集声称≠已核验」必须被抓。"""
        ref = make_verdicts(repo, ids=('0k7s', 'zzzz'))
        make_problem(repo, verification='mathnet-reviewed', mathnet_id='0ach', review_ref=ref)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert f'评审凭证 {ref} 未覆盖 mathnet_id=0ach——「数据集声称≠已核验」' in out

    def test_full_credentials_pass(self, repo, capsys):
        """防回归：凭证齐全且真实覆盖本题时必须 0 错误放行（避免铁律收紧到误杀合法入库）。"""
        ref = make_verdicts(repo, ids=('0ach',))
        make_problem(repo, verification='mathnet-reviewed', mathnet_id='0ach', review_ref=ref)
        rc, out = run_lint(capsys)
        assert rc == 0
        assert 'LINT OK: 1 题全部通过' in out

    def test_mathnet_id_coerced_to_str_before_compare(self, repo, capsys):
        """防回归：YAML 把纯数字 id 读成 int 时，不能因类型不同而误判「未覆盖」。"""
        ref = make_verdicts(repo, ids=('1234',))
        make_problem(repo, verification='mathnet-reviewed', mathnet_id=1234, review_ref=ref)
        rc, out = run_lint(capsys)
        assert rc == 0, out

    def test_sourced_does_not_require_credentials(self, repo, capsys):
        """防回归：凭证四连只对 mathnet-reviewed 生效，sourced 题不得被要求 review_ref。"""
        make_problem(repo, verification='sourced')
        rc, out = run_lint(capsys)
        assert rc == 0
        assert 'review_ref' not in out


# ---------------- 2. _verdict_ids：解析与缓存 ----------------

class TestVerdictIds:
    def test_missing_file_returns_none_not_empty_set(self, repo):
        """防回归：读不到凭证要返回 None（=不可解析），返回空集合会被误读成「凭证里没这题」。"""
        assert bank._verdict_ids('data/review/nope/verdicts.json') is None

    def test_ids_are_strings_and_non_dict_rows_skipped(self, repo):
        """防回归：凭证行混入非 dict（如 null）时不炸，且 id 统一成字符串便于与 frontmatter 比对。"""
        _write(os.path.join(repo, *VERDICTS_REL.split('/')),
               json.dumps([{'mathnet_id': '0ach'}, None, {'mathnet_id': 77}]))
        assert bank._verdict_ids(VERDICTS_REL) == {'0ach', '77'}

    def test_same_ref_read_only_once(self, repo):
        """防回归：同一 ref 必须命中 _VERDICT_CACHE —— 改文件后再调用仍返回旧集合，证明没重复读盘。"""
        ref = make_verdicts(repo, ids=('0ach',))
        assert bank._verdict_ids(ref) == {'0ach'}
        make_verdicts(repo, ids=('cccc',))          # 磁盘内容已变
        assert bank._verdict_ids(ref) == {'0ach'}   # 仍是缓存值 → 只读了一次
        bank._VERDICT_CACHE.clear()
        assert bank._verdict_ids(ref) == {'cccc'}   # 清缓存后才看到新内容


# ---------------- 3. 其余 lint 规则 ----------------

class TestLintRules:
    def test_unknown_frontmatter_field_is_rejected(self, repo, capsys):
        """防回归：字段全集以 SPEC §2 为准，清退字段或拼错字段都不能静默入库。"""
        make_problem(repo, deprecated_field='stale')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: frontmatter 含未知字段 deprecated_field' in out

    def test_missing_required_field(self, repo, capsys):
        """防回归：必填字段缺失或为空值（None/''/[]）都要报 —— 空 topics 曾能混过去。"""
        make_problem(repo, title=_DROP, topics=[])
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: 缺少必填字段 title' in out
        assert 'problems/algebra/A-001.md: 缺少必填字段 topics' in out

    def test_frontmatter_unparsable(self, repo, capsys):
        """防回归：没有 frontmatter 的 md 要报错并跳过后续检查，不能抛异常中断整轮 lint。"""
        _write(os.path.join(repo, 'problems', 'algebra', 'A-001.md'), '# 没有 frontmatter\n')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: frontmatter 缺失或无法解析' in out

    def test_id_must_match_filename(self, repo, capsys):
        """防回归：id 与文件名不一致（改名忘改 id）必须被抓，否则引用链会指到空处。"""
        make_problem(repo, pid='A-001', filename='A-002.md')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-002.md: id(A-001) 与文件名不一致' in out

    def test_id_prefix_must_match_directory(self, repo, capsys):
        """防回归：放错目录（N- 开头的题落在 algebra/）必须被抓。"""
        make_problem(repo, pid='N-001', cat='algebra')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/N-001.md: id 前缀与目录 algebra 不匹配' in out

    # True 一项防的是：bool 是 int 子类，YAML 的 difficulty: true/yes 读成 True 后
    # 曾能通过 `isinstance(x, int) and 1 <= x <= 5` 被静默当成 ★1 放行。
    @pytest.mark.parametrize('bad', [0, 6, '3', 3.0, True])
    def test_difficulty_must_be_int_1_to_5(self, repo, capsys, bad):
        """防回归：难度越界或非整数（含字符串 '3'、浮点 3.0、布尔 true）一律拒收。"""
        make_problem(repo, difficulty=bad)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: difficulty 必须是 1-5 的整数' in out

    def test_source_url_must_be_http(self, repo, capsys):
        """防回归：source_url 写成本地路径/相对引用时必须报「不是链接」。"""
        make_problem(repo, source_url='docs/sources/x.pdf')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: source_url 不是链接' in out

    def test_missing_section(self, repo, capsys):
        """防回归：三个必需小节缺任何一个都要点名报出（这里删掉「## 答案」）。"""
        body = BODY_OK.replace('## 答案\n\n17 与 8。\n\n', '')
        make_problem(repo, body=body)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: 缺少小节 ## 答案' in out
        assert '缺少小节 ## 题面' not in out

    def test_numbering_must_be_gapless(self, repo, capsys):
        """防回归：题号必须从 001 连号无空洞 —— 造 A-001+A-003，断言点名缺 [2]。"""
        make_problem(repo, pid='A-001')
        make_problem(repo, pid='A-003')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'algebra: 题号不连续，缺 [2]' in out

    def test_numbering_ok_when_consecutive(self, repo, capsys):
        """防回归：连号题库不得误报空洞（连号检查曾按「最大号」而非集合差计算）。"""
        make_problem(repo, pid='A-001')
        make_problem(repo, pid='A-002')
        rc, out = run_lint(capsys)
        assert rc == 0
        assert '题号不连续' not in out
        assert 'LINT OK: 2 题全部通过' in out


class TestVerificationEnum:
    def test_illegal_verification_rejected(self, repo, capsys):
        """防回归：verification 只能取白名单值，随手写的 'eyeballed' 必须被拒并列出合法值。"""
        make_problem(repo, verification='eyeballed')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'verification 取值非法（合法：sourced/independent-derivation/mathnet-reviewed）' in out

    def test_enum_source_of_truth_is_spar_session(self, repo, capsys):
        """防回归：合法枚举的唯一正本是 spar_session.VALID_VERIFICATION，bank 不得另存副本。"""
        assert sp.VALID_VERIFICATION == ('sourced', 'independent-derivation', 'mathnet-reviewed')
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sp, 'VALID_VERIFICATION', sp.VALID_VERIFICATION + ('provisional-2026',))
            make_problem(repo, verification='provisional-2026')
            rc, out = run_lint(capsys)
        assert rc == 0, out   # bank 若硬编码了自己的元组，这里会判非法 → 失败


# ---------------- 4. registry_report ----------------

class TestRegistryReport:
    def test_alias_resolves_to_canonical_node(self, repo):
        """防回归：registry 里登记过的别名（AM-GM → 不等式）不得报未注册。"""
        make_problem(repo, topics=['AM-GM'])
        assert bank.registry_report(bank.load_all()) == []

    def test_cross_board_canonical_node_allowed(self, repo):
        """防回归：跨界题并列他板块的规范节点（algebra 题挂 geometry 的「圆幂」）应放行。"""
        make_problem(repo, cat='algebra', topics=['不等式', '圆幂'])
        assert bank.registry_report(bank.load_all()) == []

    def test_unregistered_topic_warned(self, repo):
        """防回归：生造词必须报警告并点名题号与词本身（放行跨板块时容易连它一起放行）。"""
        make_problem(repo, topics=['祖传绝技'])
        assert bank.registry_report(bank.load_all()) == ['A-001: 「祖传绝技」']

    def test_warning_does_not_block_lint(self, repo, capsys):
        """防回归：词表警告只提示不阻塞 —— lint 仍返回 0 且打印警告条数。"""
        make_problem(repo, topics=['祖传绝技'])
        rc, out = run_lint(capsys)
        assert rc == 0
        assert '警告：1 处知识点未注册' in out
        assert 'A-001: 「祖传绝技」' in out

    def test_missing_registry_reported(self, repo):
        """防回归：registry.yml 丢失时要显式报缺，不能返回空列表让 lint 误判「全部已注册」。"""
        os.remove(os.path.join(repo, 'taxonomy', 'registry.yml'))
        assert bank.registry_report(bank.load_all()) == ['taxonomy/registry.yml 缺失']


# ---------------- 5. doclint：死链 / 禁词 / taxonomy 树一致性 ----------------

class TestDoclint:
    def test_clean_repo_passes(self, repo, capsys):
        """防回归：基线仓库（无链接、板块表与 registry 一致）必须 0 问题，避免检查本身误报。"""
        rc, out = run_doclint(capsys)
        assert rc == 0
        assert 'DOCLINT OK' in out

    def test_mathnet_full_is_not_scanned(self, repo, capsys):
        """gitignore 派生语料含非中文与 LaTeX；即使存在，也不进入全仓文档检查。"""
        _write(os.path.join(repo, 'mathnet-full', 'by-topic', 'x', 'index.md'),
               '[缺的](missing.md)\n推到 origin/main 即可。\n')
        rc, out = run_doclint(capsys)
        assert rc == 0, out
        assert 'DOCLINT OK' in out

    def test_training_contract_matches_source_passes(self, repo, capsys):
        """两手册四组标记值与 spar_session 正本一致时不得误报。"""
        rc, out = run_doclint(capsys)
        assert rc == 0, out
        assert '训练契约' in out

    def test_training_contract_source_drift_caught(self, repo, capsys, monkeypatch):
        """代码正本单独改动时，两份手册均须点名字段、期望值与抄录值。"""
        documented = sp.TIME_LIMIT[3]
        changed = {**sp.TIME_LIMIT, 3: documented + 1}
        monkeypatch.setattr(sp, 'TIME_LIMIT', changed)
        rc, out = run_doclint(capsys)
        assert rc == 1
        expected = f'攻坚限时「3」应为 {documented + 1}分钟，手册写为 {documented}分钟'
        assert f'docs/学生手册.md: {expected}' in out
        assert f'docs/教练手册.md: {expected}' in out
        assert 'DOCLINT FAILED: 2 个问题' in out

    def test_hint_cooldown_source_drift_caught(self, repo, capsys, monkeypatch):
        """提示冷却代码正本单独改动时，两份手册均须点名期望值与抄录值。"""
        documented = sp.HINT_COOLDOWN_MIN
        monkeypatch.setattr(sp, 'HINT_COOLDOWN_MIN', documented + 1)
        rc, out = run_doclint(capsys)
        assert rc == 1
        expected = f'提示冷却「每级」应为 {documented + 1}分钟，手册写为 {documented}分钟'
        assert f'docs/学生手册.md: {expected}' in out
        assert f'docs/教练手册.md: {expected}' in out
        assert 'DOCLINT FAILED: 2 个问题' in out

    def test_dead_relative_link_caught(self, repo, capsys):
        """防回归：指向不存在相对路径的链接必须被抓；外链/锚点/存在的相对链接不得误报。"""
        _write(os.path.join(repo, 'docs', 'guide.md'),
               '[缺的](missing.md)\n[在的](guide.md)\n[外链](https://example.org/a.md)\n[锚](#节)\n')
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'docs/guide.md: 死链 missing.md' in out
        assert out.count('死链') == 1
        assert 'DOCLINT FAILED: 1 个问题' in out

    def test_link_with_anchor_resolves_to_file(self, repo, capsys):
        """防回归：带 #锚点 的相对链接要先剥锚点再判存在，否则整片正常链接会被误报死链。"""
        _write(os.path.join(repo, 'docs', 'guide.md'), '[章节](guide.md#小节)\n')
        rc, out = run_doclint(capsys)
        assert rc == 0, out

    def test_math_bracket_adjacency_not_dead_link(self, repo, capsys):
        """防回归：LaTeX 里 [..](..) 相邻（如 c[(x+r)-(x+r-d-1)](x+r-1)）是数学记号，
        不得报死链——题面/答案逐字照录不可改，尺子必须避开数学环境。"""
        _write(os.path.join(repo, 'docs', 'sol.md'),
               '推导：\n$$\n\\frac{c[(x+r)-(x+r-d-1)](x+r-1)}{d+1}\n$$\n'
               '行内 $c[(a)-(b)](x-1)$ 同理。\n')
        rc, out = run_doclint(capsys)
        assert rc == 0, out

    def test_dead_link_outside_math_still_caught(self, repo, capsys):
        """防回归：挖数学环境不得放走公式之外的真死链，同文件混排时仍要抓到。"""
        _write(os.path.join(repo, 'docs', 'mix.md'),
               '$$c[(a)-(b)](x-1)$$\n\n[缺的](missing.md)\n')
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'docs/mix.md: 死链 missing.md' in out
        assert out.count('死链') == 1

    def test_forbidden_word_caught_outside_archive(self, repo, capsys):
        """防回归：DOCLINT_FORBIDDEN 里的废弃指针出现在正文要报（含行号），docs/archive/ 存档豁免。"""
        assert 'origin/main' in bank.DOCLINT_FORBIDDEN
        _write(os.path.join(repo, 'docs', 'live.md'), '# 标题\n\n推到 origin/main 即可。\n')
        _write(os.path.join(repo, 'docs', 'archive', 'old.md'), '推到 origin/main 即可。\n')
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'docs/live.md:3: 禁词「origin/main」' in out
        assert 'archive/old.md' not in out

    def test_taxonomy_missing_node_section_caught(self, repo, capsys):
        """防回归：registry 有节点而板块表没有对应「### 节点」小节 → 树与词表漂移，必须报缺。"""
        _write(os.path.join(repo, 'taxonomy', 'algebra.md'), '# algebra\n\n### 函数方程\n\n占位。\n')
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'taxonomy/algebra.md: 缺少 registry 节点小节「### 不等式」' in out

    def test_taxonomy_extra_section_caught(self, repo, capsys):
        """防回归：板块表多出 registry 里没有的小节（生造节点）也要报「树漂移」。"""
        _write(os.path.join(repo, 'taxonomy', 'algebra.md'),
               '# algebra\n\n### 不等式\n\n占位。\n\n### 函数方程\n\n占位。\n\n### 幻术\n\n占位。\n')
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'taxonomy/algebra.md: 小节「幻术」不是 registry 的 algebra 节点（树漂移）' in out

    def test_numbered_heading_strips_index(self, repo, capsys):
        """防回归：板块表小节写成「### 1. 不等式」时序号要剥掉再比对，不得因编号误判漂移。"""
        _write(os.path.join(repo, 'taxonomy', 'algebra.md'),
               '# algebra\n\n### 1. 不等式\n\n占位。\n\n### 2、函数方程\n\n占位。\n')
        rc, out = run_doclint(capsys)
        assert rc == 0, out

    def test_missing_board_file_caught(self, repo, capsys):
        """防回归：四板块表少一张时点名报缺，而不是静默跳过一致性检查。"""
        os.remove(os.path.join(repo, 'taxonomy', 'geometry.md'))
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'taxonomy/geometry.md 缺失' in out


class TestTopicsCount:
    # 上限正本是 SPEC §2「1–4 个中文规范节点」；下限（空列表）由必填字段检查兜底。
    def test_topics_over_four_rejected(self, repo, capsys):
        """防回归：topics 超过 4 个（标签堆砌）必须拒收——历史上有题挂到 5 个。"""
        make_problem(repo, topics=['不等式', '函数方程', '多项式', '数列与递推', '根与系数'])
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: topics 有 5 个，超出上限 4' in out

    def test_topics_four_passes(self, repo, capsys):
        """4 个正好在上限内，不得误伤。"""
        make_problem(repo, topics=['不等式', '函数方程', '不等式', '函数方程'])
        rc, out = run_lint(capsys)
        assert rc == 0, out

    def test_topics_non_list_rejected(self, repo, capsys):
        """防回归：topics 写成裸字符串（YAML 少写方括号）要报类型错，而不是被逐字符迭代。"""
        make_problem(repo, topics='不等式')
        rc, out = run_lint(capsys)
        assert rc == 1
        assert 'problems/algebra/A-001.md: topics 必须是列表' in out


# ---------------- 前置依赖图（doclint d 项：端点合法 + DAG） ----------------

class TestPrereqGraph:
    def test_baseline_with_cross_board_edge_passes(self, repo, capsys):
        """基线 PREREQ 含一条跨板块边（geometry/圆幂 ← number-theory/同余），必须绿。"""
        rc, out = run_doclint(capsys)
        assert rc == 0, out

    def test_missing_prereq_file_caught(self, repo, capsys):
        """防回归：prereq.yml 与 registry 同级正本，缺失要点名报，不得静默跳过。"""
        os.remove(os.path.join(repo, 'taxonomy', 'prereq.yml'))
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'taxonomy/prereq.yml 缺失' in out

    def test_unknown_node_endpoint_caught(self, repo, capsys):
        write_prereq(repo, {'algebra/函数方程': ['algebra/不存在的节点']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '端点「algebra/不存在的节点」不是 registry 规范节点' in out

    def test_alias_endpoint_caught(self, repo, capsys):
        """防回归：端点只认规范节点名——写别名（AM-GM 是不等式的别名）必须红，防词表漂移。"""
        write_prereq(repo, {'algebra/函数方程': ['algebra/AM-GM']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '端点「algebra/AM-GM」不是 registry 规范节点' in out

    def test_bare_node_without_category_caught(self, repo, capsys):
        """端点必须带 <板块>/ 限定名，裸节点名不合法。"""
        write_prereq(repo, {'algebra/函数方程': ['不等式']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '端点「不等式」不是 registry 规范节点' in out

    def test_self_loop_caught(self, repo, capsys):
        write_prereq(repo, {'algebra/不等式': ['algebra/不等式']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '「algebra/不等式」把自己列为前置（自环）' in out

    def test_two_cycle_caught_with_members(self, repo, capsys):
        write_prereq(repo, {'algebra/不等式': ['algebra/函数方程'],
                            'algebra/函数方程': ['algebra/不等式']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '依赖图有环' in out
        assert 'algebra/不等式' in out and 'algebra/函数方程' in out

    def test_three_cycle_caught(self, repo, capsys):
        write_prereq(repo, {'algebra/不等式': ['algebra/函数方程'],
                            'algebra/函数方程': ['number-theory/同余'],
                            'number-theory/同余': ['algebra/不等式']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert '依赖图有环' in out

    def test_cycle_downstream_not_reported_as_cycle_member(self, repo, capsys):
        """防回归：报错只点名真正环上的节点——依赖环的下游节点（同余 ← 函数方程）不算环成员。"""
        write_prereq(repo, {'algebra/不等式': ['algebra/函数方程'],
                            'algebra/函数方程': ['algebra/不等式'],
                            'number-theory/同余': ['algebra/函数方程']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        cyc_line = next(line for line in out.splitlines() if '依赖图有环' in line)
        assert 'algebra/不等式' in cyc_line and 'algebra/函数方程' in cyc_line
        assert 'number-theory/同余' not in cyc_line

    def test_cycle_does_not_mask_valid_branch(self, repo, capsys):
        """环之外的合法边不受牵连：报错只点名环上节点。"""
        write_prereq(repo, {'algebra/不等式': ['algebra/函数方程'],
                            'algebra/函数方程': ['algebra/不等式'],
                            'geometry/圆幂': ['number-theory/同余']})
        rc, out = run_doclint(capsys)
        assert rc == 1
        assert 'geometry/圆幂' not in out.split('依赖图有环')[1].splitlines()[0]


def run_map(repo, capsys):
    _write(os.path.join(repo, 'scripts', 'map_template.html'), '<html>__MAP_DATA__</html>')
    bank.gen_map(bank.load_all())
    out = capsys.readouterr().out
    return json.load(open(os.path.join(repo, 'maps', 'map_data.json'), encoding='utf-8')), out


class TestGenMapEdges:
    def test_map_data_contains_edges_and_node_keys(self, repo, capsys):
        """gen_map 产物：节点带 key（cat/name），edges 来自 prereq.yml。"""
        make_problem(repo)
        data, _ = run_map(repo, capsys)
        assert {'from': 'algebra/不等式', 'to': 'algebra/函数方程'} in data['edges']
        keys = {n['key'] for c in data['cats'] for n in c['nodes']}
        assert 'algebra/不等式' in keys

    def test_edges_with_unknown_endpoint_skipped(self, repo, capsys):
        """map 只消费不校验（校验正本在 doclint）：端点不认识的边跳过，不崩、不输出。"""
        write_prereq(repo, {'algebra/函数方程': ['algebra/幽灵节点']})
        make_problem(repo)
        data, _ = run_map(repo, capsys)
        assert data['edges'] == []


class TestGenMapLayers:
    """指示图叠层：供给层（候选池可补量）与掌握层（self 四档状态）的 schema 锚点。"""

    def test_supply_per_node_by_star(self, repo, capsys):
        """候选池存在时节点 stars 旁带 supply：status=ok 且 est≥学段下界才计入，按估级细分
        （计数与 candidates --gaps / gaps 台账同源 gap_counts）。"""
        make_problem(repo)
        rows = [{'mathnet_id': '0001', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 2},
                {'mathnet_id': '0002', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 3},
                {'mathnet_id': '0003', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 1},      # ★1：学段下界之下，不计
                {'mathnet_id': '0004', 'status': 'dropped', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 3}]      # 非 ok：不计
        _write(os.path.join(repo, 'candidates', 'mathnet.jsonl'),
               ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        data, _ = run_map(repo, capsys)
        assert data['has_supply'] is True
        by_key = {n['key']: n for c in data['cats'] for n in c['nodes']}
        assert by_key['algebra/不等式']['supply'] == {'2': 1, '3': 1}
        assert by_key['algebra/函数方程']['supply'] == {}

    def test_supply_omitted_without_pool(self, repo, capsys):
        """候选池缺失（clone 后常态）：节点不带 supply、has_supply=False，CLI 提示重建。"""
        make_problem(repo)
        data, out = run_map(repo, capsys)
        assert data['has_supply'] is False
        assert all('supply' not in n for c in data['cats'] for n in c['nodes'])
        assert '供给层已省略' in out and 'mathnet_ingest.py' in out

    def test_mastery_reuses_student_profile(self, repo, capsys):
        """掌握层复用 student_profile 装配：fail 证据 → 该节点薄弱，registry 其余节点未测。"""
        make_problem(repo)
        stp.save_student({'id': 'self', 'attempt_aliases': []})
        _write(os.path.join(repo, 'data', 'attempts.jsonl'),
               json.dumps({'id': 'A-001', 'result': 'fail', 'date': '2026-08-01',
                           'student': 'self'}) + '\n')
        data, out = run_map(repo, capsys)
        m = data['mastery']
        assert m['student'] == 'self' and m['evidence'] == 1
        assert m['nodes']['algebra/不等式']['status'] == '薄弱'
        assert m['nodes']['algebra/函数方程']['status'] == '未测'
        assert set(m['nodes']) == {f'{c}/{n}' for c, ns in REGISTRY.items() for n in ns}
        assert '掌握层：学生 self 证据 1 条' in out

    def test_mastery_null_without_evidence(self, repo, capsys):
        """空态两档都不报错：无档案 → null；有档案零证据 → 仍是 null。"""
        make_problem(repo)
        data, out = run_map(repo, capsys)
        assert data['mastery'] is None
        assert '掌握层：无学生证据' in out
        stp.save_student({'id': 'self', 'attempt_aliases': []})
        data, _ = run_map(repo, capsys)
        assert data['mastery'] is None

# ---------------- 机器核验凭证（可选字段 machine_check_ref） ----------------

MACHINE_REL = 'data/verify/machine-01/results.json'


def make_machine_ledger(root, rel=MACHINE_REL, rows=None, raw=None):
    """落一份机器核验台账；raw 非 None 时写原始字符串（造坏 JSON 用）。"""
    payload = raw if raw is not None else json.dumps(rows or [], ensure_ascii=False)
    _write(os.path.join(root, *rel.split('/')), payload)
    return rel


class TestMachineCheckRef:
    def test_no_field_requires_nothing(self, repo, capsys):
        """增量式：没挂 machine_check_ref 的题一切照旧——机器核验是补充凭证，不是门槛。"""
        make_problem(repo)
        rc, out = run_lint(capsys)
        assert rc == 0, out
        assert 'machine_check_ref' not in out

    def test_missing_ledger_caught(self, repo, capsys):
        make_problem(repo, machine_check_ref=MACHINE_REL)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert f'machine_check_ref 指向的核验台账不存在或无法解析：{MACHINE_REL}' in out

    def test_corrupt_ledger_caught(self, repo, capsys):
        make_machine_ledger(repo, raw='{不是 JSON')
        make_problem(repo, machine_check_ref=MACHINE_REL)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert '核验台账不存在或无法解析' in out

    def test_ledger_not_covering_problem_caught(self, repo, capsys):
        """防回归（最核心）：台账在但没本题——「裸声明不被信任」对机器核验同样成立。"""
        make_machine_ledger(repo, rows=[{'id': 'N-001', 'status': 'pass'}])
        make_problem(repo, machine_check_ref=MACHINE_REL)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert f'核验台账 {MACHINE_REL} 未覆盖 A-001 或状态非 pass' in out

    def test_ledger_status_fail_caught(self, repo, capsys):
        make_machine_ledger(repo, rows=[{'id': 'A-001', 'status': 'fail'}])
        make_problem(repo, machine_check_ref=MACHINE_REL)
        rc, out = run_lint(capsys)
        assert rc == 1
        assert '未覆盖 A-001 或状态非 pass' in out

    def test_ledger_pass_ok(self, repo, capsys):
        make_machine_ledger(repo, rows=[{'id': 'A-001', 'status': 'pass'}])
        make_problem(repo, machine_check_ref=MACHINE_REL)
        rc, out = run_lint(capsys)
        assert rc == 0, out

# ---------------- 外链检查 linkcheck（不联网：注入假 fetcher） ----------------

class TestLinkcheck:
    def _docs(self, repo):
        _write(os.path.join(repo, 'docs', 'guide.md'),
               '# 指南\n\n[好链](https://good.example/a)\n[好链再引](https://good.example/a)\n'
               '[坏链](https://dead.example/b)\n[站内](../SPEC.md)\n[邮件](mailto:x@y.z)\n')
        _write(os.path.join(repo, 'docs', 'archive', 'old.md'),
               '# 存档\n\n[史料死链](https://gone.example/z)\n')
        _write(os.path.join(repo, 'SPEC.md'), '# spec\n')

    def test_collect_dedups_and_exempts_archive(self, repo):
        """收链：http(s) 才收、同链去重记两处出现、docs/archive/ 豁免、相对链与 mailto 不收。"""
        self._docs(repo)
        links = bank.collect_external_links()
        assert set(links) == {'https://good.example/a', 'https://dead.example/b'}
        assert len(links['https://good.example/a']) == 2

    def test_dead_link_fails_and_lists_locations(self, repo, capsys):
        self._docs(repo)
        rc_ = bank.linkcheck(fetch=lambda url: 404 if 'dead' in url else 200)
        out = capsys.readouterr().out
        assert rc_ == 1
        assert 'LINKCHECK FAILED: 1 个死链' in out
        assert 'https://dead.example/b' in out
        assert 'guide.md' in out           # 报出引用位置
        assert 'gone.example' not in out   # 存档豁免：连检查都不进

    def test_all_alive_passes(self, repo, capsys):
        self._docs(repo)
        rc_ = bank.linkcheck(fetch=lambda url: 200)
        out = capsys.readouterr().out
        assert rc_ == 0
        assert 'LINKCHECK OK: 2 个外链全部可达' in out

    def test_network_error_counts_as_dead(self, repo, capsys):
        self._docs(repo)
        rc_ = bank.linkcheck(fetch=lambda url: None)
        assert rc_ == 1
