"""学生档案与能力图关键函数级用例（纯 stdlib + pytest）：

1. 证据折算与节点归属（evidence_from_assessment / evidence_from_attempt / resolve_nodes）
2. 难度加权掌握值与状态阈值（weighted / status_of）
3. 波次基础值序列（wave_rows）
4. 分类细分建议触发条件（split_suggestions ← node_table）
5. 补齐队列（gap_queue：薄弱在前、已做题不复推）

运行：uv run --group dev pytest -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import student_profile as stp  # noqa: E402

# 微型 registry：两板块、各两节点，含别名与跨界词
REG = {
    'algebra': {'不等式': ['AM-GM'], '函数方程': []},
    'combinatorics': {'图论': ['graph theory'], '计数与容斥': []},
}


def _resolve(reg, cat, t):
    """bank.resolve_topic 的同构实现（节点名或别名 → 节点）。"""
    nodes = (reg or {}).get(cat) or {}
    if t in nodes:
        return t
    for node, aliases in nodes.items():
        if t in (aliases or []):
            return node
    return None


FMMAP = {
    'A-001': {'id': 'A-001', 'category': 'algebra', 'topics': ['不等式'], 'difficulty': 3},
    'C-001': {'id': 'C-001', 'category': 'combinatorics', 'topics': ['图论'], 'difficulty': 4},
}


# ---------------- 1. 证据折算与节点归属 ----------------

class TestEvidence:
    def test_resolve_nodes_alias_and_crosscat(self):
        # 别名解析 + 跨界词（代数题挂图论）落到词所属板块
        nodes, unresolved = stp.resolve_nodes(REG, _resolve, 'algebra', ['AM-GM', 'graph theory', '没注册的词'])
        assert nodes == [('algebra', '不等式'), ('combinatorics', '图论')]
        assert unresolved == ['没注册的词']

    def test_assessment_bank_ref_prefers_live_frontmatter(self):
        # 库内题：即使快照字段是旧的，读取时以现行 frontmatter 为准（retag 后自动细化）
        rec = {'ref': 'A-001', 'wave': 'W1', 'date': '2026-08-01', 'score': 0.5,
               'category': 'combinatorics', 'topics': ['过期快照'], 'difficulty': 1}
        ev, unresolved = stp.evidence_from_assessment(rec, FMMAP, REG, _resolve)
        assert ev['category'] == 'algebra' and ev['difficulty'] == 3
        assert ev['nodes'] == [('algebra', '不等式')] and unresolved == []
        assert ev['value'] == 0.5 and ev['wave'] == 'W1'

    def test_assessment_external_ref_uses_own_fields(self):
        rec = {'ref': 'AMC10 2023 P15', 'wave': 'W1', 'date': '2026-08-01',
               'category': 'combinatorics', 'topics': ['图论'], 'difficulty': 2, 'score': 1}
        ev, _ = stp.evidence_from_assessment(rec, FMMAP, REG, _resolve)
        assert ev['category'] == 'combinatorics' and ev['nodes'] == [('combinatorics', '图论')]

    @pytest.mark.parametrize('result,value', [
        ('independent_ok', 1.0), ('hinted_ok', 0.6),
        ('solution_reconstructed', 0.3), ('fail', 0.0),
    ])
    def test_attempt_result_value(self, result, value):
        ev, _ = stp.evidence_from_attempt(
            {'id': 'C-001', 'result': result, 'date': '2026-08-02'}, FMMAP, REG, _resolve)
        assert ev['value'] == value and ev['difficulty'] == 4

    def test_attempt_unknown_id_skipped(self):
        # legacy 清退后的旧题号：不计入，不报错
        ev, _ = stp.evidence_from_attempt(
            {'id': 'A-039', 'result': 'fail', 'date': '2026-07-01'}, FMMAP, REG, _resolve)
        assert ev is None


# ---------------- 2. 掌握值与状态 ----------------

def _ev(value, d=3, **kw):
    base = {'kind': 'assessment', 'ref': 'X', 'wave': 'W1', 'date': '2026-08-01',
            'category': 'algebra', 'difficulty': d, 'value': value, 'nodes': []}
    base.update(kw)
    return base


class TestMasteryStatus:
    def test_weighted_by_difficulty(self):
        # ★1 满分 + ★4 零分 → 1/5，难题权重压低均值
        assert stp.weighted([_ev(1.0, d=1), _ev(0.0, d=4)]) == pytest.approx(0.2)
        assert stp.weighted([]) is None

    @pytest.mark.parametrize('mastery,n,expect', [
        (0.0, 0, '未测'),
        (0.39, 3, '薄弱'),           # < WEAK_MAX
        (0.40, 3, '进行中'),         # 恰在薄弱线上 → 进行中
        (0.80, 1, '进行中'),         # 达稳固线但证据不足 SOLID_MIN_N
        (0.75, 2, '稳固'),           # 恰在稳固线 + 证据够
        (0.90, 5, '稳固'),
    ])
    def test_status_thresholds(self, mastery, n, expect):
        assert stp.status_of(mastery, n) == expect

    def test_node_table_best_and_untested(self):
        evs = [_ev(1.0, d=3, nodes=[('algebra', '不等式')]),
               _ev(0.6, d=4, nodes=[('algebra', '不等式')])]
        table = stp.node_table(evs, REG)
        st = table[('algebra', '不等式')]
        assert st['n'] == 2 and st['best'] == 3          # 已证★=满分证据的最高难度
        assert table[('algebra', '函数方程')]['status'] == '未测'  # registry 全节点在表，含未测


# ---------------- 3. 波次基础值 ----------------

class TestWaves:
    def test_wave_rows_grouping_and_order(self):
        evs = [
            _ev(1.0, d=2, wave='W2', date='2026-08-20'),
            _ev(0.5, d=2, wave='W1', date='2026-08-01'),
            _ev(1.0, d=2, wave='W1', date='2026-08-02', category='combinatorics'),
            _ev(0.6, d=3, wave=None, kind='attempt', date='2026-08-03'),  # 训练不进波次
        ]
        rows = stp.wave_rows(evs)
        assert [r['wave'] for r in rows] == ['W1', 'W2']   # 按首测日期排序
        assert rows[0]['cats']['algebra']['v'] == pytest.approx(0.5)
        assert rows[0]['cats']['combinatorics']['n'] == 1
        assert 'combinatorics' not in rows[1]['cats']       # 该波未测该板块


# ---------------- 4. 细分建议 ----------------

class TestSplit:
    def _table(self, values):
        evs = [_ev(v, nodes=[('combinatorics', '图论')], ref=f'R{i}') for i, v in enumerate(values)]
        return stp.node_table(evs, REG)

    def test_triggers_on_polarized_node(self):
        # 6 条证据、强弱各 ≥2 → 触发
        sugs = stp.split_suggestions(self._table([1.0, 1.0, 0.0, 0.0, 0.6, 0.6]))
        assert len(sugs) == 1
        s = sugs[0]
        assert (s['cat'], s['node'], s['n']) == ('combinatorics', '图论', 6)
        assert len(s['strong']) == 2 and len(s['weak']) == 2

    def test_no_trigger_below_min_n_or_one_sided(self):
        assert stp.split_suggestions(self._table([1.0, 1.0, 0.0, 0.0])) == []      # n < SPLIT_MIN_N
        assert stp.split_suggestions(self._table([1.0] * 5 + [0.0])) == []          # 弱侧只有 1 条


# ---------------- 5. 补齐队列 ----------------

class TestGapQueue:
    PROBLEMS = [{'fm': FMMAP['A-001']}, {'fm': FMMAP['C-001']}]

    def test_weak_before_untested_and_seen_excluded(self):
        # 不等式薄弱（3 条 0 分），其余节点未测；A-001 已做过 → 不再推
        evs = [_ev(0.0, nodes=[('algebra', '不等式')], ref='A-001')] * 3
        table = stp.node_table(evs, REG)
        queue = stp.gap_queue(table, REG, self.PROBLEMS, _resolve, seen_refs={'A-001'})
        assert queue[0]['node'] == '不等式' and queue[0]['status'] == '薄弱'
        assert queue[0]['picks'] == []                       # 唯一库内题已做过
        tuolun = next(g for g in queue if g['node'] == '图论')
        assert tuolun['status'] == '未测' and tuolun['picks'] == [{'id': 'C-001', 'd': 4}]
