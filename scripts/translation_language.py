"""译文目标语言判定的唯一共用实现。

保真校验、落盘闸门与同文核验都应引用本模块，不在调用方复制文字脚本规则。
模块只处理字符串，不访问 MathNet 语料目录。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageConfig:
    """逐小节目标语言与乱码闸门的可配置阈值。

    中文阈值来自 CXB-522 已验证口径，不能因英文规则调整而漂移。孤立希腊字母
    在英文侧视作数学符号；连续希腊字母串才是希腊语散文证据。
    """

    zh_max_latin_ratio: float = 0.35
    zh_min_latin_letters: int = 8
    en_min_foreign_script_letters: int = 2
    mojibake_min_markers: int = 3


META_RE = re.compile(
    r'^[ \t]*[-*+][ \t]+(?P<key>(?:\*\*)?[^\n:：]+?(?:\*\*)?)[ \t]*[:：][ \t]*'
    r'(?P<value>.*)$',
    re.MULTILINE,
)
PROTECTED_RE = re.compile(
    r'(?P<code>(?<!`)\``(?!`).*?(?<!`)\``(?!`)|(?<!`)\`(?!`)[^\n`]*\`(?!`))'
    r'|(?P<display>\$\$.*?\$\$)'
    r'|(?P<environment>\\begin\{(?P<env>[^{}\n]+)\}.*?\\end\{(?P=env)\})'
    r'|(?P<inline>(?<!\$)\$(?!\$)[^$\n]*?\$(?!\$))',
    re.DOTALL,
)
IMAGE_RE = re.compile(r'!\[\]\(attached_image_(\d+)\.png\)')
_FENCED_CODE_RE = re.compile(
    r'(?ms)^[ \t]*(?:`{3}|~{3}).*?^[ \t]*(?:`{3}|~{3})[ \t]*$'
)
_PLACEHOLDER_RE = re.compile(r'\{\{MNT_\d{4}\}\}')
_HAN_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
_WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
_GENERATED_EMPTY_SECTION_RE = re.compile(r'\A（[^（）\n]+）\Z')
_MOJIBAKE_SIGNATURE_RE = re.compile(
    r'(?:\u00c3[\u0080-\u00bf]|\u00c2(?:[\u0080-\u00bf]| )'
    r'|\u00e2[\u0080-\u00bf]{1,2}|\u00f0[\u0080-\u00bf]{1,3}'
    r'|\u00ef[\u00bc\u00bd\u00be])'
)
_MOJIBAKE_MARKER_RE = re.compile(r'[\u00c2-\u00ef][\u0080-\u00bf]')
_SYMBOL_WORDS = {
    'bmod', 'cos', 'gcd', 'inf', 'lcm', 'ln', 'log', 'max', 'min', 'mod',
    'pmod', 'sin', 'sqrt', 'sup', 'tan',
}
_LATIN_PROSE_STARTERS = {
    'answer', 'both', 'calculate', 'compute', 'determine', 'do', 'find', 'given',
    'if', 'let', 'on', 'pour', 'prove', 'set', 'show', 'there', 'what', 'when',
}
_PROPER_NAME_CONNECTORS = {'and', 'de', 'der', 'of', 'the', 'van', 'von'}
_SHORT_ENGLISH_PROSE_RE = re.compile(
    r'\b(?:'
    r'no[ \t]+solutions?'
    r'|proof[ \t]+(?:is[ \t]+)?omitted'
    r'|the[ \t]+answers?[ \t]+(?:is|are)'
    r'|find\b'
    r'|set\b'
    r'|verify\b'
    r'|where\b'
    r'|alternatively[ \t]+use\b'
    r'|this[ \t]+is\b'
    r'|the\b[^\n.!?]{0,80}\b(?:follows|holds|is|are|uses|gives|satisfies)\b'
    r')',
    re.IGNORECASE,
)


def validate_language_config(config: LanguageConfig) -> None:
    if not 0 <= config.zh_max_latin_ratio <= 1:
        raise ValueError('zh_max_latin_ratio must be in [0, 1]')
    if config.zh_min_latin_letters <= 0:
        raise ValueError('zh_min_latin_letters must be positive')
    if config.en_min_foreign_script_letters <= 0:
        raise ValueError('en_min_foreign_script_letters must be positive')
    if config.mojibake_min_markers <= 0:
        raise ValueError('mojibake_min_markers must be positive')


def plain_prose(value: str) -> str:
    """剥离数学、占位符、图片与 Markdown 标记，保留可判定的散文。"""
    value = _FENCED_CODE_RE.sub(' ', value)
    value = PROTECTED_RE.sub(' ', value)
    value = _PLACEHOLDER_RE.sub(' ', value)
    value = IMAGE_RE.sub(' ', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'(?m)^#{1,6}[ \t]+.*$', ' ', value)
    value = META_RE.sub(' ', value)
    value = re.sub(r'[`*_~>|\[\]()]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def ascii_fold(value: str) -> str:
    return ''.join(
        char for char in unicodedata.normalize('NFKD', value.casefold())
        if not unicodedata.combining(char)
    )


def is_pure_symbol(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    without_commands = re.sub(r'\\[A-Za-z]+', '', candidate)
    words = re.findall(r'[^\W\d_]+', without_commands, re.UNICODE)
    return all(len(word) == 1 or word.casefold() in _SYMBOL_WORDS for word in words)


def has_translatable_prose(body: str, heading: str = '') -> bool:
    """源小节是否真的含可译内容，而非生成器写入的空内容状态。"""
    candidate = body.strip()
    if heading == '最终答案' and is_pure_symbol(candidate):
        return False
    if ((heading == '最终答案' or heading == '解法' or heading.startswith('解法 '))
            and _GENERATED_EMPTY_SECTION_RE.fullmatch(candidate)):
        return False
    return bool(re.search(r'[^\W\d_]', plain_prose(body), re.UNICODE))


def _is_latin_word(word: str) -> bool:
    return bool(word) and all(
        unicodedata.category(char).startswith('L')
        and 'LATIN' in unicodedata.name(char, '')
        for char in word
    )


def _residual_latin_letters(body: str) -> int:
    """统计中文散文中的非例外拉丁字母；保持 CXB-522 已验证口径。"""
    latin_words = [
        match.group(0) for match in _WORD_RE.finditer(body)
        if _is_latin_word(match.group(0))
    ]
    proper = []
    for word in latin_words:
        folded = ascii_fold(word)
        proper.append(
            len(word) == 1
            or folded in _SYMBOL_WORDS
            or word.isupper()
            or (word[0].isupper() and folded not in _LATIN_PROSE_STARTERS)
        )
    for index, word in enumerate(latin_words):
        if (ascii_fold(word) in _PROPER_NAME_CONNECTORS
                and 0 < index < len(latin_words) - 1
                and proper[index - 1] and proper[index + 1]):
            proper[index] = True
    return sum(
        len(word)
        for word, is_proper in zip(latin_words, proper)
        if not is_proper
    )


def _foreign_script_family(char: str) -> str | None:
    """返回英文侧可高置信区分的文字脚本；拉丁字母一律不参与。"""
    if not unicodedata.category(char).startswith('L'):
        return None
    codepoint = ord(char)
    if 0x0370 <= codepoint <= 0x03FF or 0x1F00 <= codepoint <= 0x1FFF:
        return 'greek'
    if 0x0400 <= codepoint <= 0x052F:
        return 'cyrillic'
    if 0x0590 <= codepoint <= 0x05FF:
        return 'hebrew'
    if (0x0600 <= codepoint <= 0x06FF
            or 0x0750 <= codepoint <= 0x077F
            or 0x08A0 <= codepoint <= 0x08FF):
        return 'arabic'
    if (0x3040 <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0xAC00 <= codepoint <= 0xD7AF):
        return 'cjk'
    return None


def _has_foreign_script_prose(body: str, minimum: int) -> bool:
    """只接受同一脚本的连续文字作为成句证据，孤立数学符号不算。"""
    family = None
    run_length = 0
    for char in body:
        char_family = _foreign_script_family(char)
        if char_family is None:
            if not unicodedata.combining(char):
                family = None
                run_length = 0
            continue
        if char_family == family:
            run_length += 1
        else:
            family = char_family
            run_length = 1
        if run_length >= minimum:
            return True
    return False


def target_language_mismatch(
    body: str,
    target_lang: str,
    config: LanguageConfig,
) -> bool:
    """判断散文是否缺少目标语言覆盖；所有调用方共用本判据。"""
    prose = plain_prose(body)
    if not prose:
        return False
    if target_lang == 'zh':
        foreign_script_letters = sum(
            unicodedata.category(char).startswith('L')
            and _HAN_RE.fullmatch(char) is None
            and not _is_latin_word(char)
            for char in prose
        )
        if foreign_script_letters >= config.en_min_foreign_script_letters:
            return True
        han_letters = len(_HAN_RE.findall(prose))
        latin_letters = _residual_latin_letters(prose)
        denominator = han_letters + latin_letters
        ratio = latin_letters / denominator if denominator else 0.0
        short_english = bool(_SHORT_ENGLISH_PROSE_RE.search(prose))
        minimum = (
            min(config.zh_min_latin_letters, 4)
            if short_english else config.zh_min_latin_letters
        )
        return latin_letters >= minimum and ratio > config.zh_max_latin_ratio

    # 英文与二十余种语言共用拉丁字母，短数学散文不足以可靠区分。英文侧只认
    # CJK/西里尔/希腊/希伯来/阿拉伯的连续文字；不再使用语言检测、und 兜底、
    # 拉丁变音符或片段词表。脚本证据是唯一分支，自然先于任何英文正证据。
    return _has_foreign_script_prose(
        prose, config.en_min_foreign_script_letters
    )


def looks_mojibake(body: str, config: LanguageConfig) -> bool:
    prose = plain_prose(body)
    return bool(_MOJIBAKE_SIGNATURE_RE.search(prose)) or (
        len(_MOJIBAKE_MARKER_RE.findall(prose)) >= config.mojibake_min_markers
    )
