"""译文目标语言与乱码判定。

本模块只处理字符串。英文的启发式语言判据只能由调用方用于与源文逐字相同的
小节；已经改写过的英文小节不能因人名、变音符或片段词被判为未翻译。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from source_lang import detect_source_lang


@dataclass(frozen=True)
class LanguageConfig:
    """中文覆盖、英文脚本连读与乱码判据的稳定阈值。"""

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
    r'(?P<code>(?<!`)``(?!`).*?(?<!`)``(?!`)|(?<!`)`(?!`)[^\n`]*`(?!`))'
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
_WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
_KNOWN_EMPTY_SECTIONS = {
    '（数据集未提供）',
    '（数据集未提供 / 证明题）',
}
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
    """剥离数学、代码、图片与标记，但保留列表冒号后的正文。"""
    value = _FENCED_CODE_RE.sub(' ', value)
    value = PROTECTED_RE.sub(' ', value)
    value = _PLACEHOLDER_RE.sub(' ', value)
    value = IMAGE_RE.sub(' ', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = re.sub(r'(?m)^#{1,6}[ \t]+', ' ', value)
    value = META_RE.sub(lambda match: match.group('value'), value)
    value = re.sub(r'[`*_~>|\[\]()]', ' ', value)
    value = re.sub(r'(?m)^[ \t]*[-*+][ \t]+', ' ', value)
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
    words = _WORD_RE.findall(without_commands)
    return all(len(word) == 1 or word.casefold() in _SYMBOL_WORDS for word in words)


def has_translatable_prose(body: str, heading: str = '') -> bool:
    """排除纯符号答案和生成器已知占位，不泛化到任意括号句。"""
    candidate = body.strip()
    if heading == '最终答案' and is_pure_symbol(candidate):
        return False
    if ((heading == '最终答案' or heading == '解法' or heading.startswith('解法 '))
            and candidate in _KNOWN_EMPTY_SECTIONS):
        return False
    return bool(re.search(r'[^\W\d_]', plain_prose(body), re.UNICODE))


def _is_latin_word(word: str) -> bool:
    return bool(word) and all(
        unicodedata.category(char).startswith('L')
        and 'LATIN' in unicodedata.name(char, '')
        for char in word
    )


def _is_han(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _residual_latin_letters(body: str) -> int:
    """统计中文散文中排除数学符号与专名后的拉丁字母。"""
    latin_words = [word for word in _WORD_RE.findall(body) if _is_latin_word(word)]
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
        len(word) for word, is_proper in zip(latin_words, proper) if not is_proper
    )


def _has_latin_diacritic(body: str) -> bool:
    return any(
        ord(char) > 127
        and unicodedata.category(char).startswith('L')
        and 'LATIN' in unicodedata.name(char, '')
        for char in body
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
    if _is_han(char):
        return 'han'
    if 0xAC00 <= codepoint <= 0xD7AF:
        return 'hangul'
    return None


def _has_foreign_script_prose(body: str, minimum: int) -> bool:
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
    words = _WORD_RE.findall(value)
    return bool(words) and all(
        len(word) == 1 or word.casefold() in _SYMBOL_WORDS for word in words
    )


def target_language_mismatch(
    body: str,
    target_lang: str,
    source_lang: str | None,
    config: LanguageConfig,
) -> bool:
    """判断散文是否缺少目标语言覆盖；英文调用必须由逐字相同闸门保护。"""
    prose = plain_prose(body)
    if not prose:
        return False
    if target_lang == 'zh':
        foreign_script_letters = sum(
            unicodedata.category(char).startswith('L')
            and not _is_han(char)
            and not _is_latin_word(char)
            for char in prose
        )
        if foreign_script_letters >= config.en_min_foreign_script_letters:
            return True
        han_letters = sum(_is_han(char) for char in prose)
        latin_letters = _residual_latin_letters(prose)
        denominator = han_letters + latin_letters
        ratio = latin_letters / denominator if denominator else 0.0
        short_english = bool(_SHORT_ENGLISH_PROSE_RE.search(prose))
        minimum = min(config.zh_min_latin_letters, 4) if short_english else config.zh_min_latin_letters
        return latin_letters >= minimum and ratio > config.zh_max_latin_ratio

    # 四条恢复判据和脚本连读都只在调用方确认逐字相同后使用。
    if _has_foreign_script_prose(prose, config.en_min_foreign_script_letters):
        return True
    if _is_english_symbolic_fragment(prose):
        return False
    if _NON_ENGLISH_FRAGMENT_RE.search(ascii_fold(prose)):
        return True
    if _has_latin_diacritic(prose):
        return True
    detected_lang, _confidence = detect_source_lang(prose, {})
    if detected_lang == 'en':
        return False
    if detected_lang != 'und':
        return True
    if _SHORT_ENGLISH_PROSE_RE.search(prose):
        return False
    if source_lang and re.split(r'[-_]', source_lang.casefold(), maxsplit=1)[0] == target_lang:
        return False
    return True


def looks_mojibake(body: str, config: LanguageConfig) -> bool:
    prose = plain_prose(body)
    return bool(_MOJIBAKE_SIGNATURE_RE.search(prose)) or (
        len(_MOJIBAKE_MARKER_RE.findall(prose)) >= config.mojibake_min_markers
    )
