"""英文目标语言判定。

本模块只处理字符串。这里的启发式判据只能由调用方用于与源文逐字相同的
小节；已经改写过的小节不能因人名、变音符或片段词被判为未翻译。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from source_lang import detect_source_lang


@dataclass(frozen=True)
class EnglishLanguageConfig:
    """英文闸门的稳定阈值。"""

    min_foreign_script_letters: int = 2


_PROTECTED_RE = re.compile(
    r'(?P<code>(?<!`)``(?!`).*?(?<!`)``(?!`)|(?<!`)`(?!`)[^\n`]*`(?!`))'
    r'|(?P<display>\$\$.*?\$\$)'
    r'|(?P<environment>\\begin\{(?P<env>[^{}\n]+)\}.*?\\end\{(?P=env)\})'
    r'|(?P<inline>(?<!\$)\$(?!\$)[^$\n]*?\$(?!\$))',
    re.DOTALL,
)
_IMAGE_RE = re.compile(r'!\[\]\(attached_image_(\d+)\.png\)')
_FENCED_CODE_RE = re.compile(
    r'(?ms)^[ \t]*(?:`{3}|~{3}).*?^[ \t]*(?:`{3}|~{3})[ \t]*$'
)
_PLACEHOLDER_RE = re.compile(r'\{\{MNT_\d{4}\}\}')
_WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
_KNOWN_GENERATOR_PLACEHOLDERS = {
    'Not provided in the dataset',
    'Not provided in the dataset / proof problem',
    '（数据集未提供）',
    '（数据集未提供 / 证明题）',
}
_SYMBOL_WORDS = {
    'bmod', 'cos', 'gcd', 'inf', 'lcm', 'ln', 'log', 'max', 'min', 'mod',
    'pmod', 'sin', 'sqrt', 'sup', 'tan',
}
_SHORT_ENGLISH_PROSE_RE = re.compile(
    r'\b(?:no[ \t]+solutions?|proof[ \t]+(?:is[ \t]+)?omitted'
    r'|the[ \t]+answers?[ \t]+(?:is|are)|find\b|set\b|verify\b|where\b'
    r'|alternatively[ \t]+use\b|this[ \t]+is\b'
    r'|the\b[^\n.!?]{0,80}\b(?:follows|holds|is|are|uses|gives|satisfies)\b)',
    re.IGNORECASE,
)
_NON_ENGLISH_FRAGMENT_RE = re.compile(
    r'\b(?:aucun|aucune|trouver|demontrer|preuve|reponse|omis|omise|les|des|pour'
    r'|nessun|nessuna|trovare|dimostrare|soluzione|risposta|omessa'
    r'|kein|keine|finden|beweis|antwort|losung|losungen'
    r'|ningun|ninguna|hallar|respuesta|prueba|omitida|soluciones'
    r'|nenhum|nenhuma|encontrar|resposta|omitida|solucoes'
    r'|poisci|dokazi|resitev)\b'
)


def validate_config(config: EnglishLanguageConfig) -> None:
    if config.min_foreign_script_letters <= 0:
        raise ValueError('min_foreign_script_letters must be positive')


def plain_prose(value: str) -> str:
    """剥离数学、代码、图片与 Markdown 标记，只保留语言判定所需散文。"""
    value = _FENCED_CODE_RE.sub(' ', value)
    value = _PROTECTED_RE.sub(' ', value)
    value = _PLACEHOLDER_RE.sub(' ', value)
    value = _IMAGE_RE.sub(' ', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'(?m)^#{1,6}[ \t]+', ' ', value)
    value = re.sub(r'[`*_~>|\[\]()]', ' ', value)
    value = re.sub(r'(?m)^[ \t]*[-*+][ \t]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def ascii_fold(value: str) -> str:
    return ''.join(
        char for char in unicodedata.normalize('NFKD', value.casefold())
        if not unicodedata.combining(char)
    )


def is_known_generator_placeholder(value: str) -> bool:
    return value.strip().rstrip('.') in _KNOWN_GENERATOR_PLACEHOLDERS


def _is_latin_diacritic(body: str) -> bool:
    return any(
        ord(char) > 127
        and unicodedata.category(char).startswith('L')
        and 'LATIN' in unicodedata.name(char, '')
        for word in _WORD_RE.findall(body)
        if not word[0].isupper()
        for char in word
    )


def _foreign_script_family(char: str) -> str | None:
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
    if 0x3040 <= codepoint <= 0x30FF:
        return 'kana'
    if (0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF):
        return 'han'
    if 0xAC00 <= codepoint <= 0xD7AF:
        return 'hangul'
    return None


def has_foreign_script_prose(body: str, minimum: int) -> bool:
    """只认同一脚本的连续文字；孤立或混脚本符号不构成散文。"""
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


def _is_english_symbolic_fragment(value: str) -> bool:
    without_commands = re.sub(r'\\[A-Za-z]+', '', value)
    words = _WORD_RE.findall(without_commands)
    return bool(words) and all(
        len(word) == 1 or word.casefold() in _SYMBOL_WORDS for word in words
    )


def english_target_mismatch(
    body: str,
    source_lang: str | None,
    config: EnglishLanguageConfig | None = None,
) -> bool:
    """判断逐字相同的散文是否缺少英文覆盖。"""
    config = config or EnglishLanguageConfig()
    validate_config(config)
    prose = plain_prose(body)
    if not prose or is_known_generator_placeholder(prose):
        return False
    if has_foreign_script_prose(prose, config.min_foreign_script_letters):
        return True
    if _is_english_symbolic_fragment(prose):
        return False
    if _NON_ENGLISH_FRAGMENT_RE.search(ascii_fold(prose)):
        return True
    if _is_latin_diacritic(prose):
        return True
    detected_lang, _confidence = detect_source_lang(prose, {})
    if detected_lang == 'en':
        return False
    if detected_lang != 'und':
        return True
    if _SHORT_ENGLISH_PROSE_RE.search(prose):
        return False
    if source_lang and re.split(r'[-_]', source_lang.casefold(), maxsplit=1)[0] == 'en':
        return False
    return True
