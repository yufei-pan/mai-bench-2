from mai_bench2.maibot_shape import (
    attention_block,
    deferred_reminder,
    identity,
    replyer_history,
    stamp,
    target_block,
)


def test_identity_wraps_nickname():
    text = identity("麦麦", "是一个大二女大学生，现在正在上网和群友聊天。")
    assert text == "你的名字是麦麦。\n是一个大二女大学生，现在正在上网和群友聊天。"


def test_attention_block_wraps_prompt():
    block = attention_block("群里简短。")
    assert block.startswith("在该聊天中的注意事项：\n通用注意事项：\n群里简短。")
    assert attention_block("  ") == ""


def test_stamp_is_deterministic():
    assert stamp(0) == "2026-01-01 12:00:00"
    assert stamp(90) == "2026-01-01 12:01:30"


def test_deferred_reminder_uses_system_reminder():
    text = deferred_reminder([("view_forward_message", "查看转发")])
    assert text.startswith("<system-reminder>")
    assert "view_forward_message: 查看转发" in text
    assert "tool_search" in text
    assert deferred_reminder([]) == ""


def test_replyer_history_splits_roles():
    rows = replyer_history([
        {"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "hi"},
        {"t": 1, "msg_id": "m2", "user": "麦麦", "text": "嗯", "is_self_message": True},
    ])
    assert rows == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "嗯"},
    ]
    assert "<message" not in rows[0]["content"]


def test_target_block_names_sender_and_id():
    messages = [{"t": 0, "msg_id": "m1", "user": "q1", "group_card": "小徐", "text": "麦麦 在吗"}]
    text = target_block(messages, "m1", "麦麦")
    assert "小徐" in text and "m1" in text and "麦麦 在吗" in text
    self_msg = [{"t": 0, "msg_id": "m9", "user": "麦麦", "text": "我刚说的", "is_self_message": True}]
    self_text = target_block(self_msg, "m9", "麦麦")
    assert "补充" in self_text and "我刚说的" in self_text
    assert target_block(messages, "missing", "麦麦") == ""
