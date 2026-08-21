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


def test_http_limit_one_serializes_create(tmp_path: Path):
    inside = 0
    max_inside = 0
    lock = threading.Lock()
    first_in = threading.Event()
    release = threading.Event()

    def create(**kwargs):
        nonlocal inside, max_inside
        with lock:
            inside += 1
            max_inside = max(max_inside, inside)
        first_in.set()
        assert release.wait(timeout=2)
        with lock:
            inside -= 1
        return _resp(text=kwargs["messages"][0]["content"])

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", http_limit=1),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "a"}])
        assert first_in.wait(timeout=2)
        fb = pool.submit(client.chat, [{"role": "user", "content": "b"}])
        assert not fb.done()
        with lock:
            assert max_inside == 1
        release.set()
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert {ra.text, rb.text} == {"a", "b"}
    assert max_inside == 1


def test_http_limit_omitted_allows_overlap(tmp_path: Path):
    barrier = threading.Barrier(2)
    inside = 0
    max_inside = 0
    lock = threading.Lock()

    def create(**kwargs):
        nonlocal inside, max_inside
        with lock:
            inside += 1
            max_inside = max(max_inside, inside)
        barrier.wait(timeout=2)
        with lock:
            inside -= 1
        return _resp(text=kwargs["messages"][0]["content"])

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "a"}])
        fb = pool.submit(client.chat, [{"role": "user", "content": "b"}])
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert {ra.text, rb.text} == {"a", "b"}
    assert max_inside == 2


def test_http_limit_cache_hit_skips_gate(tmp_path: Path):
    first_in = threading.Event()
    release = threading.Event()
    live_calls = {"n": 0}

    def create(**kwargs):
        live_calls["n"] += 1
        text = kwargs["messages"][0]["content"]
        if text == "live":
            first_in.set()
            assert release.wait(timeout=2)
        return _resp(text=text)

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", http_limit=1),
        "judge",
        tmp_path,
        no_cache=False,
        create_fn=create,
    )
    primed = client.chat([{"role": "user", "content": "cached"}])
    assert primed.cached is False
    live_calls["n"] = 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        live = pool.submit(client.chat, [{"role": "user", "content": "live"}])
        assert first_in.wait(timeout=2)
        hit = pool.submit(client.chat, [{"role": "user", "content": "cached"}])
        cached = hit.result(timeout=2)
        assert cached.cached is True
        assert cached.text == "cached"
        assert live_calls["n"] == 1
        assert not live.done()
        release.set()
        live.result(timeout=5)
    assert live_calls["n"] == 1


def test_http_limit_wait_is_not_a_timeout(tmp_path: Path):
    first_in = threading.Event()
    release = threading.Event()
    second_create = threading.Event()

    def create(**kwargs):
        text = kwargs["messages"][0]["content"]
        if text == "first":
            first_in.set()
            assert release.wait(timeout=2)
        else:
            second_create.set()
        return _resp(text=text)

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", timeout_s=0.05, http_limit=1),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "first"}])
        assert first_in.wait(timeout=2)
        fb = pool.submit(client.chat, [{"role": "user", "content": "second"}])
        assert not second_create.wait(timeout=0.15)
        assert not fb.done()
        try:
            assert fb.exception(timeout=0.01) is None
        except TimeoutError:
            pass  # still waiting on the semaphore — not a chat timeout
        release.set()
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert ra.text == "first"
    assert rb.text == "second"
    assert second_create.is_set()

