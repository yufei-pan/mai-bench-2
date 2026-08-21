import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from goldkit import (  # noqa: E402
    GROUP_WINDOW,
    PRIVATE_WINDOW,
    Item,
    M,
    Tape,
    counted,
    contextualize,
    is_bot_address,
    window_size,
)


def _tail():
    return [M(0, "m1", "q_x", "麦麦 你在吗", card="小徐")]


def _tape(n, *, channel="group", addressed_last=False):
    msgs = []
    for i in range(n):
        msgs.append(M(i * 10, f"t{i+1}", f"q_{i}", f"水{i}", card=f"卡{i%3}"))
    if addressed_last:
        msgs[-1] = M((n - 1) * 10, f"t{n}", "q_z", "@麦麦 在吗", card="盯")
    return Tape(id=f"{channel}-n{n}", channel=channel, messages=msgs)


def test_window_size_is_in_range_and_stable():
    g = {window_size(f"p-{i}", "group") for i in range(200)}
    p = {window_size(f"r-{i}", "private") for i in range(200)}
    assert min(g) >= GROUP_WINDOW[0] and max(g) <= GROUP_WINDOW[1]
    assert min(p) >= PRIVATE_WINDOW[0] and max(p) <= PRIVATE_WINDOW[1]
    assert window_size("p-addr-001", "group") == window_size("p-addr-001", "group")
    assert len(g) > 5 and len(p) > 5


def test_contextualize_group_hits_hashed_window():
    want = window_size("p-addr-001", "group")
    item = Item("p-addr-001", "group", _tail(), 0, "reply", reply_msg_id="m1")
    out = contextualize(item, [_tape(90)])
    visible = [m for m in out.messages if m.t <= out.target_t]
    assert len(counted(visible)) == want
    assert out.reply_msg_id in {m.msg_id for m in out.messages}
    assert out.messages[-1].text == "麦麦 你在吗"
    assert any(m.text.startswith("水") for m in visible)


def test_contextualize_drops_addressed_prefix_turns():
    item = Item("p-addr-001", "group", _tail(), 0, "reply", reply_msg_id="m1")
    out = contextualize(item, [_tape(50, addressed_last=True)])
    visible = [m for m in out.messages if m.t <= out.target_t]
    assert not any(is_bot_address(m) for m in visible[:-1])
    assert visible[-1].text == "麦麦 你在吗"


def test_contextualize_keeps_wait_arrivals_after_target():
    msgs = [
        M(0, "m1", "q_a", "等一下 我把话说完", card="小徐"),
        M(30, "m2", "q_a", "就是说那个方案得改", card="小徐"),
    ]
    item = Item("p-wait-001", "group", msgs, 0, "wait", band=(15, 60))
    out = contextualize(item, [_tape(80)])
    before = [m for m in out.messages if m.t <= out.target_t]
    after = [m for m in out.messages if m.t > out.target_t]
    assert len(counted(before)) == window_size("p-wait-001", "group")
    assert len(after) == 1
    assert after[0].text == "就是说那个方案得改"
    assert before[-1].text == "等一下 我把话说完"


def test_contextualize_private_range_and_handoff_ids():
    want = window_size("r-priv-001", "private")
    tail = [M(0, "m1", "q_p", "在忙吗")]
    item = Item(
        "r-priv-001", "private", tail, 0, "reply", reply_msg_id="m1",
        handoff={"messages": [tail[0].to_json()], "reply_reference": "", "msg_id": "m1", "analysis": "x"},
    )
    out = contextualize(item, [_tape(40, channel="private"), _tape(50, channel="private")])
    visible = [m for m in out.messages if m.t <= out.target_t]
    assert len(counted(visible)) == want
    assert out.handoff["msg_id"] == out.reply_msg_id
    assert out.handoff["messages"][-1]["text"] == "在忙吗"
    assert len(out.handoff["messages"]) == len(visible)


def test_is_bot_address():
    assert is_bot_address(M(0, "m1", "q", "@麦麦  你在", card="x"))
    assert is_bot_address(M(0, "m1", "q", "麦麦？", card="x"))
    assert is_bot_address(M(0, "m1", "q", "麦麦 你在吗", card="x"))
    assert not is_bot_address(M(0, "m1", "q", "麦麦最近话有点多", card="x"))
    assert not is_bot_address(M(0, "m1", "q", "草", card="x"))
