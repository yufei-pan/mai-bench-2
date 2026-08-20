from mai_bench2.render import clock_time, render_entry, render_log


def test_message_envelope_matches_maibot_shape():
    out = render_entry({"t": 0, "msg_id": "m1", "user": "q_1", "text": "在吗"})
    assert out == '<message msg_id="m1" time="14:00:00" user="q_1">\n在吗'


def test_optional_attributes_appear_only_when_set():
    entry = {
        "t": 65, "msg_id": "m2", "user": "q_2", "group_card": "小徐",
        "quote": "m1", "is_self_message": True, "text": "嗯",
    }
    out = render_entry(entry)
    assert out.startswith('<message msg_id="m2" quote="m1" time="14:01:05" user="q_2" ')
    assert 'group_card="小徐"' in out
    assert 'is_self_message="true"' in out
    assert render_entry({"t": 0, "msg_id": "m", "user": "u", "text": ""}).count("=") == 3


def test_non_chat_blocks():
    task = render_entry(
        {"t": 0, "kind": "proactive_task", "msg_id": "task-1", "plugin_id": "reminder", "text": "到点了"}
    )
    assert task == '<plugin_proactive_task id="task-1" plugin_id="reminder">\n到点了'
    assert render_entry({"t": 0, "kind": "system_reminder", "text": "少说点"}) == (
        "<system-reminder>\n少说点"
    )


def test_attribute_values_are_escaped():
    out = render_entry({"t": 0, "msg_id": "m1", "user": 'a"b<c&d', "text": "x"})
    assert 'user="a&quot;b&lt;c&amp;d"' in out


def test_clock_wraps_and_log_joins_in_order():
    assert clock_time(0) == "14:00:00"
    assert clock_time(36000) == "00:00:00"
    log = render_log([
        {"t": 0, "msg_id": "m1", "user": "a", "text": "一"},
        {"t": 5, "msg_id": "m2", "user": "b", "text": "二"},
    ])
    assert log.index("一") < log.index("二")
    assert log.count("<message") == 2


def test_speaker_alias_is_accepted():
    assert 'user="old"' in render_entry({"t": 0, "msg_id": "m", "speaker": "old", "text": "x"})
