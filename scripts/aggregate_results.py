#!/usr/bin/env python3
"""Aggregate local LLM translation benchmark results into comparison reports."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


KEY_COLUMNS = ["model_name", "run_id", "id", "input_variant"]
DEFAULT_QUALIFICATION = {
    "minimum_success_rate": 0.995,
    "minimum_protected_token_pass_rate": 0.99,
    "minimum_format_pass_rate": 0.99,
    "maximum_critical_error_rate": 0.01,
    "maximum_empty_output_rate": 0.005,
}
DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}

MODEL_SUMMARY_COLUMNS = [
    "model_name",
    "model_file",
    "quantization",
    "input_variant",
    "request_count",
    "success_count",
    "failure_count",
    "success_rate",
    "timeout_count",
    "empty_output_count",
    "latency_mean_ms",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_min_ms",
    "latency_max_ms",
    "latency_stddev_ms",
    "prompt_tps_mean",
    "generation_tps_mean",
    "generation_tps_p50",
    "generation_tps_p95",
    "comet_mean",
    "comet_median",
    "comet_stddev",
    "chrf_corpus",
    "sentence_chrf_mean",
    "protected_token_pass_rate",
    "required_term_pass_rate",
    "format_pass_rate",
    "rule_pass_rate_all",
    "rule_pass_rate_success_only",
    "rule_warn_rate",
    "rule_fail_rate",
    "human_accuracy_mean",
    "human_fluency_mean",
    "human_usability_pass_rate",
    "critical_error_rate",
    "qualification_status",
    "qualification_failure_reasons",
]

GROUP_COLUMNS = [
    "model_name",
    "category",
    "subcategory",
    "difficulty",
    "input_variant",
    "request_count",
    "comet_mean",
    "sentence_chrf_mean",
    "protected_token_pass_rate",
    "required_term_pass_rate",
    "rule_pass_rate",
    "critical_error_rate",
    "latency_mean_ms",
    "latency_p50_ms",
    "latency_p95_ms",
]

FINAL_COLUMNS = [
    "model_name",
    "model_file",
    "quantization",
    "test_item_count",
    "success_rate",
    "comet_mean",
    "chrf_corpus",
    "protected_token_pass_rate",
    "required_term_pass_rate",
    "format_pass_rate",
    "critical_error_rate",
    "human_usability_pass_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "generation_tps_mean",
    "peak_vram_mb",
    "ocr_comet_drop",
    "exact_output_match_rate",
    "qualification_status",
    "qualification_failure_reasons",
]

FAILED_CASE_COLUMNS = [
    "model_name",
    "run_id",
    "id",
    "category",
    "subcategory",
    "difficulty",
    "input_variant",
    "source_en",
    "reference_ja",
    "translation_ja",
    "comet_score",
    "rule_check_status",
    "failure_reasons",
    "human_comment",
]


class AggregateError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class ModelBundle:
    model_name: str
    model_dir: Path
    rows: list[dict[str, Any]]
    raw_count: int
    rule_count: int
    files: list[str]
    excluded: bool = False


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate local LLM translation benchmark results.")
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config")
    parser.add_argument("--models", help="Comma-separated model directory names or model_name values")
    parser.add_argument("--accuracy-run", default="1")
    parser.add_argument("--include-all-runs", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file")
    return parser.parse_args(argv)


def setup_logging(args: argparse.Namespace) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        path = Path(args.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip("\"'")


def load_config(path: str | None) -> dict[str, Any]:
    config = {"qualification": dict(DEFAULT_QUALIFICATION), "failed_case": {"minimum_comet_score": None}}
    if not path:
        return config
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise AggregateError(f"Failed to read config: {exc}", 1) from exc

    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            if isinstance(loaded.get("qualification"), dict):
                config["qualification"].update(loaded["qualification"])
            if isinstance(loaded.get("failed_case"), dict):
                config["failed_case"].update(loaded["failed_case"])
            return config
        raise AggregateError("Config must be a JSON/YAML object", 1)
    except json.JSONDecodeError:
        pass

    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            config.setdefault(current_section, {})
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if current_section:
            config.setdefault(current_section, {})[key] = parse_scalar(value)
        else:
            config[key] = parse_scalar(value)
    return config


def model_filter(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = [{key: value if value is not None else "" for key, value in row.items()} for row in reader]
            return rows, reader.fieldnames or []
    except OSError as exc:
        raise AggregateError(f"Failed to read CSV {path}: {exc}", 2) from exc


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise AggregateError(f"Output file already exists: {path}. Use --overwrite.", 5)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows([{key: blank(row.get(key)) for key in fieldnames} for row in rows])
    except OSError as exc:
        raise AggregateError(f"Failed to write {path}: {exc}", 5) from exc


def write_json(path: Path, data: dict[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise AggregateError(f"Output file already exists: {path}. Use --overwrite.", 5)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        raise AggregateError(f"Failed to write {path}: {exc}", 5) from exc


def blank(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return f"{value:.6f}"
    return value


def as_float(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if text == "":
            return None
        number = float(text)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "pass", "ok"}:
        return True
    if text in {"false", "0", "no", "n", "fail", "ng"}:
        return False
    return None


def key_for(row: dict[str, Any], fallback_model: str) -> tuple[str, str, str, str]:
    model = str(row.get("model_name") or fallback_model)
    run_id = str(row.get("run_id") or "1")
    item_id = str(row.get("id") or "")
    variant = str(row.get("input_variant") or "standard")
    return model, run_id, item_id, variant


def key_string(key: tuple[str, str, str, str]) -> str:
    return "|".join(key)


def find_model_dirs(results_dir: Path, filters: set[str]) -> list[Path]:
    if not results_dir.exists() or not results_dir.is_dir():
        raise AggregateError(f"Results directory is not readable: {results_dir}", 2)
    if any(results_dir.glob("*raw_results.csv")) and any(results_dir.glob("*rule_evaluation.csv")):
        candidates = [results_dir]
    else:
        candidates = [path for path in results_dir.iterdir() if path.is_dir()]
    return sorted(candidates, key=lambda path: path.name)


def find_files(model_dir: Path, pattern: str) -> list[Path]:
    return sorted({path for path in model_dir.rglob(pattern) if path.is_file()})


def add_quality(report: list[dict[str, Any]], severity: str, model_name: str, file: str, key: str, issue: str) -> None:
    report.append({"severity": severity, "model_name": model_name, "file": file, "key": key, "issue": issue})


def validate_required_columns(
    fieldnames: list[str],
    required: Iterable[str],
    report: list[dict[str, Any]],
    model_name: str,
    path: Path,
) -> bool:
    missing = [column for column in required if column not in fieldnames]
    if missing:
        add_quality(report, "ERROR", model_name, str(path), "", f"missing columns: {', '.join(missing)}")
        return False
    return True


def collect_keyed_rows(
    files: list[Path],
    fallback_model: str,
    required_columns: list[str],
    report: list[dict[str, Any]],
    label: str,
) -> tuple[dict[tuple[str, str, str, str], dict[str, str]], int, bool]:
    keyed: dict[tuple[str, str, str, str], dict[str, str]] = {}
    row_count = 0
    valid = True
    for path in files:
        rows, fieldnames = read_csv(path)
        row_count += len(rows)
        if not validate_required_columns(fieldnames, required_columns, report, fallback_model, path):
            valid = False
            continue
        seen_in_file: set[tuple[str, str, str, str]] = set()
        for index, row in enumerate(rows, start=2):
            key = key_for(row, fallback_model)
            if not key[2]:
                add_quality(report, "ERROR", fallback_model, str(path), f"line {index}", f"{label}: missing id")
                valid = False
                continue
            if key in seen_in_file or key in keyed:
                add_quality(report, "ERROR", key[0], str(path), key_string(key), f"{label}: duplicate composite key")
                valid = False
                continue
            seen_in_file.add(key)
            keyed[key] = row
    return keyed, row_count, valid


def optional_scores(
    files: list[Path],
    fallback_model: str,
    score_candidates: list[str],
    report: list[dict[str, Any]],
    label: str,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    scores: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for path in files:
        rows, fieldnames = read_csv(path)
        score_column = next((column for column in score_candidates if column in fieldnames), None)
        for index, row in enumerate(rows, start=2):
            key = key_for(row, fallback_model)
            if not key[2]:
                add_quality(report, "WARNING", fallback_model, str(path), f"line {index}", f"{label}: missing id")
                continue
            if score_column:
                value = as_float(row.get(score_column))
                if value is not None:
                    if label == "comet" and not (-1.0 <= value <= 1.5):
                        add_quality(report, "WARNING", key[0], str(path), key_string(key), "COMET score outside expected range")
                    if label == "chrf" and not (0.0 <= value <= 100.0):
                        add_quality(report, "WARNING", key[0], str(path), key_string(key), "chrF score outside expected range")
                    scores.setdefault(key, {})[f"{label}_score"] = value
            for column in fieldnames:
                if column not in KEY_COLUMNS and column != score_column:
                    scores.setdefault(key, {})[column] = row.get(column, "")
    return scores


def load_model_bundle(model_dir: Path, filters: set[str], report: list[dict[str, Any]]) -> ModelBundle | None:
    fallback_model = model_dir.name
    raw_files = find_files(model_dir, "*raw_results.csv")
    rule_files = find_files(model_dir, "*rule_evaluation.csv")
    if not raw_files:
        add_quality(report, "ERROR", fallback_model, str(model_dir), "", "missing raw_results.csv")
    if not rule_files:
        add_quality(report, "ERROR", fallback_model, str(model_dir), "", "missing rule_evaluation.csv")
    if not raw_files or not rule_files:
        return None

    raw_required = ["model_name", "run_id", "id", "request_success", "total_latency_ms"]
    rule_required = ["model_name", "run_id", "id", "rule_check_status"]
    raw_by_key, raw_count, raw_valid = collect_keyed_rows(raw_files, fallback_model, raw_required, report, "raw")
    rule_by_key, rule_count, rule_valid = collect_keyed_rows(rule_files, fallback_model, rule_required, report, "rule")
    if not raw_valid or not rule_valid:
        logging.warning("Data quality issues found for model %s; first valid rows will still be aggregated", fallback_model)

    for key in raw_by_key:
        if key not in rule_by_key:
            add_quality(report, "ERROR", key[0], "", key_string(key), "raw row has no matching rule_evaluation row")
    for key in rule_by_key:
        if key not in raw_by_key:
            add_quality(report, "ERROR", key[0], "", key_string(key), "rule_evaluation row has no matching raw row")

    comet = optional_scores(find_files(model_dir, "*comet_scores.csv"), fallback_model, ["comet_score", "score", "comet", "COMET"], report, "comet")
    chrf = optional_scores(find_files(model_dir, "*chrf_scores.csv"), fallback_model, ["chrf_score", "sentence_chrf", "chrf", "score"], report, "chrf")
    human = optional_scores(find_files(model_dir, "*human_evaluation.csv"), fallback_model, ["human_score", "score"], report, "human")

    rows: list[dict[str, Any]] = []
    for key in sorted(set(raw_by_key) | set(rule_by_key), key=key_string):
        row: dict[str, Any] = {}
        row.update(raw_by_key.get(key, {}))
        row.update(rule_by_key.get(key, {}))
        row.update(comet.get(key, {}))
        row.update(chrf.get(key, {}))
        row.update(human.get(key, {}))
        row.setdefault("model_name", key[0])
        row.setdefault("run_id", key[1])
        row.setdefault("id", key[2])
        row.setdefault("input_variant", key[3])
        rows.append(row)

    if filters and not any(row.get("model_name") in filters or fallback_model in filters for row in rows):
        return None

    files = [str(path) for path in raw_files + rule_files]
    return ModelBundle(fallback_model, model_dir, rows, raw_count, rule_count, files)


def percentile(values: list[float], percent: float) -> float | None:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * (percent / 100)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return clean[lower]
    return clean[lower] + (clean[upper] - clean[lower]) * (rank - lower)


def mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def stddev(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def is_success(row: dict[str, Any]) -> bool:
    return as_bool(row.get("request_success")) is True


def is_timeout(row: dict[str, Any]) -> bool:
    text = f"{row.get('request_error_type', '')};{row.get('error_type', '')};{row.get('error', '')}".lower()
    return "timeout" in text


def is_empty(row: dict[str, Any]) -> bool:
    value = as_bool(row.get("empty_output"))
    if value is not None:
        return value
    return str(row.get("translation_ja", "")).strip() == ""


def format_pass(row: dict[str, Any]) -> bool:
    for column in ["extra_text_detected", "markdown_detected", "unnecessary_quotes", "excessive_length"]:
        if as_bool(row.get(column)) is True:
            return False
    return True


def critical_error(row: dict[str, Any]) -> bool:
    if not is_success(row) or is_empty(row):
        return True
    if str(row.get("rule_check_status", "")).upper() == "FAIL":
        return True
    return False


def accuracy_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.include_all_runs:
        return rows
    selected = [row for row in rows if str(row.get("run_id", "")) == str(args.accuracy_run)]
    return selected or rows


def numeric_list(rows: Iterable[dict[str, Any]], column: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = as_float(row.get(column))
        if value is not None:
            values.append(value)
    return values


def score_value(row: dict[str, Any], label: str) -> float | None:
    if label == "comet":
        for column in ["comet_score", "comet", "score"]:
            value = as_float(row.get(column))
            if value is not None:
                return value
    if label == "chrf":
        for column in ["chrf_score", "sentence_chrf", "chrf"]:
            value = as_float(row.get(column))
            if value is not None:
                return value
    return None


def bool_rate(rows: list[dict[str, Any]], column: str) -> float | None:
    values = [as_bool(row.get(column)) for row in rows if as_bool(row.get(column)) is not None]
    return rate(sum(1 for value in values if value), len(values)) if values else None


def human_mean(rows: list[dict[str, Any]], candidates: list[str]) -> float | None:
    values: list[float] = []
    for row in rows:
        for column in candidates:
            value = as_float(row.get(column))
            if value is not None:
                values.append(value)
                break
    return mean(values)


def human_usability_rate(rows: list[dict[str, Any]]) -> float | None:
    values: list[bool] = []
    for row in rows:
        for column in ["human_usability_pass", "human_usability", "usability_pass", "pass"]:
            value = as_bool(row.get(column))
            if value is not None:
                values.append(value)
                break
    return rate(sum(1 for value in values if value), len(values)) if values else None


def summarize_rows(rows: list[dict[str, Any]], args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    speed_success_rows = [row for row in rows if is_success(row)]
    acc_rows = accuracy_rows(rows, args)
    request_count = len(rows)
    success_count = sum(1 for row in rows if is_success(row))
    latency_values = numeric_list(speed_success_rows, "total_latency_ms")
    prompt_tps = numeric_list(speed_success_rows, "prompt_tokens_per_second")
    gen_tps = numeric_list(speed_success_rows, "generation_tokens_per_second")
    comet_values = [value for row in acc_rows if (value := score_value(row, "comet")) is not None]
    chrf_values = [value for row in acc_rows if (value := score_value(row, "chrf")) is not None]
    rule_pass = sum(1 for row in acc_rows if str(row.get("rule_check_status", "")).upper() == "PASS")
    rule_warn = sum(1 for row in acc_rows if str(row.get("rule_check_status", "")).upper() == "WARN")
    rule_fail = sum(1 for row in acc_rows if str(row.get("rule_check_status", "")).upper() == "FAIL")
    success_only_acc = [row for row in acc_rows if is_success(row)]

    summary = {
        "model_name": rows[0].get("model_name", "") if rows else "",
        "model_file": next((row.get("model_file", "") for row in rows if row.get("model_file")), ""),
        "quantization": next((row.get("quantization", "") for row in rows if row.get("quantization")), ""),
        "input_variant": "ALL",
        "request_count": request_count,
        "success_count": success_count,
        "failure_count": request_count - success_count,
        "success_rate": rate(success_count, request_count),
        "timeout_count": sum(1 for row in rows if is_timeout(row)),
        "empty_output_count": sum(1 for row in rows if is_empty(row)),
        "latency_mean_ms": mean(latency_values),
        "latency_p50_ms": percentile(latency_values, 50),
        "latency_p95_ms": percentile(latency_values, 95),
        "latency_min_ms": min(latency_values) if latency_values else None,
        "latency_max_ms": max(latency_values) if latency_values else None,
        "latency_stddev_ms": stddev(latency_values),
        "prompt_tps_mean": mean(prompt_tps),
        "generation_tps_mean": mean(gen_tps),
        "generation_tps_p50": percentile(gen_tps, 50),
        "generation_tps_p95": percentile(gen_tps, 95),
        "comet_mean": mean(comet_values),
        "comet_median": median(comet_values),
        "comet_stddev": stddev(comet_values),
        "chrf_corpus": first_numeric(rows, ["chrf_corpus", "corpus_chrf"]),
        "sentence_chrf_mean": mean(chrf_values),
        "protected_token_pass_rate": bool_rate(acc_rows, "protected_tokens_pass"),
        "required_term_pass_rate": bool_rate(acc_rows, "required_terms_pass"),
        "format_pass_rate": rate(sum(1 for row in acc_rows if format_pass(row)), len(acc_rows)),
        "rule_pass_rate_all": rate(rule_pass, len(acc_rows)),
        "rule_pass_rate_success_only": rate(
            sum(1 for row in success_only_acc if str(row.get("rule_check_status", "")).upper() == "PASS"),
            len(success_only_acc),
        ),
        "rule_warn_rate": rate(rule_warn, len(acc_rows)),
        "rule_fail_rate": rate(rule_fail, len(acc_rows)),
        "human_accuracy_mean": human_mean(acc_rows, ["human_accuracy", "accuracy", "human_accuracy_score"]),
        "human_fluency_mean": human_mean(acc_rows, ["human_fluency", "fluency", "human_fluency_score"]),
        "human_usability_pass_rate": human_usability_rate(acc_rows),
        "critical_error_rate": rate(sum(1 for row in acc_rows if critical_error(row)), len(acc_rows)),
    }
    status, reasons = qualification(summary, config)
    summary["qualification_status"] = status
    summary["qualification_failure_reasons"] = reasons
    return summary


def first_numeric(rows: list[dict[str, Any]], columns: list[str]) -> float | None:
    for row in rows:
        for column in columns:
            value = as_float(row.get(column))
            if value is not None:
                return value
    return None


def qualification(summary: dict[str, Any], config: dict[str, Any]) -> tuple[str, str]:
    rules = config.get("qualification", DEFAULT_QUALIFICATION)
    checks = [
        ("success_rate", ">=", "minimum_success_rate"),
        ("protected_token_pass_rate", ">=", "minimum_protected_token_pass_rate"),
        ("format_pass_rate", ">=", "minimum_format_pass_rate"),
        ("critical_error_rate", "<=", "maximum_critical_error_rate"),
    ]
    if "maximum_empty_output_rate" in rules:
        request_count = int(summary.get("request_count") or 0)
        empty_rate = rate(int(summary.get("empty_output_count") or 0), request_count)
        summary["_empty_output_rate"] = empty_rate
        checks.append(("_empty_output_rate", "<=", "maximum_empty_output_rate"))

    failures: list[str] = []
    insufficient: list[str] = []
    for metric, op, threshold_key in checks:
        threshold = as_float(rules.get(threshold_key))
        value = as_float(summary.get(metric))
        if threshold is None:
            continue
        if value is None:
            insufficient.append(metric)
            continue
        if op == ">=" and value < threshold:
            failures.append(f"{metric}={value:.6f} < {threshold_key}={threshold:.6f}")
        if op == "<=" and value > threshold:
            failures.append(f"{metric}={value:.6f} > {threshold_key}={threshold:.6f}")
    if insufficient:
        return "INSUFFICIENT_DATA", ";".join(insufficient)
    if failures:
        return "FAIL", ";".join(failures)
    return "PASS", ""


def group_summary(rows: list[dict[str, Any]], group_keys: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key, "") for key in group_keys)].append(row)
    summaries: list[dict[str, Any]] = []
    for key, group_rows in grouped.items():
        latencies = numeric_list([row for row in group_rows if is_success(row)], "total_latency_ms")
        comet_values = [value for row in group_rows if (value := score_value(row, "comet")) is not None]
        chrf_values = [value for row in group_rows if (value := score_value(row, "chrf")) is not None]
        summary = {column: "" for column in GROUP_COLUMNS}
        for column, value in zip(group_keys, key):
            summary[column] = value
        summary.update(
            {
                "request_count": len(group_rows),
                "comet_mean": mean(comet_values),
                "sentence_chrf_mean": mean(chrf_values),
                "protected_token_pass_rate": bool_rate(group_rows, "protected_tokens_pass"),
                "required_term_pass_rate": bool_rate(group_rows, "required_terms_pass"),
                "rule_pass_rate": rate(sum(1 for row in group_rows if str(row.get("rule_check_status", "")).upper() == "PASS"), len(group_rows)),
                "critical_error_rate": rate(sum(1 for row in group_rows if critical_error(row)), len(group_rows)),
                "latency_mean_ms": mean(latencies),
                "latency_p50_ms": percentile(latencies, 50),
                "latency_p95_ms": percentile(latencies, 95),
            }
        )
        summaries.append(summary)
    return summaries


def sort_group_rows(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        values: list[Any] = []
        for key in keys:
            value = row.get(key, "")
            if key == "difficulty":
                values.append(DIFFICULTY_ORDER.get(str(value), 99))
            values.append(str(value))
        return tuple(values)

    return sorted(rows, key=sort_key)


def ocr_summaries(rows: list[dict[str, Any]], report: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_pair: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        variant = row.get("input_variant", "standard")
        if variant in {"ocr_clean", "ocr_noise"}:
            key = (str(row.get("model_name", "")), str(row.get("run_id", "")), str(row.get("id", "")))
            by_pair[key][variant] = row

    paired_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key, variants in by_pair.items():
        if "ocr_clean" not in variants or "ocr_noise" not in variants:
            add_quality(report, "WARNING", key[0], "", "|".join(key), "missing OCR clean/noise pair")
            continue
        paired_rows.append((variants["ocr_clean"], variants["ocr_noise"]))

    def build(pairs: list[tuple[dict[str, Any], dict[str, Any]]], extra_key: str | None = None) -> dict[str, Any]:
        clean_rows = [clean for clean, _noise in pairs]
        noise_rows = [noise for _clean, noise in pairs]
        clean_comet = [value for row in clean_rows if (value := score_value(row, "comet")) is not None]
        noise_comet = [value for row in noise_rows if (value := score_value(row, "comet")) is not None]
        clean_chrf = [value for row in clean_rows if (value := score_value(row, "chrf")) is not None]
        noise_chrf = [value for row in noise_rows if (value := score_value(row, "chrf")) is not None]
        clean_comet_mean = mean(clean_comet)
        noise_comet_mean = mean(noise_comet)
        clean_chrf_mean = mean(clean_chrf)
        noise_chrf_mean = mean(noise_chrf)
        row = {
            "model_name": pairs[0][0].get("model_name", "") if pairs else "",
            "pair_count": len(pairs),
            "clean_comet_mean": clean_comet_mean,
            "noise_comet_mean": noise_comet_mean,
            "comet_drop": clean_comet_mean - noise_comet_mean if clean_comet_mean is not None and noise_comet_mean is not None else None,
            "clean_chrf": clean_chrf_mean,
            "noise_chrf": noise_chrf_mean,
            "chrf_drop": clean_chrf_mean - noise_chrf_mean if clean_chrf_mean is not None and noise_chrf_mean is not None else None,
            "clean_protected_token_pass_rate": bool_rate(clean_rows, "protected_tokens_pass"),
            "noise_protected_token_pass_rate": bool_rate(noise_rows, "protected_tokens_pass"),
            "clean_rule_pass_rate": rate(sum(1 for row in clean_rows if str(row.get("rule_check_status", "")).upper() == "PASS"), len(clean_rows)),
            "noise_rule_pass_rate": rate(sum(1 for row in noise_rows if str(row.get("rule_check_status", "")).upper() == "PASS"), len(noise_rows)),
            "clean_critical_error_rate": rate(sum(1 for row in clean_rows if critical_error(row)), len(clean_rows)),
            "noise_critical_error_rate": rate(sum(1 for row in noise_rows if critical_error(row)), len(noise_rows)),
        }
        row["protected_token_pass_rate_drop"] = subtract(row["clean_protected_token_pass_rate"], row["noise_protected_token_pass_rate"])
        row["rule_pass_rate_drop"] = subtract(row["clean_rule_pass_rate"], row["noise_rule_pass_rate"])
        row["critical_error_rate_increase"] = subtract(row["noise_critical_error_rate"], row["clean_critical_error_rate"])
        if extra_key:
            row[extra_key] = pairs[0][0].get(extra_key, "")
        return row

    by_model: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    by_subcategory: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for pair in paired_rows:
        model = str(pair[0].get("model_name", ""))
        subcategory = str(pair[0].get("subcategory", ""))
        by_model[model].append(pair)
        by_subcategory[(model, subcategory)].append(pair)

    ocr_summary = [build(pairs) for _model, pairs in sorted(by_model.items())]
    ocr_subcategory = [build(pairs, "subcategory") for _key, pairs in sorted(by_subcategory.items())]
    return ocr_summary, ocr_subcategory


def subtract(left: Any, right: Any) -> float | None:
    left_f = as_float(left)
    right_f = as_float(right)
    if left_f is None or right_f is None:
        return None
    return left_f - right_f


def stability_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_item: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("model_name", "")), str(row.get("id", "")), str(row.get("input_variant", "standard")))
        by_item[key].append(row)

    by_model: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, item_rows in by_item.items():
        runs = {str(row.get("run_id", "")) for row in item_rows}
        if len(runs) < 2:
            continue
        translations = [str(row.get("translation_ja", "")).strip().replace("\r\n", "\n").replace("\r", "\n") for row in item_rows]
        unique_count = len(set(translations))
        item_summary = {
            "model_name": key[0],
            "input_variant": key[2],
            "exact_match": unique_count == 1,
            "unique_translation_count": unique_count,
            "latencies": numeric_list(item_rows, "total_latency_ms"),
            "generation_tps": numeric_list(item_rows, "generation_tokens_per_second"),
        }
        by_model[(key[0], key[2])].append(item_summary)

    summaries: list[dict[str, Any]] = []
    for (model, variant), items in sorted(by_model.items()):
        latency_stdevs = [stddev(item["latencies"]) for item in items if stddev(item["latencies"]) is not None]
        tps_stdevs = [stddev(item["generation_tps"]) for item in items if stddev(item["generation_tps"]) is not None]
        summaries.append(
            {
                "model_name": model,
                "input_variant": variant,
                "multi_run_item_count": len(items),
                "exact_output_match_rate": rate(sum(1 for item in items if item["exact_match"]), len(items)),
                "unique_translation_count_mean": mean([float(item["unique_translation_count"]) for item in items]),
                "latency_run_variation": mean([value for value in latency_stdevs if value is not None]),
                "generation_tps_run_variation": mean([value for value in tps_stdevs if value is not None]),
            }
        )
    return summaries


def failed_cases(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    minimum_comet = as_float(config.get("failed_case", {}).get("minimum_comet_score"))
    failed: list[dict[str, Any]] = []
    for row in rows:
        comet_score = score_value(row, "comet")
        human_pass = human_usability_rate([row])
        should_include = (
            str(row.get("rule_check_status", "")).upper() == "FAIL"
            or critical_error(row)
            or as_bool(row.get("protected_tokens_pass")) is False
            or not is_success(row)
            or is_empty(row)
            or (minimum_comet is not None and comet_score is not None and comet_score < minimum_comet)
            or human_pass == 0
        )
        if should_include:
            failed.append(
                {
                    "model_name": row.get("model_name", ""),
                    "run_id": row.get("run_id", ""),
                    "id": row.get("id", ""),
                    "category": row.get("category", ""),
                    "subcategory": row.get("subcategory", ""),
                    "difficulty": row.get("difficulty", ""),
                    "input_variant": row.get("input_variant", "standard"),
                    "source_en": row.get("source_en", ""),
                    "reference_ja": row.get("reference_ja", ""),
                    "translation_ja": row.get("translation_ja", ""),
                    "comet_score": comet_score,
                    "rule_check_status": row.get("rule_check_status", ""),
                    "failure_reasons": row.get("failure_reasons", ""),
                    "human_comment": row.get("human_comment", row.get("comment", "")),
                }
            )
    return sorted(failed, key=lambda row: (str(row["model_name"]), str(row["input_variant"]), str(row["id"]), str(row["run_id"])))


def final_comparison(
    model_summaries: list[dict[str, Any]],
    ocr_summary: list[dict[str, Any]],
    stability: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ocr_by_model = {row.get("model_name", ""): row for row in ocr_summary}
    stability_by_model: dict[str, dict[str, Any]] = {}
    for row in stability:
        stability_by_model.setdefault(str(row.get("model_name", "")), row)

    rows: list[dict[str, Any]] = []
    for summary in model_summaries:
        model = str(summary.get("model_name", ""))
        rows.append(
            {
                "model_name": model,
                "model_file": summary.get("model_file", ""),
                "quantization": summary.get("quantization", ""),
                "test_item_count": summary.get("request_count", ""),
                "success_rate": summary.get("success_rate"),
                "comet_mean": summary.get("comet_mean"),
                "chrf_corpus": summary.get("chrf_corpus"),
                "protected_token_pass_rate": summary.get("protected_token_pass_rate"),
                "required_term_pass_rate": summary.get("required_term_pass_rate"),
                "format_pass_rate": summary.get("format_pass_rate"),
                "critical_error_rate": summary.get("critical_error_rate"),
                "human_usability_pass_rate": summary.get("human_usability_pass_rate"),
                "latency_p50_ms": summary.get("latency_p50_ms"),
                "latency_p95_ms": summary.get("latency_p95_ms"),
                "generation_tps_mean": summary.get("generation_tps_mean"),
                "peak_vram_mb": summary.get("peak_vram_mb", ""),
                "ocr_comet_drop": ocr_by_model.get(model, {}).get("comet_drop"),
                "exact_output_match_rate": stability_by_model.get(model, {}).get("exact_output_match_rate"),
                "qualification_status": summary.get("qualification_status", ""),
                "qualification_failure_reasons": summary.get("qualification_failure_reasons", ""),
            }
        )
    return sorted(rows, key=final_sort_key)


def final_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    qualification_order = {"PASS": 0, "INSUFFICIENT_DATA": 1, "FAIL": 2}
    return (
        qualification_order.get(str(row.get("qualification_status", "")), 3),
        as_float(row.get("critical_error_rate")) if as_float(row.get("critical_error_rate")) is not None else 999,
        -(as_float(row.get("human_usability_pass_rate")) or -1),
        -(as_float(row.get("comet_mean")) or -999),
        as_float(row.get("latency_p95_ms")) if as_float(row.get("latency_p95_ms")) is not None else 999999999,
        str(row.get("model_name", "")),
    )


def item_set_quality(bundles: list[ModelBundle], report: list[dict[str, Any]]) -> None:
    if len(bundles) < 2:
        return
    sets = {bundle.model_name: {(str(row.get("id", "")), str(row.get("input_variant", "standard"))) for row in bundle.rows} for bundle in bundles}
    baseline_model, baseline_items = next(iter(sets.items()))
    for model, items in sets.items():
        if model == baseline_model:
            continue
        missing = baseline_items - items
        extra = items - baseline_items
        if missing:
            add_quality(report, "WARNING", model, "", "", f"missing {len(missing)} items compared with {baseline_model}")
        if extra:
            add_quality(report, "WARNING", model, "", "", f"extra {len(extra)} items compared with {baseline_model}")


def write_all_reports(
    output_dir: Path,
    overwrite: bool,
    model_summaries: list[dict[str, Any]],
    category_rows: list[dict[str, Any]],
    subcategory_rows: list[dict[str, Any]],
    difficulty_rows: list[dict[str, Any]],
    ocr_rows: list[dict[str, Any]],
    ocr_subcategory_rows: list[dict[str, Any]],
    stability_rows: list[dict[str, Any]],
    failed_rows: list[dict[str, Any]],
    quality_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    summary_json: dict[str, Any],
) -> list[str]:
    outputs = [
        ("model_summary.csv", model_summaries, MODEL_SUMMARY_COLUMNS),
        ("category_summary.csv", category_rows, GROUP_COLUMNS),
        ("subcategory_summary.csv", subcategory_rows, GROUP_COLUMNS),
        ("difficulty_summary.csv", difficulty_rows, GROUP_COLUMNS),
        (
            "ocr_summary.csv",
            ocr_rows,
            [
                "model_name",
                "pair_count",
                "clean_comet_mean",
                "noise_comet_mean",
                "comet_drop",
                "clean_chrf",
                "noise_chrf",
                "chrf_drop",
                "clean_protected_token_pass_rate",
                "noise_protected_token_pass_rate",
                "protected_token_pass_rate_drop",
                "clean_rule_pass_rate",
                "noise_rule_pass_rate",
                "rule_pass_rate_drop",
                "clean_critical_error_rate",
                "noise_critical_error_rate",
                "critical_error_rate_increase",
            ],
        ),
        (
            "ocr_subcategory_summary.csv",
            ocr_subcategory_rows,
            [
                "model_name",
                "subcategory",
                "pair_count",
                "clean_comet_mean",
                "noise_comet_mean",
                "comet_drop",
                "clean_chrf",
                "noise_chrf",
                "chrf_drop",
                "clean_protected_token_pass_rate",
                "noise_protected_token_pass_rate",
                "protected_token_pass_rate_drop",
                "clean_rule_pass_rate",
                "noise_rule_pass_rate",
                "rule_pass_rate_drop",
                "clean_critical_error_rate",
                "noise_critical_error_rate",
                "critical_error_rate_increase",
            ],
        ),
        (
            "stability_summary.csv",
            stability_rows,
            [
                "model_name",
                "input_variant",
                "multi_run_item_count",
                "exact_output_match_rate",
                "unique_translation_count_mean",
                "latency_run_variation",
                "generation_tps_run_variation",
            ],
        ),
        ("failed_cases.csv", failed_rows, FAILED_CASE_COLUMNS),
        ("data_quality_report.csv", quality_rows, ["severity", "model_name", "file", "key", "issue"]),
        ("final_comparison.csv", final_rows, FINAL_COLUMNS),
    ]
    written: list[str] = []
    for name, rows, columns in outputs:
        path = output_dir / name
        write_csv(path, rows, columns, overwrite)
        written.append(str(path))
    json_path = output_dir / "summary.json"
    write_json(json_path, summary_json, overwrite)
    written.append(str(json_path))
    return written


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    try:
        args = parse_args(argv or sys.argv[1:])
        setup_logging(args)
        config = load_config(args.config)
        results_dir = Path(args.results_dir)
        output_dir = Path(args.output_dir)
        filters = model_filter(args.models)
        model_dirs = find_model_dirs(results_dir, filters)
        if not model_dirs:
            raise AggregateError("No model result directories found", 3)

        quality_rows: list[dict[str, Any]] = []
        bundles: list[ModelBundle] = []
        for model_dir in model_dirs:
            bundle = load_model_bundle(model_dir, filters, quality_rows)
            if bundle is not None:
                bundles.append(bundle)
                logging.info("Loaded model %s: rows=%s files=%s", bundle.model_name, len(bundle.rows), len(bundle.files))
        if not bundles:
            write_csv(output_dir / "data_quality_report.csv", quality_rows, ["severity", "model_name", "file", "key", "issue"], args.overwrite)
            raise AggregateError("No aggregatable models found", 4)

        item_set_quality(bundles, quality_rows)
        all_rows = [row for bundle in bundles for row in bundle.rows]
        for row in all_rows:
            latency = as_float(row.get("total_latency_ms"))
            if latency is not None and latency < 0:
                add_quality(quality_rows, "ERROR", str(row.get("model_name", "")), "", key_string(key_for(row, "")), "negative latency")

        model_summaries = [summarize_rows(bundle.rows, args, config) for bundle in bundles]
        category_rows = sort_group_rows(group_summary(all_rows, ["model_name", "category", "input_variant"]), ["model_name", "category", "input_variant"])
        subcategory_rows = sort_group_rows(
            group_summary(all_rows, ["model_name", "category", "subcategory", "input_variant"]),
            ["model_name", "category", "subcategory", "input_variant"],
        )
        difficulty_rows = sort_group_rows(group_summary(all_rows, ["model_name", "difficulty", "input_variant"]), ["model_name", "difficulty", "input_variant"])
        ocr_rows, ocr_subcategory_rows = ocr_summaries(all_rows, quality_rows)
        stability_rows = stability_summary(all_rows)
        failed_rows = failed_cases(all_rows, config)
        final_rows = final_comparison(model_summaries, ocr_rows, stability_rows)

        summary_json = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "results_dir": str(results_dir),
            "model_count": len(bundles),
            "row_count": len(all_rows),
            "data_quality_issue_count": len(quality_rows),
            "percentile_method": "linear_interpolation",
            "models": model_summaries,
            "output_files": [
                "model_summary.csv",
                "category_summary.csv",
                "subcategory_summary.csv",
                "difficulty_summary.csv",
                "ocr_summary.csv",
                "ocr_subcategory_summary.csv",
                "stability_summary.csv",
                "failed_cases.csv",
                "data_quality_report.csv",
                "final_comparison.csv",
                "summary.json",
            ],
        }
        written = write_all_reports(
            output_dir=output_dir,
            overwrite=args.overwrite,
            model_summaries=sort_group_rows(model_summaries, ["model_name"]),
            category_rows=category_rows,
            subcategory_rows=subcategory_rows,
            difficulty_rows=difficulty_rows,
            ocr_rows=sort_group_rows(ocr_rows, ["model_name"]),
            ocr_subcategory_rows=sort_group_rows(ocr_subcategory_rows, ["model_name", "subcategory"]),
            stability_rows=sort_group_rows(stability_rows, ["model_name", "input_variant"]),
            failed_rows=failed_rows,
            quality_rows=quality_rows,
            final_rows=final_rows,
            summary_json=summary_json,
        )
        elapsed = time.perf_counter() - started
        logging.info(
            "Aggregation completed: models=%s rows=%s quality_issues=%s elapsed=%.3fs",
            len(bundles),
            len(all_rows),
            len(quality_rows),
            elapsed,
        )
        print(f"models={len(bundles)} rows={len(all_rows)} quality_issues={len(quality_rows)} output_dir={output_dir}")
        logging.debug("Wrote files: %s", written)
        return 0
    except AggregateError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    except Exception:
        logging.exception("Unexpected internal error")
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
