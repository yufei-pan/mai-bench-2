from dataclasses import asdict
from pathlib import Path

import pytest

from mai_bench2.checkpoint import (
    Checkpoint,
    CheckpointError,
    ItemRecord,
    SeatSnapshot,
    classify_item,
    is_complete,
    load_checkpoint,
    retryable_items,
    save_checkpoint,
    seat_snapshot,
    update_item,
)
from mai_bench2.config import EndpointConfig
from mai_bench2.planner_loop import PlannerTrace, planner_trace_from_payload


def _ckpt(**kwargs) -> Checkpoint:
    seats = kwargs.pop("seats", {})
    items = kwargs.pop(
        "items",
        [ItemRecord("planner", "p-1", 0, "pending")],
    )
    return Checkpoint(
        version=1,
        stamp="2026-08-25T000000Z",
        state="running",
        smoke=False,
        suite_flag=None,
        rubric_hash="abc",
        persona_id="official",
        persona_hex="77be5c59f150",
        prompts_id="official",
        prompts_hex="bbbb",
        gold_ids={"planner": ["p-1"]},
        seats=seats,
        items=items,
        **kwargs,
    )


def test_save_load_roundtrip(tmp_path: Path):
    seat = SeatSnapshot("m", "xhigh", 0.0, True, {}, "http://x")
    ckpt = _ckpt(seats={"planner": seat})
    save_checkpoint(tmp_path, ckpt)
    loaded = load_checkpoint(tmp_path)
    assert loaded.stamp == ckpt.stamp
    assert loaded.seats["planner"].model == "m"
    assert loaded.items[0].status == "pending"


def test_load_missing_is_corrupt(tmp_path: Path):
    with pytest.raises(CheckpointError, match="corrupt checkpoint"):
        load_checkpoint(tmp_path)


def test_load_garbage_is_corrupt(tmp_path: Path):
    (tmp_path / "checkpoint.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CheckpointError, match="corrupt checkpoint"):
        load_checkpoint(tmp_path)


def test_classify_planner_exception_is_transport_fail():
    status, payload, error = classify_item("planner", RuntimeError("down"))
    assert status == "transport_fail"
    assert payload is None
    assert "RuntimeError" in error


def test_classify_planner_trace_is_ok():
    trace = PlannerTrace(
        action="none",
        tools_called=[],
        wait_seconds=None,
        reply_args={},
        handoff_messages=[],
        tool_reference_text="",
        step_count=1,
        tool_hits=[("query_memory", True)],
    )
    status, payload, error = classify_item("planner", trace)
    assert status == "ok"
    assert error is None
    restored = planner_trace_from_payload(payload)
    assert restored.action == "none"
    assert restored.tool_hits == [("query_memory", True)]


def test_classify_replyer_judge_fail_is_ok():
    class R:
        kind = "ok"
        visible = "hi"
        row = {"judge_fail": True, "in_character": 0}
        error = None

    status, payload, error = classify_item("replyer", R())
    assert status == "ok"
    assert payload["row"]["judge_fail"] is True


def test_classify_replyer_judge_transport_is_retryable():
    class R:
        kind = "judge_transport"
        visible = "hi"
        row = None
        error = "Timeout: x"

    status, payload, error = classify_item("replyer", R())
    assert status == "transport_fail"
    assert payload is None
    assert "Timeout" in error


def test_classify_e2e_judge_error_is_retryable():
    class E:
        judge_error = "Timeout: j"
        judge_unparsed = False

    status, payload, error = classify_item("e2e", E())
    assert status == "transport_fail"


def test_retryable_and_complete():
    ckpt = _ckpt(
        items=[
            ItemRecord("planner", "a", 0, "ok", payload={}),
            ItemRecord("planner", "b", 0, "transport_fail", error="x"),
            ItemRecord("planner", "c", 0, "pending"),
        ]
    )
    kinds = {row.id: row.status for row in retryable_items(ckpt)}
    assert kinds == {"b": "transport_fail", "c": "pending"}
    assert is_complete(ckpt) is False
    ckpt.items[1].status = "ok"
    ckpt.items[2].status = "ok"
    assert is_complete(ckpt) is True


def test_update_item_writes_payload():
    ckpt = _ckpt()
    update_item(ckpt, suite="planner", id="p-1", sample=0, status="ok", payload={"action": "none"}, error=None)
    assert ckpt.items[0].payload == {"action": "none"}


def test_seat_snapshot_copies_fields():
    snap = seat_snapshot(EndpointConfig("http://u", "SECRET", "mdl", reasoning_effort="high"))
    assert snap.model == "mdl"
    assert snap.base_url == "http://u"
    assert snap.reasoning_effort == "high"
