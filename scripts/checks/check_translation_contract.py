#!/usr/bin/env python3
"""抽检 ``mathnet-full/`` 译文产物契约。

语料是 gitignore 派生产物：目录不存在时必须安静跳过。目录存在时只检查抽中的
``translation.json``，并显式报告抽样覆盖与耗时。文件布局与字段语义正本由
``docs/译文契约-mathnet-full.md`` 提供（CXB-495）；本模块只执行该契约。

CXB-497 的保真校验器合入后，本模块会从约定候选模块中寻找
``verify_translation(source, translated)`` 并调用；在此之前明确报告该项 skipped，
不在这里复制数学环境/图片引用的实现。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import time
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_SAMPLE = 100
VALID_MODES = {'passthrough', 'translated', 'failed'}
VALID_LANGUAGES = {'en', 'zh'}
VALID_CONFIDENCE = {'high', 'medium', 'low'}
SHA256_RE = re.compile(r'[0-9a-f]{64}')
LANG_RE = re.compile(r'(?:[a-z]{2}|und)')

# CXB-497 尚未合入时只保留调用点。候选名覆盖该工单最自然的独立模块名，
# 以及校验器被暂时放进 mathnet_translate.py 的兼容情况。
FIDELITY_MODULE_CANDIDATES = (
    'scripts/mathnet_translation_verify.py',
    'scripts/translation_verify.py',
    'scripts/translation_fidelity.py',
    'scripts/mathnet_translate.py',
)


@dataclass
class CheckResult:
    status: str
    discovered: int = 0
    checked: int = 0
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)
    fidelity: str = 'skipped'
    fidelity_note: str = 'CXB-497 保真校验器尚未合入'


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _relative(path, corpus):
    return os.path.relpath(path, corpus).replace(os.sep, '/')


def discover_contracts(corpus):
    """只走真实目录，不跟随 by-contest/ 与多知识点挂载产生的符号链接。"""
    paths = []
    for dirpath, dirnames, filenames in os.walk(corpus, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames
            if not os.path.islink(os.path.join(dirpath, name))
        )
        if 'translation.json' in filenames:
            paths.append(os.path.join(dirpath, 'translation.json'))
    return sorted(paths)


def _select_sample(contracts, corpus, sample):
    """按稳定路径哈希取样，避免永远只检查字典序靠前的同一板块。"""
    ranked = sorted(
        contracts,
        key=lambda path: hashlib.sha256(_relative(path, corpus).encode()).digest(),
    )
    return ranked[:sample]


def _schema_errors(payload, rel):
    errors = []
    if not isinstance(payload, dict):
        return [f'{rel}: translation.json schema 非法：顶层必须是对象']

    mathnet_id = payload.get('mathnet_id')
    if not isinstance(mathnet_id, str) or not mathnet_id:
        errors.append(f'{rel}: translation.json schema 非法：mathnet_id 必须是非空字符串')

    source_sha = payload.get('source_sha256')
    if not isinstance(source_sha, str) or not SHA256_RE.fullmatch(source_sha):
        errors.append(f'{rel}: translation.json schema 非法：source_sha256 必须是 64 位小写 sha256')

    source_lang = payload.get('source_lang')
    if not isinstance(source_lang, str) or not LANG_RE.fullmatch(source_lang):
        errors.append(f'{rel}: translation.json schema 非法：source_lang 必须是 ISO 639-1 代码或 und')

    confidence = payload.get('source_lang_confidence')
    if confidence not in VALID_CONFIDENCE:
        errors.append(f'{rel}: translation.json schema 非法：source_lang_confidence 必须是 '
                      f'{sorted(VALID_CONFIDENCE)} 之一')

    variants = payload.get('variants')
    if not isinstance(variants, dict):
        errors.append(f'{rel}: translation.json schema 非法：variants 必须是对象')
        return errors
    if not variants:
        errors.append(f'{rel}: translation.json schema 非法：variants 不得为空')

    for lang, variant in variants.items():
        if lang not in VALID_LANGUAGES:
            errors.append(f'{rel}: translation.json schema 非法：未知 variant 语言 {lang!r}')
            continue
        if not isinstance(variant, dict):
            errors.append(f'{rel}: translation.json schema 非法：variants.{lang} 必须是对象')
            continue
        mode = variant.get('mode')
        if mode not in VALID_MODES:
            errors.append(f'{rel}: translation.json schema 非法：variants.{lang}.mode={mode!r}，'
                          f'必须是 {sorted(VALID_MODES)} 之一')
            continue
        sha = variant.get('sha256')
        if mode != 'failed' and (not isinstance(sha, str) or not SHA256_RE.fullmatch(sha)):
            errors.append(f'{rel}: translation.json schema 非法：mode={mode} 的 variants.{lang}.sha256 '
                          '必须是 64 位小写 sha256')
        elif sha is not None and (not isinstance(sha, str) or not SHA256_RE.fullmatch(sha)):
            errors.append(f'{rel}: translation.json schema 非法：variants.{lang}.sha256 '
                          '必须是 64 位小写 sha256')
    return errors


def _load_fidelity_verifier(root):
    for rel in FIDELITY_MODULE_CANDIDATES:
        path = os.path.join(root, *rel.split('/'))
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location('_translation_fidelity_hook', path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            return None, f'{rel} 加载失败：{exc!r}'
        verifier = getattr(module, 'verify_translation', None)
        if callable(verifier):
            return verifier, rel
    return None, 'CXB-497 保真校验器尚未合入'


def _format_finding(finding):
    if isinstance(finding, dict):
        kind = finding.get('kind') or finding.get('type') or 'unknown'
        section = finding.get('section')
    else:
        kind = getattr(finding, 'kind', None) or getattr(finding, 'type', None) or type(finding).__name__
        section = getattr(finding, 'section', None)
    return f'{kind}（{section}）' if section else str(kind)


def _check_one(contract_path, corpus, verifier):
    rel = _relative(contract_path, corpus)
    errors = []
    try:
        with open(contract_path, encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f'{rel}: translation.json schema 非法：无法解析（{exc}）']

    errors.extend(_schema_errors(payload, rel))
    if not isinstance(payload, dict):
        return errors

    problem_dir = os.path.dirname(contract_path)
    source_path = os.path.join(problem_dir, 'index.md')
    try:
        source = open(source_path, 'rb').read()
    except OSError:
        return errors + [f'{rel}: 原文文件 index.md 缺失']

    expected_source_sha = payload.get('source_sha256')
    if isinstance(expected_source_sha, str) and SHA256_RE.fullmatch(expected_source_sha):
        actual_source_sha = _sha256(source)
        if actual_source_sha != expected_source_sha:
            errors.append(f'{rel}: 译文过期：source_sha256 与当前 index.md 不一致')

    variants = payload.get('variants')
    if not isinstance(variants, dict):
        return errors
    for lang, variant in variants.items():
        if lang not in VALID_LANGUAGES or not isinstance(variant, dict):
            continue
        mode = variant.get('mode')
        if mode not in VALID_MODES or mode == 'failed':
            continue
        variant_path = os.path.join(problem_dir, f'index.{lang}.md')
        try:
            body = open(variant_path, 'rb').read()
        except OSError:
            errors.append(f'{rel}: variant 文件缺失：index.{lang}.md（mode={mode}）')
            continue

        expected_sha = variant.get('sha256')
        if isinstance(expected_sha, str) and SHA256_RE.fullmatch(expected_sha):
            if _sha256(body) != expected_sha:
                errors.append(f'{rel}: variant sha256 对不上：index.{lang}.md')
        if mode == 'passthrough' and body != source:
            errors.append(f'{rel}: passthrough 内容与原文不一致：index.{lang}.md')
        # passthrough 已由逐字节相等这个更强的条件覆盖；保真校验器只检查机器译文，
        # 避免 CXB-497 将「译文等于原文」的漏译规则误套到合法 passthrough。
        if verifier is not None and mode == 'translated':
            try:
                findings = verifier(source.decode('utf-8'), body.decode('utf-8'))
            except Exception as exc:
                errors.append(f'{rel}: 保真校验器异常：index.{lang}.md（{exc!r}）')
            else:
                if findings:
                    detail = '、'.join(_format_finding(f) for f in findings)
                    errors.append(f'{rel}: 保真校验失败：index.{lang}.md：{detail}')
    return errors


def check_corpus(root=ROOT, corpus=None, sample=DEFAULT_SAMPLE, fidelity_verifier=None):
    """返回结构化检查结果；``fidelity_verifier`` 仅供 CXB-497 接口测试/显式注入。"""
    started = time.perf_counter()
    corpus = corpus or os.path.join(root, 'mathnet-full')
    if not os.path.isdir(corpus):
        return CheckResult(status='skipped', elapsed=time.perf_counter() - started)
    if not isinstance(sample, int) or sample <= 0:
        raise ValueError('sample 必须是正整数')

    contracts = discover_contracts(corpus)
    selected = _select_sample(contracts, corpus, sample)
    if fidelity_verifier is None:
        fidelity_verifier, fidelity_note = _load_fidelity_verifier(root)
        fidelity = 'enabled' if fidelity_verifier is not None else 'skipped'
    else:
        fidelity, fidelity_note = 'enabled', '显式注入 verify_translation'
    errors = []
    for path in selected:
        errors.extend(_check_one(path, corpus, fidelity_verifier))
    return CheckResult(
        status='failed' if errors else 'ok',
        discovered=len(contracts),
        checked=len(selected),
        elapsed=time.perf_counter() - started,
        errors=errors,
        fidelity=fidelity,
        fidelity_note=fidelity_note,
    )


def print_result(result):
    if result.status == 'skipped':
        print('TRANSLATION CHECK skipped: mathnet-full/ 不存在；译文契约检查未运行'
              f'（0 题，{result.elapsed:.3f}s）')
        return

    coverage = f'抽样 {result.checked}/{result.discovered} 题'
    if result.checked < result.discovered:
        coverage += f'；覆盖受限：另 {result.discovered - result.checked} 题未检查'
    fidelity = f'保真校验 {result.fidelity}（{result.fidelity_note}）'
    if result.status == 'failed':
        print('\n'.join(result.errors))
        print(f'\nTRANSLATION CHECK FAILED: {len(result.errors)} 个问题；{coverage}；'
              f'{fidelity}；耗时 {result.elapsed:.3f}s')
    else:
        print(f'TRANSLATION CHECK OK: {coverage}；{fidelity}；耗时 {result.elapsed:.3f}s')


def _positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('必须是正整数')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description='抽检 mathnet-full/ 译文产物契约；无语料时安静跳过')
    parser.add_argument('--sample', type=_positive_int, default=DEFAULT_SAMPLE,
                        help=f'最多抽检多少题（默认 {DEFAULT_SAMPLE}；输出会注明实际覆盖）')
    parser.add_argument('--root', default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument('--corpus', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = check_corpus(root=os.path.abspath(args.root), corpus=args.corpus, sample=args.sample)
    print_result(result)
    return 1 if result.status == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
