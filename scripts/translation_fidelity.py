#!/usr/bin/env python3
"""MathNet 译文保真校验器。

本模块只接收原文与译文字符串，不读写 ``translation.json``，也不依赖 MathNet
语料目录。调用方应把产物契约中的 ``mode`` 作为参数传入。命令行入口既能扫描
契约原生的单目录布局，也能按相对路径配对两个目录，适合本地批次检查与 CI 汇总。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from enum import Enum
from itertools import zip_longest
from pathlib import Path


class FindingType(str, Enum):
    """稳定的问题类型；字符串值可直接写入 JSON/CI 台账。"""

    MATH_MISMATCH = 'math_mismatch'
    IMAGE_MISMATCH = 'image_mismatch'
    STRUCTURE_MISMATCH = 'structure_mismatch'
    FINAL_ANSWER_MISMATCH = 'final_answer_mismatch'
    EMPTY_TRANSLATION = 'empty_translation'
    UNTRANSLATED = 'untranslated'
    MODEL_META_LEAK = 'model_meta_leak'
    EXTRA_CODE_FENCE = 'extra_code_fence'
    MISSING_TRANSLATION = 'missing_translation'


@dataclass(frozen=True)
class Finding:
    """一条可供人工定位的译文问题。"""

    type: FindingType
    section: str
    source_excerpt: str
    translated_excerpt: str

    def to_dict(self) -> dict[str, str]:
        row = asdict(self)
        row['type'] = self.type.value
        return row


@dataclass(frozen=True)
class DirectoryReport:
    """目录校验汇总，``files`` 只收录未通过的相对路径。"""

    total: int
    passed: int
    failed: int
    finding_counts: dict[str, int]
    files: dict[str, list[Finding]]

    def to_dict(self) -> dict[str, object]:
        return {
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'finding_counts': self.finding_counts,
            'files': {
                path: [finding.to_dict() for finding in findings]
                for path, findings in self.files.items()
            },
        }


@dataclass(frozen=True)
class _Occurrence:
    text: str
    section: str


@dataclass(frozen=True)
class _Section:
    heading: str
    body: str


_H1_RE = re.compile(r'^#[ \t]+(.+?)[ \t]*$', re.MULTILINE)
_H2_RE = re.compile(r'^##[ \t]+(.+?)[ \t]*$', re.MULTILINE)
_META_RE = re.compile(
    r'^[ \t]*[-*+][ \t]+(?P<key>(?:\*\*)?[^\n:：]+?(?:\*\*)?)[ \t]*[:：][ \t]*(?P<value>.*)$',
    re.MULTILINE,
)
_PROTECTED_RE = re.compile(
    r'(?P<code>(?<!`)``(?!`).*?(?<!`)``(?!`)|(?<!`)`(?!`)[^\n`]*`(?!`))'
    r'|(?P<display>\$\$.*?\$\$)'
    r'|(?P<environment>\\begin\{(?P<env>[^{}\n]+)\}.*?\\end\{(?P=env)\})'
    r'|(?P<inline>(?<!\$)\$(?!\$)[^$\n]*?\$(?!\$))',
    re.DOTALL,
)
_IMAGE_RE = re.compile(r'!\[\]\(attached_image_(\d+)\.png\)')
_WHOLE_FENCE_RE = re.compile(
    r'\A[ \t]*(```|~~~)[^\n]*\n(?P<body>.*)\n\1[ \t]*\Z',
    re.DOTALL,
)
_MODEL_META_PATTERNS = (
    re.compile(r'以下(?:内容)?是(?:翻译|译文)'),
    re.compile(r'作为(?:一个)?\s*AI', re.IGNORECASE),
    re.compile(r'\bhere (?:is|are) the translation\b', re.IGNORECASE),
    re.compile(r'\bas an AI\b', re.IGNORECASE),
    re.compile(r'\bI (?:have )?translated\b', re.IGNORECASE),
)


def _excerpt(value: str, limit: int = 160) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + '…'


def _section_matches(text: str) -> list[re.Match[str]]:
    return list(_H2_RE.finditer(text))


def _section_at(text: str, offset: int) -> str:
    current = '元信息'
    for match in _H2_RE.finditer(text):
        if match.start() > offset:
            break
        current = match.group(1)
    return current


def _sections(text: str) -> list[_Section]:
    matches = _section_matches(text)
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(_Section(match.group(1), text[match.end():end]))
    return sections


def _metadata(text: str) -> list[tuple[str, str]]:
    """返回首个 H2 之前的元信息 ``(键, 原行)``，值允许被翻译。"""
    first_h2 = _H2_RE.search(text)
    prefix = text[:first_h2.start()] if first_h2 else text
    return [(match.group('key').strip(), match.group(0)) for match in _META_RE.finditer(prefix)]


def _protected(text: str) -> list[_Occurrence]:
    return [
        _Occurrence(match.group(0), _section_at(text, match.start()))
        for match in _PROTECTED_RE.finditer(text)
    ]


def _images(text: str) -> list[_Occurrence]:
    return [
        _Occurrence(match.group(1), _section_at(text, match.start()))
        for match in _IMAGE_RE.finditer(text)
    ]


def _compare_occurrences(
    finding_type: FindingType,
    source: list[_Occurrence],
    translated: list[_Occurrence],
) -> list[Finding]:
    findings = []
    for source_item, translated_item in zip_longest(source, translated):
        if source_item is not None and translated_item is not None and source_item.text == translated_item.text:
            continue
        section = source_item.section if source_item is not None else translated_item.section
        findings.append(Finding(
            finding_type,
            section,
            _excerpt(source_item.text) if source_item is not None else '',
            _excerpt(translated_item.text) if translated_item is not None else '',
        ))
    return findings


def _structure_findings(source: str, translated: str) -> list[Finding]:
    findings = []
    source_h1 = [match.group(0) for match in _H1_RE.finditer(source)]
    translated_h1 = [match.group(0) for match in _H1_RE.finditer(translated)]
    if source_h1 != translated_h1:
        findings.append(Finding(
            FindingType.STRUCTURE_MISMATCH,
            'H1',
            _excerpt('\n'.join(source_h1)),
            _excerpt('\n'.join(translated_h1)),
        ))

    source_meta = _metadata(source)
    translated_meta = _metadata(translated)
    if [item[0] for item in source_meta] != [item[0] for item in translated_meta]:
        findings.append(Finding(
            FindingType.STRUCTURE_MISMATCH,
            '元信息',
            _excerpt('\n'.join(item[1] for item in source_meta)),
            _excerpt('\n'.join(item[1] for item in translated_meta)),
        ))

    source_headings = [section.heading for section in _sections(source)]
    translated_headings = [section.heading for section in _sections(translated)]
    if source_headings != translated_headings:
        mismatch_at = next(
            (index for index, pair in enumerate(zip_longest(source_headings, translated_headings))
             if pair[0] != pair[1]),
            0,
        )
        section = (
            source_headings[mismatch_at] if mismatch_at < len(source_headings)
            else translated_headings[mismatch_at]
        )
        findings.append(Finding(
            FindingType.STRUCTURE_MISMATCH,
            section,
            _excerpt('\n'.join(source_headings)),
            _excerpt('\n'.join(translated_headings)),
        ))
    return findings


def _is_pure_symbol(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    without_commands = re.sub(r'\\[A-Za-z]+', '', candidate)
    words = re.findall(r'[^\W\d_]+', without_commands, re.UNICODE)
    return all(len(word) == 1 or word.casefold() in {'mod', 'pmod', 'bmod'} for word in words)


def _has_translatable_prose(section: _Section) -> bool:
    if section.heading == '最终答案' and _is_pure_symbol(section.body):
        return False
    body = _PROTECTED_RE.sub('', section.body)
    body = _IMAGE_RE.sub('', body)
    body = re.sub(r'<[^>]+>', '', body)
    return bool(re.search(r'[^\W\d_]', body, re.UNICODE))


def _content_findings(source: str, translated: str, mode: str) -> list[Finding]:
    findings = []
    if not translated.strip():
        return [Finding(
            FindingType.EMPTY_TRANSLATION,
            '文档',
            _excerpt(source),
            '',
        )]

    source_sections = _sections(source)
    translated_sections = _sections(translated)
    for index, translated_section in enumerate(translated_sections):
        if not translated_section.body.strip():
            source_excerpt = ''
            if (index < len(source_sections)
                    and source_sections[index].heading == translated_section.heading):
                source_excerpt = _excerpt(source_sections[index].body)
            findings.append(Finding(
                FindingType.EMPTY_TRANSLATION,
                translated_section.heading,
                source_excerpt,
                '',
            ))

    if mode == 'translated':
        for source_section, translated_section in zip(source_sections, translated_sections):
            if source_section.heading != translated_section.heading:
                continue
            if (source_section.body == translated_section.body
                    and _has_translatable_prose(source_section)):
                findings.append(Finding(
                    FindingType.UNTRANSLATED,
                    source_section.heading,
                    _excerpt(source_section.body),
                    _excerpt(translated_section.body),
                ))

    source_answers = [section for section in source_sections if section.heading == '最终答案']
    translated_answers = [section for section in translated_sections if section.heading == '最终答案']
    if source_answers and translated_answers:
        source_answer = source_answers[0].body
        translated_answer = translated_answers[0].body
        if _is_pure_symbol(source_answer) and source_answer != translated_answer:
            findings.append(Finding(
                FindingType.FINAL_ANSWER_MISMATCH,
                '最终答案',
                _excerpt(source_answer),
                _excerpt(translated_answer),
            ))
    return findings


def _leak_findings(source: str, translated: str) -> list[Finding]:
    findings = []
    fence = _WHOLE_FENCE_RE.match(translated.strip())
    if fence:
        findings.append(Finding(
            FindingType.EXTRA_CODE_FENCE,
            '文档',
            _excerpt(source),
            _excerpt(translated),
        ))
    for pattern in _MODEL_META_PATTERNS:
        for match in pattern.finditer(translated):
            findings.append(Finding(
                FindingType.MODEL_META_LEAK,
                _section_at(translated, match.start()),
                '',
                _excerpt(match.group(0)),
            ))
    return findings


def verify_translation(source: str, translated: str, *, mode: str = 'translated') -> list[Finding]:
    """校验一对 Markdown 字符串，返回全部可定位问题。

    ``mode='passthrough'`` 只关闭“正文未翻译”检查；数学、图片、骨架与泄漏检查
    仍然执行。调用方可直接传入 ``translation.json`` 中对应 variant 的 mode。
    """
    if mode not in {'translated', 'passthrough', 'failed'}:
        raise ValueError(f'unsupported translation mode: {mode}')
    findings = []
    findings.extend(_compare_occurrences(
        FindingType.MATH_MISMATCH, _protected(source), _protected(translated)))
    findings.extend(_compare_occurrences(
        FindingType.IMAGE_MISMATCH, _images(source), _images(translated)))
    findings.extend(_structure_findings(source, translated))
    findings.extend(_content_findings(source, translated, mode))
    findings.extend(_leak_findings(source, translated))
    return findings


def verify_directory(
    source_dir: str | Path,
    translated_dir: str | Path | None = None,
    *,
    mode: str = 'translated',
    pattern: str | None = None,
    variant: str = 'zh',
) -> DirectoryReport:
    """校验目录并返回题数与各问题类型统计。

    只传 ``source_dir`` 时使用产物契约原生布局，递归配对同目录下的
    ``index.md`` 与 ``index.<variant>.md``。传入 ``translated_dir`` 时按两个
    目录的相对路径配对，方便批次导出与 CI fixture。
    """
    if variant not in {'en', 'zh'}:
        raise ValueError(f'unsupported translation variant: {variant}')
    source_root = Path(source_dir)
    if not source_root.is_dir():
        raise FileNotFoundError(f'source directory does not exist: {source_root}')
    translated_root = Path(translated_dir) if translated_dir is not None else None
    failed_files = {}
    counts: Counter[str] = Counter()
    source_pattern = pattern or ('*.md' if translated_root is not None else 'index.md')
    source_paths = sorted(path for path in source_root.rglob(source_pattern) if path.is_file())
    for source_path in source_paths:
        relative = source_path.relative_to(source_root)
        translated_path = (
            translated_root / relative if translated_root is not None
            else source_path.with_name(f'index.{variant}.md')
        )
        if translated_path.is_file():
            findings = verify_translation(
                source_path.read_text(encoding='utf-8'),
                translated_path.read_text(encoding='utf-8'),
                mode=mode,
            )
        else:
            findings = [Finding(
                FindingType.MISSING_TRANSLATION,
                '文档',
                str(relative),
                '',
            )]
        if findings:
            failed_files[relative.as_posix()] = findings
            counts.update(finding.type.value for finding in findings)
    failed = len(failed_files)
    return DirectoryReport(
        total=len(source_paths),
        passed=len(source_paths) - failed,
        failed=failed,
        finding_counts=dict(sorted(counts.items())),
        files=failed_files,
    )


def _print_report(report: DirectoryReport) -> None:
    print(f'译文保真校验：共 {report.total} 题；通过 {report.passed}；失败 {report.failed}')
    if report.finding_counts:
        print('问题统计：')
        for finding_type, count in report.finding_counts.items():
            print(f'  {finding_type}: {count}')
    for path, findings in report.files.items():
        print(f'{path}:')
        for finding in findings:
            print(f'  [{finding.type.value}] {finding.section}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='逐字校验 MathNet Markdown 译文保真度')
    parser.add_argument('source_dir', help='原文目录，或含 index.md/index.<语言>.md 的语料根目录')
    parser.add_argument('translated_dir', nargs='?',
                        help='可选译文目录；给出时按相对路径与原文目录配对')
    parser.add_argument('--variant', choices=('en', 'zh'), default='zh',
                        help='单目录模式的目标语言（默认 zh）')
    parser.add_argument('--mode', choices=('translated', 'passthrough', 'failed'), default='translated',
                        help='译文模式（由调用方从 translation.json 传入；默认 translated）')
    parser.add_argument('--glob', help='递归原文匹配模式（双目录默认 *.md；单目录默认 index.md）')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    args = parser.parse_args(argv)
    report = verify_directory(
        args.source_dir,
        args.translated_dir,
        mode=args.mode,
        pattern=args.glob,
        variant=args.variant,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 1 if report.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
