"""机器核验运行器（scripts/checks/run_checks.py）回归用例。

全部在 tmp_path 造的假仓库上跑，不碰真实 problems/ 与 data/verify/。
锁的契约：全 pass 且台账一致才绿；check 失败/异常、台账覆盖脚本已删的题、
台账留 fail 记录、check 指向库外题、一题多 check —— 全部红。
"""
import importlib.util
import json
import os

import pytest

_RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'scripts', 'checks', 'run_checks.py')
_spec = importlib.util.spec_from_file_location('run_checks', _RUNNER)
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def make_check_module(root, name='check_t1.py', body=None):
    _write(os.path.join(root, 'scripts', 'checks', name),
           body or "CHECKS = {'A-001': lambda: (True, '2+2 独立重算 == 4')}\n")


def make_bank_problem(root, pid='A-001', mathnet_id='0ach'):
    _write(os.path.join(root, 'problems', 'algebra', pid + '.md'),
           f'---\nid: {pid}\nmathnet_id: "{mathnet_id}"\n---\n\n## 题面\n\nx\n')


def make_ledger(root, rows, batch='machine-01'):
    _write(os.path.join(root, 'data', 'verify', batch, 'results.json'),
           json.dumps(rows, ensure_ascii=False))


@pytest.fixture()
def repo(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, 'scripts', 'checks'))
    make_bank_problem(root)
    return root


class TestVerifyMode:
    def test_all_pass_and_consistent_ledger_green(self, repo):
        make_check_module(repo)
        make_ledger(repo, [{'id': 'A-001', 'status': 'pass'}])
        assert rc.verify_mode(repo) == []

    def test_no_ledger_yet_still_green(self, repo):
        """骨架先落地、台账随入库节奏补——没有台账不算错。"""
        make_check_module(repo)
        assert rc.verify_mode(repo) == []

    def test_failing_check_red(self, repo):
        make_check_module(repo, body="CHECKS = {'A-001': lambda: (False, '独立重算得 5，与答案 4 不符')}\n")
        errs = rc.verify_mode(repo)
        assert any('核验失败' in e and 'A-001' in e for e in errs)

    def test_check_exception_counts_as_fail(self, repo):
        make_check_module(repo, body="CHECKS = {'A-001': lambda: 1 / 0}\n")
        errs = rc.verify_mode(repo)
        assert any('核验失败' in e and 'check 异常' in e for e in errs)

    def test_ledger_pass_without_check_red(self, repo):
        """防回归（最核心）：凭证在、脚本删了——凭证不可信，必须红。"""
        make_ledger(repo, [{'id': 'A-001', 'status': 'pass'}])
        errs = rc.verify_mode(repo)
        assert any('有 pass 凭证但 scripts/checks/ 里没有对应 check' in e for e in errs)

    def test_ledger_fail_row_red(self, repo):
        make_check_module(repo)
        make_ledger(repo, [{'id': 'A-001', 'status': 'fail'}])
        errs = rc.verify_mode(repo)
        assert any('fail 记录不留在台账里' in e for e in errs)

    def test_check_for_removed_problem_red(self, repo):
        make_check_module(repo, body="CHECKS = {'A-999': lambda: (True, 'x')}\n")
        errs = rc.verify_mode(repo)
        assert any('A-999 不在库内' in e for e in errs)

    def test_duplicate_check_id_red(self, repo):
        make_check_module(repo, 'check_t1.py')
        make_check_module(repo, 'check_t2.py')
        errs = rc.verify_mode(repo)
        assert any('已在 scripts/checks/check_t1.py 注册过核验' in e for e in errs)

    def test_corrupt_ledger_red(self, repo):
        make_check_module(repo)
        _write(os.path.join(repo, 'data', 'verify', 'machine-01', 'results.json'), '{坏')
        errs = rc.verify_mode(repo)
        assert any('台账无法解析' in e for e in errs)


class TestWriteMode:
    def test_write_creates_ledger_with_frontmatter_mathnet_id(self, repo, capsys):
        make_check_module(repo)
        assert rc.write_mode(repo, 'machine-01') == []
        rows = json.load(open(os.path.join(repo, 'data', 'verify', 'machine-01', 'results.json'),
                              encoding='utf-8'))
        assert rows[0]['id'] == 'A-001'
        assert rows[0]['mathnet_id'] == '0ach'          # 从题文件 frontmatter 带出，防错挂
        assert rows[0]['status'] == 'pass'
        assert rows[0]['script'] == 'scripts/checks/check_t1.py'
        assert '2+2 独立重算 == 4' in rows[0]['method']

    def test_write_refused_when_any_check_fails(self, repo):
        make_check_module(repo, body="CHECKS = {'A-001': lambda: (False, '不符')}\n")
        errs = rc.write_mode(repo, 'machine-01')
        assert any('台账未写入' in e for e in errs)
        assert not os.path.exists(os.path.join(repo, 'data', 'verify', 'machine-01', 'results.json'))
