from pathlib import Path
from mai_bench2.client import ChatClient
from mai_bench2.config import EndpointConfig

class FakeMsg:
    def __init__(self):
        self.content = ""
        self.tool_calls = [
            type("TC", (), {
                "id": "c1",
                "function": type("F", (), {"name": "reply", "arguments": '{"msg_id":"m1"}'})(),
            })()
        ]

class FakeResp:
    def __init__(self):
        self.choices = [type("C", (), {"message": FakeMsg()})()]
        self.usage = None

def test_chat_parses_tool_calls(tmp_path: Path):
    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=lambda **kwargs: FakeResp(),
    )
    result = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "reply"}}])
    assert result.tool_calls[0].name == "reply"
    assert result.tool_calls[0].arguments["msg_id"] == "m1"


import json

import pytest

from mai_bench2.usage import add_counts, extract_usage


class Boom(Exception):
    def __init__(self, status_code=None, message=""):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _tool_call(name, arguments, call_id="c1"):
    return type(
        "TC",
        (),
        {"id": call_id, "function": type("F", (), {"name": name, "arguments": arguments})()},
    )()


def _resp(text="", tool_calls=None, usage=None):
    msg = type("Msg", (), {"content": text, "tool_calls": tool_calls})()
    return type("Resp", (), {"choices": [type("C", (), {"message": msg})()], "usage": usage})()


def test_invalid_tool_arguments_json_uses_raw(tmp_path: Path):
    raw = "{not-json"
    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=lambda **kwargs: _resp(tool_calls=[_tool_call("reply", raw)]),
    )
    result = client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "reply"}}],
    )
    assert result.tool_calls[0].arguments == {"_raw": raw}


def test_non_object_tool_arguments_json_uses_raw(tmp_path: Path):
    raw = '["msg_id"]'
    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=lambda **kwargs: _resp(tool_calls=[_tool_call("reply", raw)]),
    )
    result = client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "reply"}}],
    )
    assert result.tool_calls[0].arguments == {"_raw": raw}


def test_passes_tools_and_tool_choice_only_when_tools_set(tmp_path: Path):
    seen = []

    def create_fn(**kwargs):
        seen.append(kwargs)
        return FakeResp()

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=create_fn,
    )
    tools = [{"type": "function", "function": {"name": "reply"}}]
    client.chat([{"role": "user", "content": "hi"}], tools=tools)
    client.chat([{"role": "user", "content": "hi"}])

    assert seen[0]["tools"] == tools
    assert seen[0]["tool_choice"] == "auto"
    assert "tools" not in seen[1]
    assert "tool_choice" not in seen[1]
    assert "api_key" not in seen[0]
    assert "api_key" not in seen[1]


def test_cache_key_includes_tools(tmp_path: Path):
    calls = {"n": 0}

    def create_fn(**kwargs):
        calls["n"] += 1
        name = kwargs["tools"][0]["function"]["name"]
        return _resp(
            text=str(calls["n"]),
            tool_calls=[_tool_call(name, '{"msg_id":"m1"}', call_id=str(calls["n"]))],
        )

    tools_a = [{"type": "function", "function": {"name": "reply"}}]
    tools_b = [{"type": "function", "function": {"name": "wait"}}]
    messages = [{"role": "user", "content": "hi"}]
    first = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=False,
        create_fn=create_fn,
    )
    second = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=False,
        create_fn=create_fn,
    )

    live = first.chat(messages, tools=tools_a)
    hit = second.chat(messages, tools=tools_a)
    other = second.chat(messages, tools=tools_b)

    assert live.cached is False
    assert hit.cached is True
    assert hit.text == live.text
    assert hit.tool_calls[0].name == "reply"
    assert hit.tool_calls[0].arguments["msg_id"] == "m1"
    assert other.cached is False
    assert other.tool_calls[0].name == "wait"
    assert calls["n"] == 2

    payloads = [json.loads(path.read_text()) for path in tmp_path.rglob("*.json")]
    assert payloads
    for payload in payloads:
        assert "text" in payload
        assert "usage" in payload
        assert "tool_calls" in payload


@pytest.mark.parametrize("status_code", [429, 500, 502, 503])
def test_retries_transient_status_with_backoff(tmp_path: Path, status_code: int):
    attempts = {"n": 0}
    sleeps = []

    def create_fn(**kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Boom(status_code=status_code, message="temporary")
        return FakeResp()

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=create_fn,
        sleep_fn=sleeps.append,
    )
    client.chat([{"role": "user", "content": "x"}])
    assert attempts["n"] == 3
    assert sleeps == [2.0, 8.0]


def test_extract_usage_and_add_counts():
    missing, flag = extract_usage(None)
    assert flag is True
    assert missing.requests == 1
    assert missing.usage_missing == 1

    counts, flag = extract_usage(
        type(
            "U",
            (),
            {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
                "reasoning_tokens": 1,
                "completion_tokens_details": None,
            },
        )()
    )
    assert flag is False
    assert counts.prompt_tokens == 2
    assert counts.completion_tokens == 3
    assert counts.reasoning_tokens == 1
    assert counts.total_tokens == 5
    assert counts.requests == 1

    summed = add_counts(counts, missing)
    assert summed.prompt_tokens == 2
    assert summed.requests == 2
    assert summed.usage_missing == 1


def test_chat_none_max_tokens_uses_endpoint_probe_stays_1(tmp_path: Path):
    seen = []

    def create_fn(**kwargs):
        seen.append(kwargs)
        return FakeResp()

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", max_tokens=123),
        "planner",
        tmp_path,
        no_cache=True,
        create_fn=create_fn,
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert seen[0]["max_tokens"] == 123
    seen.clear()
    client.probe([{"role": "user", "content": "ping"}], max_tokens=1)
    assert seen[0]["max_tokens"] == 1


def test_sdk_retries_are_disabled_so_ours_are_the_only_ones(monkeypatch):
    """SDK max_retries=2 multiplied with our attempts into 9 upstream requests per
    logical call, which hammers a router that is already out of keys."""
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = type(
                "C", (), {"completions": type("D", (), {"create": staticmethod(lambda **k: None)})()}
            )()

    monkeypatch.setattr("mai_bench2.client.OpenAI", FakeOpenAI)
    ChatClient(EndpointConfig("http://x/v1", "k", "m"), "judge", Path("/tmp/x"), True)
    assert captured["max_retries"] == 0


def test_retry_delay_honours_retry_after_and_caps_it():
    from mai_bench2.client import retry_delay

    class WithHeader(Exception):
        def __init__(self, value):
            self.response = type("R", (), {"headers": {"retry-after": value}})()

    assert retry_delay(WithHeader("30"), 0) == 30.0
    assert retry_delay(WithHeader("99999"), 0) == 120.0  # capped
    assert retry_delay(WithHeader("Wed, 21 Oct 2026 07:28:00 GMT"), 0) == 2.0  # falls back
    assert retry_delay(Exception(), 0) == 2.0
    assert retry_delay(Exception(), 3) == 45.0
    assert retry_delay(Exception(), 99) == 90.0  # clamped to the last step


def test_max_attempts_is_configurable(tmp_path: Path):
    attempts = {"n": 0}
    sleeps = []

    def create_fn(**kwargs):
        attempts["n"] += 1
        raise Boom(status_code=503, message="exhausted")

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", max_attempts=5),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create_fn,
        sleep_fn=sleeps.append,
    )
    with pytest.raises(Boom):
        client.chat([{"role": "user", "content": "x"}])
    assert attempts["n"] == 5
    assert sleeps == [2.0, 8.0, 20.0, 45.0]


import threading
from concurrent.futures import ThreadPoolExecutor


def test_cache_writes_from_two_threads(tmp_path: Path):
    barrier = threading.Barrier(2)

    def create(**kwargs):
        barrier.wait(timeout=2)
        text = kwargs["messages"][0]["content"]
        return _resp(text=text)

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "planner",
        tmp_path,
        no_cache=False,
        create_fn=create,
    )

    def one(text):
        return client.chat([{"role": "user", "content": text}])

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(one, "alpha")
        fb = pool.submit(one, "beta")
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert {ra.text, rb.text} == {"alpha", "beta"}
    files = list((tmp_path / "llm").glob("*.json"))
    assert len(files) == 2
    snap = client.usage_snapshot()
    assert snap.requests >= 0
