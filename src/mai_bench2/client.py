from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from openai import OpenAI

from mai_bench2.config import EndpointConfig
from mai_bench2.types import ChatResult, TokenCounts, ToolCall
from mai_bench2.usage import add_counts, extract_usage

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504, 529}
# Seconds. Tests inject sleep_fn, so these are the real-world values, not
# test-speed ones: 10ms of backoff against a rate-limited router is no backoff.
# "all providers, keys, and models were exhausted" clears on a timescale of
# minutes, so the tail of this schedule is deliberately long.
_BACKOFF_SECONDS = (2.0, 8.0, 20.0, 45.0, 90.0)
# A server may ask for an unreasonable wait; cap what we will honour.
_MAX_RETRY_AFTER = 120.0
# GLM: thinking.type=enabled and reasoning_effort in {low, high, max}.
_GLM_EFFORT = {
    "none": "low",
    "off": "low",
    "disabled": "low",
    "minimal": "low",
    "min": "low",
    "low": "low",
    "medium": "high",
    "med": "high",
    "high": "high",
    "max": "max",
    "xhigh": "max",
    "xxhigh": "max",
    "ultra": "max",
}
_ANTHROPIC_EFFORT = {
    "minimal": "low",
    "min": "low",
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "max": "max",
    "xhigh": "max",
    "xxhigh": "max",
    "ultra": "max",
}
_ANTHROPIC_OFF = frozenset({"none", "off", "disabled"})
_GEMINI_LEVEL = {
    "none": "minimal",
    "off": "minimal",
    "disabled": "minimal",
    "minimal": "minimal",
    "min": "minimal",
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "max": "high",
    "xhigh": "high",
    "xxhigh": "high",
    "ultra": "high",
}


class ChatClient:
    def __init__(
        self,
        endpoint: EndpointConfig,
        role: str,
        cache_dir: Path,
        no_cache: bool,
        create_fn=None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self._endpoint = endpoint
        self._role = role
        self._cache_dir = Path(cache_dir)
        self._no_cache = no_cache
        self._sleep = sleep_fn
        self._usage = TokenCounts()
        self._usage_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        raw_limit = endpoint.http_limit
        if raw_limit is None:
            self._http_sema = None
        else:
            self._http_sema = threading.BoundedSemaphore(max(1, int(raw_limit)))
        self._sample = 0

        if create_fn is None:
            openai_client = OpenAI(
                base_url=endpoint.base_url,
                api_key=endpoint.api_key,
                timeout=endpoint.timeout_s,
                # This class owns the retry policy. The SDK defaults to 2 retries
                # of its own, which multiplied with ours into 9 upstream requests
                # per logical call and hammered an already-exhausted router.
                max_retries=0,
            )
            create_fn = openai_client.chat.completions.create
        self._create = create_fn

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict] | None = None,
    ) -> ChatResult:
        effective_temperature = (
            self._endpoint.temperature if temperature is None else temperature
        )
        effective_max_tokens = (
            self._endpoint.max_tokens if max_tokens is None else max_tokens
        )
        cache_path = self._cache_path(
            messages,
            max_tokens=effective_max_tokens,
            temperature=effective_temperature,
            tools=tools,
        )

        if not self._no_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text())
            usage = TokenCounts(**cached["usage"])
            usage.cached_requests += 1
            tool_calls = [
                ToolCall(item["id"], item["name"], item["arguments"])
                for item in cached.get("tool_calls", [])
            ]
            with self._usage_lock:
                self._usage = add_counts(self._usage, usage)
            return ChatResult(
                text=cached["text"],
                usage=usage,
                cached=True,
                usage_missing=bool(usage.usage_missing),
                tool_calls=tool_calls,
            )

        kwargs = self._request_kwargs(
            messages,
            max_tokens=effective_max_tokens,
            temperature=effective_temperature,
            tools=tools,
        )
        try:
            with self._http_slot():
                response = self._create_with_retries(kwargs)
            text, tool_calls, usage, usage_missing = _unpack_completion(response)
        except Exception as error:
            if not is_content_block(error):
                raise
            text, tool_calls, usage, usage_missing = "", [], TokenCounts(), True
        if is_content_block(text) and not tool_calls:
            text = ""
        with self._usage_lock:
            self._usage = add_counts(self._usage, usage)
        result = ChatResult(
            text=text,
            usage=usage,
            cached=False,
            usage_missing=usage_missing,
            tool_calls=tool_calls,
        )

        if not self._no_cache:
            payload = json.dumps(
                {
                    "text": text,
                    "usage": asdict(usage),
                    "tool_calls": [asdict(call) for call in tool_calls],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            with self._cache_lock:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(payload)

        return result

    def probe(self, messages: list[dict], *, max_tokens: int = 16) -> None:
        """A reasoning endpoint can spend the whole budget thinking, so max_tokens=1
        was a needlessly sharp edge for a liveness check."""
        kwargs = self._request_kwargs(
            messages,
            max_tokens=max_tokens,
            temperature=self._endpoint.temperature,
            tools=None,
        )
        with self._http_slot():
            self._create_with_retries(kwargs)

    @contextlib.contextmanager
    def _http_slot(self):
        sema = self._http_sema
        if sema is None:
            yield
            return
        sema.acquire()  # blocking, no timeout
        try:
            yield
        finally:
            sema.release()

    def set_sample(self, sample: int) -> None:
        """Repeat index. It joins the cache key so run k is not run 0 replayed."""
        self._sample = int(sample)

    def usage_snapshot(self) -> TokenCounts:
        with self._usage_lock:
            return replace(self._usage)

    def _request_kwargs(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None,
        temperature: float,
        tools: list[dict] | None,
    ) -> dict:
        kwargs = {"model": self._endpoint.model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        extra = dict(self._endpoint.extra_body) if self._endpoint.extra_body else {}
        _apply_request_style(
            kwargs,
            style=self._endpoint.request_style,
            effort=self._endpoint.reasoning_effort,
            extra=extra,
        )
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    def _cache_path(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None,
        temperature: float,
        tools: list[dict] | None,
    ) -> Path:
        cache_key = {
            "role": self._role,
            "base_url": self._endpoint.base_url,
            "model": self._endpoint.model,
            "temperature": temperature,
            "reasoning_effort": self._endpoint.reasoning_effort,
            "request_style": self._endpoint.request_style,
            "extra_body": self._endpoint.extra_body,
            "messages": messages,
            "max_tokens": max_tokens,
            "tools": tools,
            "sample": self._sample,
        }
        canonical = json.dumps(cache_key, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return self._cache_dir / "llm" / f"{digest}.json"

    def _create_with_retries(self, kwargs: dict):
        attempts = max(1, int(self._endpoint.max_attempts))
        for attempt in range(attempts):
            try:
                return self._create(**kwargs)
            except Exception as error:
                if not _is_retryable(error) or attempt == attempts - 1:
                    raise
                self._sleep(retry_delay(error, attempt))
        raise AssertionError("unreachable")


def is_content_block(source: object) -> bool:
    """Gemini (and similar proxies) may HTTP 200 an error JSON instead of a completion."""
    blob = _flatten_error(source).upper()
    return "PROHIBITED_CONTENT" in blob or "BLOCKED THE REQUEST" in blob


def _flatten_error(source: object) -> str:
    if source is None:
        return ""
    if isinstance(source, dict):
        try:
            return json.dumps(source, ensure_ascii=False)
        except TypeError:
            return str(source)
    if isinstance(source, Exception):
        parts = [str(source)]
        message = getattr(source, "message", None)
        if message:
            parts.append(str(message))
        body = getattr(source, "body", None)
        if body is not None:
            parts.append(_flatten_error(body))
        return "\n".join(parts)
    return str(source)


def _unpack_completion(response) -> tuple[str, list[ToolCall], TokenCounts, bool]:
    err = getattr(response, "error", None)
    if err is not None and is_content_block(err):
        return "", [], TokenCounts(), True
    choices = getattr(response, "choices", None) or []
    if not choices:
        if is_content_block(response):
            return "", [], TokenCounts(), True
        raise IndexError("completion has no choices")
    message = choices[0].message
    text = message.content or ""
    tool_calls = _parse_tool_calls(message)
    usage, usage_missing = extract_usage(response.usage)
    return text, tool_calls, usage, usage_missing


def _apply_request_style(kwargs: dict, *, style: str, effort: str | None, extra: dict) -> None:
    name = (style or "openai").strip().lower() or "openai"
    if name == "glm":
        _shape_glm(kwargs, extra, effort)
        return
    if name == "anthropic":
        _shape_anthropic(kwargs, extra, effort)
        return
    if name == "gemini":
        _shape_gemini(kwargs, extra, effort)
        return
    if name == "openrouter":
        _shape_openrouter(kwargs, extra, effort)
        return
    if name == "none":
        if extra:
            kwargs["extra_body"] = extra
        return
    if effort is not None:
        kwargs["reasoning_effort"] = effort
    if extra:
        kwargs["extra_body"] = extra


def _shape_glm(kwargs: dict, extra: dict, effort: str | None) -> None:
    extra = _deep_merge(extra, {"thinking": {"type": "enabled"}})
    raw = "" if effort is None else str(effort).strip().lower()
    kwargs["reasoning_effort"] = _GLM_EFFORT.get(raw, "max") if raw else "max"
    kwargs["extra_body"] = extra


def _shape_anthropic(kwargs: dict, extra: dict, effort: str | None) -> None:
    raw = "" if effort is None else str(effort).strip().lower()
    if raw in _ANTHROPIC_OFF:
        extra = _deep_merge(extra, {"thinking": {"type": "disabled"}})
    elif raw:
        extra = _deep_merge(
            extra,
            {
                "thinking": {"type": "enabled"},
                "output_config": {"effort": _ANTHROPIC_EFFORT.get(raw, "max")},
            },
        )
    if extra:
        kwargs["extra_body"] = extra


def _shape_gemini(kwargs: dict, extra: dict, effort: str | None) -> None:
    raw = "" if effort is None else str(effort).strip().lower()
    if raw:
        extra = _deep_merge(
            extra,
            {
                "extra_body": {
                    "google": {
                        "thinking_config": {
                            "thinking_level": _GEMINI_LEVEL.get(raw, "high"),
                        }
                    }
                }
            },
        )
    if extra:
        kwargs["extra_body"] = extra


def _shape_openrouter(kwargs: dict, extra: dict, effort: str | None) -> None:
    if effort is not None:
        extra = _deep_merge(extra, {"reasoning": {"effort": effort}})
    if extra:
        kwargs["extra_body"] = extra


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        existing = out.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            out[key] = _deep_merge(existing, value)
        else:
            out[key] = value
    return out


def _is_retryable(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    return status_code in _RETRYABLE_STATUS_CODES


def retry_delay(error: Exception, attempt: int) -> float:
    """Honour Retry-After when the server sends one, else the backoff schedule."""
    requested = _retry_after(error)
    if requested is not None:
        return min(requested, _MAX_RETRY_AFTER)
    return _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]


def _retry_after(error: Exception) -> float | None:
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None  # HTTP-date form; fall back to the schedule


def _parse_tool_calls(message) -> list[ToolCall]:
    raw_calls = getattr(message, "tool_calls", None) or []
    parsed: list[ToolCall] = []
    for call in raw_calls:
        function = getattr(call, "function", None)
        name = getattr(function, "name", "") if function is not None else ""
        raw_arguments = (
            getattr(function, "arguments", None) if function is not None else None
        )
        parsed.append(
            ToolCall(
                id=str(getattr(call, "id", "") or ""),
                name=name,
                arguments=_parse_arguments(raw_arguments),
            )
        )
    return parsed


def _parse_arguments(raw) -> dict:
    payload = raw or "{}"
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        payload = str(payload)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {"_raw": payload}
    if not isinstance(parsed, dict):
        return {"_raw": payload}
    return parsed
