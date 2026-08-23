from mai_bench2.narrative import generate_narrative
from mai_bench2.types import ChatResult, TokenCounts

_DIGEST = {
    "smoke": True,
    "headline_reasons": ["smoke"],
    "meanings": ["这是 smoke（planner 1），不能当正式 headline。"],
    "suites": [
        {"name": "planner", "status": "ok", "n_items": 1, "subscore": 13.3, "native": {"action": 0.0}}
    ],
    "worst": [
        {
            "suite": "planner",
            "id": "gold-001",
            "gold": "reply",
            "pred": "none",
            "tag": "idle_instead_of_reply",
            "meaning": "本应原生 reply，规划器却 none。真实麦麦不会说话。",
            "tools_called": [],
            "quote": None,
        }
    ],
}

_GLOSS = "含义\n- 规划器没有原生 tool_calls。\n\n最差样本\n- gold-001  reply→none  本应原生 reply。\n"


class ScriptClient:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self._texts:
            raise RuntimeError("no scripted replies")
        return ChatResult(self._texts.pop(0), TokenCounts(), False, True, [])


def test_generate_narrative_skips_without_judge_client():
    result = generate_narrative(None, _DIGEST)
    assert result.text is None
    assert result.skip_reason == "no_judge"
    assert result.error_message is None


def test_generate_narrative_returns_judge_text():
    client = ScriptClient([_GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 1
    assert client.calls[0]["tools"] is None


def test_generate_narrative_prompt_is_gloss_not_contract_lecture():
    client = ScriptClient([_GLOSS])
    generate_narrative(client, _DIGEST)
    blob = "\n".join(message["content"] for message in client.calls[0]["messages"])
    assert "润色员" in blob
    assert "给人类在命令行看" in blob
    assert "尽量言简意赅" in blob
    assert "不要编造" in blob
    assert "含义" in blob
    assert "最差样本" in blob
    assert "gold-001" in blob
    assert "idle_instead_of_reply" in blob
    assert "view_forward_message" not in blob
    assert "不会被执行" not in blob
    assert "只输出一个 JSON 对象" not in blob


def test_generate_narrative_retries_empty_then_succeeds():
    client = ScriptClient(["", _GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 2
    second = client.calls[1]["messages"][0]["content"]
    assert second.startswith("上一份不是中文终端报告")
    assert "只输出一个 JSON 对象" not in second


def test_generate_narrative_retries_json_looking_then_succeeds():
    client = ScriptClient(['{"in_character": 8}', _GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 2


def test_generate_narrative_retries_too_long_then_errors():
    long_text = "\n".join(f"行{i}" for i in range(31))
    client = ScriptClient([long_text, long_text])
    result = generate_narrative(client, _DIGEST)
    assert result.text is None
    assert result.error_message is not None
    assert len(client.calls) == 2


class BoomClient:
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        raise RuntimeError("network down")


def test_generate_narrative_chat_error_does_not_raise():
    result = generate_narrative(BoomClient(), _DIGEST)
    assert result.text is None
    assert "network down" in result.error_message


# --- the constraints have to be in the prompt that is actually sent first ----


def test_first_attempt_states_the_line_budget_and_bans_markdown_headers():
    """15-25 lines and 'no ##' only existed in the retry prefix, and _valid_gloss
    never rejected a '##' report — so the retry never fired and the run shipped a
    markdown document instead of a terminal report."""
    client = ScriptClient([_GLOSS])
    generate_narrative(client, _DIGEST)
    first = client.calls[0]["messages"][0]["content"]
    assert "15" in first and "25" in first
    assert "##" in first


def test_markdown_header_output_is_rejected_and_retried():
    marked = "## 含义\n\n- **首次动作**：8 条里约 6 条正确。\n\n## 最差样本\n\n- gold-001\n"
    client = ScriptClient([marked, _GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 2


def test_a_plain_terminal_report_is_still_accepted():
    client = ScriptClient([_GLOSS])
    assert generate_narrative(client, _DIGEST).text == _GLOSS


def test_narrative_prompt_carries_the_new_failure_evidence():
    digest = dict(_DIGEST)
    digest["worst"] = [
        dict(_DIGEST["worst"][0], comment="判词：与依据相悖", analysis="分析：团团说等我五分钟")
    ]
    client = ScriptClient([_GLOSS])
    generate_narrative(client, digest)
    blob = client.calls[0]["messages"][0]["content"]
    assert "判词：与依据相悖" in blob
    assert "分析：团团说等我五分钟" in blob
