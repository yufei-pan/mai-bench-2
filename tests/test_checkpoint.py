from dataclasses import asdict
from pathlib import Path
import json

import pytest

from mai_bench2.checkpoint import (
    Checkpoint,
    CheckpointError,
    ItemRecord,
    SeatSnapshot,
    classify_item,
    is_complete,
    list_resumable,
    load_checkpoint,
    load_or_synthesize,
    planned_items,
    retryable_items,
    save_checkpoint,
    seat_snapshot,
    synthesize_legacy,
    update_item,
)
from mai_bench2.config import EndpointConfig
from mai_bench2.parallel import Abandoned
from mai_bench2.planner_loop import PlannerTrace, planner_trace_from_payload


def _ckpt(**kwargs) -> Checkpoint:
    seats = kwargs.pop("seats", {})
    items = kwargs.pop(
        "items",
        [ItemRecord("planner", "p-1", 0, "pending")],
    )
    return Checkpoint(
        version=kwargs.pop("version", 1),
        stamp=kwargs.pop("stamp", "2026-08-25T000000Z"),
        state=kwargs.pop("state", "running"),
        smoke=kwargs.pop("smoke", False),
        suite_flag=kwargs.pop("suite_flag", None),
        rubric_hash=kwargs.pop("rubric_hash", "abc"),
        persona_id=kwargs.pop("persona_id", "official"),
        persona_hex=kwargs.pop("persona_hex", "77be5c59f150"),
        prompts_id=kwargs.pop("prompts_id", "official"),
        prompts_hex=kwargs.pop("prompts_hex", "bbbb"),
        gold_ids=kwargs.pop("gold_ids", {"planner": ["p-1"]}),
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


def test_classify_abandoned():
    status, payload, error = classify_item("planner", Abandoned())
    assert status == "abandoned"


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


def test_planned_items_repeats():
    rows = planned_items({"planner": ["a", "b"]}, repeats=2)
    keys = {(r.id, r.sample) for r in rows}
    assert keys == {("a", 0), ("a", 1), ("b", 0), ("b", 1)}
    assert all(r.status == "pending" for r in rows)


def test_synthesize_legacy_missing_prediction(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "rubric_hash": "abc",
                "persona_id": "official",
                "persona_hex": "77be5c59f150",
                "prompts_id": "official",
                "prompts_hex": "bbbb",
                "smoke": False,
                "suite_flag": None,
                "suites": [{"name": "planner", "predictions": [{"id": "keep"}]}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "planner.json").write_text(
        json.dumps({"predictions": [{"id": "keep", "gold": "none", "pred": "none", "extra": {}}]}),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text('[planner]\nmodel = "m"\nbase_url = "http://x"\n', encoding="utf-8")
    ckpt = synthesize_legacy(tmp_path, {"planner": ["keep", "drop"]})
    assert ckpt is not None
    by_id = {row.id: row for row in ckpt.items}
    assert by_id["keep"].status == "ok"
    assert by_id["keep"].payload is None
    assert by_id["drop"].status == "transport_fail"


def test_synthesize_legacy_complete_returns_none(tmp_path: Path):
    (tmp_path / "summary.json").write_text(json.dumps({"rubric_hash": "abc", "suites": []}), encoding="utf-8")
    (tmp_path / "planner.json").write_text(
        json.dumps({"predictions": [{"id": "a"}]}), encoding="utf-8"
    )
    assert synthesize_legacy(tmp_path, {"planner": ["a"]}) is None


def test_list_resumable_skips_complete_and_sorts(tmp_path: Path):
    old = tmp_path / "2026-08-24T000000Z"
    new = tmp_path / "2026-08-25T000000Z"
    old.mkdir()
    new.mkdir()
    save_checkpoint(old, _ckpt(stamp=old.name, state="incomplete"))
    done = _ckpt(stamp=new.name, state="complete", items=[ItemRecord("planner", "p-1", 0, "ok", payload={})])
    save_checkpoint(new, done)
    listed = list_resumable(tmp_path, gold_ids={"planner": ["p-1"]})
    assert [c.stamp for c in listed] == [old.name]


def test_planned_items_clamps_repeats_and_sets_suite():
    rows = planned_items({"planner": ["a"]}, repeats=0)
    assert [(r.suite, r.id, r.sample, r.status) for r in rows] == [("planner", "a", 0, "pending")]


def test_synthesize_legacy_missing_suite_file_is_transport_fail(tmp_path: Path):
    (tmp_path / "summary.json").write_text(json.dumps({"rubric_hash": "abc"}), encoding="utf-8")
    ckpt = synthesize_legacy(tmp_path, {"planner": ["a"]})
    assert ckpt is not None
    assert ckpt.items[0].status == "transport_fail"
    assert ckpt.seats == {}
    assert ckpt.stamp == tmp_path.name
    assert ckpt.state == "incomplete"
    assert ckpt.version == 1


def test_synthesize_legacy_seats_from_config(tmp_path: Path):
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "rubric_hash": "abc",
                "persona_id": "official",
                "persona_hex": "77be5c59f150",
                "prompts_id": "official",
                "prompts_hex": "bbbb",
                "smoke": False,
                "suite_flag": None,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "planner.json").write_text(json.dumps({"predictions": []}), encoding="utf-8")
    (tmp_path / "config.toml").write_text('[planner]\nmodel = "m"\nbase_url = "http://x"\n', encoding="utf-8")
    ckpt = synthesize_legacy(tmp_path, {"planner": ["drop"]})
    assert ckpt is not None
    assert ckpt.seats["planner"].model == "m"
    assert ckpt.seats["planner"].base_url == "http://x"
    assert ckpt.persona_id == "official"
    assert ckpt.gold_ids == {"planner": ["drop"]}


def test_list_resumable_skips_corrupt_checkpoint(tmp_path: Path):
    bad = tmp_path / "2026-08-24T000000Z"
    good = tmp_path / "2026-08-25T000000Z"
    bad.mkdir()
    good.mkdir()
    (bad / "checkpoint.json").write_text("{not json", encoding="utf-8")
    save_checkpoint(good, _ckpt(stamp=good.name, state="incomplete"))
    listed = list_resumable(tmp_path, gold_ids={"planner": ["p-1"]})
    assert [c.stamp for c in listed] == [good.name]


def test_list_resumable_skips_is_complete_even_if_running(tmp_path: Path):
    d = tmp_path / "2026-08-25T000000Z"
    d.mkdir()
    save_checkpoint(
        d,
        _ckpt(stamp=d.name, state="running", items=[ItemRecord("planner", "p-1", 0, "ok", payload={})]),
    )
    listed = list_resumable(tmp_path, gold_ids={"planner": ["p-1"]})
    assert listed == []


def test_list_resumable_includes_legacy(tmp_path: Path):
    stamp = tmp_path / "2026-08-20T000000Z"
    stamp.mkdir()
    (stamp / "summary.json").write_text(json.dumps({"rubric_hash": "abc"}), encoding="utf-8")
    listed = list_resumable(tmp_path, gold_ids={"planner": ["a"]})
    assert [c.stamp for c in listed] == [stamp.name]


def test_load_or_synthesize_prefers_checkpoint(tmp_path: Path):
    save_checkpoint(tmp_path, _ckpt())
    loaded = load_or_synthesize(tmp_path, {"planner": ["other"]})
    assert loaded.items[0].status == "pending"
    assert loaded.stamp == "2026-08-25T000000Z"


def test_load_or_synthesize_synthesizes_legacy(tmp_path: Path):
    (tmp_path / "summary.json").write_text(json.dumps({"rubric_hash": "abc"}), encoding="utf-8")
    loaded = load_or_synthesize(tmp_path, {"planner": ["a"]})
    assert loaded.state == "incomplete"
    assert loaded.items[0].status == "transport_fail"


def test_load_or_synthesize_missing_raises(tmp_path: Path):
    with pytest.raises(CheckpointError):
        load_or_synthesize(tmp_path, {"planner": ["a"]})
