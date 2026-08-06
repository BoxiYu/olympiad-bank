#!/usr/bin/env python3
"""MathNet 三语译文批次的导出与写回骨架。

本脚本只读 ``mathnet-full/**/index.md``，绝不修改原文。译文契约的唯一正本见
``docs/译文契约-mathnet-full.md``。

常用命令：
  uv run python scripts/mathnet_translate.py export --out /tmp/batch.jsonl
  uv run python scripts/mathnet_translate.py apply --in /tmp/translations.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from source_lang import detect_source_lang, should_passthrough
from translation_fidelity import verify_translation

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "mathnet-full"
TARGET_LANGS = ("en", "zh")

SECTION_RE = re.compile(r"(?m)^## (?P<title>题面|解法(?: \d+)?|最终答案)[ \t]*\r?\n")
PLACEHOLDER_RE = re.compile(r"\{\{MNT_\d{4}\}\}")
PROTECTED_RE = re.compile(
    r"(?P<display>(?<!\\)\$\$.*?(?<!\\)\$\$)"
    r"|(?P<environment>\\begin\{(?P<environment_name>[^{}\s]+)\}.*?"
    r"\\end\{(?P=environment_name)\})"
    r"|(?P<image>!\[[^\]\n]*\]\(attached_image_\d+\.png(?:\s+\"[^\"]*\")?\))"
    r"|(?P<code>`[^`\n]*`)"
    r"|(?P<inline>(?<!\\)(?<!\$)\$(?!\$).*?(?<!\\)\$(?!\$))",
    re.DOTALL,
)
CHOICE_RE = re.compile(r"\(?[A-E]\)?[.)]?", re.IGNORECASE)
SYMBOL_WORDS = {
    "mod", "gcd", "lcm", "sqrt", "sin", "cos", "tan", "log", "ln", "min", "max", "inf", "sup"
}


class TranslateError(ValueError):
    """一条题目记录无法安全导出或写回。"""


@dataclass(frozen=True)
class Section:
    title: str
    unit_id: str
    heading: str
    leading: str
    core: str
    trailing: str


@dataclass(frozen=True)
class Document:
    prefix: str
    sections: tuple[Section, ...]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_wrapping_newlines(body: str) -> tuple[str, str, str]:
    start = len(body) - len(body.lstrip("\r\n"))
    end = len(body.rstrip("\r\n"))
    if end < start:
        end = start
    return body[:start], body[start:end], body[end:]


def parse_document(text: str, path: str = "index.md") -> Document:
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        raise TranslateError(f"{path}: 未找到题面/解法/最终答案骨架")

    sections: list[Section] = []
    solution_seq = 0
    seen: set[str] = set()
    for index, match in enumerate(matches):
        title = match.group("title")
        if title == "题面":
            unit_id = "statement"
        elif title == "最终答案":
            unit_id = "final_answer"
        else:
            solution_seq += 1
            number = title.removeprefix("解法 ") if title != "解法" else str(solution_seq)
            unit_id = f"solution_{number}"
        if unit_id in seen:
            raise TranslateError(f"{path}: 重复小节 {title}")
        seen.add(unit_id)

        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():body_end]
        leading, core, trailing = split_wrapping_newlines(body)
        sections.append(Section(title, unit_id, match.group(0), leading, core, trailing))

    required = {"statement", "final_answer"}
    missing = sorted(required - seen)
    if missing:
        raise TranslateError(f"{path}: 缺少必要单元 {', '.join(missing)}")
    return Document(text[:matches[0].start()], tuple(sections))


def is_symbolic_answer(text: str) -> bool:
    """保守识别无需翻译的纯符号答案，包括 MCQ 选项与裸同余式。"""
    value = text.strip()
    if not value:
        return True
    if CHOICE_RE.fullmatch(value):
        return True
    outside_protected = PROTECTED_RE.sub("", value).strip()
    if not outside_protected:
        return True
    if re.search(r"[\u3400-\u9fff\u3040-\u30ff\u0400-\u04ff]", outside_protected):
        return False
    words = re.findall(r"[A-Za-z]+", outside_protected)
    return bool(re.search(r"[=≡<>≤≥+\-*/^%]|\d", outside_protected)) and all(
        len(word) == 1 or word.lower() in SYMBOL_WORDS for word in words
    )


def protect_text(text: str, *, whole: bool = False) -> tuple[str, dict[str, str]]:
    if not text:
        return text, {}
    if whole:
        return "{{MNT_0001}}", {"{{MNT_0001}}": text}

    protected: dict[str, str] = {}
    parts: list[str] = []
    cursor = 0
    placeholder_number = 1
    for match in PROTECTED_RE.finditer(text):
        parts.append(text[cursor:match.start()])
        while True:
            placeholder = f"{{{{MNT_{placeholder_number:04d}}}}}"
            placeholder_number += 1
            if placeholder not in text:
                break
        parts.append(placeholder)
        protected[placeholder] = match.group(0)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts), protected


def export_units(document: Document) -> list[dict[str, Any]]:
    units = []
    for section in document.sections:
        whole = section.unit_id == "final_answer" and is_symbolic_answer(section.core)
        source, protected = protect_text(section.core, whole=whole)
        units.append({
            "id": section.unit_id,
            "section": section.title,
            "source": source,
            "protected": protected,
            "translatable": bool(section.core) and not whole,
        })
    return units


def unit_index(document: Document) -> dict[str, tuple[Section, dict[str, Any]]]:
    exported = export_units(document)
    return {unit["id"]: (section, unit) for section, unit in zip(document.sections, exported)}


def restore_translation(text: str, unit: dict[str, Any]) -> str:
    if not isinstance(text, str):
        raise TranslateError(f"单元 {unit['id']} 的译文不是字符串")
    protected = unit["protected"]
    found = PLACEHOLDER_RE.findall(text)
    if found != list(protected):
        raise TranslateError(f"单元 {unit['id']} 的不可译占位缺失、重复或被篡改")
    if not unit["translatable"] and text != unit["source"]:
        raise TranslateError(f"单元 {unit['id']} 是纯符号/空单元，内容必须逐字保留")
    restored = text
    for placeholder, original in protected.items():
        restored = restored.replace(placeholder, original)
    restored = restored.strip("\r\n")
    if re.search(r"(?m)^## (?:题面|解法(?: \d+)?|最终答案)[ \t]*$", restored):
        raise TranslateError(f"单元 {unit['id']} 不得注入结构小节标题")
    return restored


def render_variant(document: Document, translations: dict[str, Any]) -> str:
    expected = unit_index(document)
    missing = sorted(set(expected) - set(translations))
    extra = sorted(set(translations) - set(expected))
    if missing:
        raise TranslateError(f"缺少译文单元: {', '.join(missing)}")
    if extra:
        raise TranslateError(f"出现未知译文单元: {', '.join(extra)}")

    chunks = [document.prefix]
    for unit_id, (section, unit) in expected.items():
        restored = restore_translation(translations[unit_id], unit)
        chunks.extend((section.heading, section.leading, restored, section.trailing))
    return "".join(chunks)


def atomic_write(path: Path, data: bytes) -> bool:
    """同目录临时文件 + fsync + replace；内容相同时不改 mtime。"""
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return True


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslateError(f"无法读取 JSON {path}: {exc}") from exc


def load_language_map(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = load_json(path)
    if not isinstance(value, dict):
        raise TranslateError("--source-lang-map 必须是 JSON 对象")
    return value


def source_language(mathnet_id: str, language_map: dict[str, Any], fallback: str,
                    fallback_confidence: str) -> tuple[str, str]:
    value = language_map.get(mathnet_id, fallback)
    if isinstance(value, str):
        result = value, fallback_confidence
    elif isinstance(value, dict) and isinstance(value.get("source_lang"), str):
        result = value["source_lang"], value.get("source_lang_confidence", fallback_confidence)
    else:
        raise TranslateError(f"{mathnet_id}: source language 映射必须是字符串或带 source_lang 的对象")
    if not all(isinstance(item, str) and item for item in result):
        raise TranslateError(f"{mathnet_id}: source_lang 与 source_lang_confidence 必须是非空字符串")
    return result


def iter_source_files(root: Path) -> list[Path]:
    scan_root = root / "by-topic" if (root / "by-topic").is_dir() else root
    found: list[Path] = []
    for directory, dirs, files in os.walk(scan_root, followlinks=False):
        dirs.sort()
        if "index.md" in files:
            found.append(Path(directory) / "index.md")
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def read_translation_state(question_dir: Path) -> dict[str, Any] | None:
    path = question_dir / "translation.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def variant_current(question_dir: Path, state: dict[str, Any] | None, mathnet_id: str,
                    source_sha256: str, lang: str) -> bool:
    if not state or state.get("mathnet_id") != mathnet_id or state.get("source_sha256") != source_sha256:
        return False
    variant = (state.get("variants") or {}).get(lang)
    target = question_dir / f"index.{lang}.md"
    if not isinstance(variant, dict) or variant.get("mode") not in {"passthrough", "translated"}:
        return False
    if not target.is_file():
        return False
    return variant.get("sha256") == sha256_bytes(target.read_bytes())


def export_records(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise TranslateError(f"语料目录不存在: {root}")
    language_map = load_language_map(Path(args.source_lang_map) if args.source_lang_map else None)
    selected = set(args.only or [])
    rows: list[dict[str, Any]] = []
    found_ids: set[str] = set()
    skipped_current = 0
    skipped_unselected = 0
    truncated = False

    if args.limit == 0:
        atomic_write(Path(args.out), b"")
        print(f"export: 0 题 -> {args.out}")
        print("export: --limit 0 显式截断全部覆盖")
        return 0

    for path in iter_source_files(root):
        mathnet_id = path.parent.name
        if selected and mathnet_id not in selected:
            skipped_unselected += 1
            continue
        found_ids.add(mathnet_id)
        source_bytes = path.read_bytes()
        try:
            text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TranslateError(f"{path}: index.md 不是 UTF-8") from exc
        source_sha = sha256_bytes(source_bytes)
        lang, confidence = source_language(
            mathnet_id, language_map, args.source_lang, args.source_lang_confidence
        )
        state = read_translation_state(path.parent)
        targets = [
            target for target in TARGET_LANGS
            if not variant_current(path.parent, state, mathnet_id, source_sha, target)
        ]
        if not targets:
            skipped_current += 1
            continue
        if args.limit is not None and len(rows) >= args.limit:
            truncated = True
            continue
        relative = path.relative_to(root).as_posix()
        rows.append({
            "mathnet_id": mathnet_id,
            "path": relative,
            "source_sha256": source_sha,
            "source_lang": lang,
            "source_lang_confidence": confidence,
            "units": export_units(parse_document(text, relative)),
            "targets": targets,
            "target_modes": {
                target: "passthrough" if should_passthrough(lang, confidence, target)
                else "translated"
                for target in targets
            },
        })
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows
    )
    atomic_write(Path(args.out), payload)
    print(f"export: {len(rows)} 题 -> {args.out}")
    if skipped_current:
        print(f"export: 显式跳过 {skipped_current} 题（译文有效且 source_sha256 未变）")
    if selected:
        missing = sorted(selected - found_ids)
        print(f"export: --only 筛选跳过 {skipped_unselected} 题")
        if missing:
            print(f"export: --only 未找到 {len(missing)} 个 id：{', '.join(missing)}")
    if truncated:
        print(f"export: --limit {args.limit} 已截断本次覆盖")
    return 0


def validate_generated_at(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise TranslateError(f"{context}: generated_at 必须是带时区的 ISO8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TranslateError(f"{context}: generated_at 不是 ISO8601") from exc
    if parsed.tzinfo is None:
        raise TranslateError(f"{context}: generated_at 必须带时区")
    return value


def safe_record_path(root: Path, record: dict[str, Any]) -> Path:
    relative = record.get("path")
    if not isinstance(relative, str) or not relative:
        raise TranslateError("path 缺失")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise TranslateError(f"path 越出语料目录: {relative}") from exc
    if candidate.name != "index.md" or not candidate.is_file():
        raise TranslateError(f"原文不存在或不是 index.md: {relative}")
    return candidate


def kept_variants(question_dir: Path, state: dict[str, Any] | None, mathnet_id: str,
                  source_sha: str) -> dict[str, Any]:
    if not state or state.get("mathnet_id") != mathnet_id or state.get("source_sha256") != source_sha:
        return {}
    variants = state.get("variants")
    if not isinstance(variants, dict):
        return {}
    kept = {}
    for lang in TARGET_LANGS:
        variant = variants.get(lang)
        if variant_current(question_dir, state, mathnet_id, source_sha, lang) or (
            isinstance(variant, dict) and variant.get("mode") == "failed"
        ):
            kept[lang] = variant
    return kept


def prepare_variant(lang: str, payload: Any, source_lang: str, confidence: str,
                    source_bytes: bytes, document: Document) -> tuple[bytes | None, dict[str, Any]]:
    context = f"variants.{lang}"
    if lang not in TARGET_LANGS or not isinstance(payload, dict):
        raise TranslateError(f"{context}: 目标语言或记录格式非法")
    mode = payload.get("mode")
    if mode not in {"passthrough", "translated", "failed"}:
        raise TranslateError(f"{context}: mode 必须是 passthrough/translated/failed")
    generated_at = validate_generated_at(payload.get("generated_at"), context)

    if mode == "failed":
        error = payload.get("error")
        if not isinstance(error, str) or not error.strip():
            raise TranslateError(f"{context}: failed 必须带非空 error")
        return None, {
            "mode": "failed",
            "model": payload.get("model"),
            "generated_at": generated_at,
            "error": error,
        }

    if mode == "passthrough":
        if not should_passthrough(source_lang, confidence, lang):
            raise TranslateError(
                f"{context}: 仅 source_lang=en 且 source_lang_confidence=high "
                "可 passthrough 为 en"
            )
        if payload.get("model") not in {None, ""}:
            raise TranslateError(f"{context}: passthrough 不得记录模型")
        target_bytes = source_bytes
        return target_bytes, {
            "mode": "passthrough",
            "model": None,
            "generated_at": generated_at,
            "sha256": sha256_bytes(target_bytes),
        }

    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise TranslateError(f"{context}: translated 必须带模型标识")
    translations = payload.get("units")
    if not isinstance(translations, dict):
        raise TranslateError(f"{context}: translated 必须带 units 对象")
    target_bytes = render_variant(document, translations).encode()
    return target_bytes, {
        "mode": "translated",
        "model": model,
        "generated_at": generated_at,
        "sha256": sha256_bytes(target_bytes),
    }


def apply_record(root: Path, record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise TranslateError("JSONL 行必须是对象")
    path = safe_record_path(root, record)
    mathnet_id = record.get("mathnet_id")
    if not isinstance(mathnet_id, str) or mathnet_id != path.parent.name:
        raise TranslateError(f"mathnet_id 与目录名不一致: {mathnet_id!r} != {path.parent.name!r}")

    source_bytes = path.read_bytes()
    source_sha = sha256_bytes(source_bytes)
    if record.get("source_sha256") != source_sha:
        raise TranslateError("source_sha256 与当前 index.md 不一致，拒绝写回过期译文")
    source_lang = record.get("source_lang")
    confidence = record.get("source_lang_confidence", "unknown")
    if not isinstance(source_lang, str) or not source_lang:
        raise TranslateError("source_lang 缺失")
    if not isinstance(confidence, str) or not confidence:
        raise TranslateError("source_lang_confidence 必须是非空字符串")
    variants = record.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise TranslateError("variants 必须是非空对象")

    try:
        document = parse_document(source_bytes.decode("utf-8"), record["path"])
    except UnicodeDecodeError as exc:
        raise TranslateError("index.md 不是 UTF-8") from exc

    # 先完整校验并在内存中渲染；任何单元缺失时，题目目录不会发生写入。
    prepared: dict[str, bytes | None] = {}
    metadata_updates: dict[str, Any] = {}
    for lang, payload in variants.items():
        target_bytes, metadata = prepare_variant(
            lang, payload, source_lang, confidence, source_bytes, document
        )
        prepared[lang] = target_bytes
        metadata_updates[lang] = metadata

    old_state = read_translation_state(path.parent)
    metadata_variants = kept_variants(path.parent, old_state, mathnet_id, source_sha)
    metadata_variants.update(metadata_updates)
    state = {
        "mathnet_id": mathnet_id,
        "source_sha256": source_sha,
        "source_lang": source_lang,
        "source_lang_confidence": confidence,
        "variants": metadata_variants,
    }

    for lang, target_bytes in prepared.items():
        if target_bytes is not None:
            atomic_write(path.parent / f"index.{lang}.md", target_bytes)
    atomic_write(path.parent / "translation.json", json_bytes(state))


def load_jsonl(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    failures: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TranslateError(f"无法读取输入 {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("不是 JSON 对象")
            rows.append((line_number, value))
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append({"line": line_number, "mathnet_id": None, "error": f"JSON 无法解析: {exc}"})
    return rows, failures


def apply_records(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise TranslateError(f"语料目录不存在: {root}")
    input_path = Path(args.input)
    rows, failures = load_jsonl(input_path)
    seen: set[str] = set()
    applied = 0
    for line_number, record in rows:
        mathnet_id = record.get("mathnet_id")
        if isinstance(mathnet_id, str) and mathnet_id in seen:
            failures.append({"line": line_number, "mathnet_id": mathnet_id, "error": "输入中 mathnet_id 重复"})
            continue
        if isinstance(mathnet_id, str):
            seen.add(mathnet_id)
        try:
            apply_record(root, record)
            applied += 1
        except (OSError, TranslateError) as exc:
            failures.append({"line": line_number, "mathnet_id": mathnet_id, "error": str(exc)})

    failure_path = Path(args.failures) if args.failures else input_path.with_suffix(input_path.suffix + ".failures.jsonl")
    failure_data = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in failures
    )
    atomic_write(failure_path, failure_data)
    print(f"apply: {applied} 题成功，{len(failures)} 题失败；失败清单 {failure_path}")
    return 1 if failures else 0


LANGUAGE_META_RE = re.compile(r"(?m)^[-*+]\s+语言[：:]\s*(?P<language>.+?)\s*$")


def utc_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def source_meta(text: str) -> dict[str, str]:
    match = LANGUAGE_META_RE.search(text)
    return {"language": match.group("language").strip()} if match else {}


def build_source_language_map(
    root: Path, destination: Path, selected: set[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Detect languages, retaining hash-keyed cached results across resumed runs."""
    cached: dict[str, Any] = {}
    if destination.is_file():
        value = load_json(destination)
        if not isinstance(value, dict):
            raise TranslateError(f"语言映射不是 JSON 对象: {destination}")
        cached = value

    result: dict[str, Any] = {}
    source_hashes: dict[str, str] = {}
    reused = detected = skipped = 0
    for path in iter_source_files(root):
        mathnet_id = path.parent.name
        if selected and mathnet_id not in selected:
            skipped += 1
            continue
        source_bytes = path.read_bytes()
        source_sha = sha256_bytes(source_bytes)
        source_hashes[mathnet_id] = source_sha
        previous = cached.get(mathnet_id)
        if isinstance(previous, dict) and previous.get("source_sha256") == source_sha:
            lang = previous.get("source_lang")
            confidence = previous.get("source_lang_confidence")
            if isinstance(lang, str) and isinstance(confidence, str):
                result[mathnet_id] = previous
                reused += 1
                continue
        try:
            text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TranslateError(f"{path}: index.md 不是 UTF-8") from exc
        lang, confidence = detect_source_lang(text, source_meta(text))
        result[mathnet_id] = {
            "source_lang": lang,
            "source_lang_confidence": confidence,
            "source_sha256": source_sha,
        }
        detected += 1

    persisted = dict(cached) if selected else {}
    persisted.update(result)
    atomic_write(destination, json_bytes(persisted))
    print(
        f"language-map: {len(result)} 题（复用 {reused}，重新检测 {detected}）-> {destination}"
    )
    if selected:
        print(f"language-map: --only 显式跳过 {skipped} 题")
        missing = sorted(selected - set(result))
        if missing:
            print(f"language-map: --only 未找到 {len(missing)} 个 id：{', '.join(missing)}")
    return result, source_hashes


class ProgressStore:
    def __init__(self, path: Path):
        self.path = path
        self.value: dict[str, Any] = {"version": 1, "questions": {}, "batches": {}}
        if path.is_file():
            loaded = load_json(path)
            if not isinstance(loaded, dict) or loaded.get("version") != 1:
                raise TranslateError(f"进度文件版本或格式非法: {path}")
            if not isinstance(loaded.get("questions"), dict) or not isinstance(
                loaded.get("batches"), dict
            ):
                raise TranslateError(f"进度文件缺少 questions/batches: {path}")
            self.value = loaded

    @property
    def questions(self) -> dict[str, Any]:
        return self.value["questions"]

    @property
    def batches(self) -> dict[str, Any]:
        return self.value["batches"]

    def reconcile_sources(self, source_hashes: dict[str, str], *, full_scan: bool) -> None:
        stale = []
        for mathnet_id, entry in list(self.questions.items()):
            current = source_hashes.get(mathnet_id)
            if current is None and not full_scan:
                continue
            if not current or not isinstance(entry, dict) or entry.get("source_sha256") != current:
                stale.append(mathnet_id)
                del self.questions[mathnet_id]
        interrupted = 0
        for entry in self.batches.values():
            if isinstance(entry, dict) and entry.get("status") == "running":
                entry["status"] = "interrupted"
                entry["updated_at"] = utc_now()
                interrupted += 1
        if stale:
            print(
                f"resume: source_sha256 变化或题目消失，显式丢弃 {len(stale)} 条题级进度："
                + ", ".join(sorted(stale)[:20])
            )
        if interrupted:
            print(f"resume: 发现 {interrupted} 个中断批次，将重新核对其暂存 JSON")

    def record_target(self, row: dict[str, Any], target: str, status: str, error: str | None = None) -> None:
        entry = self.questions.setdefault(row["mathnet_id"], {})
        if entry.get("source_sha256") != row["source_sha256"]:
            entry.clear()
            entry.update({"source_sha256": row["source_sha256"], "targets": {}})
        targets = entry.setdefault("targets", {})
        targets[target] = {"status": status, "updated_at": utc_now()}
        if error:
            targets[target]["error"] = error

    def save(self) -> None:
        atomic_write(self.path, json_bytes(self.value))


class FailureLedger:
    def __init__(self, path: Path):
        self.path = path
        self.rows: dict[str, dict[str, Any]] = {}
        if path.is_file():
            existing, malformed = load_jsonl(path)
            if malformed:
                raise TranslateError(f"失败清单损坏: {path}: {malformed[0]['error']}")
            for _, row in existing:
                key = row.get("key")
                if isinstance(key, str):
                    self.rows[key] = row

    def add_batch(self, batch_id: str, target: str, error: str, attempts: int) -> None:
        self.rows[f"batch:{batch_id}"] = {
            "key": f"batch:{batch_id}",
            "scope": "batch",
            "batch_id": batch_id,
            "target_lang": target,
            "attempts": attempts,
            "error": error,
            "updated_at": utc_now(),
        }

    def clear_batch(self, batch_id: str) -> None:
        self.rows.pop(f"batch:{batch_id}", None)

    def add_target(self, row: dict[str, Any], target: str, error: str, batch_id: str | None) -> None:
        key = f"target:{row['mathnet_id']}:{target}"
        self.rows[key] = {
            "key": key,
            "scope": "target",
            "batch_id": batch_id,
            "mathnet_id": row["mathnet_id"],
            "source_sha256": row["source_sha256"],
            "target_lang": target,
            "error": error,
            "updated_at": utc_now(),
        }

    def clear_target(self, row: dict[str, Any], target: str) -> None:
        self.rows.pop(f"target:{row['mathnet_id']}:{target}", None)

    def save(self) -> None:
        data = b"".join(
            (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            for _, row in sorted(self.rows.items())
        )
        atomic_write(self.path, data)


@dataclass(frozen=True)
class BatchJob:
    batch_id: str
    target_lang: str
    records: tuple[dict[str, Any], ...]
    directory: Path

    @property
    def input_path(self) -> Path:
        return self.directory / "batch.json"

    @property
    def prompt_path(self) -> Path:
        return self.directory / "task.md"

    @property
    def output_path(self) -> Path:
        return self.directory / "translations.json"


@dataclass(frozen=True)
class BatchResult:
    job: BatchJob
    variants: dict[str, dict[str, Any]] | None
    attempts: int
    error: str | None = None
    reused: bool = False


def batch_payload(target: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "target_lang": target,
        "records": [
            {
                key: row[key]
                for key in (
                    "mathnet_id",
                    "path",
                    "source_sha256",
                    "source_lang",
                    "source_lang_confidence",
                    "units",
                )
            }
            for row in rows
        ],
    }


def make_batch_jobs(rows: list[dict[str, Any]], batch_size: int, work_dir: Path) -> list[BatchJob]:
    by_target = {
        target: [row for row in rows if row["target_modes"].get(target) == "translated"]
        for target in TARGET_LANGS
    }
    jobs = []
    for target, target_rows in by_target.items():
        for offset in range(0, len(target_rows), batch_size):
            chunk = target_rows[offset:offset + batch_size]
            payload = batch_payload(target, chunk)
            digest = sha256_bytes(json_bytes(payload))
            jobs.append(BatchJob(
                batch_id=f"{target}-{digest[:16]}",
                target_lang=target,
                records=tuple(chunk),
                directory=work_dir / "batches" / f"{target}-{digest[:16]}",
            ))
    return jobs


def render_batch_prompt(job: BatchJob) -> str:
    return f"""你是奥数题库的三语翻译执行 agent。当前目标语言：{job.target_lang}。
批次目录的显式绝对路径：{job.directory.resolve()}
输入文件：{job.input_path.resolve()}
输出文件：{job.output_path.resolve()}

读取 batch.json 的全部 records。只翻译每个 unit 的 source 自然语言；所有 {{{{MNT_NNNN}}}}
占位必须各出现一次且顺序不变，不得改写。不得修改 index.md 或语料目录里的任何文件。

把结果写成一个 JSON 对象到 translations.json，严格形如：
{{"model":"实际模型标识","translations":[
  {{"mathnet_id":"输入 id","units":{{"statement":"...","solution_1":"...","final_answer":"..."}}}}
]}}
translations 必须与输入题目一一对应；units 的键必须与各题输入完全一致。不要创建其他文件。
"""


def write_batch_files(job: BatchJob) -> None:
    job.directory.mkdir(parents=True, exist_ok=True)
    atomic_write(job.input_path, json_bytes(batch_payload(job.target_lang, list(job.records))))
    # dispatch 每次调用还会重 render，先写一份方便人类检查待跑批次。
    atomic_write(job.prompt_path, render_batch_prompt(job).encode())


def validate_batch_output(job: BatchJob) -> dict[str, dict[str, Any]]:
    value = load_json(job.output_path)
    if not isinstance(value, dict):
        raise TranslateError("translations.json 顶层必须是对象")
    model = value.get("model")
    translations = value.get("translations")
    if not isinstance(model, str) or not model.strip():
        raise TranslateError("translations.json 缺少非空 model")
    if not isinstance(translations, list):
        raise TranslateError("translations.json 的 translations 必须是数组")

    expected = {row["mathnet_id"]: row for row in job.records}
    result: dict[str, dict[str, Any]] = {}
    for item in translations:
        if not isinstance(item, dict) or not isinstance(item.get("mathnet_id"), str):
            raise TranslateError("translations 项必须是带 mathnet_id 的对象")
        mathnet_id = item["mathnet_id"]
        if mathnet_id not in expected:
            raise TranslateError(f"translations 出现未知 id: {mathnet_id}")
        if mathnet_id in result:
            raise TranslateError(f"translations 出现重复 id: {mathnet_id}")
        units = item.get("units")
        if not isinstance(units, dict):
            raise TranslateError(f"{mathnet_id}: units 必须是对象")
        expected_units = {unit["id"] for unit in expected[mathnet_id]["units"]}
        if set(units) != expected_units or not all(isinstance(text, str) for text in units.values()):
            raise TranslateError(f"{mathnet_id}: units 键集合或值类型与输入不一致")
        result[mathnet_id] = {
            "mode": "translated",
            "model": model,
            "generated_at": utc_now(),
            "units": units,
        }
    missing = sorted(set(expected) - set(result))
    if missing:
        raise TranslateError(f"translations 缺少 {len(missing)} 题：{', '.join(missing)}")
    return result


class ProcessRegistry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.processes: dict[int, tuple[subprocess.Popen[str], Path]] = {}

    def add(self, process: subprocess.Popen[str], output_path: Path) -> None:
        with self.lock:
            self.processes[process.pid] = process, output_path

    def remove(self, process: subprocess.Popen[str]) -> None:
        with self.lock:
            self.processes.pop(process.pid, None)

    @staticmethod
    def terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    def terminate_all(self) -> None:
        with self.lock:
            active = list(self.processes.values())
        for process, output_path in active:
            self.terminate(process)
            output_path.unlink(missing_ok=True)


def dispatch_batch(
    job: BatchJob,
    companion: Path,
    timeout: float,
    retries: int,
    retry_backoff: float,
    retry_backoff_max: float,
    registry: ProcessRegistry,
    stop_event: threading.Event,
) -> BatchResult:
    write_batch_files(job)
    if job.output_path.is_file():
        try:
            variants = validate_batch_output(job)
            print(f"dispatch: 复用中断前已完整产出的批次 {job.batch_id}")
            return BatchResult(job, variants, 0, reused=True)
        except (OSError, TranslateError) as exc:
            print(f"dispatch: 丢弃批次 {job.batch_id} 的无效暂存 JSON：{exc}")
            job.output_path.unlink(missing_ok=True)

    last_error = "unknown dispatch failure"
    for attempt in range(1, retries + 2):
        if stop_event.is_set():
            return BatchResult(job, None, attempt - 1, "运行已中断")
        # 自愈：companion 可能把 cwd 归一到仓库根；每次派单都重写含绝对路径的提示词。
        atomic_write(job.prompt_path, render_batch_prompt(job).encode())
        job.output_path.unlink(missing_ok=True)
        try:
            process = subprocess.Popen(
                [
                    "node",
                    str(companion),
                    "task",
                    "--prompt-file",
                    str(job.prompt_path),
                    "--cwd",
                    str(job.directory),
                    "--write",
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            last_error = f"无法启动 companion：{exc}"
            if attempt <= retries:
                delay = min(retry_backoff * (2 ** (attempt - 1)), retry_backoff_max)
                print(
                    f"dispatch: {job.batch_id} 第 {attempt} 次失败：{last_error}；"
                    f"{delay:g}s 后重试"
                )
                if stop_event.wait(delay):
                    return BatchResult(job, None, attempt, "运行已中断")
                continue
            return BatchResult(job, None, attempt, last_error)
        registry.add(process, job.output_path)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            registry.terminate(process)
            stdout, stderr = process.communicate()
            last_error = f"超时（{timeout:g}s）"
        finally:
            registry.remove(process)

        if stop_event.is_set():
            job.output_path.unlink(missing_ok=True)
            return BatchResult(job, None, attempt, "运行已中断")
        if timed_out:
            pass
        elif process.returncode != 0:
            tail = (stderr or stdout or "").strip()[-300:]
            last_error = f"companion 退出码 {process.returncode}" + (f"：{tail}" if tail else "")
        elif not job.output_path.is_file():
            last_error = "Codex 未产出 translations.json"
        else:
            try:
                return BatchResult(job, validate_batch_output(job), attempt)
            except (OSError, TranslateError) as exc:
                last_error = f"translations.json 非法：{exc}"
        job.output_path.unlink(missing_ok=True)
        if attempt <= retries:
            delay = min(retry_backoff * (2 ** (attempt - 1)), retry_backoff_max)
            print(
                f"dispatch: {job.batch_id} 第 {attempt} 次失败：{last_error}；"
                f"{delay:g}s 后重试"
            )
            if stop_event.wait(delay):
                return BatchResult(job, None, attempt, "运行已中断")
    return BatchResult(job, None, retries + 1, last_error)


def apply_payload(root: Path, row: dict[str, Any], target: str, payload: dict[str, Any]) -> None:
    source_path = safe_record_path(root, row)
    source_bytes = source_path.read_bytes()
    if sha256_bytes(source_bytes) != row["source_sha256"]:
        raise TranslateError("source_sha256 已变化，拒绝应用过期批次")
    document = parse_document(source_bytes.decode("utf-8"), row["path"])
    target_bytes, _ = prepare_variant(
        target,
        payload,
        row["source_lang"],
        row["source_lang_confidence"],
        source_bytes,
        document,
    )
    if target_bytes is None:
        raise TranslateError("内部错误：成功路径收到 failed variant")
    findings = verify_translation(
        source_bytes.decode("utf-8"),
        target_bytes.decode("utf-8"),
        mode=payload["mode"],
        target_lang=target,
        source_lang=row["source_lang"],
    )
    if findings:
        summary = "; ".join(
            f"{finding.type.value}@{finding.section}" for finding in findings[:8]
        )
        if len(findings) > 8:
            summary += f"; 另有 {len(findings) - 8} 项"
        raise TranslateError(f"保真校验失败：{summary}")
    record = {
        key: row[key]
        for key in (
            "mathnet_id",
            "path",
            "source_sha256",
            "source_lang",
            "source_lang_confidence",
        )
    }
    record["variants"] = {target: payload}
    apply_record(root, record)


def record_failed_variant(root: Path, row: dict[str, Any], target: str, error: str) -> None:
    record = {
        key: row[key]
        for key in (
            "mathnet_id",
            "path",
            "source_sha256",
            "source_lang",
            "source_lang_confidence",
        )
    }
    record["variants"] = {
        target: {
            "mode": "failed",
            "model": "codex-companion",
            "generated_at": utc_now(),
            "error": error,
        }
    }
    apply_record(root, record)


def format_eta(started: float, finished: int, total: int) -> str:
    if finished <= 0 or finished >= total:
        return "0s" if finished >= total else "计算中"
    elapsed = max(time.monotonic() - started, 0.001)
    seconds = int((total - finished) * elapsed / finished)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def print_run_progress(
    started: float,
    finished_batches: int,
    failed_batches: int,
    total_batches: int,
    finished_targets: int,
    failed_targets: int,
    total_targets: int,
    completed_questions: int,
    remaining_questions: int,
    failed_questions: int,
) -> None:
    print(
        "progress: "
        f"批次 完成 {finished_batches - failed_batches}/{total_batches}、"
        f"剩余 {total_batches - finished_batches}、失败 {failed_batches}；"
        f"题 完成 {completed_questions}、剩余 {remaining_questions}、失败 {failed_questions}；"
        f"题×目标 完成 {finished_targets - failed_targets}/{total_targets}、"
        f"剩余 {total_targets - finished_targets}、失败 {failed_targets}；"
        f"ETA {format_eta(started, finished_targets, total_targets)}"
    )


def question_progress(rows: list[dict[str, Any]], progress: ProgressStore) -> tuple[int, int, int]:
    completed = remaining = failed = 0
    for row in rows:
        entry = progress.questions.get(row["mathnet_id"], {})
        targets = entry.get("targets", {}) if isinstance(entry, dict) else {}
        statuses = [
            targets.get(target, {}).get("status")
            for target in row["targets"]
        ]
        if any(status == "failed" for status in statuses):
            failed += 1
        elif statuses and all(status == "completed" for status in statuses):
            completed += 1
        else:
            remaining += 1
    return completed, remaining, failed


def run_records(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise TranslateError(f"语料目录不存在: {root}")
    work_dir = Path(args.work_dir).resolve() if args.work_dir else root / ".mathnet-translate-run"
    work_dir.mkdir(parents=True, exist_ok=True)
    language_map_path = work_dir / "source-lang-map.json"
    progress = ProgressStore(Path(args.progress).resolve() if args.progress else work_dir / ".translate-progress.json")
    failures = FailureLedger(Path(args.failures).resolve() if args.failures else work_dir / ".translate-failures.jsonl")
    selected = set(args.only or [])
    _, source_hashes = build_source_language_map(root, language_map_path, selected)
    progress.reconcile_sources(source_hashes, full_scan=not selected)
    progress.save()

    export_path = work_dir / "export.jsonl"
    export_args = argparse.Namespace(
        root=root,
        out=export_path,
        limit=args.limit,
        only=args.only,
        source_lang="und",
        source_lang_confidence="unknown",
        source_lang_map=language_map_path,
    )
    export_records(export_args)
    exported, malformed = load_jsonl(export_path)
    if malformed:
        raise TranslateError(f"内部 export JSONL 非法: {malformed[0]['error']}")
    rows = [row for _, row in exported]
    if not rows:
        failures.save()
        print("run: 没有待处理译文；全部为有效译文或筛选结果为空")
        return 0

    passthrough = [
        (row, target)
        for row in rows
        for target in row["targets"]
        if row["target_modes"][target] == "passthrough"
    ]
    jobs = make_batch_jobs(rows, args.batch_size, work_dir)
    translated_targets = sum(len(job.records) for job in jobs)
    total_targets = len(passthrough) + translated_targets
    print(
        f"run: {len(rows)} 题、{total_targets} 份待处理；passthrough {len(passthrough)} 份，"
        f"真翻 {translated_targets} 份 / {len(jobs)} 批；并发 {args.concurrency}，批大小 {args.batch_size}"
    )

    started = time.monotonic()
    finished_targets = failed_targets = 0
    finished_batches = failed_batches = 0
    any_failure = False
    for row, target in passthrough:
        payload = {"mode": "passthrough", "model": None, "generated_at": utc_now()}
        try:
            apply_payload(root, row, target, payload)
            progress.record_target(row, target, "completed")
            failures.clear_target(row, target)
        except (OSError, TranslateError, ValueError) as exc:
            error = str(exc)
            any_failure = True
            failed_targets += 1
            progress.record_target(row, target, "failed", error)
            failures.add_target(row, target, error, None)
            try:
                record_failed_variant(root, row, target, error)
            except (OSError, TranslateError):
                pass
            print(f"failed: {row['mathnet_id']}:{target}: {error}")
        finished_targets += 1
        progress.save()
        failures.save()
        if any_failure and args.strict:
            print_run_progress(
                started, 0, 0, len(jobs), finished_targets, failed_targets, total_targets,
                *question_progress(rows, progress),
            )
            return 1

    if not jobs:
        print_run_progress(
            started, 0, 0, 0, finished_targets, failed_targets, total_targets,
            *question_progress(rows, progress),
        )
        return 1 if any_failure else 0

    from mathnet_review import find_companion

    companion_value = args.companion or find_companion()
    if not companion_value:
        raise TranslateError("找不到 codex-companion；可用 --companion 指定假桩或本地安装路径")
    companion = Path(companion_value).resolve()
    if not companion.is_file():
        raise TranslateError(f"companion 不存在: {companion}")

    registry = ProcessRegistry()
    stop_event = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency)
    futures: dict[concurrent.futures.Future[BatchResult], BatchJob] = {}
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupt_on_sigterm(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt_on_sigterm)
    interrupted = False
    try:
        for job in jobs:
            write_batch_files(job)
            progress.batches[job.batch_id] = {
                "status": "running",
                "target_lang": job.target_lang,
                "question_ids": [row["mathnet_id"] for row in job.records],
                "updated_at": utc_now(),
            }
            future = executor.submit(
                dispatch_batch,
                job,
                companion,
                args.timeout,
                args.retries,
                args.retry_backoff,
                args.retry_backoff_max,
                registry,
                stop_event,
            )
            futures[future] = job
        progress.save()

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            job = result.job
            batch_failed = False
            if result.variants is None:
                batch_failed = True
                any_failure = True
                failed_batches += 1
                failures.add_batch(job.batch_id, job.target_lang, result.error or "unknown", result.attempts)
                for row in job.records:
                    error = f"批次 {job.batch_id} 失败：{result.error}"
                    progress.record_target(row, job.target_lang, "failed", error)
                    failures.add_target(row, job.target_lang, error, job.batch_id)
                    try:
                        record_failed_variant(root, row, job.target_lang, error)
                    except (OSError, TranslateError) as exc:
                        print(f"failed-state: {row['mathnet_id']}:{job.target_lang}: {exc}")
                    failed_targets += 1
                finished_targets += len(job.records)
                print(
                    f"batch failed: {job.batch_id}，尝试 {result.attempts} 次：{result.error}"
                )
            else:
                failures.clear_batch(job.batch_id)
                for row in job.records:
                    payload = result.variants[row["mathnet_id"]]
                    try:
                        apply_payload(root, row, job.target_lang, payload)
                        progress.record_target(row, job.target_lang, "completed")
                        failures.clear_target(row, job.target_lang)
                    except (OSError, TranslateError, ValueError) as exc:
                        error = str(exc)
                        batch_failed = True
                        any_failure = True
                        failed_targets += 1
                        progress.record_target(row, job.target_lang, "failed", error)
                        failures.add_target(row, job.target_lang, error, job.batch_id)
                        try:
                            record_failed_variant(root, row, job.target_lang, error)
                        except (OSError, TranslateError) as state_exc:
                            print(f"failed-state: {row['mathnet_id']}:{job.target_lang}: {state_exc}")
                        print(f"failed: {row['mathnet_id']}:{job.target_lang}: {error}")
                        if args.strict:
                            break
                    finally:
                        finished_targets += 1
                if batch_failed:
                    job.output_path.unlink(missing_ok=True)
                    print(
                        f"dispatch: {job.batch_id} 的译文未通过 apply/保真门禁，"
                        "已清除缓存以便下次重新派单"
                    )
                    failed_batches += 1

            finished_batches += 1
            progress.batches[job.batch_id] = {
                "status": "failed" if batch_failed else "completed",
                "target_lang": job.target_lang,
                "question_ids": [row["mathnet_id"] for row in job.records],
                "attempts": result.attempts,
                "reused_output": result.reused,
                "error": result.error if batch_failed else None,
                "updated_at": utc_now(),
            }
            progress.save()
            failures.save()
            print_run_progress(
                started,
                finished_batches,
                failed_batches,
                len(jobs),
                finished_targets,
                failed_targets,
                total_targets,
                *question_progress(rows, progress),
            )
            if batch_failed and args.strict:
                stop_event.set()
                registry.terminate_all()
                for pending in futures:
                    pending.cancel()
                break
    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        print("run: 收到中断，正在终止全部 companion 子进程…", file=sys.stderr)
        registry.terminate_all()
        for future in futures:
            future.cancel()
    finally:
        stop_event.set()
        registry.terminate_all()
        executor.shutdown(wait=True, cancel_futures=True)
        signal.signal(signal.SIGTERM, previous_sigterm)
        for entry in progress.batches.values():
            if isinstance(entry, dict) and entry.get("status") == "running":
                entry["status"] = "interrupted"
                entry["updated_at"] = utc_now()
        progress.save()
        failures.save()

    if interrupted:
        print("run: 已干净退出；进度已原子落盘，无活动 companion 子进程", file=sys.stderr)
        return 130
    return 1 if any_failure else 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        description="导出/写回 MathNet 中英译文批次；契约见 docs/译文契约-mathnet-full.md"
    )
    commands = top.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser("export", help="扫描 index.md，导出仍缺译文的 JSONL 单元")
    export_parser.add_argument("--root", default=DEFAULT_CORPUS, help="语料根目录（默认 mathnet-full/）")
    export_parser.add_argument("--out", required=True, help="输出批次 JSONL")
    export_parser.add_argument("--limit", type=int, help="最多导出多少题")
    export_parser.add_argument("--only", action="append", metavar="ID", help="只导指定 id；可重复")
    export_parser.add_argument("--source-lang", default="und", help="源语言占位/统一外部值（默认 und）")
    export_parser.add_argument(
        "--source-lang-confidence", default="unknown", help="统一语言置信度（默认 unknown）"
    )
    export_parser.add_argument(
        "--source-lang-map",
        help="外部 JSON 映射：id -> 语言，或 id -> {source_lang, source_lang_confidence}",
    )
    export_parser.set_defaults(func=export_records)

    apply_parser = commands.add_parser("apply", help="校验译文 JSONL 并原子写回三语产物")
    apply_parser.add_argument("--root", default=DEFAULT_CORPUS, help="语料根目录（默认 mathnet-full/）")
    apply_parser.add_argument("--in", dest="input", required=True, help="待写回译文 JSONL")
    apply_parser.add_argument("--failures", help="失败清单路径（默认 <输入>.failures.jsonl）")
    apply_parser.set_defaults(func=apply_records)

    run_parser = commands.add_parser("run", help="生成语言图并并发串起 export → Codex → apply")
    run_parser.add_argument("--root", default=DEFAULT_CORPUS, help="语料根目录（默认 mathnet-full/）")
    run_parser.add_argument("--work-dir", help="批次暂存目录（默认 <root>/.mathnet-translate-run）")
    run_parser.add_argument("--progress", help="原子双层进度文件（默认 <work-dir>/.translate-progress.json）")
    run_parser.add_argument("--failures", help="失败清单（默认 <work-dir>/.translate-failures.jsonl）")
    run_parser.add_argument("--limit", type=int, help="最多导出多少题，直接透传 export")
    run_parser.add_argument("--only", action="append", metavar="ID", help="只跑指定 id；可重复")
    run_parser.add_argument("--batch-size", type=int, default=25, help="每个目标语言每批题数（默认 25）")
    run_parser.add_argument("--concurrency", type=int, default=4, help="并行 companion 子进程数（默认 4）")
    run_parser.add_argument("--timeout", type=float, default=1200, help="单次派单超时秒数（默认 1200）")
    run_parser.add_argument("--retries", type=int, default=3, help="失败后的最大重试次数（默认 3）")
    run_parser.add_argument("--retry-backoff", type=float, default=2, help="首次退避秒数（默认 2）")
    run_parser.add_argument(
        "--retry-backoff-max", type=float, default=60, help="指数退避上限秒数（默认 60）"
    )
    run_parser.add_argument("--strict", action="store_true", help="首个批次或题级失败即停止")
    run_parser.add_argument("--companion", help="显式 companion 路径（测试假桩；默认 find_companion）")
    run_parser.set_defaults(func=run_records)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "limit", None) is not None and args.limit < 0:
        parser().error("--limit 不得小于 0")
    for name in ("batch_size", "concurrency"):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser().error(f"--{name.replace('_', '-')} 必须大于 0")
    for name in ("timeout", "retry_backoff", "retry_backoff_max"):
        if hasattr(args, name) and getattr(args, name) < 0:
            parser().error(f"--{name.replace('_', '-')} 不得小于 0")
    if hasattr(args, "retries") and args.retries < 0:
        parser().error("--retries 不得小于 0")
    try:
        return args.func(args)
    except TranslateError as exc:
        print(f"mathnet_translate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
