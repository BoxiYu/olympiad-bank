"""缺口台账与产物自检回归用例：bank.cmd_gaps / load_gap_picks / pick_week / doctor。

全部在 tmp_path 造的假仓库上跑（monkeypatch bank.ROOT + 学生/训练数据路径），不碰真实
maps/ candidates/ data/。核心防两件事：一是 gaps.json 台账的键空间与降级语义漂移
（coach --from-gaps 只读这份台账，schema 变了它会静默拿不到缺口）；二是 doctor 对
「缺失（clone 后正常）／陈旧／残留」三态的判定与退出码——它是生成产物唯一的新鲜度闸。

运行：uv run --group dev pytest -q
"""
import argparse
import json
import os
import random
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import bank  # noqa: E402
import spar_session as sp  # noqa: E402
import student_profile as stp  # noqa: E402

REGISTRY = {
    'algebra': {'不等式': ['AM-GM'], '函数方程': None},
    'number-theory': {'同余': None},
    'combinatorics': {'计数': None},
    'geometry': {'圆幂': None},
}

# 函数方程以不等式为前置：拓扑序（不等式在前）与 blocked_by 标注都靠这条边验证
PREREQ = {'algebra/函数方程': ['algebra/不等式']}

BODY_OK = '\n## 题面\n\n占位。\n\n## 答案\n\n42。\n\n## 解法要点\n\n直接算。\n'


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def make_problem(root, pid='A-001', cat='algebra', **over):
    fm = {'id': pid, 'title': f'测试题 {pid}', 'category': cat, 'source_ref': 'MathNet test',
          'difficulty': 2, 'topics': ['不等式'], 'verification': 'sourced',
          'source_url': 'https://example.org/p'}
    fm.update(over)
    text = '---\n' + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + '---\n' + BODY_OK
    _write(os.path.join(root, 'problems', cat, pid + '.md'), text)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """假仓库：四个题目目录 + registry + prereq；学生与训练数据路径一并重定向进 tmp。"""
    root = str(tmp_path)
    for cat in bank.CATEGORIES:
        os.makedirs(os.path.join(root, 'problems', cat))
    _write(os.path.join(root, 'taxonomy', 'registry.yml'),
           yaml.safe_dump(REGISTRY, allow_unicode=True, sort_keys=False))
    _write(os.path.join(root, 'taxonomy', 'prereq.yml'),
           yaml.safe_dump({'prereq': PREREQ}, allow_unicode=True, sort_keys=False))
    monkeypatch.setattr(bank, 'ROOT', root)
    monkeypatch.setattr(stp, 'STUDENTS_ROOT', os.path.join(root, 'data', 'students'))
    monkeypatch.setattr(sp, 'ATTEMPTS_PATH', os.path.join(root, 'data', 'attempts.jsonl'))
    yield root


def run_gaps(repo, capsys, student='self'):
    bank.cmd_gaps(bank.load_all(), argparse.Namespace(student=student))
    out = capsys.readouterr().out
    return json.load(open(os.path.join(repo, 'maps', 'gaps.json'), encoding='utf-8')), out


def write_gaps_ledger(root, evidence_total, nodes=()):
    _write(os.path.join(root, 'maps', 'gaps.json'),
           json.dumps({'generated': '2026-08-06T00:00:00', '_note': 'x', 'student': 'self',
                       'evidence_total': evidence_total, 'nodes': list(nodes)},
                      ensure_ascii=False))


# ---------------- 1. gaps.json：schema 与合并语义 ----------------

class TestGapsLedger:
    def test_schema_and_top_fields(self, repo, capsys):
        """台账顶部必须是 generated + _note（生成产物标识），节点行带齐合并后的全部键。"""
        make_problem(repo)
        data, out = run_gaps(repo, capsys)
        assert list(data)[:2] == ['generated', '_note']
        assert '勿手改' in data['_note'] and 'bank.py gaps' in data['_note']
        assert data['student'] == 'self'
        assert len(data['nodes']) == sum(len(v) for v in REGISTRY.values())
        keys = {'cat', 'node', 'status', 'mastery', 'n', 'bank', 'cand',
                'queue_rank', 'picks', 'blocked_by'}
        assert all(keys == set(e) for e in data['nodes'])
        assert 'maps/gaps.json 已生成' in out

    def test_no_student_no_candidates_degrades(self, repo, capsys):
        """优雅降级：无档案 → 零证据照常产出（全未测）；候选池缺失 → cand 置 null 并提示重建。"""
        make_problem(repo)
        data, out = run_gaps(repo, capsys)
        assert '学生 self 无档案' in out
        assert '候选池不存在' in out and 'mathnet_ingest.py' in out
        assert data['evidence_total'] == 0
        assert all(e['status'] == '未测' for e in data['nodes'])
        assert all(e['cand'] is None for e in data['nodes'])
        ineq = next(e for e in data['nodes'] if e['node'] == '不等式')
        assert ineq['bank'] == {'total': 1, 'by_star': {'2': 1}}
        assert ineq['picks'] == [{'id': 'A-001', 'd': 2}]

    def test_candidate_counts_respect_grade_floor(self, repo, capsys):
        """候选计数与 candidates --gaps 同源：status=ok 且 est≥学段下界才计入，带星级细分。"""
        make_problem(repo)
        rows = [{'mathnet_id': '0001', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 2},
                {'mathnet_id': '0002', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 3},
                {'mathnet_id': '0003', 'status': 'ok', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 1},     # ★1：学段下界之下，不计
                {'mathnet_id': '0004', 'status': 'dropped', 'category': 'algebra',
                 'topics': ['不等式'], 'difficulty_est': 3}]     # 非 ok：不计
        _write(os.path.join(repo, 'candidates', 'mathnet.jsonl'),
               ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        data, _ = run_gaps(repo, capsys)
        ineq = next(e for e in data['nodes'] if e['node'] == '不等式')
        assert ineq['cand'] == {'total': 2, 'by_star': {'2': 1, '3': 1}}

    def test_student_evidence_marks_weak_and_orders_queue(self, repo, capsys):
        """有证据时：fail 题所在节点转薄弱且排队列最前；做过的题不再进 picks；
        前置薄弱的下游节点带 blocked_by 标注；同状态桶内上游（不等式）先于下游（函数方程）。"""
        make_problem(repo, pid='A-001', topics=['不等式'])
        make_problem(repo, pid='A-002', topics=['不等式'])
        make_problem(repo, pid='A-003', topics=['函数方程'])
        stp.save_student({'id': 'self', 'attempt_aliases': []})
        _write(os.path.join(repo, 'data', 'attempts.jsonl'),
               json.dumps({'id': 'A-001', 'result': 'fail', 'date': '2026-08-01',
                           'student': 'self'}) + '\n')
        data, _ = run_gaps(repo, capsys)
        assert data['evidence_total'] == 1
        by_node = {e['node']: e for e in data['nodes']}
        assert by_node['不等式']['status'] == '薄弱'
        assert by_node['不等式']['queue_rank'] == 0          # 薄弱桶在未测桶之前
        assert by_node['不等式']['picks'] == [{'id': 'A-002', 'd': 2}]   # A-001 已做，排除
        assert by_node['函数方程']['status'] == '未测'
        assert by_node['函数方程']['blocked_by'] == ['algebra/不等式']
        assert by_node['函数方程']['queue_rank'] > by_node['不等式']['queue_rank']


# ---------------- 2. coach --from-gaps：证据闸与名额消耗 ----------------

class TestFromGaps:
    PROFILE = {2: 1, 3: 1}

    def test_missing_ledger_degrades(self, repo, capsys):
        assert bank.load_gap_picks([], self.PROFILE, set()) == []
        out = capsys.readouterr().out
        assert 'maps/gaps.json 不存在' in out and '降级' in out

    def test_evidence_floor_degrades(self, repo, capsys):
        """证据闸（防回归核心）：attempts+assessments 合计不足下限 → 空缺口 + 中文降级提示。"""
        write_gaps_ledger(repo, evidence_total=bank.GAPS_MIN_EVIDENCE - 1)
        assert bank.load_gap_picks([], self.PROFILE, set()) == []
        out = capsys.readouterr().out
        assert '证据不足' in out and f'<{bank.GAPS_MIN_EVIDENCE}' in out and '降级为纯配比' in out

    def test_picks_weak_first_and_filtered(self, repo, capsys):
        """选题序：薄弱节点先于未测（同状态保持台账队列序）；排除集内与配比外星级的题被滤掉。"""
        make_problem(repo, pid='A-001', difficulty=2)
        make_problem(repo, pid='A-002', difficulty=3)
        make_problem(repo, pid='A-003', difficulty=5)   # ★5 不在配比里
        write_gaps_ledger(repo, evidence_total=bank.GAPS_MIN_EVIDENCE, nodes=[
            {'cat': 'algebra', 'node': '函数方程', 'status': '未测', 'queue_rank': 1,
             'picks': [{'id': 'A-002', 'd': 3}, {'id': 'A-003', 'd': 5}]},
            {'cat': 'algebra', 'node': '不等式', 'status': '薄弱', 'queue_rank': 3,
             'picks': [{'id': 'A-001', 'd': 2}, {'id': 'A-004', 'd': 2}]},   # A-004 在排除集
        ])
        picks = bank.load_gap_picks(bank.load_all(), self.PROFILE, {'A-004'})
        assert [fm['id'] for fm in picks] == ['A-001', 'A-002']

    def test_pick_week_gap_quota_preserves_star_mix(self, repo):
        """缺口名额占用对应星级配额：整周星级构成与纯配比一致，缺口题从队列头就地消耗。"""
        fms = {pid: {'id': pid, 'category': 'algebra', 'difficulty': d}
               for pid, d in [('A-001', 2), ('A-002', 2), ('A-003', 3), ('A-004', 3)]}
        pool = {2: [fms['A-001'], fms['A-002']], 3: [fms['A-003'], fms['A-004']]}
        gap_fms = [fms['A-001']]
        picked = bank.pick_week(pool, self.PROFILE, 4, random.Random(0), gap_fms)
        assert gap_fms == []                                   # 就地消耗
        assert [fm['id'] for fm in picked] == ['A-001', 'A-002', 'A-003', 'A-004']
        assert sorted(fm['difficulty'] for fm in picked) == [2, 2, 3, 3]   # 配比不被缺口挤占

    def test_rotation_pick_is_consumed_from_gap_queue(self, repo):
        """防重复（回归）：某周缺口名额被队列前面的题占满、队列靠后的题恰被轮转选中时，
        该题曾在下一周被缺口循环再排一次——同一题不得出现在两周计划里。"""
        fms = {pid: {'id': pid, 'category': 'algebra', 'difficulty': d}
               for pid, d in [('A-001', 2), ('A-002', 3)]}
        pool = {2: [fms['A-001']], 3: [fms['A-002']]}
        gap_fms = [fms['A-002'], fms['A-001']]     # ★3 先占满缺口名额，★2 由轮转选中
        rng = random.Random(0)
        week1 = bank.pick_week(pool, self.PROFILE, 2, rng, gap_fms)
        week2 = bank.pick_week(pool, self.PROFILE, 2, rng, gap_fms)
        assert {fm['id'] for fm in week1} == {'A-001', 'A-002'}
        assert week2 == []                                     # 题池耗尽，不得回收已排的题

    def test_coach_integration_degrades_on_thin_evidence(self, repo, capsys):
        """全链路：现库仅 1 条证据时 coach --from-gaps 打印降级提示，周计划照常产出无缺口标记。"""
        for i in range(1, 4):
            make_problem(repo, pid=f'A-00{i}', difficulty=2 + i % 2)
        write_gaps_ledger(repo, evidence_total=1)
        args = argparse.Namespace(target='AMC10', weeks=1, n=4, seed=1, save=False, from_gaps=True)
        bank.coach(bank.load_all(), args)
        out = capsys.readouterr().out
        assert '证据不足' in out and '降级为纯配比' in out
        assert '〔缺口〕' not in out

    def test_coach_integration_marks_gap_picks(self, repo, capsys):
        """全链路：证据够时缺口题进入周计划并带〔缺口〕标记，plan 头部说明名额来源。"""
        for i in range(1, 6):
            make_problem(repo, pid=f'A-00{i}', difficulty=2 + i % 2)
        write_gaps_ledger(repo, evidence_total=bank.GAPS_MIN_EVIDENCE, nodes=[
            {'cat': 'algebra', 'node': '不等式', 'status': '薄弱', 'queue_rank': 0,
             'picks': [{'id': 'A-002', 'd': 2}]}])
        args = argparse.Namespace(target='AMC10', weeks=1, n=4, seed=1, save=False, from_gaps=True)
        bank.coach(bank.load_all(), args)
        out = capsys.readouterr().out
        assert 'A-002' in out and '〔缺口〕' in out
        assert 'maps/gaps.json 队列' in out


# ---------------- 3. doctor：缺失 / 陈旧 / 残留 / 跳过 ----------------

def write_map_data(root, total, generated='2026-08-06'):
    _write(os.path.join(root, 'maps', 'map_data.json'),
           json.dumps({'generated': generated, 'total': total}))


def write_simindex(root, bank_n, cand_n=0, bank_only=True, with_cand_npz=False):
    _write(os.path.join(root, 'candidates', 'simindex', 'config.json'),
           json.dumps({'model': 'm', 'dim': 8, 'built': '2026-08-06T00:00:00',
                       'bank_n': bank_n, 'cand_n': cand_n, 'bank_only': bank_only}))
    if with_cand_npz:
        _write(os.path.join(root, 'candidates', 'simindex', 'cand.npz'), 'x')


def run_doctor(capsys):
    rc = bank.doctor(bank.load_all())
    return rc, capsys.readouterr().out


class TestDoctor:
    def test_missing_artifacts_reported_and_exit_1(self, repo, capsys):
        """clone 后常态：两产物都缺 → 各自点名「缺失（clone 后正常）」+重建命令，exit 1。"""
        make_problem(repo)
        rc, out = run_doctor(capsys)
        assert rc == 1
        assert 'maps/map_data.json：缺失（clone 后正常）' in out and 'bank.py map' in out
        assert 'candidates/simindex/config.json：缺失（clone 后正常）' in out
        assert 'similar_index.py build --bank-only' in out
        assert 'DOCTOR: 2 项缺失或陈旧' in out

    def test_all_fresh_exit_0(self, repo, capsys):
        make_problem(repo)
        write_map_data(repo, total=1)
        write_simindex(repo, bank_n=1)
        rc, out = run_doctor(capsys)
        assert rc == 0
        assert out.count('：新鲜') == 2
        assert 'DOCTOR OK' in out

    def test_stale_map_data_caught(self, repo, capsys):
        """防回归（核心）：入库后忘了重跑 map → total 落后于现库题数，必须点名新旧数值。"""
        make_problem(repo, pid='A-001')
        make_problem(repo, pid='A-002')
        write_map_data(repo, total=1)
        write_simindex(repo, bank_n=2)
        rc, out = run_doctor(capsys)
        assert rc == 1
        assert 'maps/map_data.json：陈旧' in out
        assert '记 1 题' in out and '现库 2 题' in out and 'bank.py map' in out

    def test_stale_simindex_bank_n_caught(self, repo, capsys):
        make_problem(repo, pid='A-001')
        make_problem(repo, pid='A-002')
        write_map_data(repo, total=2)
        write_simindex(repo, bank_n=1)
        rc, out = run_doctor(capsys)
        assert rc == 1
        assert 'candidates/simindex/config.json：陈旧（bank_n=1，现库 2 题' in out

    def test_bank_only_with_cand_npz_is_residue(self, repo, capsys):
        """防回归：--bank-only 重建后旧的候选索引没删干净 → similar 会混入陈旧候选，必须报残留。"""
        make_problem(repo)
        write_map_data(repo, total=1)
        write_simindex(repo, bank_n=1, bank_only=True, with_cand_npz=True)
        rc, out = run_doctor(capsys)
        assert rc == 1
        assert '残留' in out and 'cand.npz' in out and '--group mathnet' in out

    def test_full_build_with_cand_npz_not_residue(self, repo, capsys):
        """全量构建（bank_only=false）本来就带 cand.npz，不得误报残留。"""
        make_problem(repo)
        write_map_data(repo, total=1)
        write_simindex(repo, bank_n=1, cand_n=3, bank_only=False, with_cand_npz=True)
        rc, out = run_doctor(capsys)
        assert rc == 0, out

    def test_corrupt_json_caught(self, repo, capsys):
        make_problem(repo)
        _write(os.path.join(repo, 'maps', 'map_data.json'), '{坏 JSON')
        write_simindex(repo, bank_n=1)
        rc, out = run_doctor(capsys)
        assert rc == 1
        assert 'maps/map_data.json：无法解析' in out

    def test_profile_html_skipped_without_failing(self, repo, capsys):
        """能力图 HTML 内嵌数据无题数字段：注明跳过即可，不得影响退出码。"""
        make_problem(repo)
        write_map_data(repo, total=1)
        write_simindex(repo, bank_n=1)
        _write(os.path.join(repo, 'maps', '能力图-self.html'),
               '<html>{"generated": "2026-08-01", "student": {"id": "self"}}</html>')
        rc, out = run_doctor(capsys)
        assert rc == 0
        assert '能力图-self.html：跳过' in out and '2026-08-01' in out
