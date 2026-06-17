#!/usr/bin/env python3
"""Rule-based checks for local LLM translation benchmark outputs."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = [
    "model_name",
    "run_id",
    "id",
    "category",
    "source_en",
    "reference_ja",
    "translation_ja",
    "protected_tokens",
    "required_terms",
    "http_status",
    "error",
]

ADDED_COLUMNS = [
    "request_success",
    "request_error_type",
    "protected_tokens_pass",
    "protected_token_expected_count",
    "protected_token_matched_count",
    "missing_protected_tokens",
    "required_terms_pass",
    "missing_required_terms",
    "empty_output",
    "extra_text_detected",
    "extra_text_pattern",
    "markdown_detected",
    "unnecessary_quotes",
    "english_residue_detected",
    "english_character_ratio",
    "longest_source_match",
    "excessive_length",
    "output_length",
    "reference_length",
    "output_length_ratio",
    "rule_check_status",
    "failure_reasons",
    "warning_reasons",
]

DEFAULT_CONFIG = {
    "max_reference_length_ratio": 4.0,
    "minimum_excessive_length": 80,
    "english_ratio_warning": 0.35,
    "english_word_warning_count": 4,
    "longest_source_match_warning": 20,
    "max_tokens_finish_reasons": ["length", "max_tokens"],
    "allowed_english_terms": [
        "HP",
        "MP",
        "SP",
        "XP",
        "DPS",
        "HUD",
        "UI",
        "HDR",
        "QTE",
        "FPS",
        "NPC",
        "DLC",
        "A",
        "B",
        "X",
        "Y",
        "L",
        "R",
        "LT",
        "RT",
        "LB",
        "RB",
        "Esc",
        "Enter",
        "Shift",
        "Ctrl",
        "Alt",
        "Tab",
        "Space",
        "WASD",
    ],
}

EXTRA_TEXT_PATTERNS = [
    r"^\s*翻訳\s*[:：]",
    r"^\s*日本語訳\s*[:：]",
    r"以下が翻訳です",
    r"^\s*Translation\s*:",
    r"The Japanese translation is\s*:",
    r"Here is the translation\s*:",
]

QUOTE_PAIRS = [("「", "」"), ("『", "』"), ('"', '"'), ("'", "'")]


class RuleEvalError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate translation benchmark rows with deterministic rules.")
    parser.add_argument("--input", required=True, help="Raw results CSV from run_benchmark.py")
    parser.add_argument("--output", required=True, help="Rule evaluation CSV")
    parser.add_argument("--config", help="Optional JSON config file")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--strict-terms", action="store_true", help="Treat required-term mismatch as FAIL")
    parser.add_argument("--allow-quotes", action="store_true", help="Allow translation wrapped in quotes")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file")
    return parser.parse_args(argv)


def setup_logging(args: argparse.Namespace) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def load_config(path: str | None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if not path:
        return config
    try:
        user_config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuleEvalError(f"Failed to read config: {exc}", 1) from exc
    except json.JSONDecodeError as exc:
        raise RuleEvalError(f"Config must be JSON: {exc}", 1) from exc
    if not isinstance(user_config, dict):
        raise RuleEvalError("Config must be a JSON object", 1)
    config.update(user_config)
    return config


def normalize_text(text: Any) -> str:
    normalized = unicodedata.normalize("NFKC", "" if text is None else str(text))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    return normalized


def split_semicolon(value: str) -> list[str]:
    return [normalize_text(part) for part in str(value or "").split(";") if normalize_text(part)]


def parse_required_terms(value: str) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = []
    for part in split_semicolon(value):
        if "=" not in part:
            continue
        source, target = part.split("=", 1)
        source = normalize_text(source)
        target = normalize_text(target)
        if target:
            terms.append((source, target))
    return terms


def is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "pass", "ok"}


def to_int(value: Any) -> int | None:
    try:
        if str(value).strip() == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        if str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def find_token_count(text: str, token: str) -> int:
    if not token:
        return 0
    escaped = re.escape(token)
    if re.fullmatch(r"\d+(?:\.\d+)?%?", token):
        pattern = rf"(?<![0-9.]){escaped}(?![0-9.])"
        return len(re.findall(pattern, text))
    return text.count(token)


def check_protected_tokens(translation: str, protected_tokens: str) -> dict[str, Any]:
    tokens = split_semicolon(protected_tokens)
    expected = Counter(tokens)
    matched_total = 0
    missing: list[str] = []
    for token, count in expected.items():
        matched = min(find_token_count(translation, token), count)
        matched_total += matched
        if matched < count:
            missing.extend([token] * (count - matched))
    expected_total = sum(expected.values())
    return {
        "protected_tokens_pass": expected_total == matched_total,
        "protected_token_expected_count": expected_total,
        "protected_token_matched_count": matched_total,
        "missing_protected_tokens": ";".join(missing),
    }


def check_required_terms(translation: str, required_terms: str) -> dict[str, Any]:
    terms = parse_required_terms(required_terms)
    expected = Counter(target for _source, target in terms)
    missing: list[str] = []
    for target, count in expected.items():
        matched = min(translation.count(target), count)
        if matched < count:
            missing.extend([target] * (count - matched))
    return {
        "required_terms_pass": not missing,
        "missing_required_terms": ";".join(missing),
    }


def detect_extra_text(translation: str) -> tuple[bool, str]:
    for pattern in EXTRA_TEXT_PATTERNS:
        try:
            if re.search(pattern, translation, flags=re.IGNORECASE | re.MULTILINE):
                return True, pattern
        except re.error:
            continue
    return False, ""


def detect_markdown(translation: str) -> bool:
    patterns = [
        r"```",
        r"(?m)^\s{0,3}#{1,6}\s+\S",
        r"(?m)^\s{0,3}[-*+]\s+\S",
        r"(?m)^\s{0,3}\d+\.\s+\S",
        r"(?m)^\s{0,3}>\s+\S",
        r"(\*\*|__)[^*_]+(\*\*|__)",
    ]
    return any(re.search(pattern, translation) for pattern in patterns)


def detect_unnecessary_quotes(translation: str, allow_quotes: bool) -> bool:
    if allow_quotes:
        return False
    if len(translation) < 2:
        return False
    return any(translation.startswith(left) and translation.endswith(right) for left, right in QUOTE_PAIRS)


def allowed_english_terms(row: dict[str, str], config: dict[str, Any]) -> set[str]:
    allowed = {str(term).lower() for term in config.get("allowed_english_terms", [])}
    allowed.update(token.lower() for token in split_semicolon(row.get("protected_tokens", "")) if re.search(r"[A-Za-z]", token))
    allowed.update(source.lower() for source, _target in parse_required_terms(row.get("required_terms", "")) if source)
    allowed.update(match.group(0).lower() for match in re.finditer(r"\{[^}]+\}|%[A-Za-z0-9_]+%?|<[A-Za-z0-9_/.-]+>", row.get("source_en", "")))
    return allowed


def longest_source_match(source: str, translation: str) -> int:
    source_norm = normalize_text(source).lower()
    translation_norm = normalize_text(translation).lower()
    if not source_norm or not translation_norm:
        return 0
    match = SequenceMatcher(None, source_norm, translation_norm, autojunk=False).find_longest_match(
        0, len(source_norm), 0, len(translation_norm)
    )
    return int(match.size)


def check_english_residue(row: dict[str, str], translation: str, config: dict[str, Any]) -> dict[str, Any]:
    compact_length = len(re.sub(r"\s+", "", translation))
    alpha_count = len(re.findall(r"[A-Za-z]", translation))
    ratio = alpha_count / compact_length if compact_length else 0.0
    words = [match.group(0) for match in re.finditer(r"[A-Za-z][A-Za-z0-9_'-]*", translation)]
    allowed = allowed_english_terms(row, config)
    suspicious_words = [word for word in words if word.lower() not in allowed]
    longest = longest_source_match(row.get("source_en", ""), translation)
    detected = (
        len(suspicious_words) >= int(config["english_word_warning_count"])
        or ratio >= float(config["english_ratio_warning"])
        or longest >= int(config["longest_source_match_warning"])
    )
    return {
        "english_residue_detected": detected,
        "english_character_ratio": ratio,
        "longest_source_match": longest,
    }


def check_excessive_length(row: dict[str, str], translation: str, config: dict[str, Any]) -> dict[str, Any]:
    output_length = len(translation)
    reference_length = len(normalize_text(row.get("reference_ja", "")))
    ratio = output_length / reference_length if reference_length else None
    excessive = False
    if ratio is not None:
        excessive = (
            output_length >= int(config["minimum_excessive_length"])
            and ratio > float(config["max_reference_length_ratio"])
        )
    if not excessive and len(normalize_text(row.get("source_en", ""))) <= 40 and output_length >= int(config["minimum_excessive_length"]) * 2:
        excessive = True
    return {
        "excessive_length": excessive,
        "output_length": output_length,
        "reference_length": reference_length,
        "output_length_ratio": ratio,
    }


def check_request_success(row: dict[str, str], translation: str, empty_output: bool, config: dict[str, Any]) -> tuple[bool, str]:
    reasons: list[str] = []
    http_status = to_int(row.get("http_status", ""))
    raw_success = row.get("request_success", "")
    finish_reason = normalize_text(row.get("finish_reason", "")).lower()
    error_text = normalize_text(row.get("error", ""))
    raw_error_type = normalize_text(row.get("error_type", ""))

    if raw_success and not is_truthy(raw_success):
        reasons.append("request_success=false")
    if http_status is not None and http_status != 200:
        reasons.append(f"http_status={http_status}")
    if error_text:
        reasons.append("error")
    if raw_error_type:
        reasons.append(raw_error_type)
    if empty_output:
        reasons.append("empty_output")
    if finish_reason in {str(item).lower() for item in config.get("max_tokens_finish_reasons", [])}:
        reasons.append(f"finish_reason={finish_reason}")
    return not reasons, ";".join(dict.fromkeys(reasons))


def evaluate_row(row: dict[str, str], args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    translation_raw = row.get("translation_ja", "")
    translation = normalize_text(translation_raw)
    normalized_row = {key: normalize_text(value) for key, value in row.items()}
    normalized_row["translation_ja"] = translation

    empty_output = translation.lower() in {"", "null", "none", "nan"}
    request_success, request_error_type = check_request_success(row, translation, empty_output, config)
    protected = check_protected_tokens(translation, row.get("protected_tokens", ""))
    terms = check_required_terms(translation, row.get("required_terms", ""))
    extra_text_detected, extra_text_pattern = detect_extra_text(translation)
    markdown_detected = detect_markdown(translation)
    unnecessary_quotes = detect_unnecessary_quotes(translation, args.allow_quotes)
    english = check_english_residue(normalized_row, translation, config)
    excessive = check_excessive_length(normalized_row, translation, config)

    strict_terms = args.strict_terms or normalize_text(row.get("input_variant", "standard")) == "terminology"
    failures: list[str] = []
    warnings: list[str] = []

    if not request_success:
        failures.append("request_success=false")
    if empty_output:
        failures.append("empty_output")
    if not protected["protected_tokens_pass"]:
        failures.append("protected_tokens_missing")
    if extra_text_detected:
        failures.append("extra_text_detected")
    if markdown_detected:
        failures.append("markdown_detected")
    if excessive["excessive_length"]:
        failures.append("excessive_length")
    if not terms["required_terms_pass"]:
        if strict_terms:
            failures.append("required_terms_missing")
        else:
            warnings.append("required_terms_missing")
    if unnecessary_quotes:
        warnings.append("unnecessary_quotes")
    if english["english_residue_detected"]:
        warnings.append("english_residue_detected")

    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    output = dict(row)
    output.update(
        {
            "request_success": bool_text(request_success),
            "request_error_type": request_error_type,
            "protected_tokens_pass": bool_text(bool(protected["protected_tokens_pass"])),
            "protected_token_expected_count": protected["protected_token_expected_count"],
            "protected_token_matched_count": protected["protected_token_matched_count"],
            "missing_protected_tokens": protected["missing_protected_tokens"],
            "required_terms_pass": bool_text(bool(terms["required_terms_pass"])),
            "missing_required_terms": terms["missing_required_terms"],
            "empty_output": bool_text(empty_output),
            "extra_text_detected": bool_text(extra_text_detected),
            "extra_text_pattern": extra_text_pattern,
            "markdown_detected": bool_text(markdown_detected),
            "unnecessary_quotes": bool_text(unnecessary_quotes),
            "english_residue_detected": bool_text(bool(english["english_residue_detected"])),
            "english_character_ratio": f"{english['english_character_ratio']:.6f}",
            "longest_source_match": english["longest_source_match"],
            "excessive_length": bool_text(bool(excessive["excessive_length"])),
            "output_length": excessive["output_length"],
            "reference_length": excessive["reference_length"],
            "output_length_ratio": "" if excessive["output_length_ratio"] is None else f"{excessive['output_length_ratio']:.6f}",
            "rule_check_status": status,
            "failure_reasons": ";".join(failures),
            "warning_reasons": ";".join(warnings),
        }
    )
    return output


def read_rows(path: Path, encoding: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.DictReader(handle)
            return [{key: value if value is not None else "" for key, value in row.items()} for row in reader], reader.fieldnames or []
    except OSError as exc:
        raise RuleEvalError(f"Failed to read input CSV: {exc}", 2) from exc


def validate_columns(fieldnames: list[str]) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise RuleEvalError(f"Missing required columns: {', '.join(missing)}", 3)


def output_fieldnames(input_fieldnames: list[str]) -> list[str]:
    fields = list(input_fieldnames)
    for column in ADDED_COLUMNS:
        if column not in fields:
            fields.append(column)
    return fields


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise RuleEvalError("Output file already exists. Use --overwrite to replace it.", 4)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise RuleEvalError(f"Failed to write output CSV: {exc}", 4) from exc


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    try:
        args = parse_args(argv or sys.argv[1:])
        setup_logging(args)
        config = load_config(args.config)
        input_path = Path(args.input)
        output_path = Path(args.output)
        rows, fieldnames = read_rows(input_path, args.encoding)
        validate_columns(fieldnames)
        logging.info("Rule evaluation started: input=%s output=%s rows=%s", input_path, output_path, len(rows))
        evaluated = [evaluate_row(row, args, config) for row in rows]
        write_rows(output_path, output_fieldnames(fieldnames), evaluated, args.overwrite)

        counts = Counter(row["rule_check_status"] for row in evaluated)
        elapsed = time.perf_counter() - started
        logging.info(
            "Rule evaluation completed: rows=%s PASS=%s WARN=%s FAIL=%s elapsed=%.3fs",
            len(evaluated),
            counts.get("PASS", 0),
            counts.get("WARN", 0),
            counts.get("FAIL", 0),
            elapsed,
        )
        print(
            f"rows={len(evaluated)} PASS={counts.get('PASS', 0)} "
            f"WARN={counts.get('WARN', 0)} FAIL={counts.get('FAIL', 0)} "
            f"output={output_path}"
        )
        return 0
    except RuleEvalError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    except Exception:
        logging.exception("Unexpected internal error")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
