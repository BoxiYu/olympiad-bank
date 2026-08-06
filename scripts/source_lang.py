"""Conservative source-language detection for MathNet problem statements.

The detector deliberately optimizes against false English results: ``en`` is
used by the translation pipeline as a passthrough decision, so an uncertain
non-English statement must cost one translation call rather than silently pass
through untranslated.

Only the Python standard library is used.  The supported Latin-script language
features reflect the sizeable MathNet groups (English, Spanish, Portuguese,
French and Slovenian); unmistakable Cyrillic and CJK text is handled before
Latin-language scoring.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

CONFIDENCE = {"high", "medium", "low"}

_SECTION_RE = re.compile(r"(?m)^##[ \t]+题面[ \t]*$\n?")
_NEXT_SECTION_RE = re.compile(r"(?m)^##[ \t]+[^\n]+$")
_FENCED_CODE_RE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$")
_INDENTED_CODE_RE = re.compile(r"(?m)^(?: {4}|\t).*$")
_HTML_IMG_RE = re.compile(r"(?is)<img\b[^>]*>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\n)]*(?:\([^\n)]*\)[^\n)]*)*\)")
_REFERENCE_IMAGE_RE = re.compile(r"(?m)^\s*!\[[^\]]*\]\s*\[[^\]]*\]\s*$")
_REFERENCE_DEF_RE = re.compile(r"(?m)^\s*\[[^\]]+\]:\s*\S+(?:\s+.*)?$")
_LATEX_ENV_RE = re.compile(
    r"(?is)\\begin\{(?:equation\*?|align\*?|aligned|gather\*?|multline\*?|"
    r"displaymath|math|array|cases|matrix|[pbvBV]matrix)\}.*?"
    r"\\end\{(?:equation\*?|align\*?|aligned|gather\*?|multline\*?|"
    r"displaymath|math|array|cases|matrix|[pbvBV]matrix)\}"
)
_DISPLAY_MATH_RE = re.compile(r"(?s)\$\$.*?\$\$|\\\[.*?\\\]")
_INLINE_MATH_RE = re.compile(r"(?s)(?<!\\)\$(?!\$).*?(?<!\\)\$|\\\(.*?\\\)")
_INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
_INCLUDE_GRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}")
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_LANGUAGE_NAMES = {
    "en": "en", "eng": "en", "english": "en", "anglais": "en", "ingles": "en",
    "es": "es", "spa": "es", "spanish": "es", "espanol": "es", "castellano": "es",
    "pt": "pt", "por": "pt", "portuguese": "pt", "portugues": "pt",
    "fr": "fr", "fra": "fr", "fre": "fr", "french": "fr", "francais": "fr",
    "sl": "sl", "slv": "sl", "slovenian": "sl", "slovene": "sl", "slovenscina": "sl",
    "ru": "ru", "rus": "ru", "russian": "ru", "russkii": "ru",
    "zh": "zh", "zho": "zh", "chi": "zh", "chinese": "zh", "mandarin": "zh",
    "de": "de", "deu": "de", "ger": "de", "german": "de", "deutsch": "de",
    "it": "it", "ita": "it", "italian": "it", "italiano": "it",
    "ar": "ar", "ara": "ar", "arabic": "ar",
}

# Function words are more stable than topic vocabulary.  Prefix-only words
# (notably "Problem" and "Solution") are intentionally absent.
_FEATURE_WORDS = {
    "en": {
        "a", "all", "an", "and", "are", "be", "by", "determine", "each", "every",
        "find", "for", "from", "given", "has", "if", "in", "integers", "is", "let",
        "numbers", "of", "on", "positive", "prove", "such", "that", "the", "then",
        "there", "to", "which", "with",
    },
    "es": {
        "cada", "con", "cual", "de", "del", "demostrar", "determine", "donde", "enteros",
        "es", "existe", "hallar", "las", "los", "numeros", "para", "por", "positivos",
        "que", "sea", "sean", "si", "tal", "todos", "una", "y",
    },
    "pt": {
        "cada", "com", "de", "determinar", "demonstrar", "dos", "e", "em", "existe",
        "inteiros", "numeros", "onde", "os", "para", "por", "positivos", "que", "se",
        "seja", "sejam", "sao", "tal", "todos", "uma",
    },
    "fr": {
        "avec", "dans", "de", "des", "determiner", "entiers", "est", "existe", "les",
        "montrer", "nombres", "ou", "par", "pour", "positifs", "que", "quel", "si", "soit",
        "soient", "tel", "tous", "trouver", "un", "une",
    },
    "sl": {
        "bo", "cela", "ce", "da", "doloci", "dokazi", "in", "je", "ki", "lahko", "naj",
        "obstaja", "poisci", "pozitivna", "stevila", "tako", "ter", "vsak", "za",
    },
}

_DISTINCTIVE_CHARS = {
    "es": set("ñ¿¡"),
    "pt": set("ãõç"),
    "fr": set("œæëïÿ"),
    "sl": set("čšž"),
}

_DISTINCTIVE_PHRASES = {
    "en": ("such that", "positive integers", "prove that", "let us"),
    "es": ("tal que", "numeros enteros", "números enteros", "para todo"),
    "pt": ("tais que", "tal que", "numeros inteiros", "números inteiros"),
    "fr": ("tels que", "tel que", "nombres entiers", "pour tout"),
    "sl": ("tako da", "cela stevila", "cela števila", "naj bo"),
}


def _ascii_fold(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(ch)
    )


def _problem_section(body: str) -> str:
    """Return only ``## 题面`` when a full problem Markdown file is supplied."""
    match = _SECTION_RE.search(body)
    if not match:
        return body
    text = body[match.end():]
    next_section = _NEXT_SECTION_RE.search(text)
    return text[:next_section.start()] if next_section else text


def _strip_noise(body: str) -> str:
    """Remove material that must not contribute source-language evidence."""
    text = _problem_section(body or "")
    for pattern in (
        _FENCED_CODE_RE,
        _INDENTED_CODE_RE,
        _HTML_IMG_RE,
        _MARKDOWN_IMAGE_RE,
        _REFERENCE_IMAGE_RE,
        _REFERENCE_DEF_RE,
        _LATEX_ENV_RE,
        _DISPLAY_MATH_RE,
        _INLINE_MATH_RE,
        _INLINE_CODE_RE,
        _INCLUDE_GRAPHICS_RE,
        _LATEX_COMMAND_RE,
        _HTML_TAG_RE,
    ):
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalise_meta_language(meta: dict) -> tuple[set[str], bool]:
    """Return known ISO codes and whether the field was explicitly multilingual."""
    raw = meta.get("language") if isinstance(meta, dict) else None
    if raw is None:
        return set(), False
    if isinstance(raw, (list, tuple, set)):
        parts = [str(part) for part in raw]
        multilingual = len(parts) > 1
    else:
        value = str(raw).strip()
        if not value:
            return set(), False
        parts = re.split(r"\s*(?:;|,|/|\||\+|\band\b|\by\b)\s*", value, flags=re.I)
        multilingual = len([part for part in parts if part.strip()]) > 1
    codes = set()
    for part in parts:
        key = re.sub(r"[^a-z]+", "", _ascii_fold(part))
        if key in _LANGUAGE_NAMES:
            codes.add(_LANGUAGE_NAMES[key])
    return codes, multilingual or len(codes) > 1


def _enough_language_text(text: str) -> bool:
    letters = _WORD_RE.findall(text)
    letter_count = sum(len(word) for word in letters)
    # CJK languages do not normally use spaces, so four ideographs already
    # carry more linguistic signal than a four-letter Latin fragment.
    return letter_count >= 12 or len(_CJK_RE.findall(text)) >= 4


def _script_evidence(text: str) -> tuple[str, str] | None:
    cjk = len(_CJK_RE.findall(text))
    cyrillic = len(_CYRILLIC_RE.findall(text))
    if cjk >= 4 and cjk >= cyrillic * 2:
        return "zh", "high"
    if cyrillic >= 6 and cyrillic >= cjk * 2:
        return "ru", "high"
    return None


def _latin_evidence(text: str) -> tuple[str, str]:
    folded = _ascii_fold(text)
    words = _WORD_RE.findall(folded)
    counts = Counter(words)
    scores: dict[str, int] = {}
    for lang, features in _FEATURE_WORDS.items():
        score = sum(min(counts[word], 3) for word in features)
        score += 2 * sum(folded.count(_ascii_fold(phrase)) for phrase in _DISTINCTIVE_PHRASES[lang])
        score += 2 * sum(text.casefold().count(ch) for ch in _DISTINCTIVE_CHARS.get(lang, set()))
        scores[lang] = score

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (winner, best), (_, second) = ranked[:2]
    margin = best - second
    # English passthrough needs several independent function-word hits and a
    # clear lead.  Ambiguous Latin prose becomes und, never optimistic English.
    if best < 3 or margin < 2:
        return "und", "low"
    if best >= 7 and margin >= 4:
        return winner, "high"
    return winner, "medium"


def _text_evidence(text: str) -> tuple[str, str]:
    script = _script_evidence(text)
    return script if script else _latin_evidence(text)


def detect_source_lang(body: str, meta: dict) -> tuple[str, str]:
    """Detect a problem's source language and confidence.

    ``body`` may be either raw statement Markdown or a complete problem file;
    in the latter case only the ``## 题面`` section is considered.  The return
    confidence is one of ``high``, ``medium`` or ``low``.
    """
    clean = _strip_noise(body)
    if not _enough_language_text(clean):
        return "und", "low"

    text_lang, text_conf = _text_evidence(clean)
    meta_langs, multilingual = _normalise_meta_language(meta)

    # An explicit multilingual label containing English means the source
    # already contains an English version, but can never justify high confidence.
    if multilingual and "en" in meta_langs:
        if text_lang not in {"und", "en"}:
            return text_lang, "low"
        return "en", "medium" if text_lang == "en" else "low"

    if not meta_langs:
        return text_lang, text_conf

    meta_lang = next(iter(meta_langs)) if len(meta_langs) == 1 else None
    if meta_lang is None:
        if text_lang in meta_langs and text_lang != "und":
            return text_lang, "low"
        non_english = sorted(lang for lang in meta_langs if lang != "en")
        return (non_english[0], "low") if non_english else ("und", "low")

    if text_lang == "und":
        # The index field is too dirty to authorize English passthrough alone.
        # A non-English label is still useful as a safe routing hint, but low
        # confidence accurately records that there was no matching body signal.
        return ("und", "low") if meta_lang == "en" else (meta_lang, "low")
    if text_lang == meta_lang:
        return text_lang, "high" if text_conf in {"high", "medium"} else "medium"

    # Conflicting evidence is always low-confidence and biased away from en.
    if text_lang == "en":
        return meta_lang, "low"
    return text_lang, "low"


__all__ = ["detect_source_lang"]
