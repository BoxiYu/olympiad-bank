"""Conservative source-language detection for MathNet problem statements.

The detector deliberately optimizes against false English results: ``en/high``
is used by the translation pipeline as a passthrough decision, so an uncertain
non-English statement must cost one translation call rather than silently pass
through untranslated.

Only the Python standard library is used.  The supported Latin-script language
features reflect the sizeable MathNet groups (English, German, Italian,
Spanish, Portuguese, French, Dutch, Romanian and Slovenian).  Script evidence
is used conservatively: shared Han and Cyrillic ranges are not treated as proof
of one particular language without matching function words or distinctive
letters.
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
_GREEK_WORD_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]{2,}")
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")

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
    "nl": "nl", "nld": "nl", "dut": "nl", "dutch": "nl", "nederlands": "nl",
    "ro": "ro", "ron": "ro", "rum": "ro", "romanian": "ro", "romana": "ro",
    "el": "el", "ell": "el", "gre": "el", "greek": "el", "ellinika": "el",
    "mn": "mn", "mon": "mn", "mongolian": "mn", "mongol": "mn",
    "mk": "mk", "mkd": "mk", "macedonian": "mk", "makedonski": "mk",
    "ar": "ar", "ara": "ar", "arabic": "ar",
}

# Function words are more stable than topic vocabulary.  Prefix-only words
# (notably "Problem" and "Solution") are intentionally absent.
_FEATURE_WORDS = {
    "en": {
        "a", "all", "an", "and", "are", "be", "by", "determine", "each", "every",
        "equality", "equals", "find", "for", "from", "given", "has", "holds", "if", "in",
        "integers", "is", "let", "numbers", "of", "on", "only", "positive", "prove", "such",
        "that", "the", "then", "there", "to", "which", "with",
    },
    "es": {
        "cada", "con", "cual", "de", "del", "demostrar", "determine", "donde", "enteros",
        "es", "existe", "hallar", "las", "los", "numeros", "para", "por", "positivos",
        "que", "sea", "sean", "si", "tal", "todos", "una", "y",
    },
    "pt": {
        "ao", "aos", "as", "cada", "com", "da", "das", "de", "determinar", "demonstrar",
        "do", "dos", "e", "em", "existe", "inteiros", "nas", "no", "numeros", "o", "onde",
        "os", "para", "por", "positivos", "quanto", "que", "se", "seja", "sejam", "sao",
        "tal", "todos", "um", "uma",
    },
    "fr": {
        "avec", "dans", "de", "des", "determiner", "entiers", "est", "existe", "les",
        "montrer", "nombres", "ou", "par", "pour", "positifs", "que", "quel", "si", "soit",
        "soient", "tel", "tous", "trouver", "un", "une",
    },
    "de": {
        "alle", "auch", "das", "dass", "denen", "der", "die", "ein", "eine", "einem",
        "einen", "einer", "es", "folgende", "fur", "in", "ist", "man", "mit", "oder",
        "sei", "seien", "sind", "so", "und", "was", "wenn", "zeige",
    },
    "it": {
        "a", "ad", "al", "alla", "altre", "che", "come", "con", "da", "dal", "dei",
        "del", "della", "delle", "di", "e", "gli", "i", "il", "in", "la", "le", "loro",
        "nei", "nel", "o", "per", "possiamo", "sia", "siano", "sono", "tra", "tutti",
        "una", "uno",
    },
    # ``en/in/is/of`` are valid Dutch words but are deliberately omitted: in
    # short English mathematical sections they erase the English score margin
    # and produced seven measured false positives in the frozen corpus.
    "nl": {
        "als", "alle", "bepaal", "bewijs", "dat", "de", "deze", "dit", "door",
        "een", "elk", "er", "gegeven", "gehele", "getal", "getallen", "heeft",
        "het", "met", "niet", "op", "positieve", "voor", "waar",
        "waarvoor", "wordt", "zijn", "zodat",
    },
    "ro": {
        "aratati", "avem", "ca", "cand", "care", "cu", "daca", "demonstrati",
        "determinati", "este", "exista", "fie", "fiecare", "gasiti", "numar", "numarul",
        "numere", "pentru", "pozitive", "sunt", "toate", "toti", "un", "unei",
    },
    "sl": {
        "bo", "cela", "ce", "da", "doloci", "dokazi", "in", "je", "ki", "lahko", "naj",
        "obstaja", "poisci", "pozitivna", "stevila", "tako", "ter", "vsak", "za",
    },
}

# A single shared function word (for example French ``on`` or Dutch ``is``)
# must never authorize zero-call English passthrough.  The tiny-text rescue is
# limited to unambiguous olympiad prompt markers already scored as English.
_EN_TINY_MARKERS = {"determine", "find", "let", "prove"}

_DISTINCTIVE_CHARS = {
    "es": set("ñ¿¡"),
    "pt": set("ãõç"),
    "fr": set("œæëïÿ"),
    "de": set("äöüß"),
    "it": set("ìòù"),
    "nl": set("ĳ"),
    # ``â`` is deliberately absent: Portuguese uses it in common geometry
    # words such as "ângulo" and "triângulo".  Counting it as Romanian was the
    # cause of the Portuguese and bilingual false positives measured in the
    # Brazil/JBMO groups for CXB-520.
    "ro": set("ăîșț"),
    "sl": set("čšž"),
}

_DISTINCTIVE_PHRASES = {
    "en": ("such that", "positive integers", "prove that", "let us"),
    "es": ("tal que", "numeros enteros", "números enteros", "para todo"),
    "pt": ("tais que", "tal que", "numeros inteiros", "números inteiros", "quanto mede"),
    "fr": ("tels que", "tel que", "nombres entiers", "pour tout"),
    "de": ("so dass", "in denen", "zeige dass", "fur alle", "für alle"),
    "it": ("in cui", "tale che", "in modo che", "uno dei", "una delle"),
    "nl": ("zodat", "voor alle", "gehele getallen", "bepaal alle", "bewijs dat"),
    "ro": ("astfel incat", "pentru toate", "numere intregi", "sa se arate"),
    "sl": ("tako da", "cela stevila", "cela števila", "naj bo"),
}

_ZH_MARKERS = (
    "所有", "满足", "方程", "正整数", "证明", "条件", "每个", "答案",
    "设", "其中", "存在", "性质", "下列", "所得", "必定",
)
_RU_FEATURE_WORDS = {
    "все", "данное", "для", "докажите", "каждое", "которых", "натуральные",
    "найдите", "положительных", "решения", "свойством", "существует", "указанным",
    "уравнение", "целое", "целых", "чисел", "число", "что",
}
_MN_FEATURE_WORDS = {
    "ав", "бүх", "бүхэл", "гэж", "натурал", "нотол", "олго", "ол", "тоо",
    "тоонууд", "шийд", "шийдийг", "эерэг",
}
_MK_FEATURE_WORDS = {
    "број", "броеви", "бројот", "го", "докажи", "докажете", "за", "кои",
    "најди", "најдете", "позитивни", "сите", "цели", "дека", "земе", "земи",
}
_MN_DISTINCTIVE_RE = re.compile(r"[ӨөҮү]")
# Serbian shares Ј/Љ/Њ/Џ with Macedonian.  Only Ѓ/Ѕ/Ќ are safe positive
# Macedonian evidence; otherwise shared Cyrillic prose remains und/low.
_MK_DISTINCTIVE_RE = re.compile(r"[ЃѓЅѕЌќ]")


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
    # East Asian languages do not normally use spaces, so four characters already
    # carry more linguistic signal than a four-letter Latin fragment.
    east_asian = (
        len(_HAN_RE.findall(text))
        + len(_KANA_RE.findall(text))
        + len(_HANGUL_RE.findall(text))
    )
    # Short olympiad prompts such as "Find x." still contain dense language
    # evidence.  The scorer below must establish that density; this gate only
    # rejects empty/symbol-only fragments before scoring.
    return letter_count >= 4 or east_asian >= 4


def _script_evidence(text: str) -> tuple[str, str] | None:
    han = len(_HAN_RE.findall(text))
    kana = len(_KANA_RE.findall(text))
    hangul = len(_HANGUL_RE.findall(text))
    cyrillic = len(_CYRILLIC_RE.findall(text))
    greek_words = _GREEK_WORD_RE.findall(text)
    if kana >= 2 or hangul >= 2:
        # Japanese and Korean are safely non-English, but not currently among
        # the detector's supported output languages.
        return "und", "low"
    if han >= 4 and han >= cyrillic * 2:
        marker_hits = sum(marker in text for marker in _ZH_MARKERS)
        return ("zh", "high") if marker_hits >= 2 else ("und", "low")
    # Greek mathematical variables embedded in English are often bare
    # one-letter symbols.  Require multiple Greek words, not merely a count of
    # Greek code points, before identifying prose as Greek.
    if len(greek_words) >= 2 and sum(map(len, greek_words)) >= 6:
        return "el", "high"
    if cyrillic >= 6 and cyrillic >= han * 2:
        words = Counter(word.casefold() for word in _WORD_RE.findall(text))
        mn_hits = sum(min(words[word], 3) for word in _MN_FEATURE_WORDS)
        mk_hits = sum(min(words[word], 3) for word in _MK_FEATURE_WORDS)
        russian_hits = sum(min(words[word], 3) for word in _RU_FEATURE_WORDS)
        if mn_hits >= 2 and _MN_DISTINCTIVE_RE.search(text):
            return "mn", "high"
        if mk_hits >= 2 and _MK_DISTINCTIVE_RE.search(text):
            return "mk", "high"
        if russian_hits >= 3:
            return "ru", "high"
        return "und", "low"
    return None


def _latin_evidence(text: str) -> tuple[str, str]:
    folded = _ascii_fold(text)
    words = _WORD_RE.findall(folded)
    counts = Counter(words)
    scores: dict[str, int] = {}
    feature_hits: dict[str, int] = {}
    for lang, features in _FEATURE_WORDS.items():
        hits = sum(min(counts[word], 3) for word in features)
        feature_hits[lang] = hits
        score = hits
        score += 2 * sum(folded.count(_ascii_fold(phrase)) for phrase in _DISTINCTIVE_PHRASES[lang])
        score += 2 * sum(text.casefold().count(ch) for ch in _DISTINCTIVE_CHARS.get(lang, set()))
        scores[lang] = score

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (winner, best), (_, second) = ranked[:2]
    margin = best - second
    density = feature_hits[winner] / max(len(words), 1)
    # English passthrough needs several independent function-word hits and a
    # clear lead.  Ambiguous Latin prose becomes und, never optimistic English.
    if best < 3 or margin < 2:
        # A one-word mathematical imperative plus symbols is legitimate prose
        # after math stripping (for example "Find $x$.").  Only English gets
        # this tiny-text exception, and only with a clear lead and at least
        # half of the remaining words drawn from its function-word set.
        # Every marker is also an English feature, so its presence already
        # implies at least one feature hit; a separate ``best >= 1`` is dead.
        if (winner == "en" and len(words) <= 3
                and any(word in _EN_TINY_MARKERS for word in words)
                and margin >= 1 and density >= 0.5):
            return "en", "high"
        return "und", "low"
    # Long prose still needs the established absolute evidence threshold.
    # Short mathematical prompts can instead earn high confidence from dense,
    # independent function words and a clear lead over every other language.
    density_threshold = 0.4 if winner == "en" else 0.5
    # Reaching this point already implies ``margin >= 2`` from the guard above.
    if density >= density_threshold:
        return winner, "high"
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
    if text_lang == "und" and sum(len(word) for word in _WORD_RE.findall(clean)) < 12:
        # A tiny uninformative prefix must not become a language claim from
        # metadata alone.  Dense prompts such as "Find x." have already earned
        # explicit text evidence above and therefore do not take this branch.
        return "und", "low"
    meta_langs, multilingual = _normalise_meta_language(meta)

    # An explicit multilingual label containing English means the source
    # already contains an English version, but can never justify high confidence.
    if multilingual and "en" in meta_langs:
        if text_lang == "und":
            return "und", "low"
        if text_lang != "en":
            return text_lang, "low"
        return "en", "medium"

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


def should_passthrough(source_lang: str, confidence: str, target_lang: str) -> bool:
    """Return whether the translation contract permits ``mode=passthrough``.

    This is deliberately an exact comparison: metadata variants such as
    ``en-US`` and every confidence below ``high`` must take the translation
    path.  All producers and validators share this single policy gate.
    """
    return source_lang == "en" and confidence == "high" and target_lang == "en"


__all__ = ["detect_source_lang", "should_passthrough"]
