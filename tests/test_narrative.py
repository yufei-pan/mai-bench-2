from mai_bench2.config import AppConfig, EndpointConfig, RunConfig, SuiteConfig
from mai_bench2.headlines import HeadlineOutcome
from mai_bench2.narrative import generate_narrative
from mai_bench2.types import ChatResult, Prediction, SuiteResult, TokenCounts, UsageSplit


class ScriptClient:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "tools": tools,
            }
        )
        if not self._texts:
            raise RuntimeError("no scripted replies")
        return ChatResult(self._texts.pop(0), TokenCounts(), False, True, [])


class _Persona:
    id = "official"
    hex = "1a46dd3e9eb3"
    nickname = "麦麦"


def _cfg():
    return AppConfig(
        EndpointConfig("http://p/v1", "k", "cursor/grok-4.6-xhigh"),
        EndpointConfig("http://r/v1", "k", "cursor/grok-4.6-xhigh"),
        EndpointConfig("http://j/v1", "k", "cliproxyapi/gpt-5.6-sol(max)"),
        RunConfig(smoke=True),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        "x",
    )


def _results():
    return [
        SuiteResult(
            "planner",
            "ok",
            {"action": 0.33, "tool_f1": 0.0},
            13.3,
            UsageSplit(),
            1.0,
            1,
            predictions=[
                Prediction(
                    "gold-001",
                    "reply",
                    "none",
                    extra={
                        "tools_called": [],
                        "wait_seconds": None,
                        "native_tool_call_count": 0,
                        "assistant_text": '{"name": "query_memory", "arguments": {"query": "上海"}}',
                    },
                )
            ],
        )
    ]


def test_generate_narrative_skips_without_judge_client():
    result = generate_narrative(
        None,
        cfg=None,
        persona=None,
        results=[],
        table="",
        headlines=None,
    )
    assert result.text is None
    assert result.skip_reason == "no_judge"
    assert result.error_message is None


def test_generate_narrative_returns_judge_text():
    client = ScriptClient(["## 发现\n规划器没有原生 tool_calls。"])
    result = generate_narrative(
        client,
        cfg=_cfg(),
        persona=_Persona(),
        results=_results(),
        table="planner  ok",
        headlines=HeadlineOutcome({}, ["smoke"]),
    )
    assert result.text == "## 发现\n规划器没有原生 tool_calls。"
    assert result.skip_reason is None
    assert result.error_message is None
    assert len(client.calls) == 1
    assert client.calls[0]["tools"] is None


def test_generate_narrative_prompt_includes_contract_and_evidence():
    client = ScriptClient(["ok"])
    generate_narrative(
        client,
        cfg=_cfg(),
        persona=_Persona(),
        results=_results(),
        table="planner  ok  action=0.33",
        headlines=HeadlineOutcome({}, ["smoke"]),
    )
    blob = "\n".join(message["content"] for message in client.calls[0]["messages"])
    assert any("\u4e00" <= ch <= "\u9fff" for ch in blob)
    assert "tool_calls" in blob
    assert "原生" in blob
    assert "JSON" in blob
    assert "不会被执行" in blob or "不会执行" in blob
    assert "发现" in blob
    assert "MaiBot" in blob
    assert "建议" in blob
    assert "不可发表" in blob or "not publishable" in blob.lower()
    assert "query_memory" in blob
    assert "gold-001" in blob
    assert "cursor/grok-4.6-xhigh" in blob
    assert "cliproxyapi/gpt-5.6-sol(max)" in blob
    assert "official" in blob
    assert "1a46dd3e9eb3" in blob
    assert "planner  ok  action=0.33" in blob
    assert "native_tool_call_count" in blob or "native tool" in blob.lower()


def test_generate_narrative_prompt_omits_api_keys():
    cfg = AppConfig(
        EndpointConfig("http://p/v1", "SUPER_SECRET_KEY", "m"),
        EndpointConfig("http://r/v1", "REPLY_SECRET_KEY", "m"),
        EndpointConfig("http://j/v1", "JUDGE_SECRET_KEY", "judge-m"),
        RunConfig(smoke=True),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        "x",
    )
    client = ScriptClient(["ok"])
    generate_narrative(
        client,
        cfg=cfg,
        persona=_Persona(),
        results=_results(),
        table="planner  ok",
        headlines=HeadlineOutcome({}, ["smoke"]),
    )
    blob = "\n".join(message["content"] for message in client.calls[0]["messages"])
    assert "SUPER_SECRET_KEY" not in blob
    assert "REPLY_SECRET_KEY" not in blob
    assert "JUDGE_SECRET_KEY" not in blob


class BoomClient:
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        raise RuntimeError("network down")


def test_generate_narrative_chat_error_does_not_raise():
    result = generate_narrative(
        BoomClient(),
        cfg=_cfg(),
        persona=_Persona(),
        results=_results(),
        table="planner  ok",
        headlines=HeadlineOutcome({}, ["smoke"]),
    )
    assert result.text is None
    assert result.skip_reason is None
    assert result.error_message is not None
    assert "network down" in result.error_message


def test_generate_narrative_retries_empty_then_succeeds():
    client = ScriptClient(["", "## 发现\nok"])
    result = generate_narrative(
        client,
        cfg=_cfg(),
        persona=_Persona(),
        results=_results(),
        table="planner  ok",
        headlines=HeadlineOutcome({}, ["smoke"]),
    )
    assert result.text == "## 发现\nok"
    assert len(client.calls) == 2
    assert result.error_message is None

