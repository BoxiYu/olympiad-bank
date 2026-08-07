"""译文目标语言判定的唯一共用实现。

保真校验、落盘闸门与同文核验都应引用本模块，不在调用方复制文字脚本或
``und`` 兜底规则。模块只处理字符串，不访问 MathNet 语料目录。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from source_lang import detect_source_lang


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
_NON_ENGLISH_SCRIPT_RE = re.compile(
    r'[\u0400-\u052f'
    r'\u0590-\u05ff'
    r'\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff'
    r'\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
    r'\uac00-\ud7af]'
)
_GREEK_CHAR_RE = re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]')
_GREEK_PROSE_RE = re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]{2,}')
_CYRILLIC_PROSE_RE = re.compile(r'[\u0400-\u052f]{2,}')
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
_NON_ENGLISH_FRAGMENT_RE = re.compile(
    r'\b(?:'
    r'aucun|aucune|trouver|demontrer|preuve|reponse|omis|omise|les|des|pour'
    r'|nessun|nessuna|trovare|dimostrare|soluzione|risposta|omessa'
    r'|kein|keine|finden|beweis|antwort|losung|losungen'
    r'|ningun|ninguna|hallar|respuesta|prueba|omitida|soluciones'
    r'|nenhum|nenhuma|encontrar|resposta|omitida|solucoes'
    r'|poisci|dokazi|resitev'
    r')\b'
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


def _is_english_symbolic_fragment(value: str) -> bool:
    words = re.findall(r'[^\W\d_]+', value, re.UNICODE)
    return bool(words) and all(
        len(word) == 1
        or word.casefold() in _SYMBOL_WORDS
        or (
            len(word) == 2
            and sum(_GREEK_CHAR_RE.fullmatch(char) is not None for char in word) == 1
        )
        for word in words
    )


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


def _has_latin_diacritic(body: str) -> bool:
    return any(
        ord(char) > 127
        and unicodedata.category(char).startswith('L')
        and 'LATIN' in unicodedata.name(char, '')
        for char in body
    )


def _has_non_english_script_or_latin_diacritic(body: str) -> bool:
    """返回明确的非英文散文证据；孤立希腊字母是数学符号。"""
    if _NON_ENGLISH_SCRIPT_RE.search(body) or _GREEK_PROSE_RE.search(body):
        return True
    return _has_latin_diacritic(body)


def _same_language_family(source_lang: str | None, target_lang: str) -> bool:
    if not source_lang:
        return False
    family = re.split(r'[-_]', source_lang.casefold(), maxsplit=1)[0]
    return family == target_lang


def target_language_mismatch(
    body: str,
    target_lang: str,
    source_lang: str | None,
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

    if _is_english_symbolic_fragment(prose):
        return False
    if (_NON_ENGLISH_FRAGMENT_RE.search(ascii_fold(prose))
            or _GREEK_PROSE_RE.search(prose)
            or _CYRILLIC_PROSE_RE.search(prose)):
        return True
    detected_lang, _confidence = detect_source_lang(prose, {})
    if detected_lang == 'en':
        return False
    if detected_lang != 'und':
        return True
    if _SHORT_ENGLISH_PROSE_RE.search(prose):
        return False
    if _has_non_english_script_or_latin_diacritic(prose):
        return True
    if _same_language_family(source_lang, target_lang):
        return False
    return True


def looks_mojibake(body: str, config: LanguageConfig) -> bool:
    prose = plain_prose(body)
    return bool(_MOJIBAKE_SIGNATURE_RE.search(prose)) or (
        len(_MOJIBAKE_MARKER_RE.findall(prose)) >= config.mojibake_min_markers
    )
