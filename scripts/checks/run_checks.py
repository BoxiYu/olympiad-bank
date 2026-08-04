#!/usr/bin/env python3
"""持续机器核验：重跑 scripts/checks/check_*.py 的答案核验，并与 data/verify/ 台账双向比对。

分工（本文件头注是机器核验机制的正本，SPEC §7 已登记）：
- `bank.py lint` 只查凭证覆盖——题的 machine_check_ref 指向的台账存在、含本题、status=pass
  （秒级、零 sympy 依赖）；
- 本脚本负责「凭证是否仍真」：CI 每次全量重跑全部 check，任一 fail 或台账与脚本
  不一致即退出 1。凭证不是写完就算数的——这正是 review_ref「裸声明不被信任」哲学
  在机器核验上的镜像。
- 与 review_ref（Codex 人审凭证）正交：机器核验是**补充凭证**，不改 verification 档位，
  也不是入库门槛——只有数值/闭式答案题能核验，证明题不进本机制。

check_<batch>.py 契约：模块级 `CHECKS = {'A-005': callable}`，callable() 返回
`(ok: bool, method: str)`；method 一句话写清「怎么验的」，写台账时落入 results.json。
核验代码风格沿用 scripts/verify/ 存量脚本：sympy/Fraction 精确计算、小范围穷举、
双实现互证；**不许把题目答案抄进断言再「验证」它等于自己**——必须独立推导或穷举。

用法：
  uv run --with sympy python scripts/checks/run_checks.py                 # CI 模式：重跑 + 比对台账
  uv run --with sympy python scripts/checks/run_checks.py --write machine-01  # 全 pass 后写/更新台账
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERIFY_DIR = 'data/verify'


def discover_checks(root):
    """scripts/checks/check_*.py → {check_id: (callable, 脚本相对路径)}；题号重复注册即报错。"""
    checks, errors = {}, []
    cdir = os.path.join(root, 'scripts', 'checks')
    for fn in sorted(os.listdir(cdir)):
        if not (fn.startswith('check_') and fn.endswith('.py')):
            continue
        rel = f'scripts/checks/{fn}'
        spec = importlib.util.spec_from_file_location(fn[:-3], os.path.join(cdir, fn))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for pid, fn_check in getattr(mod, 'CHECKS', {}).items():
            if pid in checks:
                errors.append(f'{rel}: 题号 {pid} 已在 {checks[pid][1]} 注册过核验（一题一 check）')
            else:
                checks[pid] = (fn_check, rel)
    return checks, errors


def bank_ids(root):
    """problems/ 里现存的题号集合（不解析 frontmatter，文件名即题号——lint 已强制二者一致）。"""
    ids = set()
    pdir = os.path.join(root, 'problems')
    for _dirpath, _dirnames, filenames in os.walk(pdir):
        ids.update(fn[:-3] for fn in filenames if re.fullmatch(r'[ANCG]-\d{3}\.md', fn))
    return ids


def load_ledgers(root):
    """data/verify/<batch>/results.json → [(相对路径, rows)]；不可解析的台账整份报错。"""
    ledgers, errors = [], []
    vdir = os.path.join(root, *VERIFY_DIR.split('/'))
    if not os.path.isdir(vdir):
        return ledgers, errors
    for batch in sorted(os.listdir(vdir)):
        path = os.path.join(vdir, batch, 'results.json')
        if not os.path.exists(path):
            continue
        rel = f'{VERIFY_DIR}/{batch}/results.json'
        try:
            rows = json.load(open(path, encoding='utf-8'))
            ledgers.append((rel, [r for r in rows if isinstance(r, dict)]))
        except (OSError, ValueError):
            errors.append(f'{rel}: 台账无法解析')
    return ledgers, errors


def run_all(checks):
    """执行全部 check → ({id: (ok, method)}, 失败题号列表)。check 抛异常按 fail 记。"""
    results, failed = {}, []
    for pid in sorted(checks):
        fn_check, rel = checks[pid]
        try:
            ok, method = fn_check()
        except Exception as e:  # 核验代码自身崩溃 = 凭证不可信，按 fail 处理
            ok, method = False, f'check 异常：{e!r}'
        results[pid] = (bool(ok), str(method))
        if not ok:
            failed.append(f'{pid}（{rel}）：{method}')
    return results, failed


def verify_mode(root):
    """CI 模式：重跑全部 check + 台账双向比对。返回错误列表（空 = 绿）。"""
    checks, errors = discover_checks(root)
    ids = bank_ids(root)
    for pid, (_fn, rel) in checks.items():
        if pid not in ids:
            errors.append(f'{rel}: 核验的题号 {pid} 不在库内（题被清退后 check 要一并删除）')
    results, failed = run_all(checks)
    errors.extend(f'核验失败：{f}' for f in failed)
    ledgers, ledger_errors = load_ledgers(root)
    errors.extend(ledger_errors)
    for rel, rows in ledgers:
        for r in rows:
            pid = str(r.get('id'))
            if r.get('status') != 'pass':
                errors.append(f'{rel}: {pid} 状态为 {r.get("status")}——fail 记录不留在台账里，'
                              f'修复后重新 --write')
            elif pid not in results:
                errors.append(f'{rel}: {pid} 有 pass 凭证但 scripts/checks/ 里没有对应 check'
                              f'——凭证在、脚本没了，凭证不可信')
            elif not results[pid][0]:
                pass  # 已在「核验失败」里报过，不重复
    return errors


def write_mode(root, batch):
    """--write：全 pass 才落台账；台账行含 mathnet_id（从题文件 frontmatter 带出）。"""
    import yaml
    checks, errors = discover_checks(root)
    results, failed = run_all(checks)
    if errors or failed:
        return errors + [f'核验失败：{f}' for f in failed] + ['台账未写入：先修复上面的问题']
    rows = []
    for pid in sorted(results):
        cat_dir = {'A': 'algebra', 'N': 'number-theory', 'C': 'combinatorics', 'G': 'geometry'}[pid[0]]
        path = os.path.join(root, 'problems', cat_dir, pid + '.md')
        text = open(path, encoding='utf-8').read()
        fm = yaml.safe_load(re.match(r'---\n(.*?)\n---\n', text, re.S).group(1))
        rows.append({'id': pid, 'mathnet_id': str(fm.get('mathnet_id') or ''),
                     'status': 'pass', 'method': results[pid][1],
                     'script': checks[pid][1],
                     'checked_at': datetime.date.today().isoformat()})
    out = os.path.join(root, *VERIFY_DIR.split('/'), batch, 'results.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f'{VERIFY_DIR}/{batch}/results.json 已写入：{len(rows)} 条 pass 凭证')
    return []


def main():
    ap = argparse.ArgumentParser(description='持续机器核验（语义见模块 docstring）')
    ap.add_argument('--write', metavar='BATCH', help='全 pass 后写台账 data/verify/<BATCH>/results.json')
    args = ap.parse_args()
    errors = write_mode(ROOT, args.write) if args.write else verify_mode(ROOT)
    if errors:
        print('\n'.join(errors))
        print(f'\nMACHINE CHECK FAILED: {len(errors)} 个问题')
        sys.exit(1)
    if not args.write:
        checks, _ = discover_checks(ROOT)
        print(f'MACHINE CHECK OK: {len(checks)} 个核验全部通过且与台账一致')


if __name__ == '__main__':
    main()
