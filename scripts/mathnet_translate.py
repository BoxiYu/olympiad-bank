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
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from source_lang import should_passthrough

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

    if args.limit == 0:
        atomic_write(Path(args.out), b"")
        print(f"export: 0 题 -> {args.out}")
        return 0

    for path in iter_source_files(root):
        mathnet_id = path.parent.name
        if selected and mathnet_id not in selected:
            continue
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
        if args.limit is not None and len(rows) >= args.limit:
            break

    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode() for row in rows
    )
    atomic_write(Path(args.out), payload)
    print(f"export: {len(rows)} 题 -> {args.out}")
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
    if not state:
        return {}
    return {
        lang: state["variants"][lang]
        for lang in TARGET_LANGS
        if variant_current(question_dir, state, mathnet_id, source_sha, lang)
    }


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
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if getattr(args, "limit", None) is not None and args.limit < 0:
        parser().error("--limit 不得小于 0")
    try:
        return args.func(args)
    except TranslateError as exc:
        print(f"mathnet_translate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
