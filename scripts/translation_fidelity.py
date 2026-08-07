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
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from enum import Enum
from itertools import zip_longest
from pathlib import Path

from source_lang import detect_source_lang


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
    BATCH_BOILERPLATE = 'batch_boilerplate'
    LENGTH_RATIO = 'length_ratio'
    CONTENT_ANCHOR_MISSING = 'content_anchor_missing'


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
class BatchConfig:
    """批级退化信号的可配置阈值与权重。

    默认 ``boilerplate_min_group=3`` 对应最小可行动重复簇：两条短题偶然同句很常见，
    三条不同原文却共用译文已足以复核。相似度 0.9 在剥离数学与标点后容忍少量虚词漂移；
    译文相似度还须比同一个 peer 的源文相似度至少高 0.2，避免把两个 peer 的
    相似度极值错配后误伤忠实的同模板翻译。译文相似度相差不超过 0.02 的 peer
    视为并列，并选择其中源文最接近者作保守比较。若同一译文簇里至少有两个
    各由两种近似源文支撑、彼此却明显不同的模板子组，则保留簇级跨模板证据，
    避免每条记录都借同组 peer 击穿检测。
    少于 8 个文字/数字的短答案不参与套话聚类。长度比采用很宽的 0.25--4.0 区间，
    适配英中字符密度差；长度和锚点各权重 1，必须同时出现才达到默认阻断分 2，
    因而长度异常绝不会单独产生 Finding。套话权重 2，可独立阻断。
    超过 500 条时只做线性精确聚类，避免目录审计退化成二次复杂度；生产翻译批次默认 25，
    SOP 最大 100，仍会执行高重合模糊比较。
    """

    boilerplate_min_group: int = 3
    boilerplate_similarity: float = 0.9
    boilerplate_source_similarity_gap: float = 0.2
    boilerplate_pair_similarity_tolerance: float = 0.02
    boilerplate_min_chars: int = 8
    length_ratio_min: float = 0.25
    length_ratio_max: float = 4.0
    boilerplate_weight: int = 2
    length_weight: int = 1
    anchor_weight: int = 1
    block_score: int = 2
    fuzzy_comparison_limit: int = 500


@dataclass(frozen=True)
class BatchSignal:
    """一条独立批级信号；信号不等于阻断 Finding。"""

    type: FindingType
    section: str
    score: float
    detail: str

    def to_dict(self) -> dict[str, str | float]:
        row = asdict(self)
        row['type'] = self.type.value
        return row


@dataclass(frozen=True)
class BatchReport:
    """批级报告；``findings`` 只含达到阻断权重的条目。"""

    findings: dict[str, list[Finding]]
    signals: dict[str, list[BatchSignal]]

    def to_dict(self) -> dict[str, object]:
        return {
            'findings': {
                key: [finding.to_dict() for finding in findings]
                for key, findings in self.findings.items()
            },
            'signals': {
                key: [signal.to_dict() for signal in signals]
                for key, signals in self.signals.items()
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
_PLACEHOLDER_RE = re.compile(r'\{\{MNT_\d{4}\}\}')
_HAN_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
_SYMBOL_WORDS = {
    'bmod', 'cos', 'gcd', 'inf', 'lcm', 'ln', 'log', 'max', 'min', 'mod',
    'pmod', 'sin', 'sqrt', 'sup', 'tan',
}
_SHORT_ENGLISH_PROSE_RE = re.compile(
    r'\b(?:'
    r'no[ \t]+solutions?'
    r'|proof[ \t]+(?:is[ \t]+)?omitted'
    r'|the[ \t]+answers?[ \t]+(?:is|are)'
    r'|find\b'
    r'|set\b'
    r'|verify\b'
    r'|alternatively[ \t]+use\b'
    r')',
    re.IGNORECASE,
)
_NON_ENGLISH_FRAGMENT_RE = re.compile(
    r'\b(?:'
    # Common concise-answer and instruction words from MathNet's major
    # Latin-script source languages.  They are checked only when the
    # conservative document detector returned ``und``.
    r'aucun|aucune|trouver|demontrer|preuve|reponse|omis|omise|les|des|pour'
    r'|nessun|nessuna|trovare|dimostrare|soluzione|risposta|omessa'
    r'|kein|keine|finden|beweis|antwort|losung|losungen'
    r'|ningun|ninguna|hallar|respuesta|prueba|omitida|soluciones'
    r'|nenhum|nenhuma|encontrar|resposta|omitida|solucoes'
    r'|poisci|dokazi|resitev'
    r')\b'
)
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
_TASK_ANCHORS = (
    (
        re.compile(
            r'\b(?:prove|show|demonstrate|demostrar|demonstrar|demontrer|montrer|prouver|'
            r'beweisen|zeig(?:e|en)|dimostrare|dokazi)\b|证明|证实|表明|докажите|доказать',
            re.IGNORECASE,
        ),
        {
            'en': re.compile(r'\b(?:prove|show|demonstrate|establish|verify)\b', re.IGNORECASE),
            'zh': re.compile(r'证明|证实|说明|表明'),
        },
    ),
    (
        re.compile(
            r'\b(?:find|determine|hallar|encontrar|determinar|trouver|determiner|finden|'
            r'bestimmen|trovare|determinare|poisci|doloci)\b|求出|求得|找出|确定|'
            r'найдите|определите',
            re.IGNORECASE,
        ),
        {
            'en': re.compile(r'\b(?:find|determine|identify|obtain)\b', re.IGNORECASE),
            'zh': re.compile(r'求(?:出|得)?|找出|确定'),
        },
    ),
    (
        re.compile(
            r'\b(?:calculate|compute|evaluate|calcular|calculer|berechnen|calcolare|'
            r'izracunaj)\b|计算|вычислите',
            re.IGNORECASE,
        ),
        {
            'en': re.compile(r'\b(?:calculate|compute|evaluate)\b', re.IGNORECASE),
            'zh': re.compile(r'计算|求(?:出|得)?'),
        },
    ),
    (
        re.compile(
            r'\b(?:solve|resolver|resoudre|losen|risolvere|resi)\b|求解|解方程|解不等式|'
            r'решите',
            re.IGNORECASE,
        ),
        {
            'en': re.compile(r'\bsolve\b', re.IGNORECASE),
            'zh': re.compile(r'求解|解(?:出|方程|不等式)'),
        },
    ),
    (
        re.compile(
            r'\b(?:construct|construir|construire|konstruieren|costruire|konstruiraj)\b|'
            r'构造|作出|постройте',
            re.IGNORECASE,
        ),
        {
            'en': re.compile(r'\b(?:construct|draw)\b', re.IGNORECASE),
            'zh': re.compile(r'构造|作出'),
        },
    ),
    (
        re.compile(r'\b(?:classif(?:y|ies)|clasificar|classer|klassifizieren)\b|分类|归类',
                   re.IGNORECASE),
        {
            'en': re.compile(r'\b(?:classif(?:y|ies)|categorize)\b', re.IGNORECASE),
            'zh': re.compile(r'分类|归类'),
        },
    ),
    (
        re.compile(r'\b(?:count|contar|compter|zahlen|contare|prestej)\b|计数|数出|сколько',
                   re.IGNORECASE),
        {
            'en': re.compile(r'\b(?:count|how many|number of)\b', re.IGNORECASE),
            'zh': re.compile(r'计数|数出|多少'),
        },
    ),
)
_ZH_NAME_ANCHORS = {
    'cauchy': re.compile(r'Cauchy|柯西', re.IGNORECASE),
    'ceva': re.compile(r'Ceva|塞瓦', re.IGNORECASE),
    'euler': re.compile(r'Euler|欧拉', re.IGNORECASE),
    'fermat': re.compile(r'Fermat|费马', re.IGNORECASE),
    'fibonacci': re.compile(r'Fibonacci|斐波那契', re.IGNORECASE),
    'jensen': re.compile(r'Jensen|詹森|琴生', re.IGNORECASE),
    'menelaus': re.compile(r'Menelaus|梅涅劳斯', re.IGNORECASE),
    'pascal': re.compile(r'Pascal|帕斯卡', re.IGNORECASE),
    'pythagoras': re.compile(r'Pythagoras|Pythagorean|勾股|毕达哥拉斯', re.IGNORECASE),
    'schwarz': re.compile(r'Schwarz|施瓦茨', re.IGNORECASE),
    'vieta': re.compile(r'Vieta|韦达', re.IGNORECASE),
    'wilson': re.compile(r'Wilson|威尔逊', re.IGNORECASE),
}
_EN_NAME_ANCHORS = {
    'cauchy': re.compile(r'\bCauchy\b', re.IGNORECASE),
    'ceva': re.compile(r'\bCeva\b', re.IGNORECASE),
    'euler': re.compile(r'\bEuler\b', re.IGNORECASE),
    'fermat': re.compile(r'\bFermat\b', re.IGNORECASE),
    'fibonacci': re.compile(r'\bFibonacci\b', re.IGNORECASE),
    'jensen': re.compile(r'\bJensen\b', re.IGNORECASE),
    'menelaus': re.compile(r'\bMenelaus\b', re.IGNORECASE),
    'pascal': re.compile(r'\bPascal\b', re.IGNORECASE),
    'pythagoras': re.compile(r'\bPythagoras|Pythagorean\b', re.IGNORECASE),
    'schwarz': re.compile(r'\bSchwarz\b', re.IGNORECASE),
    'vieta': re.compile(r'\bVieta\b', re.IGNORECASE),
    'wilson': re.compile(r'\bWilson\b', re.IGNORECASE),
}


def _excerpt(value: str, limit: int = 160) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + '…'


def _plain_prose(value: str) -> str:
    """剥离数学、占位符、图片与 Markdown 标记，保留可比较的散文。"""
    value = _PROTECTED_RE.sub(' ', value)
    value = _PLACEHOLDER_RE.sub(' ', value)
    value = _IMAGE_RE.sub(' ', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'(?m)^#{1,6}[ \t]+.*$', ' ', value)
    value = _META_RE.sub(' ', value)
    value = re.sub(r'[`*_~>|\[\]()]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _normalize_prose(value: str) -> str:
    folded = unicodedata.normalize('NFKC', _plain_prose(value)).casefold()
    return ''.join(char for char in folded if char.isalpha() or char.isdigit())


def _semantic_sections(value: str) -> list[tuple[str, str, str]]:
    sections = _sections(value)
    if not sections:
        plain = _plain_prose(value)
        return [('文档', _normalize_prose(value), plain)]
    return [
        (section.heading, _normalize_prose(section.body), _plain_prose(section.body))
        for section in sections
    ]


def _missing_content_anchors(source: str, translated: str, target_lang: str) -> list[str]:
    """返回保守的缺失内容锚点；没有可靠跨语言映射时不猜。"""
    missing = []
    source_plain = _plain_prose(source)
    translated_plain = _plain_prose(translated)
    source_folded = _ascii_fold(source_plain)
    translated_folded = _ascii_fold(translated_plain)
    for source_pattern, target_patterns in _TASK_ANCHORS:
        match = source_pattern.search(source_folded)
        if match and not target_patterns[target_lang].search(translated_folded):
            missing.append(match.group(0).casefold())
    # 只锚定至少两位的十进制数；个位数常是列表编号或被自然改写，误报风险更高。
    for number in sorted(set(re.findall(r'(?<!\w)\d{2,}(?!\w)', source_plain))):
        if number not in translated_plain:
            missing.append(number)
    target_name_patterns = _ZH_NAME_ANCHORS if target_lang == 'zh' else _EN_NAME_ANCHORS
    for name, source_pattern in _ZH_NAME_ANCHORS.items():
        if (source_pattern.search(source_plain)
                and not target_name_patterns[name].search(translated_plain)):
            missing.append(name)
    # 仅检查明确独立出现的单字母变量，排除英文冠词 a 与代词 I；数学环境中的变量已被剥离。
    variables = set(re.findall(r'(?<![A-Za-z])[b-hj-zB-HJ-Z](?![A-Za-z])', source_plain))
    for variable in sorted(variables):
        if not re.search(rf'(?<![A-Za-z]){re.escape(variable)}(?![A-Za-z])', translated_plain):
            missing.append(variable)
    return missing


def _validate_batch_config(config: BatchConfig) -> None:
    if config.boilerplate_min_group < 2:
        raise ValueError('boilerplate_min_group must be at least 2')
    if not 0 < config.boilerplate_similarity <= 1:
        raise ValueError('boilerplate_similarity must be in (0, 1]')
    if not 0 <= config.boilerplate_source_similarity_gap <= 1:
        raise ValueError('boilerplate_source_similarity_gap must be in [0, 1]')
    if not 0 <= config.boilerplate_pair_similarity_tolerance <= 1:
        raise ValueError('boilerplate_pair_similarity_tolerance must be in [0, 1]')
    if config.boilerplate_min_chars <= 0:
        raise ValueError('boilerplate_min_chars must be positive')
    if not 0 < config.length_ratio_min < config.length_ratio_max:
        raise ValueError('length ratio thresholds must satisfy 0 < min < max')
    if min(config.boilerplate_weight, config.length_weight, config.anchor_weight) < 0:
        raise ValueError('signal weights must be non-negative')
    if config.block_score <= 0 or config.fuzzy_comparison_limit < 0:
        raise ValueError('block_score must be positive and fuzzy_comparison_limit non-negative')


def _cluster_find(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _cluster_union(parent: list[int], left: int, right: int) -> None:
    left_root = _cluster_find(parent, left)
    right_root = _cluster_find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def _paired_source_template_witness(
    sources: list[str],
    targets: list[str],
    config: BatchConfig,
) -> tuple[float, float] | None:
    """返回多个有独立支撑的源文模板子组之间的退化证据。

    单个近似源文对子加一个 outsider 仍不足以阻断；但两个或更多子组各自包含
    至少两种近似源文时，不能让每条记录只选择组内 peer 而丢掉跨组证据。
    该补充检查只在模糊比较上限内运行，避免目录审计引入新的二次复杂度。
    """
    if len(sources) < 4 or len(sources) > config.fuzzy_comparison_limit:
        return None

    parent = list(range(len(sources)))
    pair_scores: dict[tuple[int, int], tuple[float, float]] = {}
    for left in range(len(sources)):
        if not sources[left]:
            continue
        for right in range(left + 1, len(sources)):
            if not sources[right]:
                continue
            target_similarity = SequenceMatcher(None, targets[left], targets[right]).ratio()
            source_similarity = SequenceMatcher(None, sources[left], sources[right]).ratio()
            pair_scores[(left, right)] = (target_similarity, source_similarity)
            if (target_similarity >= config.boilerplate_similarity
                    and target_similarity - source_similarity
                    < config.boilerplate_source_similarity_gap):
                _cluster_union(parent, left, right)

    components: dict[int, list[int]] = {}
    for index in range(len(sources)):
        components.setdefault(_cluster_find(parent, index), []).append(index)
    supported = [
        indexes for indexes in components.values()
        if len({sources[index] for index in indexes if sources[index]}) >= 2
    ]
    if len(supported) < 2:
        return None

    witnesses = []
    for left_group in range(len(supported)):
        for right_group in range(left_group + 1, len(supported)):
            for left in supported[left_group]:
                for right in supported[right_group]:
                    pair = (min(left, right), max(left, right))
                    target_similarity, source_similarity = pair_scores[pair]
                    if (target_similarity >= config.boilerplate_similarity
                            and target_similarity - source_similarity
                            >= config.boilerplate_source_similarity_gap):
                        witnesses.append((target_similarity, source_similarity))
    if not witnesses:
        return None
    return max(witnesses, key=lambda score: (score[0] - score[1], score[0]))


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
    return all(len(word) == 1 or word.casefold() in _SYMBOL_WORDS for word in words)


def _has_translatable_prose(section: _Section) -> bool:
    if section.heading == '最终答案' and _is_pure_symbol(section.body):
        return False
    body = _PROTECTED_RE.sub('', section.body)
    body = _IMAGE_RE.sub('', body)
    body = re.sub(r'<[^>]+>', '', body)
    return bool(re.search(r'[^\W\d_]', body, re.UNICODE))


def _same_language_family(source_lang: str | None, target_lang: str) -> bool:
    if not source_lang:
        return False
    family = re.split(r'[-_]', source_lang.casefold(), maxsplit=1)[0]
    return family == target_lang


def _ascii_fold(value: str) -> str:
    return ''.join(
        char for char in unicodedata.normalize('NFKD', value.casefold())
        if not unicodedata.combining(char)
    )


def _has_prose_outside_target_language(
    section: _Section,
    target_lang: str,
    source_lang: str | None,
) -> bool:
    """源小节是否仍含需要翻到目标语言的散文。

    ``untranslated`` 只该拦截本应翻译却逐字照抄的内容。MathNet 生成器会把
    已是中文的状态说明写进英文原文（例如证明题的答案占位）；这类文字在中文
    variant 中原样保留是正确的，不能仅因“非空且相同”而告警。

    中文可按 Unicode 文字脚本精确判断；英文先使用与派单相同的保守语言检测器，
    再单独识别检测器因篇幅太短而返回 ``und`` 的常见英文答案。文件级
    ``source_lang=en`` 只在小节完成语言扫描且没有任何外语证据后作为佐证，不能
    再像整篇提前返回那样覆盖混合语言小节。``translated`` 仍表示内容确实经过
    模型核验；``passthrough`` 继续只表示派单前的 ``en/high`` 直通。数学、图片与
    纯符号内容仍由 ``_has_translatable_prose`` 先行排除。
    """
    if not _has_translatable_prose(section):
        return False

    body = _PROTECTED_RE.sub('', section.body)
    body = _IMAGE_RE.sub('', body)
    body = re.sub(r'<[^>]+>', '', body)
    letters = [ch for ch in body if unicodedata.category(ch).startswith('L')]
    if target_lang == 'zh':
        return any(_HAN_RE.fullmatch(ch) is None for ch in letters)
    # ``und`` must never prove that unchanged prose is already English.  True
    # English documents are promoted to en/high by the source detector before
    # dispatch; keeping this boundary closed prevents stale/weak detections
    # from authorizing an unchanged model echo.
    if not source_lang or _same_language_family(source_lang, 'und'):
        return True
    if _NON_ENGLISH_FRAGMENT_RE.search(_ascii_fold(body)):
        return True
    detected_lang, confidence = detect_source_lang(body, {})
    if detected_lang == 'en' and confidence == 'high':
        return False
    if detected_lang not in {'en', 'und'}:
        return True
    if _SHORT_ENGLISH_PROSE_RE.search(body):
        return False
    if _same_language_family(source_lang, target_lang):
        return False
    return True


def _content_findings(
    source: str,
    translated: str,
    mode: str,
    target_lang: str,
    source_lang: str | None,
) -> list[Finding]:
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
                    and _has_prose_outside_target_language(
                        source_section, target_lang, source_lang)):
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


def verify_translation(
    source: str,
    translated: str,
    *,
    mode: str = 'translated',
    target_lang: str = 'zh',
    source_lang: str | None = None,
) -> list[Finding]:
    """校验一对 Markdown 字符串，返回全部可定位问题。

    ``mode='passthrough'`` 只关闭“正文未翻译”检查；数学、图片、骨架与泄漏检查
    仍然执行。调用方可直接传入 ``translation.json`` 中对应 variant 的 mode。
    ``source_lang`` 保留在调用契约中，但不能绕过混合语言文档的逐小节证据检查。
    """
    if mode not in {'translated', 'passthrough', 'failed'}:
        raise ValueError(f'unsupported translation mode: {mode}')
    if target_lang not in {'en', 'zh'}:
        raise ValueError(f'unsupported target language: {target_lang}')
    findings = []
    findings.extend(_compare_occurrences(
        FindingType.MATH_MISMATCH, _protected(source), _protected(translated)))
    findings.extend(_compare_occurrences(
        FindingType.IMAGE_MISMATCH, _images(source), _images(translated)))
    findings.extend(_structure_findings(source, translated))
    findings.extend(_content_findings(source, translated, mode, target_lang, source_lang))
    findings.extend(_leak_findings(source, translated))
    return findings


def verify_batch(
    sources: list[str] | tuple[str, ...],
    translated: list[str] | tuple[str, ...],
    *,
    keys: list[str] | tuple[str, ...] | None = None,
    target_lang: str = 'zh',
    config: BatchConfig | None = None,
) -> BatchReport:
    """从跨条目视角检查退化翻译，不改变逐题 ``verify_translation`` 的语义。

    返回的三路 ``signals`` 始终分开保留；只有单条信号权重之和达到
    ``config.block_score`` 才会复制为阻断 ``findings``。默认套话信号可独立阻断，
    长度异常与锚点缺失必须共同出现。调用方应按一次模型输出或一次抽检批次传入，
    不能把互不相关的目标语言混在同一批。
    """
    if target_lang not in {'en', 'zh'}:
        raise ValueError(f'unsupported target language: {target_lang}')
    if len(sources) != len(translated):
        raise ValueError('sources and translated must have the same length')
    item_keys = list(keys) if keys is not None else [str(index) for index in range(len(sources))]
    if len(item_keys) != len(sources):
        raise ValueError('keys and sources must have the same length')
    if len(set(item_keys)) != len(item_keys) or not all(isinstance(key, str) for key in item_keys):
        raise ValueError('keys must be unique strings')
    if not all(isinstance(value, str) for value in (*sources, *translated)):
        raise TypeError('batch source and translated values must be strings')

    batch_config = config or BatchConfig()
    _validate_batch_config(batch_config)
    signals: dict[str, list[BatchSignal]] = {key: [] for key in item_keys}
    source_by_key = dict(zip(item_keys, sources))
    translated_by_key = dict(zip(item_keys, translated))

    source_sections = [dict(
        (heading, normalized) for heading, normalized, _plain in _semantic_sections(value)
    ) for value in sources]
    translated_sections = [_semantic_sections(value) for value in translated]
    by_heading: dict[str, list[tuple[int, str, str]]] = {}
    for index, sections in enumerate(translated_sections):
        for heading, normalized, plain in sections:
            if len(normalized) >= batch_config.boilerplate_min_chars:
                by_heading.setdefault(heading, []).append((index, normalized, plain))

    for heading, entries in by_heading.items():
        parent = list(range(len(entries)))

        exact: dict[str, list[int]] = {}
        for position, (_index, normalized, _plain) in enumerate(entries):
            exact.setdefault(normalized, []).append(position)
        for positions in exact.values():
            for position in positions[1:]:
                _cluster_union(parent, positions[0], position)

        if len(entries) <= batch_config.fuzzy_comparison_limit:
            for left in range(len(entries)):
                left_text = entries[left][1]
                for right in range(left + 1, len(entries)):
                    right_text = entries[right][1]
                    if left_text == right_text:
                        continue
                    shorter, longer = sorted((len(left_text), len(right_text)))
                    if shorter / longer < batch_config.boilerplate_similarity:
                        continue
                    similarity = SequenceMatcher(None, left_text, right_text).ratio()
                    if similarity >= batch_config.boilerplate_similarity:
                        _cluster_union(parent, left, right)

        groups: dict[int, list[int]] = {}
        for position in range(len(entries)):
            groups.setdefault(_cluster_find(parent, position), []).append(position)
        for positions in groups.values():
            if len(positions) < batch_config.boilerplate_min_group:
                continue
            item_indexes = [entries[position][0] for position in positions]
            group_sources = [source_sections[index].get(heading, '') for index in item_indexes]
            distinct_sources = set(group_sources) - {''}
            # 相同原文的合法重复翻译不构成退化；至少要看到两种不同源散文。
            if len(distinct_sources) < 2:
                continue
            group_texts = [entries[position][1] for position in positions]
            if len(positions) > batch_config.fuzzy_comparison_limit:
                # 大目录只会把完全相同的译文连成大簇；用四个源文极值作线性近似，
                # 足以找到“译文全同但源文明显不同”的证据且避免 O(n²)。
                exemplar_indexes = {
                    min(range(len(group_sources)), key=lambda index: len(group_sources[index])),
                    max(range(len(group_sources)), key=lambda index: len(group_sources[index])),
                    min(range(len(group_sources)), key=group_sources.__getitem__),
                    max(range(len(group_sources)), key=group_sources.__getitem__),
                }
            else:
                exemplar_indexes = set(range(len(group_sources)))
            candidates = []
            for group_position, position in enumerate(positions):
                item_index, normalized, _plain = entries[position]
                source_text = group_sources[group_position]
                peer_scores = [
                    (
                        1.0 if len(positions) > batch_config.fuzzy_comparison_limit
                        else SequenceMatcher(None, normalized, group_texts[peer_index]).ratio(),
                        SequenceMatcher(None, source_text, group_sources[peer_index]).ratio(),
                    )
                    for peer_index in exemplar_indexes
                    # 完全相同的源文不是独立的体裁证据；整簇源文都相同的情形
                    # 已由 distinct_sources 门槛作为合法重复提前退出。
                    if (peer_index != group_position and source_text
                        and group_sources[peer_index]
                        and group_sources[peer_index] != source_text)
                ]
                if not peer_scores:
                    continue
                best_target_similarity = max(score[0] for score in peer_scores)
                comparable_scores = [
                    score for score in peer_scores
                    if score[0] >= (
                        best_target_similarity
                        - batch_config.boilerplate_pair_similarity_tolerance
                    )
                ]
                target_similarity, source_similarity = max(
                    comparable_scores,
                    key=lambda score: (score[1], score[0]),
                )
                if (target_similarity - source_similarity
                        < batch_config.boilerplate_source_similarity_gap):
                    continue
                candidates.append((item_index, target_similarity, source_similarity))
            cluster_witness = None
            if len(candidates) < batch_config.boilerplate_min_group:
                cluster_witness = _paired_source_template_witness(
                    group_sources, group_texts, batch_config
                )
                if cluster_witness is None:
                    continue
                target_similarity, source_similarity = cluster_witness
                candidates = [
                    (entries[position][0], target_similarity, source_similarity)
                    for position in positions
                ]
            for item_index, target_similarity, source_similarity in candidates:
                evidence = (
                    '簇级跨模板源文相似度'
                    if cluster_witness is not None
                    else '匹配 peer 的源文相似度'
                )
                signals[item_keys[item_index]].append(BatchSignal(
                    FindingType.BATCH_BOILERPLATE,
                    heading,
                    target_similarity,
                    f'{len(candidates)} 条译文散文高度重合；译文相似度 {target_similarity:.3f}，'
                    f'{evidence} {source_similarity:.3f}',
                ))

    for key, source, target in zip(item_keys, sources, translated):
        source_length = len(_normalize_prose(source))
        target_length = len(_normalize_prose(target))
        if source_length:
            ratio = target_length / source_length
            if ratio < batch_config.length_ratio_min or ratio > batch_config.length_ratio_max:
                signals[key].append(BatchSignal(
                    FindingType.LENGTH_RATIO,
                    '文档',
                    ratio,
                    f'散文长度比 {ratio:.3f}，默认合理区间 '
                    f'[{batch_config.length_ratio_min:.3f}, {batch_config.length_ratio_max:.3f}]',
                ))
        missing_anchors = _missing_content_anchors(source, target, target_lang)
        if missing_anchors:
            signals[key].append(BatchSignal(
                FindingType.CONTENT_ANCHOR_MISSING,
                '文档',
                float(len(missing_anchors)),
                '缺少保守内容锚点：' + ', '.join(missing_anchors),
            ))

    weights = {
        FindingType.BATCH_BOILERPLATE: batch_config.boilerplate_weight,
        FindingType.LENGTH_RATIO: batch_config.length_weight,
        FindingType.CONTENT_ANCHOR_MISSING: batch_config.anchor_weight,
    }
    findings: dict[str, list[Finding]] = {}
    for key, item_signals in signals.items():
        if sum(weights[signal.type] for signal in item_signals) < batch_config.block_score:
            continue
        findings[key] = [
            Finding(
                signal.type,
                signal.section,
                _excerpt(source_by_key[key]),
                _excerpt(translated_by_key[key]),
            )
            for signal in item_signals
        ]
    return BatchReport(
        findings=findings,
        signals={key: value for key, value in signals.items() if value},
    )


def verify_directory(
    source_dir: str | Path,
    translated_dir: str | Path | None = None,
    *,
    mode: str = 'translated',
    pattern: str | None = None,
    variant: str = 'zh',
    only_translated: bool = False,
) -> DirectoryReport:
    """校验目录并返回题数与各问题类型统计。

    只传 ``source_dir`` 时使用产物契约原生布局，递归配对同目录下的
    ``index.md`` 与 ``index.<variant>.md``。传入 ``translated_dir`` 时按两个
    目录的相对路径配对，方便批次导出与 CI fixture。默认把缺少译文的原文计为
    ``missing_translation``，用于完整性审计；``only_translated`` 只检查已有译文。
    """
    if variant not in {'en', 'zh'}:
        raise ValueError(f'unsupported translation variant: {variant}')
    source_root = Path(source_dir)
    if not source_root.is_dir():
        raise FileNotFoundError(f'source directory does not exist: {source_root}')
    translated_root = Path(translated_dir) if translated_dir is not None else None
    failed_files = {}
    source_pattern = pattern or ('*.md' if translated_root is not None else 'index.md')
    source_paths = sorted(path for path in source_root.rglob(source_pattern) if path.is_file())
    pairs = []
    for source_path in source_paths:
        relative = source_path.relative_to(source_root)
        translated_path = (
            translated_root / relative if translated_root is not None
            else source_path.with_name(f'index.{variant}.md')
        )
        if only_translated and not translated_path.is_file():
            continue
        pairs.append((source_path, relative, translated_path))
    batch_sources = []
    batch_translated = []
    batch_keys = []
    for source_path, relative, translated_path in pairs:
        if translated_path.is_file():
            source_text = source_path.read_text(encoding='utf-8')
            translated_text = translated_path.read_text(encoding='utf-8')
            findings = verify_translation(
                source_text,
                translated_text,
                mode=mode,
                target_lang=variant,
            )
            if mode == 'translated':
                batch_sources.append(source_text)
                batch_translated.append(translated_text)
                batch_keys.append(relative.as_posix())
        else:
            findings = [Finding(
                FindingType.MISSING_TRANSLATION,
                '文档',
                str(relative),
                '',
            )]
        if findings:
            failed_files[relative.as_posix()] = findings
    batch_report = verify_batch(
        batch_sources,
        batch_translated,
        keys=batch_keys,
        target_lang=variant,
    )
    for key, findings in batch_report.findings.items():
        failed_files.setdefault(key, []).extend(findings)
    counts: Counter[str] = Counter(
        finding.type.value
        for findings in failed_files.values()
        for finding in findings
    )
    failed = len(failed_files)
    return DirectoryReport(
        total=len(pairs),
        passed=len(pairs) - failed,
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
    parser.add_argument('--only-translated', action='store_true',
                        help='只检查已有目标 variant；默认也把缺少译文报告为 missing_translation')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    args = parser.parse_args(argv)
    report = verify_directory(
        args.source_dir,
        args.translated_dir,
        mode=args.mode,
        pattern=args.glob,
        variant=args.variant,
        only_translated=args.only_translated,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 1 if report.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
