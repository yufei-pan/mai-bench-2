from mai_bench2.persona import load_persona
from mai_bench2.prompts import load_prompts
from mai_bench2.suites.replyer import _replyer_messages
from conftest import ROOT


def test_replyer_messages_role_split_and_current_thinking():
    persona = load_persona("official", root=ROOT)
    prompts = load_prompts("official", root=ROOT)
    item = {
        "channel": "group",
        "target_t": 0,
        "oracle_handoff": {
            "messages": [
                {"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "麦麦 在吗"},
                {"t": 1, "msg_id": "m2", "user": "麦麦", "text": "嗯", "is_self_message": True},
            ],
            "reply_reference": "",
            "analysis": "对方在叫我，回一声",
            "msg_id": "m1",
        },
    }
    messages = _replyer_messages(persona, item, prompts)
    assert messages[0]["role"] == "system"
    assert "你的名字是麦麦。" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "麦麦 在吗"}
    assert messages[2] == {"role": "assistant", "content": "嗯"}
    assert any(m["content"].startswith("当前思考：") for m in messages)
    assert any("当前时间：2026-01-01 12:00:00" in m["content"] for m in messages)
    assert any("m1" in m["content"] and "小徐" in m["content"] for m in messages)
    assert any("请自然地回复" in m["content"] for m in messages)
