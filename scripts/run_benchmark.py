#!/usr/bin/env python3
"""Run translation latency benchmarks against an OpenAI-compatible llama-server."""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import json
import logging
import platform
import random
import socket
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_API_MODEL = "local-model"
DEFAULT_SYSTEM_PROMPT = """You are a professional game localization translator.
Translate the English game text into natural Japanese.

Output only the Japanese translation.
Do not add explanations, headings, quotation marks, or alternative translations.
Preserve numbers, percentages, key names, placeholders, tags, and proper nouns.
Do not invent information that is not present in the source text.
"""

REQUIRED_INPUT_COLUMNS = [
    "id",
    "category",
    "subcategory",
    "difficulty",
    "source_en",
    "reference_ja",
    "protected_tokens",
    "required_terms",
    "evaluation_focus",
]
INHERITED_COLUMNS = [
    "id",
    "category",
    "subcategory",
    "difficulty",
    "context_ja",
    "source_en",
    "clean_source_en",
    "reference_ja",
    "required_terms",
    "protected_tokens",
    "evaluation_focus",
]
OUTPUT_COLUMNS = INHERITED_COLUMNS + [
    "model_name",
    "api_model",
    "model_file",
    "quantization",
    "run_id",
    "input_variant",
    "execution_index",
    "started_at",
    "completed_at",
    "system_prompt_hash",
    "input_text",
    "translation_ja",
    "reasoning_content",
    "finish_reason",
    "total_latency_ms",
    "ttft_ms",
    "stream_duration_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_ms",
    "prompt_tokens_per_second",
    "generation_ms",
    "generation_tokens_per_second",
    "cached_tokens",
    "request_success",
    "http_status",
    "attempt_count",
    "retry_count",
    "error_type",
    "error",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "max_tokens",
    "seed",
    "stream",
    "timeout_seconds",
    "total_elapsed_with_retries_ms",
    "concurrency",
    "requests_per_second",
    "batch_total_time_ms",
    "raw_response",
]

RETRY_HTTP_STATUS = {429, 500, 502, 503, 504}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_VARIANTS = {"standard", "ocr_clean", "ocr_noise", "context", "terminology"}


@dataclass
class ApiResult:
    ok: bool
    status: int | None = None
    data: dict[str, Any] | None = None
    raw_text: str = ""
    error_type: str = ""
    error: str = ""
    total_latency_ms: float | None = None
    ttft_ms: float | None = None
    stream_duration_ms: float | None = None


class BenchmarkError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blank_if_none(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return value


def parse_csv_list(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def parse_json_option(value: str | None, label: str) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"{label} is not valid JSON: {exc}", 1) from exc


def read_json_file(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BenchmarkError(f"Failed to read {label}: {exc}", 1) from exc
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"{label} is not valid JSON: {exc}", 1) from exc


def load_system_prompt(path: Path | None) -> str:
    if path is None:
        return DEFAULT_SYSTEM_PROMPT.strip()
    try:
        return path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise BenchmarkError(f"Failed to read system prompt file: {exc}", 1) from exc


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


def load_input_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            rows = [{key: (value if value is not None else "") for key, value in row.items()} for row in reader]
    except OSError as exc:
        raise BenchmarkError(f"Failed to read input CSV: {exc}", 2) from exc
    return rows, fieldnames


def validate_input_rows(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    missing = [name for name in REQUIRED_INPUT_COLUMNS if name not in fieldnames]
    if missing:
        raise BenchmarkError(f"Missing required columns: {', '.join(missing)}", 3)

    if args.input_variant == "ocr_clean" and "clean_source_en" not in fieldnames:
        raise BenchmarkError("ocr_clean requires clean_source_en column", 3)
    if args.input_variant == "context" and "context_ja" not in fieldnames:
        raise BenchmarkError("context variant requires context_ja column", 3)

    errors: list[str] = []
    valid_rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for line_no, row in enumerate(rows, start=2):
        row_errors: list[str] = []
        row_id = row.get("id", "").strip()
        if not row_id:
            row_errors.append("id is empty")
        elif row_id in seen_ids:
            row_errors.append(f"duplicate id: {row_id}")
        seen_ids.add(row_id)

        if not row.get("source_en", "").strip():
            row_errors.append("source_en is empty")
        if not row.get("reference_ja", "").strip():
            row_errors.append("reference_ja is empty")
        if row.get("difficulty", "").strip() not in VALID_DIFFICULTIES:
            row_errors.append("difficulty must be easy, medium, or hard")
        if args.input_variant == "ocr_clean" and not row.get("clean_source_en", "").strip():
            row_errors.append("clean_source_en is empty for ocr_clean")
        if args.input_variant == "context" and not row.get("context_ja", "").strip():
            row_errors.append("context_ja is empty for context variant")
        if args.input_variant == "terminology" and not row.get("required_terms", "").strip():
            row_errors.append("required_terms is empty for terminology variant")

        if row_errors:
            message = f"line {line_no} ({row_id or 'no-id'}): {', '.join(row_errors)}"
            if args.skip_invalid_rows:
                logging.warning("Skipping invalid row: %s", message)
                continue
            errors.append(message)
        else:
            valid_rows.append(row)

    if errors:
        preview = "\n".join(errors[:20])
        suffix = "" if len(errors) <= 20 else f"\n... and {len(errors) - 20} more"
        raise BenchmarkError(f"Invalid input rows:\n{preview}{suffix}", 3)
    return valid_rows


def prepare_output(path: Path, args: argparse.Namespace) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and args.overwrite:
            path.unlink()
        if path.exists() and not args.resume:
            raise BenchmarkError(
                "Output file already exists. Use --resume to append/skip or --overwrite to replace it.",
                5,
            )
        with path.open("a", encoding="utf-8", newline=""):
            pass
    except BenchmarkError:
        raise
    except OSError as exc:
        raise BenchmarkError(f"Failed to prepare output CSV: {exc}", 5) from exc


def existing_result_keys(path: Path, args: argparse.Namespace) -> set[tuple[str, str, str, str]]:
    if not args.resume or not path.exists():
        return set()
    keys: set[tuple[str, str, str, str]] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if args.retry_failed and row.get("request_success", "").lower() != "true":
                    continue
                key = (
                    row.get("model_name", ""),
                    row.get("run_id", ""),
                    row.get("id", ""),
                    row.get("input_variant", ""),
                )
                if all(key):
                    keys.add(key)
    except OSError as exc:
        raise BenchmarkError(f"Failed to read existing output for resume: {exc}", 5) from exc
    return keys


def filter_rows(rows: list[dict[str, str]], args: argparse.Namespace) -> list[dict[str, str]]:
    selected = list(rows)
    ids = parse_csv_list(args.ids)
    categories = parse_csv_list(args.categories)
    if ids:
        selected = [row for row in selected if row.get("id", "") in ids]
    if categories:
        selected = [row for row in selected if row.get("category", "") in categories]
    start = max(args.start_index - 1, 0) if args.start_index is not None else 0
    end = max(args.end_index, 0) if args.end_index is not None else len(selected)
    selected = selected[start:end]
    if args.shuffle:
        rng = random.Random(args.shuffle_seed)
        rng.shuffle(selected)
    return selected


def build_input_text(row: dict[str, str], variant: str) -> str:
    source = row.get("source_en", "")
    if variant == "standard":
        return source
    if variant == "ocr_clean":
        return row.get("clean_source_en", "")
    if variant == "ocr_noise":
        return source
    if variant == "context":
        return f"Context:\n{row.get('context_ja', '')}\n\nText:\n{source}"
    if variant == "terminology":
        return f"Terminology:\n{row.get('required_terms', '')}\n\nText:\n{source}"
    raise ValueError(f"unsupported input variant: {variant}")


def make_payload(args: argparse.Namespace, system_prompt: str, input_text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.api_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": input_text},
        ],
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "stream": bool(args.stream),
    }
    optional_values = {
        "top_k": args.top_k,
        "min_p": args.min_p,
        "repeat_penalty": args.repeat_penalty,
        "frequency_penalty": args.frequency_penalty,
        "presence_penalty": args.presence_penalty,
        "stop": args.stop if args.stop else None,
        "chat_template_kwargs": args.chat_template_kwargs,
    }
    for key, value in optional_values.items():
        if value is not None:
            payload[key] = value
    return payload


def connection_for(url: str, connect_timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BenchmarkError(f"Invalid API URL: {url}", 1)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    return conn_cls(parsed.netloc, timeout=connect_timeout), path


def request_completion(
    url: str,
    payload: dict[str, Any],
    connect_timeout: float,
    read_timeout: float,
) -> ApiResult:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "text/event-stream" if payload.get("stream") else "application/json",
    }
    conn: http.client.HTTPConnection | None = None
    started = time.perf_counter()
    try:
        conn, path = connection_for(url, connect_timeout)
        conn.request("POST", path, body=body, headers=headers)
        if conn.sock is not None:
            conn.sock.settimeout(read_timeout)
        response = conn.getresponse()
        if payload.get("stream"):
            return read_streaming_response(response, started)
        raw = response.read()
        completed = time.perf_counter()
        raw_text = raw.decode("utf-8", errors="replace")
        latency_ms = (completed - started) * 1000
        if response.status < 200 or response.status >= 300:
            return ApiResult(
                ok=False,
                status=response.status,
                raw_text=raw_text,
                error_type="http",
                error=f"HTTP {response.status}: {raw_text[:500]}",
                total_latency_ms=latency_ms,
            )
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return ApiResult(
                ok=False,
                status=response.status,
                raw_text=raw_text,
                error_type="json",
                error=str(exc),
                total_latency_ms=latency_ms,
            )
        return ApiResult(ok=True, status=response.status, data=data, raw_text=raw_text, total_latency_ms=latency_ms)
    except (socket.timeout, TimeoutError) as exc:
        return ApiResult(ok=False, error_type="timeout", error=str(exc), total_latency_ms=(time.perf_counter() - started) * 1000)
    except OSError as exc:
        return ApiResult(ok=False, error_type="connection", error=str(exc), total_latency_ms=(time.perf_counter() - started) * 1000)
    finally:
        if conn is not None:
            conn.close()


def read_streaming_response(response: http.client.HTTPResponse, started: float) -> ApiResult:
    status = response.status
    if status < 200 or status >= 300:
        raw = response.read()
        completed = time.perf_counter()
        raw_text = raw.decode("utf-8", errors="replace")
        return ApiResult(
            ok=False,
            status=status,
            raw_text=raw_text,
            error_type="http",
            error=f"HTTP {status}: {raw_text[:500]}",
            total_latency_ms=(completed - started) * 1000,
        )

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason = ""
    usage: dict[str, Any] = {}
    timings: dict[str, Any] = {}
    raw_events: list[str] = []
    first_token_time: float | None = None
    last_token_time: float | None = None

    try:
        while True:
            line = response.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            text_line = stripped.decode("utf-8", errors="replace")
            raw_events.append(text_line)
            if not text_line.startswith("data:"):
                continue
            data_text = text_line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                event = json.loads(data_text)
            except json.JSONDecodeError as exc:
                return ApiResult(
                    ok=False,
                    status=status,
                    raw_text="\n".join(raw_events),
                    error_type="json",
                    error=f"Invalid stream event JSON: {exc}",
                    total_latency_ms=(time.perf_counter() - started) * 1000,
                )
            usage = event.get("usage") or usage
            timings = event.get("timings") or timings
            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or choice.get("message") or {}
            piece = delta.get("content") or ""
            reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if piece or reasoning_piece:
                now = time.perf_counter()
                if first_token_time is None:
                    first_token_time = now
                last_token_time = now
            if piece:
                content_parts.append(piece)
            if reasoning_piece:
                reasoning_parts.append(reasoning_piece)
            if choice.get("finish_reason") is not None:
                finish_reason = choice.get("finish_reason") or ""
    except (socket.timeout, TimeoutError) as exc:
        return ApiResult(
            ok=False,
            status=status,
            raw_text="\n".join(raw_events),
            error_type="timeout",
            error=f"Stream timeout: {exc}",
            total_latency_ms=(time.perf_counter() - started) * 1000,
        )
    except OSError as exc:
        return ApiResult(
            ok=False,
            status=status,
            raw_text="\n".join(raw_events),
            error_type="connection",
            error=f"Stream interrupted: {exc}",
            total_latency_ms=(time.perf_counter() - started) * 1000,
        )

    completed = time.perf_counter()
    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    data = {
        "choices": [
            {
                "message": {"content": content, "reasoning_content": reasoning},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
        "timings": timings,
    }
    ttft_ms = (first_token_time - started) * 1000 if first_token_time is not None else None
    stream_duration_ms = (
        (last_token_time - first_token_time) * 1000
        if first_token_time is not None and last_token_time is not None
        else None
    )
    return ApiResult(
        ok=True,
        status=status,
        data=data,
        raw_text="\n".join(raw_events),
        total_latency_ms=(completed - started) * 1000,
        ttft_ms=ttft_ms,
        stream_duration_ms=stream_duration_ms,
    )


def should_retry(result: ApiResult) -> bool:
    if result.error_type in {"connection", "timeout"}:
        return True
    if result.error_type == "http" and result.status in RETRY_HTTP_STATUS:
        return True
    return False


def call_with_retries(
    args: argparse.Namespace,
    system_prompt: str,
    input_text: str,
) -> tuple[ApiResult, int, float]:
    payload = make_payload(args, system_prompt, input_text)
    attempt_count = 0
    started = time.perf_counter()
    last_result = ApiResult(ok=False, error_type="internal", error="request was not attempted")
    for attempt in range(args.retries + 1):
        attempt_count = attempt + 1
        last_result = request_completion(args.api_url, payload, args.connect_timeout, args.read_timeout)
        if last_result.ok or not should_retry(last_result) or attempt >= args.retries:
            break
        logging.warning("Retrying after %s: %s", last_result.error_type, last_result.error)
        time.sleep(args.retry_wait)
    return last_result, attempt_count, (time.perf_counter() - started) * 1000


def extract_response_fields(result: ApiResult) -> tuple[dict[str, Any], str, str]:
    if not result.ok or result.data is None:
        return {}, result.error_type, result.error

    data = result.data
    choices = data.get("choices") or []
    if not choices:
        return {}, "missing_field", "choices[0] is missing"

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        content = choice.get("text")
    reasoning = message.get("reasoning_content") or message.get("reasoning") or choice.get("reasoning_content") or ""

    usage = data.get("usage") or {}
    timings = data.get("timings") or data.get("timing") or {}
    fields = {
        "translation_ja": str(content).strip() if content is not None else "",
        "reasoning_content": str(reasoning).strip() if reasoning is not None else "",
        "finish_reason": choice.get("finish_reason") or "",
        "prompt_tokens": usage.get("prompt_tokens", timings.get("prompt_n")),
        "completion_tokens": usage.get("completion_tokens", timings.get("predicted_n")),
        "total_tokens": usage.get("total_tokens"),
        "prompt_ms": timings.get("prompt_ms"),
        "prompt_tokens_per_second": timings.get("prompt_per_second"),
        "generation_ms": timings.get("predicted_ms"),
        "generation_tokens_per_second": timings.get("predicted_per_second"),
        "cached_tokens": timings.get("cache_n"),
    }
    if content is None:
        return fields, "missing_field", "message.content is missing"
    if fields["translation_ja"] == "" and not fields["reasoning_content"]:
        return fields, "empty_response", "message.content is empty"
    return fields, "", ""


def build_output_row(
    row: dict[str, str],
    args: argparse.Namespace,
    system_prompt_hash: str,
    execution_index: int,
    started_at: str,
    completed_at: str,
    input_text: str,
    api_result: ApiResult,
    attempt_count: int,
    total_elapsed_with_retries_ms: float,
    batch_total_time_ms: float | None = None,
    requests_per_second: float | None = None,
) -> dict[str, Any]:
    fields, parse_error_type, parse_error = extract_response_fields(api_result)
    success = api_result.ok and not parse_error_type
    output: dict[str, Any] = {name: row.get(name, "") for name in INHERITED_COLUMNS}
    output.update(
        {
            "model_name": args.model_name,
            "api_model": args.api_model,
            "model_file": args.model_file,
            "quantization": args.quantization,
            "run_id": args.run_id,
            "input_variant": args.input_variant,
            "execution_index": execution_index,
            "started_at": started_at,
            "completed_at": completed_at,
            "system_prompt_hash": system_prompt_hash,
            "input_text": input_text,
            "translation_ja": fields.get("translation_ja", ""),
            "reasoning_content": fields.get("reasoning_content", ""),
            "finish_reason": fields.get("finish_reason", ""),
            "total_latency_ms": api_result.total_latency_ms,
            "ttft_ms": api_result.ttft_ms,
            "stream_duration_ms": api_result.stream_duration_ms,
            "prompt_tokens": fields.get("prompt_tokens"),
            "completion_tokens": fields.get("completion_tokens"),
            "total_tokens": fields.get("total_tokens"),
            "prompt_ms": fields.get("prompt_ms"),
            "prompt_tokens_per_second": fields.get("prompt_tokens_per_second"),
            "generation_ms": fields.get("generation_ms"),
            "generation_tokens_per_second": fields.get("generation_tokens_per_second"),
            "cached_tokens": fields.get("cached_tokens"),
            "request_success": success,
            "http_status": api_result.status,
            "attempt_count": attempt_count,
            "retry_count": max(attempt_count - 1, 0),
            "error_type": "" if success else (parse_error_type or api_result.error_type),
            "error": "" if success else (parse_error or api_result.error),
            "temperature": args.temperature,
            "top_p": args.top_p,
            "top_k": args.top_k,
            "min_p": args.min_p,
            "max_tokens": args.max_tokens,
            "seed": args.seed,
            "stream": bool(args.stream),
            "timeout_seconds": args.read_timeout,
            "total_elapsed_with_retries_ms": total_elapsed_with_retries_ms,
            "concurrency": args.concurrency,
            "requests_per_second": requests_per_second,
            "batch_total_time_ms": batch_total_time_ms,
            "raw_response": api_result.raw_text if args.save_raw_response else "",
        }
    )
    return {key: blank_if_none(output.get(key)) for key in OUTPUT_COLUMNS}


def write_row(writer: csv.DictWriter, handle: Any, row: dict[str, Any]) -> None:
    writer.writerow(row)
    handle.flush()


def open_output_writer(path: Path) -> tuple[Any, csv.DictWriter]:
    file_exists = path.exists() and path.stat().st_size > 0
    handle = path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
    if not file_exists:
        writer.writeheader()
        handle.flush()
    return handle, writer


def maybe_get_json(url: str, timeout: float) -> Any:
    conn: http.client.HTTPConnection | None = None
    try:
        conn, path = connection_for(url, timeout)
        conn.request("GET", path, headers={"Accept": "application/json"})
        response = conn.getresponse()
        text = response.read().decode("utf-8", errors="replace")
        if response.status < 200 or response.status >= 300:
            return {"status": response.status, "error": text[:500]}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": response.status, "body": text[:500]}
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def sibling_url(api_url: str, path: str) -> str:
    parsed = urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def preflight(args: argparse.Namespace, system_prompt: str) -> dict[str, Any]:
    info = {
        "health": maybe_get_json(sibling_url(args.api_url, "/health"), args.connect_timeout),
        "models": maybe_get_json(sibling_url(args.api_url, "/v1/models"), args.connect_timeout),
    }
    payload = make_payload(args, system_prompt, "Press E to interact.")
    payload["stream"] = False
    payload["max_tokens"] = min(int(args.max_tokens), 32)
    result = request_completion(args.api_url, payload, args.connect_timeout, args.read_timeout)
    fields, error_type, error = extract_response_fields(result)
    if not result.ok or error_type:
        raise BenchmarkError(f"API preflight failed: {error_type or result.error_type} {error or result.error}", 4)
    logging.info("API preflight succeeded: HTTP %s", result.status)
    info["preflight"] = {
        "status": result.status,
        "latency_ms": result.total_latency_ms,
        "finish_reason": fields.get("finish_reason", ""),
    }
    return info


def warmup(args: argparse.Namespace, system_prompt: str, first_text: str) -> int:
    if args.warmup_count <= 0:
        return 0
    success_count = 0
    warmup_text = first_text or "Press E to interact."
    logging.info("Warmup started: count=%s", args.warmup_count)
    for index in range(1, args.warmup_count + 1):
        result, attempt_count, _elapsed = call_with_retries(args, system_prompt, warmup_text)
        fields, error_type, error = extract_response_fields(result)
        if result.ok and not error_type:
            success_count += 1
            logging.info("Warmup %s/%s succeeded in %.1f ms", index, args.warmup_count, result.total_latency_ms or 0)
        else:
            logging.warning(
                "Warmup %s/%s failed after %s attempts: %s %s",
                index,
                args.warmup_count,
                attempt_count,
                error_type or result.error_type,
                error or result.error,
            )
    if success_count == 0:
        raise BenchmarkError("All warmup requests failed", 4)
    return success_count


def process_one(
    row: dict[str, str],
    args: argparse.Namespace,
    system_prompt: str,
    system_prompt_hash: str,
    execution_index: int,
) -> dict[str, Any]:
    input_text = build_input_text(row, args.input_variant)
    started_at = now_iso()
    api_result, attempt_count, total_elapsed_with_retries_ms = call_with_retries(args, system_prompt, input_text)
    completed_at = now_iso()
    return build_output_row(
        row=row,
        args=args,
        system_prompt_hash=system_prompt_hash,
        execution_index=execution_index,
        started_at=started_at,
        completed_at=completed_at,
        input_text=input_text,
        api_result=api_result,
        attempt_count=attempt_count,
        total_elapsed_with_retries_ms=total_elapsed_with_retries_ms,
    )


def print_progress(done: int, total: int, row: dict[str, Any], started_perf: float) -> None:
    elapsed = time.perf_counter() - started_perf
    status = "PASS" if row.get("request_success") == "true" else "FAIL"
    latency = row.get("total_latency_ms") or ""
    tok_s = row.get("generation_tokens_per_second") or ""
    print(f"[{done}/{total}] {row.get('id')} {row.get('category')} {status} {latency} ms {tok_s} tok/s elapsed={elapsed:.1f}s")


def write_metadata(
    args: argparse.Namespace,
    output_path: Path,
    started_at: str,
    completed_at: str,
    input_sha256: str,
    system_prompt_hash: str,
    preflight_info: dict[str, Any],
    total_items: int,
    success_count: int,
    failure_count: int,
) -> None:
    metadata_path = Path(args.metadata_output) if args.metadata_output else output_path.parent / "run_metadata.json"
    metadata = {
        "model_name": args.model_name,
        "model_file": args.model_file,
        "quantization": args.quantization,
        "run_id": args.run_id,
        "input_variant": args.input_variant,
        "api_url": args.api_url,
        "api_model": args.api_model,
        "llama_cpp_version": "",
        "started_at": started_at,
        "completed_at": completed_at,
        "input_file": str(Path(args.input).resolve()),
        "input_file_sha256": input_sha256,
        "system_prompt_sha256": system_prompt_hash,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "min_p": args.min_p,
        "max_tokens": args.max_tokens,
        "seed": args.seed,
        "warmup_count": args.warmup_count,
        "total_items": total_items,
        "success_count": success_count,
        "failure_count": failure_count,
        "stream": bool(args.stream),
        "concurrency": args.concurrency,
        "connect_timeout": args.connect_timeout,
        "read_timeout": args.read_timeout,
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "cpu": platform.processor(),
        "preflight": preflight_info,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(rows: list[dict[str, Any]], total_elapsed_s: float, args: argparse.Namespace, output_path: Path) -> int:
    successes = [row for row in rows if row.get("request_success") == "true"]
    failures = len(rows) - len(successes)
    latencies = [float(row["total_latency_ms"]) for row in successes if row.get("total_latency_ms")]
    gen_speeds = [float(row["generation_tokens_per_second"]) for row in successes if row.get("generation_tokens_per_second")]
    avg_latency = statistics.fmean(latencies) if latencies else None
    p50 = statistics.median(latencies) if latencies else None
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else None)
    avg_gen_speed = statistics.fmean(gen_speeds) if gen_speeds else None
    success_rate = (len(successes) / len(rows) * 100) if rows else 0.0

    print("\nSummary")
    print(f"model_name: {args.model_name}")
    print(f"input_variant: {args.input_variant}")
    print(f"total_items: {len(rows)}")
    print(f"success_count: {len(successes)}")
    print(f"failure_count: {failures}")
    print(f"success_rate: {success_rate:.1f}%")
    print(f"avg_latency_ms: {blank_if_none(avg_latency)}")
    print(f"p50_latency_ms: {blank_if_none(p50)}")
    print(f"p95_latency_ms: {blank_if_none(p95)}")
    print(f"avg_generation_tokens_per_second: {blank_if_none(avg_gen_speed)}")
    print(f"total_elapsed_seconds: {total_elapsed_s:.1f}")
    print(f"output_file: {output_path}")
    return 0 if successes or not rows else 6


def run_benchmark(
    rows: list[dict[str, str]],
    args: argparse.Namespace,
    output_path: Path,
    system_prompt: str,
    system_prompt_hash: str,
    start_perf: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    handle, writer = open_output_writer(output_path)
    try:
        if args.concurrency <= 1:
            for done, (execution_index, row) in enumerate(enumerate(rows, start=1), start=1):
                result_row = process_one(row, args, system_prompt, system_prompt_hash, execution_index)
                write_row(writer, handle, result_row)
                results.append(result_row)
                if args.progress_interval > 0 and (done % args.progress_interval == 0 or done == len(rows)):
                    print_progress(done, len(rows), result_row, start_perf)
                if args.request_delay_ms > 0 and done < len(rows):
                    time.sleep(args.request_delay_ms / 1000)
        else:
            batch_start = time.perf_counter()
            indexed_rows = list(enumerate(rows, start=1))
            with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                future_map = {}
                for execution_index, row in indexed_rows:
                    future = executor.submit(process_one, row, args, system_prompt, system_prompt_hash, execution_index)
                    future_map[future] = execution_index
                    if args.request_delay_ms > 0:
                        time.sleep(args.request_delay_ms / 1000)
                for done, future in enumerate(as_completed(future_map), start=1):
                    result_row = future.result()
                    batch_total_ms = (time.perf_counter() - batch_start) * 1000
                    result_row["batch_total_time_ms"] = blank_if_none(batch_total_ms)
                    result_row["requests_per_second"] = blank_if_none(done / (batch_total_ms / 1000) if batch_total_ms else None)
                    write_row(writer, handle, result_row)
                    results.append(result_row)
                    if args.progress_interval > 0 and (done % args.progress_interval == 0 or done == len(rows)):
                        print_progress(done, len(rows), result_row, start_perf)
    finally:
        handle.close()
    return results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local LLM translation benchmark.")
    parser.add_argument("--input", required=True, help="Input benchmark CSV")
    parser.add_argument("--output", required=True, help="Output raw results CSV")
    parser.add_argument("--model-name", required=True, help="Model name used for aggregation")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--api-model", default=DEFAULT_API_MODEL, help="Model value sent to the API")
    parser.add_argument("--model-file", default="")
    parser.add_argument("--quantization", default="")
    parser.add_argument("--run-id", default=1, type=int)
    parser.add_argument("--input-variant", choices=sorted(VALID_VARIANTS), default="standard")
    parser.add_argument("--system-prompt-file", type=Path)
    parser.add_argument("--temperature", default=0.0, type=float)
    parser.add_argument("--top-p", default=1.0, type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--min-p", type=float)
    parser.add_argument("--repeat-penalty", type=float)
    parser.add_argument("--frequency-penalty", type=float)
    parser.add_argument("--presence-penalty", type=float)
    parser.add_argument("--stop", action="append", help="Stop sequence; may be specified multiple times")
    parser.add_argument("--chat-template-kwargs", help="JSON object sent as chat_template_kwargs")
    parser.add_argument("--chat-template-kwargs-file", type=Path, help="JSON file for chat_template_kwargs")
    parser.add_argument("--max-tokens", default=256, type=int)
    parser.add_argument("--seed", default=42, type=int)
    parser.add_argument("--timeout", default=60.0, type=float, help="Read timeout in seconds")
    parser.add_argument("--connect-timeout", default=10.0, type=float)
    parser.add_argument("--read-timeout", type=float)
    parser.add_argument("--retries", default=1, type=int)
    parser.add_argument("--retry-wait", default=2.0, type=float)
    parser.add_argument("--warmup-count", default=5, type=int)
    parser.add_argument("--start-index", type=int, help="1-based inclusive start index after filters")
    parser.add_argument("--end-index", type=int, help="1-based inclusive end index after filters")
    parser.add_argument("--ids", help="Comma-separated IDs to run")
    parser.add_argument("--categories", help="Comma-separated categories to run")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--request-delay-ms", default=0, type=int)
    parser.add_argument("--save-raw-response", action="store_true")
    parser.add_argument("--log-file")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--shuffle-seed", default=42, type=int)
    parser.add_argument("--skip-invalid-rows", action="store_true")
    parser.add_argument("--progress-interval", default=1, type=int)
    parser.add_argument("--concurrency", default=1, type=int)
    parser.add_argument("--metadata-output")
    args = parser.parse_args(argv)

    if args.retries < 0 or args.warmup_count < 0 or args.concurrency < 1:
        raise BenchmarkError("retries, warmup-count, and concurrency must be non-negative/positive values", 1)
    args.read_timeout = args.timeout if args.read_timeout is None else args.read_timeout
    if args.chat_template_kwargs and args.chat_template_kwargs_file:
        raise BenchmarkError("Use either --chat-template-kwargs or --chat-template-kwargs-file, not both", 1)
    if args.chat_template_kwargs_file:
        args.chat_template_kwargs = read_json_file(args.chat_template_kwargs_file, "chat-template-kwargs file")
    else:
        args.chat_template_kwargs = parse_json_option(args.chat_template_kwargs, "--chat-template-kwargs")
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        setup_logging(args)
        input_path = Path(args.input)
        output_path = Path(args.output)
        if not input_path.exists():
            raise BenchmarkError(f"Input file does not exist: {input_path}", 2)

        system_prompt = load_system_prompt(args.system_prompt_file)
        system_prompt_hash = sha256_text(system_prompt)
        rows, fieldnames = load_input_rows(input_path)
        rows = validate_input_rows(rows, fieldnames, args)
        rows = filter_rows(rows, args)
        prepare_output(output_path, args)

        resume_keys = existing_result_keys(output_path, args)
        if resume_keys:
            before = len(rows)
            rows = [
                row
                for row in rows
                if (args.model_name, str(args.run_id), row.get("id", ""), args.input_variant) not in resume_keys
            ]
            logging.info("Resume skipped %s existing rows", before - len(rows))

        logging.info("Benchmark started: model=%s input=%s output=%s api=%s variant=%s", args.model_name, input_path, output_path, args.api_url, args.input_variant)
        run_started_at = now_iso()
        input_hash = sha256_file(input_path)
        preflight_info = preflight(args, system_prompt)
        first_text = build_input_text(rows[0], args.input_variant) if rows else "Press E to interact."
        warmup_success = warmup(args, system_prompt, first_text)
        preflight_info["warmup_success_count"] = warmup_success

        start_perf = time.perf_counter()
        result_rows = run_benchmark(rows, args, output_path, system_prompt, system_prompt_hash, start_perf)
        total_elapsed_s = time.perf_counter() - start_perf
        success_count = sum(1 for row in result_rows if row.get("request_success") == "true")
        failure_count = len(result_rows) - success_count
        completed_at = now_iso()
        write_metadata(
            args=args,
            output_path=output_path,
            started_at=run_started_at,
            completed_at=completed_at,
            input_sha256=input_hash,
            system_prompt_hash=system_prompt_hash,
            preflight_info=preflight_info,
            total_items=len(result_rows),
            success_count=success_count,
            failure_count=failure_count,
        )
        logging.info("Benchmark completed: success=%s failure=%s elapsed=%.1fs", success_count, failure_count, total_elapsed_s)
        return summarize(result_rows, total_elapsed_s, args, output_path)
    except KeyboardInterrupt:
        logging.error("Interrupted by user")
        return 130
    except BenchmarkError as exc:
        logging.error("%s", exc)
        return exc.exit_code
    except Exception:
        logging.exception("Unexpected internal error")
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
