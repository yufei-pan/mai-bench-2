# Resume and Checkpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist per-item progress into `results/<stamp>/checkpoint.json` so `mai-bench-2 resume` can retry transport/interrupt leftovers in place, with SIGINT drain/abandon and seat-model gates.

**Architecture:** A `Checkpoint` JSON file is the source of truth. Live runs create the stamp first and update it after every gold item. `map_items` becomes a concurrency-sized queue that honours `RunControl` drain/abandon. `resume` loads that file (or synthesizes one from a legacy folder), gates identity/seats, re-runs only retryable ids, and rewrites the same stamp.

**Tech Stack:** Python ≥ 3.11, stdlib `json`/`signal`/`threading`/`concurrent.futures`, existing `pytest`, `tomllib`, `rich` (progress only). No new dependencies. Picker uses `report.grid` + `input()`.

**Spec:** `docs/superpowers/specs/2026-08-25-resume-checkpoint-design.md`

## Global Constraints

- Do not change scoring formulas, gold, prompts, headline gates, LLM cache keys, or `compare` grouping.
- Retry set is `pending` | `transport_fail` | `abandoned` only. `judge_fail` parse miss is `ok`.
- In-place stamp only. Never write a second stamp on resume.
- Seat block: `model`, `reasoning_effort`, `temperature`, `assistant_prefill`, `extra_body`. Never `api_key`. `base_url` mismatch is a warning.
- Identity block: `rubric_hash`, `persona_hex`, `prompts_hex`, gold id set per suite. Resume loads persona/prompts from the **checkpoint ids**, then sets `cfg.run.smoke` from the checkpoint.
- Incomplete leftovers exit 1; SIGINT with leftovers exits 130; complete run exit codes stay as today.
- Atomic checkpoint write: `checkpoint.json.tmp` + `os.fsync` + `os.replace`.
- `KeyboardInterrupt` / `SystemExit` inside `fn` still propagate (not stored as `transport_fail`).
- Tests: `.venv/bin/python -m pytest <args>`. No live HTTP.
- Commits: do not run `git config`. Do not `--no-verify`. Do not push. Do not add unrelated uncommitted `compare` files (`src/mai_bench2/compare.py`, `tests/test_compare.py`, README compare docs) unless they are already in HEAD.
- Run only the tests named in the task until that task’s end; full pytest in Task 9.

## File map

| Path | Responsibility |
|---|---|
| `src/mai_bench2/checkpoint.py` | **New.** Schema, atomic IO, classify, planned rows, legacy synthesize, list resumable |
| `src/mai_bench2/parallel.py` | `RunControl`, `Abandoned`, queue `map_items`, `on_item` |
| `src/mai_bench2/resume.py` | **New.** Picker, gates, drive resume, merge |
| `src/mai_bench2/cli.py` | `resume` parse/dispatch; stamp-at-start; signals; incomplete exit |
| `src/mai_bench2/report.py` | `write_redacted_config` (tiny wrapper around existing `_dump_config_toml`) |
| `src/mai_bench2/suites/planner.py` | `only_ids`, `sample`, `control`, `on_item`; skip `None` slots |
| `src/mai_bench2/suites/replyer.py` | same |
| `src/mai_bench2/suites/e2e.py` | same |
| `src/mai_bench2/planner_loop.py` | `planner_trace_from_payload` helper (tuple `tool_hits`) |
| `tests/test_checkpoint.py` | **New.** |
| `tests/test_parallel.py` | Drain/abandon/None slots; keep existing tests green |
| `tests/test_resume.py` | **New.** Gates, `--stamp`, merge, legacy |
| `tests/test_cli_help.py` | `resume` is not a suite |
| `tests/test_cli_run.py` | Stamp exists before suites; incomplete exit 1 |
| `README.md`, `README.zh-CN.md`, `tests/test_docs.py` | `mai-bench-2 resume` |

---

### Task 1: Checkpoint schema, classify, atomic IO

**Files:**
- Create: `src/mai_bench2/checkpoint.py`
- Create: `tests/test_checkpoint.py`
- Modify: `src/mai_bench2/planner_loop.py` (add `planner_trace_from_payload`)

**Interfaces:**
- Consumes: `PlannerTrace`, dataclasses `asdict`
- Produces:
  - `RETRYABLE = frozenset({"pending", "transport_fail", "abandoned"})`
  - `class CheckpointError(Exception)`
  - `class Abandoned: ...` lives in `parallel.py` — for this task, classify treats a duck type with attribute `abandoned is True` OR import `Abandoned` only in Task 2. **This task:** classify `Exception` and suite result objects; `Abandoned` added in Task 2 and hooked then.
  - `@dataclass class SeatSnapshot` with fields `model: str`, `reasoning_effort: str | None`, `temperature: float | None`, `assistant_prefill: bool`, `extra_body: dict`, `base_url: str`
  - `@dataclass class ItemRecord` with `suite: str`, `id: str`, `sample: int`, `status: str`, `error: str | None = None`, `payload: dict | None = None`
  - `@dataclass class Checkpoint` with `version: int`, `stamp: str`, `state: str`, `smoke: bool`, `suite_flag: str | None`, `rubric_hash: str`, `persona_id: str`, `persona_hex: str`, `prompts_id: str`, `prompts_hex: str`, `gold_ids: dict[str, list[str]]`, `seats: dict[str, SeatSnapshot]`, `items: list[ItemRecord]`
  - `def checkpoint_to_dict(ckpt: Checkpoint) -> dict`
  - `def checkpoint_from_dict(data: dict) -> Checkpoint` — raises `CheckpointError` on bad/missing `version` or `items`
  - `def save_checkpoint(directory: Path, ckpt: Checkpoint) -> None` writes `directory/checkpoint.json`
  - `def load_checkpoint(directory: Path) -> Checkpoint`
  - `def classify_item(suite: str, result: object) -> ItemRecord` — **does not set suite/id/sample**; returns status/error/payload only. Better signature: `def classify_item(suite: str, result: object) -> tuple[str, dict | None, str | None]` → `(status, payload, error)`
  - `def update_item(ckpt: Checkpoint, *, suite: str, id: str, sample: int, status: str, payload: dict | None, error: str | None) -> None` mutates the matching row
  - `def retryable_items(ckpt: Checkpoint) -> list[ItemRecord]`
  - `def is_complete(ckpt: Checkpoint) -> bool` — every item `status=="ok"`
  - `def seat_snapshot(endpoint) -> SeatSnapshot` — `endpoint` is `EndpointConfig`
  - `def planner_trace_from_payload(payload: dict) -> PlannerTrace` in `planner_loop.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_checkpoint.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py -v`

Expected: FAIL collecting (`No module named 'mai_bench2.checkpoint'`) or import error.

- [ ] **Step 3: Implement**

`src/mai_bench2/checkpoint.py`: dataclasses as in Interfaces. `save_checkpoint` writes JSON of `checkpoint_to_dict` to `directory / "checkpoint.json.tmp"`, `flush`+`os.fsync`, `os.replace` onto `checkpoint.json`. `load_checkpoint` reads `directory / "checkpoint.json"`; `FileNotFoundError` / `json.JSONDecodeError` / missing keys → `CheckpointError(f"corrupt checkpoint: {exc}")`.

`classify_item`: `Exception` → `transport_fail`. `suite=="planner"` + dataclass → `ok` + `asdict`. `suite=="replyer"`: `kind in {"model_fail","judge_transport"}` → `transport_fail` with `error`; else `ok` + `asdict` (use a small `_plain(obj)` that copies `kind/visible/row/error` if not a dataclass). `suite=="e2e"`: if `getattr(result, "judge_error", None)` → `transport_fail`; else `ok` + `asdict` (nested traces OK).

`planner_loop.py`:

```python
def planner_trace_from_payload(payload: dict) -> PlannerTrace:
    data = dict(payload)
    data["tool_hits"] = [tuple(item) for item in (data.get("tool_hits") or [])]
    return PlannerTrace(**data)
```

`seat_snapshot`: copy the six fields from `EndpointConfig`.

`update_item`: find the row with matching suite/id/sample; raise `CheckpointError` if missing; set status/payload/error.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/checkpoint.py src/mai_bench2/planner_loop.py tests/test_checkpoint.py
git commit -m "feat: add checkpoint.json schema and atomic IO"
```

---

### Task 2: Planned rows, list resumable, legacy synthesize

**Files:**
- Modify: `src/mai_bench2/checkpoint.py`
- Modify: `tests/test_checkpoint.py`

**Interfaces:**
- Consumes: Task 1 types; `json.loads` of `summary.json`; `tomllib` of redacted `config.toml`
- Produces:
  - `def planned_items(gold_ids: dict[str, list[str]], repeats: int) -> list[ItemRecord]` — `sample` in `range(max(1, repeats))`, one row per suite×id×sample, status `pending`
  - `def list_resumable(output_dir: Path) -> list[Checkpoint]` — newest stamp first (`path.name` descending). Include `state in {"running","incomplete"}` OR synthesized legacy. Skip `complete`. Skip folders with no checkpoint and no retryable legacy hole.
  - `def synthesize_legacy(directory: Path) -> Checkpoint | None` — `None` if not resumable
  - `def load_or_synthesize(directory: Path) -> Checkpoint` — load if `checkpoint.json` exists, else synthesize or `CheckpointError`

Legacy rules (spec 2.3):
- Need `summary.json`. Gold ids = checkpoint `gold_ids` from `summary["suites"]` names × we do **not** have gold on disk in this function — pass `gold_ids: dict[str, list[str]]` into `synthesize_legacy(directory, gold_ids)`.
- Final signature: `synthesize_legacy(directory: Path, gold_ids: dict[str, list[str]]) -> Checkpoint | None`
- For each suite in `gold_ids`: prediction ids = `[p["id"] for p in suite_json.get("predictions") or []]` from `directory / f"{suite}.json"` if present, else from `summary["suites"]` we only have aggregates — **use per-suite JSON**. If the suite file is missing, every gold id for that suite is `transport_fail`.
- Present ids → `ok` payload `None`. Missing ids → `transport_fail`.
- If no missing ids across suites → return `None` (not listed).
- Identity from `summary.json` keys that already exist (`rubric_hash`, `persona_*`, `prompts_*`, `smoke`, `suite_flag`). Seats from `config.toml` via `tomllib` + `SeatSnapshot` (missing file → empty seats).
- `stamp = directory.name`, `state="incomplete"`, `version=1`.

`list_resumable(output_dir, gold_ids_by_suite: dict[str, list[str]])` needs gold ids. Spec picker uses gold from **this checkout**. So `list_resumable(output_dir: Path, *, gold_ids: dict[str, list[str]])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checkpoint.py`:

```python
import json

from mai_bench2.checkpoint import list_resumable, planned_items, synthesize_legacy


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py::test_planned_items_repeats tests/test_checkpoint.py::test_synthesize_legacy_missing_prediction tests/test_checkpoint.py::test_list_resumable_skips_complete_and_sorts -v`

Expected: FAIL (`not defined`)

- [ ] **Step 3: Implement** the three functions. `list_resumable`: iterate `output_dir` dirs; if `checkpoint.json` exists, `load_checkpoint` (skip `CheckpointError`); if `is_complete` or `state=="complete"`, skip; else include. If no checkpoint, `synthesize_legacy` and include if not None. Sort by `stamp` descending.

Treat `state=="complete"` OR `is_complete(ckpt)` as not listed.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_checkpoint.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/checkpoint.py tests/test_checkpoint.py
git commit -m "feat: list resumable stamps and synthesize legacy checkpoints"
```

---

### Task 3: `map_items` queue, drain, abandon

**Files:**
- Modify: `src/mai_bench2/parallel.py`
- Modify: `tests/test_parallel.py`

**Interfaces:**
- Consumes: existing `map_items` callers (default kwargs must keep today’s tests passing)
- Produces:
  - `class Abandoned: ...` empty marker; `isinstance(result, Abandoned)`
  - `class RunControl:` with `drain: threading.Event`, `abandon: threading.Event`; methods `request_drain(self) -> None` (sets drain), `request_abandon(self) -> None` (sets drain **and** abandon)
  - `map_items(fn, items, *, concurrency, progress=None, suite: str, control: RunControl | None = None, on_item=None) -> list`
  - Return list length `len(items)`, input order. Unstarted slots are `None`. Abandoned in-flight slots are `Abandoned()` unless the future already finished — then store the real value (`ok` / `Exception` wins).
  - `on_item(item: dict, result: object) -> None` called on the **main thread** once per item that **started**, after the slot is filled (including `Abandoned` and `Exception`). Not called for unstarted `None`.
  - At most `concurrency` in flight. Do not submit the whole list up front.
  - Drain: do not start further items; wait for in-flight.
  - Abandon: `shutdown(wait=False, cancel_futures=True)` (Python 3.11+); do not wait; in-flight not yet done → `Abandoned`.
  - Serial (`concurrency<=1` or `len(items)<=1`): drain/abandon checked **before starting** the next item; the current `fn` always runs to completion (cannot cancel one thread).

- [ ] **Step 1: Write the failing tests**

Keep existing four tests. Append:

```python
from mai_bench2.parallel import Abandoned, RunControl, map_items


def test_drain_does_not_start_the_rest():
    started = []
    released = threading.Event()
    control = RunControl()

    def fn(item):
        started.append(item["id"])
        if item["id"] in {"a", "b"}:
            released.wait(timeout=2)
        return item["id"]

    def killer():
        while len(started) < 2:
            pass
        control.request_drain()
        released.set()

    threading.Thread(target=killer, daemon=True).start()
    out = map_items(
        fn,
        [{"id": x} for x in "abcd"],
        concurrency=2,
        progress=None,
        suite="planner",
        control=control,
    )
    assert out[0] == "a" and out[1] == "b"
    assert out[2] is None and out[3] is None
    assert set(started) == {"a", "b"}


def test_abandon_marks_in_flight():
    started = threading.Barrier(3)  # two workers + test thread
    control = RunControl()
    hold = threading.Event()

    def fn(item):
        started.wait(timeout=2)
        hold.wait(timeout=2)
        return item["id"]

    def killer():
        started.wait(timeout=2)
        control.request_abandon()
        hold.set()

    threading.Thread(target=killer, daemon=True).start()
    out = map_items(
        fn,
        [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        concurrency=2,
        progress=None,
        suite="planner",
        control=control,
    )
    assert out[2] is None
    assert all(slot in {"a", "b", None} or isinstance(slot, Abandoned) for slot in out[:2])


def test_on_item_not_called_for_unstarted(tmp_path=None):
    seen = []
    control = RunControl()
    control.request_drain()
    out = map_items(
        lambda item: item["id"],
        [{"id": "a"}, {"id": "b"}],
        concurrency=1,
        progress=None,
        suite="planner",
        control=control,
        on_item=lambda item, result: seen.append(item["id"]),
    )
    assert out == [None, None]
    assert seen == []
```

For `test_on_item_not_called_for_unstarted`: drain **before** the first item means **nothing starts**. Spec drain is “stop starting **new** items” after SIGINT during a run, so the first items already in flight continue. Pre-set drain before `map_items` is the same as “SIGINT before any item” — valid, all `None`.

If that feels too sharp, start one item then drain. Prefer: drain before start ⇒ zero started. Document that in the test name.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_parallel.py::test_drain_does_not_start_the_rest tests/test_parallel.py::test_abandon_marks_in_flight tests/test_parallel.py::test_on_item_not_called_for_unstarted -v`

Expected: FAIL (`cannot import RunControl` or drain does not exist; current pool submits all four items)

- [ ] **Step 3: Implement queue `map_items`**

Use `concurrent.futures.wait(..., return_when=FIRST_COMPLETED, timeout=0.05)` loop. `start_more` submits while `len(in_flight) < workers` and `next_index < len(items)` and not drain/abandon.

Classify `Abandoned` in `checkpoint.classify_item` in this same commit: if `isinstance(result, Abandoned): return "abandoned", None, None`. Add one test in `test_checkpoint.py`:

```python
from mai_bench2.parallel import Abandoned

def test_classify_abandoned():
    status, payload, error = classify_item("planner", Abandoned())
    assert status == "abandoned"
```

- [ ] **Step 4: Run parallel + checkpoint tests**

Run: `.venv/bin/python -m pytest tests/test_parallel.py tests/test_checkpoint.py -v`

Expected: PASS (including the original four `map_items` tests)

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/parallel.py src/mai_bench2/checkpoint.py tests/test_parallel.py tests/test_checkpoint.py
git commit -m "feat: queue map_items with drain and abandon"
```

---

### Task 4: `resume` argv parsing

**Files:**
- Modify: `src/mai_bench2/cli.py` (`parse_args`, `_parse_resume_args`)
- Modify: `tests/test_cli_help.py`

**Interfaces:**
- Consumes: existing compare prefix dispatch
- Produces: `parse_args(["resume"])` → `command=="resume"`, `stamp is None`, `config is None`. `parse_args(["resume", "--stamp", "2026-08-25T000000Z"])`. Help lists `--stamp` and `--config`, not `--repeats`, not `--full`. `parse_args(["resume", "--full"])` exits 2.

Also add `resume` to the **run** parser epilog next to `compare`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_help.py`

```python
def test_parse_resume_subcommand():
    ns = parse_args(["resume"])
    assert ns.command == "resume"
    assert ns.stamp is None
    assert ns.config is None


def test_parse_resume_stamp():
    ns = parse_args(["resume", "--stamp", "2026-08-25T000000Z", "--config", "c.toml"])
    assert ns.stamp == "2026-08-25T000000Z"
    assert ns.config == "c.toml"


def test_parse_resume_rejects_full():
    with pytest.raises(SystemExit) as exited:
        parse_args(["resume", "--full"])
    assert exited.value.code == 2


def test_resume_help(capsys):
    with pytest.raises(SystemExit) as exited:
        parse_args(["resume", "-h"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "--stamp" in out
    assert "--repeats" not in out
```

Add `assert "resume" in out` to `_assert_argparse_help`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_help.py::test_parse_resume_subcommand tests/test_cli_help.py::test_parse_resume_rejects_full tests/test_cli_help.py::test_resume_help -v`

Expected: FAIL (`invalid choice: 'resume'`)

- [ ] **Step 3: Implement** prefix `if argv and argv[0] == "resume": return _parse_resume_args(argv[1:])` **before** compare check (order: resume, compare, run). `_parse_resume_args` only `--stamp` and `--config`.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_cli_help.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/cli.py tests/test_cli_help.py
git commit -m "feat: parse mai-bench-2 resume --stamp"
```

---

### Task 5: Suites honour `only_ids`, `control`, `on_item`, `None` slots

**Files:**
- Modify: `src/mai_bench2/suites/planner.py`
- Modify: `src/mai_bench2/suites/replyer.py`
- Modify: `src/mai_bench2/suites/e2e.py`
- Modify: `src/mai_bench2/cli.py` (`run_suites` passes control/on_item/sample)
- Test: `tests/test_suite_planner.py` (one new test), existing suite tests must stay green

**Interfaces:**
- Consumes: `map_items(..., control=, on_item=)`
- Produces: each `run_*_suite(..., *, only_ids: set[str] | None = None, control: RunControl | None = None, on_item=None)`
  - After `select_items`, if `only_ids` is not None, `selected = [item for item in selected if str(item.get("id") or "") in only_ids]` preserving order.
  - Fold: `if result is None: continue` (do **not** increment `failures`). `Abandoned` → treat as not scored, do **not** increment `failures` (checkpoint owns abandon; suite `n_items` is scored `ok` only).
  - Call `on_item(item, result)` from **inside map_items** (suites just pass it through). Do not double-call.

Planner fold today: `Exception` → failures. Keep that. `None`/`Abandoned` → skip, no failure.

Replyer: `Abandoned`/`None` skip. `model_fail`/`judge_transport` still increment as today **when those results are present** (live run that finished the item). On drain, unstarted must not become `failed_items`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_suite_planner.py` (reuse existing fake client helpers in that file):

```python
from mai_bench2.parallel import RunControl

def test_planner_only_ids_runs_subset(tmp_path, monkeypatch):
    # copy the smallest existing planner suite test pattern in this file:
    # load gold from tmp_path with two items, pass only_ids={second id},
    # assert n_items==1 and the prediction id is the second.
```

Read `tests/test_suite_planner.py` for the actual gold fixture helper and paste a full test that:
1. Writes two planner gold items in `tmp_path/data/gold/planner.jsonl` (or uses the file’s existing `_write_gold`).
2. Fake client returns a `none`/idle trace.
3. `run_planner_suite(..., only_ids={id_of_second})`.
4. `result.n_items == 1` and `result.predictions[0].id ==` that id.

Also add:

```python
def test_planner_none_slot_is_not_a_failure():
```

This one can unit-test fold by calling `map_items` with pre-set drain after monkeypatching `map_items` to return `[trace, None]` — simpler to inject by running `control.request_drain()` before the suite if only one worker... Easier path: **don’t** add the None-slot suite test here; Task 3 already returns None. In planner fold, add the `if result is None: continue` and cover it by a suite test that monkeypatches `map_items`:

```python
def test_planner_skips_unstarted_slots(monkeypatch, tmp_path):
    # after building a 2-item gold, monkeypatch map_items to return [trace, None]
    # n_items == 1, failed_items == 0
```

Use the real `PlannerTrace` idle object from other tests in that file.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_suite_planner.py::test_planner_only_ids_runs_subset tests/test_suite_planner.py::test_planner_skips_unstarted_slots -v`

Expected: FAIL (`unexpected keyword only_ids` or `n_items==2`)

- [ ] **Step 3: Implement** `only_ids` filter + `None`/`Abandoned` skip in all three suites. Thread `control` and `on_item` into `map_items`. `run_suites` gains `control=None` and `on_item=None` and passes `sample` into on_item via closure later (Task 6). For this task, `run_suites` just forwards `control` and `on_item` if provided; default `None` keeps existing tests.

Replyer/e2e: same `only_ids`/`None`/`Abandoned` handling. Add analogous `test_replyer_only_ids_runs_subset` only if planner test isn’t enough — **one suite is enough** if all three get the same three-line filter. Still edit all three suite files in this task.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_suite_planner.py tests/test_suite_replyer.py tests/test_suite_e2e.py tests/test_parallel.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/planner.py src/mai_bench2/suites/replyer.py src/mai_bench2/suites/e2e.py src/mai_bench2/cli.py tests/test_suite_planner.py
git commit -m "feat: suites accept only_ids and skip unstarted map slots"
```

---

### Task 6: Live run creates stamp first, checkpoints each item, SIGINT, incomplete exit

**Files:**
- Modify: `src/mai_bench2/cli.py`
- Modify: `src/mai_bench2/report.py` (add `write_redacted_config`)
- Modify: `src/mai_bench2/progress.py` `planned_total` unchanged
- Test: `tests/test_cli_run.py`

**Interfaces:**
- Consumes: `Checkpoint`, `planned_items`, `save_checkpoint`, `classify_item`, `update_item`, `RunControl`, `write_artifacts`, `write_redacted_config`
- Produces:
  - `def write_redacted_config(out_dir: Path, cfg) -> None` in `report.py` — mkdir, write `_dump_config_toml(cfg)`
  - `def install_run_signals(control: RunControl) -> None` in `cli.py` — SIGINT: if `control.abandon.is_set()` already, ignore; if `control.drain.is_set()`, `request_abandon()`; else `request_drain()`. SIGTERM: `request_drain()` only
  - Live `console()` for `command==run`: after clients+persona+prompts, **before** `run_suites`:
    1. `stamp = utc now`, `out_dir = Path(cfg.run.output_dir).expanduser() / stamp`, mkdir
    2. Build `gold_ids` via `select_items(load_gold(...), smoke=cfg.run.smoke, smoke_n=...)` for each name in `requested_suites(cfg)` (same names `run_suites` will run). Repeats = `max(1, cfg.run.repeats)`.
    3. `Checkpoint(..., state="running", items=planned_items(gold_ids, repeats), seats={role: seat_snapshot(ep) for role, ep in ... if ep})`
    4. `save_checkpoint` + `write_redacted_config`
    5. `control = RunControl()`; `install_run_signals(control)`
    6. `caught_signal = {"n": 0}` — handlers also set `caught_signal["n"]=1` on first drain and `2` on abandon so exit code can be 130
    7. `on_item` closure uses `progress` sample: `run_suites` must call `on_item` with the current sample. Change `run_suites` to accept `checkpoint` + `checkpoint_dir` and wrap:

```python
def _make_on_item(ckpt, directory, sample):
    def on_item(item, result, suite=None):
        ...
```

`map_items` `on_item` is `(item, result)` without suite. So `run_suites` passes a different closure **per suite**:

```python
def hook(item, result, *, suite=name, sample=sample):
    status, payload, error = classify_item(suite, result)
    update_item(ckpt, suite=suite, id=str(item.get("id") or ""), sample=sample, status=status, payload=payload, error=error)
    save_checkpoint(out_dir, ckpt)
```

Pass `on_item=functools.partial(hook, suite=name)` — but `on_item(item, result)` only. Use a factory `make_hook(suite, sample)`.

  8. After `run_suites`, if `retryable_items(ckpt)`: `ckpt.state="incomplete"`; save; fold artifacts from **suite results as returned** (already only started items). Print table. `sys.exit(130 if signal else 1)`.
  9. Else `ckpt.state="complete"`; save; `write_artifacts` as today (overwrite config); `sys.exit(code)` from suites.

`failed_items` on a drained suite: unstarted are checkpoint `pending`, not suite failures. Suite `n_items` is scored count → headlines gated. Good.

If signal fired but `is_complete(ckpt)`: write full artifacts, exit 0.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli_run.py`

Follow `test_console_run_writes_redacted_artifacts` (fake clients, tmp config). Add:

```python
def test_console_writes_checkpoint_before_suites(tmp_path, monkeypatch, capsys):
    # same config scaffolding as test_console_run_writes_redacted_artifacts
    # monkeypatch run_planner_suite to:
    #   assert (out_dir glob checkpoint.json).exists()  -- need out_dir. Easier:
    #   look at cfg.run.output_dir after console returns
    ...
    with pytest.raises(SystemExit) as exited:
        console(["--config", str(cfg_path), "planner", "--smoke"])
    assert exited.value.code == 0
    runs = list(Path(out_dir).iterdir())
    assert (runs[0] / "checkpoint.json").is_file()
    data = json.loads((runs[0] / "checkpoint.json").read_text())
    assert data["state"] == "complete"
    assert data["items"]
```

```python
def test_console_transport_fail_exits_1(tmp_path, monkeypatch, capsys):
    # fake planner fn raises on one item — use suite monkeypatch returning
    # SuiteResult ok with failed_items, BUT checkpoint must have transport_fail.
    # Stronger: monkeypatch run_planner_suite to call real fold... too heavy.
    # Instead monkeypatch classify via a custom run_suites on_item by using a
    # planner client.chat that raises on the second call.
```

Use the existing `_patch_clients_and_suites` if it replaces the whole suite. If it does, this test cannot see per-item checkpoint updates.

Read `_patch_clients_and_suites`. If it stubs `run_planner_suite`, write a more integration-style test with `_FakeClient` that raises once — `test_cli_run.py` already has fake clients for a real planner suite.

Look at `test_console_run_writes_redacted_artifacts` — it stubs suites. Then add a test that does **not** stub suites, only `ChatClient`, like other tests in that file (`test_dead_judge_...`).

Minimum: one test with stubbed suite that still checks stamp/checkpoint **created even when suite is stubbed** — so `console` must write checkpoint **before** `run_suites`. The stubbed suite won’t update items to `ok`; after run, items still `pending` → incomplete → exit 1.

That’s the behavior we want for leftovers. Stubbed suite that returns `SuiteResult ok n_items=3` but checkpoint still pending would incorrectly exit 1. So **after** `run_suites`, completeness is **checkpoint-based**, not suite-result-based.

If suites are stubbed and never call `on_item`, checkpoint stays pending → exit 1 even on a “successful” stub. That would **break** `test_console_run_writes_redacted_artifacts` (expects exit 0).

**Fix:** `run_suites` stubs must either call `on_item` or console must treat “no items planned” … planned items exist.

For existing tests that stub `run_planner_suite`, update those tests **or** have `run_suites` when the real suite is replaced... They monkeypatch `run_planner_suite`. Planned items still written. on_item never called. Exit 1. **Broken.**

Resolution: existing stub tests must still exit 0. Options:
1. After suites, if checkpoint items are all still `pending` **and** every `SuiteResult.status in {ok, skipped}` with no retryable updates, mark all planned rows `ok` with empty payload — **wrong**, would hide real drain.
2. Change stub tests to complete checkpoint in the stub.
3. `on_item` is optional; completeness = checkpoint retryable **if any item left pending that was planned AND a suite actually ran items**. Messy.

**Chosen:** monkeypatched `run_*_suite` in existing tests keeps working because `console` only uses checkpoint completeness when `on_item` was installed **and** at least one `update_item` happened, **OR** we update existing tests.

Cleaner: **always** checkpoint-based. Update existing `test_console_*` that expect exit 0 to either not care (if they already assert code==0) — **must update those stubs** to mark items ok.

Few tests: grep `test_cli_run.py` for `SystemExit` and `_patch_clients_and_suites`.

Executor: grep `def _patch_clients_and_suites` and every `console(` test. For each stubbed suite success path, after writing artifacts expectation, also either:
- have the stub call a provided `on_item` — stubs don’t get on_item today
- **or** in `console`, if `run_suites` returns all `ok`/`skipped` and **zero** `on_item` calls, skip the pending-as-incomplete rule (detect via `ckpt` still all pending **and** suite statuses ok). That’s a loophole: a run that fails to wire `on_item` would exit 0 with all pending.

**Do not add that loophole.** Update `_patch_clients_and_suites` to accept checkpoint hooks — too awkward.

Simplest path that preserves tests: **`run_suites` itself** writes checkpoint updates when given `checkpoint=`. Stubs of `run_planner_suite` bypass that. Existing tests stub suites → checkpoint stays pending.

Update `_patch_clients_and_suites` wrappers: the fake `run_planner_suite` signature includes `**kwargs` and if `result.status=="ok"`, mark all planner rows ok. The patch function is in the test file — **Task 6 Step 1 includes updating `_patch_clients_and_suites`** so fakes call:

```python
def fake_planner(cfg, client, persona, **kwargs):
    ckpt = kwargs.get("checkpoint")
    if ckpt is not None:
        for row in ckpt.items:
            if row.suite == "planner":
                row.status = "ok"
                row.payload = {"action": "none"}
    return SuiteResult(...)
```

And `run_suites` must pass `checkpoint=ckpt` into `run_*_suite`. Real suites ignore unknown kw if we add `checkpoint=None` unused — real suites use `on_item` instead.

Real suites: `on_item` from `run_suites`. Fake suites: mutate `checkpoint` kwarg.

`run_planner_suite(..., checkpoint=None, **ignored)` — add `checkpoint=None` and don’t use it in production (on_item does the work). Tests’ fakes use `checkpoint`.

- [ ] **Step 2: Run** `test_console_run_writes_redacted_artifacts` **first** after adding checkpoint-before-suites without updating fakes — expect FAIL exit 1. Then update fakes. Then add the new tests.

- [ ] **Step 3: Implement** `console` + `run_suites(..., control, checkpoint, checkpoint_dir)` + `write_redacted_config` + `install_run_signals`.

`run_suites` loop:

```python
for sample in range(repeats):
    ...
    result = _run_one(..., control=control, on_item=make_hook(name, sample), only_ids=None, checkpoint=checkpoint)
```

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_cli_run.py tests/test_cli_help.py tests/test_checkpoint.py tests/test_parallel.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/cli.py src/mai_bench2/report.py tests/test_cli_run.py
git commit -m "feat: checkpoint live runs and exit 1 when items remain retryable"
```

---

### Task 7: Resume gates and `--stamp` already-complete / unknown

**Files:**
- Create: `src/mai_bench2/resume.py` (gates + load; not the full runner yet)
- Create: `tests/test_resume.py`
- Modify: `src/mai_bench2/cli.py` (`_resume_console`)

**Interfaces:**
- Consumes: `load_checkpoint`, `load_or_synthesize`, `list_resumable`, `resolve_output_dir`, `ConfigError`, `load_persona`, `load_prompts`, `rubric_hash`, `seat_snapshot`, `select_items`, `load_gold`
- Produces:
  - `class ResumeError(Exception)`
  - `def gate_resume(ckpt, cfg, *, root: Path, package_root: Path) -> list[str]` — returns warning strings (`base_url` diffs). Raises `ResumeError` on hex/gold/seat mismatch.
  - `def load_resume_target(output_dir: Path, stamp: str | None, *, gold_ids: dict[str, list[str]], tty: bool, stdin, stdout, stderr) -> Checkpoint` — `--stamp` path, already complete, unknown, picker **stub in this task**: if `stamp is None` and not tty, print list + raise `ResumeError("no TTY")` with message `no resumable runs` if empty else the list. Picker UI is Task 8; this task: `stamp is None` and tty=False → stderr list, `ResumeError`.
  - `_resume_console(args) -> int` wired from `console` like compare.

Gate order (spec 5.1):
1. Load checkpoint (`load_or_synthesize` needs gold_ids from `package_root` + `ckpt.smoke` — chicken/egg for legacy). For `--stamp`: if `checkpoint.json` exists, load it (smoke inside). If not, synthesize with gold_ids computed using **summary.json smoke** if present else `False`. Helper `def gold_ids_for(root, smoke, cfg) -> dict[str, list[str]]` using each suite’s `smoke_n`.
2. Load persona/prompts from **ckpt.persona_id / ckpt.prompts_id**. Compare hexes and `rubric_hash(prompts)` to ckpt. Mismatch → `ResumeError("persona_hex: checkpoint=X live=Y")` (name the field).
3. Recompute gold ids with `ckpt.smoke` and `cfg` smoke_n; if sets differ → `ResumeError("gold changed")`.
4. For each seat in `ckpt.seats`: live endpoint missing → error. Compare model, reasoning_effort, temperature, assistant_prefill, extra_body. `extra_body` compare as JSON `sort_keys`.
5. `base_url` differ → append warning string, do not raise.

`cfg.run.smoke = ckpt.smoke` before gold select.

Already complete: stdout `already complete: {stamp}\n`, return 0.

Unknown stamp: `ResumeError(f"unknown stamp: {stamp}")`.

- [ ] **Step 1: Write failing tests** in `tests/test_resume.py` using tmp_path checkpoints and a tiny `AppConfig` / official persona from `ROOT`.

```python
def test_gate_model_mismatch(tmp_path):
    ...
    with pytest.raises(ResumeError, match="planner"):
        gate_resume(ckpt, cfg, root=ROOT, package_root=ROOT)

def test_gate_base_url_warns(tmp_path):
    warnings = gate_resume(...)
    assert any("base_url" in w for w in warnings)

def test_resume_stamp_unknown(tmp_path, capsys):
    # console(["resume", "--stamp", "nope", "--config", cfg])
    ...
    assert exited.value.code == 1
    assert "unknown stamp" in capsys.readouterr().err

def test_resume_already_complete(tmp_path, capsys):
    ...
    assert exited.value.code == 0
    assert "already complete" in capsys.readouterr().out
```

Hex mismatch: copy official persona hex from `personas/official.toml` via `load_persona("official", root=ROOT)` then change `ckpt.persona_hex` to `"deadbeefdead"`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_resume.py -v`

Expected: FAIL import

- [ ] **Step 3: Implement** `resume.py` + `_resume_console` that: resolve output_dir; if stamp: load dir or error; if complete: print and 0; else gate; **this task stops before running suites** — after successful gate print `would resume {stamp}` is **forbidden**. After gate, Task 7 `_resume_console` should `return 0` only for already-complete; for a real incomplete stamp **without** running, tests for mismatch don’t call the runner.

Split: `_resume_console` calls `prepare_resume(...)` then `execute_resume(...)` which Task 7 stubs as `raise ResumeError("not implemented")` — **no**. Task 7 tests only gates + already-complete + unknown. `_resume_console`:

```python
def _resume_console(args) -> int:
    try:
        ... prepare ...
        if is_complete(ckpt):
            print(f"already complete: {ckpt.stamp}")
            return 0
        warnings = gate_resume(...)
        for line in warnings:
            print(line, file=sys.stderr)
        return execute_resume(...)  # defined in Task 8; for Task 7 implement execute_resume as running the merge if you can, or
```

To keep Task 7 shippable: implement `execute_resume` as a function in `resume.py` that Task 8 fills. Task 7 can put:

```python
def execute_resume(...):
    raise ResumeError("resume execute not wired")
```

Then incomplete+matching seats would exit 1 with that message — add **no** test for that yet.

Alternatively Task 7 `_resume_console` only handles complete/unknown/gate-fail; if gates pass, call `execute_resume` from Task 8. Incomplete happy path tests wait for Task 8.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_resume.py tests/test_cli_help.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/resume.py src/mai_bench2/cli.py tests/test_resume.py
git commit -m "feat: gate resume on identity and seat models"
```

---

### Task 8: Execute resume (retryable ids, merge, rewrite stamp)

**Files:**
- Modify: `src/mai_bench2/resume.py` (`execute_resume`)
- Modify: `src/mai_bench2/cli.py` (`run_suites` already has hooks)
- Modify: `tests/test_resume.py`

**Interfaces:**
- Consumes: `run_suites` / per-suite `run_*_suite` with `only_ids`, `on_item`, `control`, live clients from `_build_clients`
- Produces: `execute_resume(ckpt, cfg, *, root, out_dir, clients, persona, prompts, control) -> int`
  - `ckpt.state = "running"`; save
  - For each suite with retryable rows: `only_ids = {row.id for row in retryable if row.suite==name}` (all samples: `run_suites` repeats loop — **problem**: repeats re-run whole suite). Spec: only retry rows whose sample is retryable.
  - `run_suites` today loops all samples then all suites. Need `only_ids` **and** skip samples that have no retryable rows: inside the sample loop, `ids = {r.id for r in retryable_items(ckpt) if r.suite==name and r.sample==sample}`; if empty, skip calling the suite.
  - Cold suite: if any `ok` row for that suite has `payload is None`, set `only_ids=None` (run all selected ids) for that suite/sample.
  - After suites, fold: **prefer rebuilding from checkpoint payloads** so drained `None` slots plus new ok rows combine.

**Fold-from-payload** (required for in-place merge):

Add `src/mai_bench2/suites/planner.py`:

```python
def fold_planner(selected: list[dict], traces: list[PlannerTrace | None], *, usage, wall_s) -> SuiteResult:
    # existing fold body, skip None traces, failures only for Exception — traces are already objects
```

To avoid a huge refactor in one task: **execute_resume** calls `run_*_suite(only_ids=retryable)` which **only returns SuiteResult for retried items**. Then **merge predictions** with previous per-suite JSON on disk.

Merging natives from two SuiteResults is **wrong** (rates). Spec: fold **all** ok payloads in gold order.

So this task **must** rebuild traces from payloads:

`planner_trace_from_payload` already exists. Replyer: reconstruct a simple namespace/`_ReplyerOne(**payload)`. E2e: `asdict` reverse — add `e2e_result_from_payload(payload)` in `e2e.py` that builds `_E2eOne` with `planner_trace_from_payload(payload["trace"])`.

Then `fold_planner_from_records(items, records: list[ItemRecord])`:
- for each selected gold item, find record sample 0 (execute_resume for repeats>1 is later-correct if we only test repeats=1)
- status ok → include in scored
- retryable remaining → failures += 1

Call existing native functions.

`execute_resume` flow:
1. Retry via real suite `fn` path with `only_ids` + `on_item` updating ckpt (same as live)
2. Reload ckpt
3. If retryable left: `state=incomplete`; `_write_snapshot` from fold-from-payload; return 1
4. Else `state=complete`; fold-from-payload all ok; `write_artifacts`; print table+digest; return 0

`_write_snapshot` = same print+write_artifacts as console, shared helper `_emit_report(...)` extracted in `cli.py` from today’s console tail.

- [ ] **Step 1: Failing test**

```python
def test_execute_resume_retries_only_transport_fail(tmp_path, monkeypatch, capsys):
    # checkpoint two planner ids, first ok with a real PlannerTrace payload (idle),
    # second transport_fail.
    # Fake ChatClient: chat() increments a counter and returns idle analysis.
    # console(["resume", "--stamp", stamp, "--config", cfg])
    # assert create/chat called once (only the hole)
    # assert checkpoint complete
    # assert summary.json headlines or n_items==2
```

Build the ok payload with `asdict(idle_trace)`. Gold: two tiny items in package ROOT gold is huge — use `tmp_path` as `package_root` with two-line planner jsonl **and** monkeypatch `run_suites` root.

`console` uses `Path(__file__).resolve().parents[2]` as package_root — **cannot** point at tmp gold without monkeypatching that path.

Monkeypatch `cli.console` package_root: the test monkeypatches `run_planner_suite` to a function that uses tmp gold... 

**Test without full console:** call `execute_resume` directly with fake suite:

```python
calls = []
def fake_planner(cfg, client, persona, *, only_ids=None, on_item=None, **k):
    calls.append(only_ids)
    item = {"id": "p-drop", ...}
    trace = idle_trace()
    if on_item:
        on_item(item, trace)
    return SuiteResult("planner", "ok", {}, 1.0, UsageSplit(), 0.0, 1)
```

Monkeypatch `mai_bench2.resume.run_planner_suite` or pass suites in — `execute_resume` should import from `mai_bench2.suites.planner`.

Assert `calls == [{"p-drop"}]` (set). After execute, load checkpoint both ok.

- [ ] **Step 2: Run test, expect FAIL** (`execute_resume` raises not wired)

- [ ] **Step 3: Implement** `execute_resume` + fold-from-payload helpers + extract `_emit_report` in cli if that reduces duplication. Resume sets `cfg.run.smoke` from ckpt before suite select.

Install signals the same way as live run.

- [ ] **Step 4: Run** `.venv/bin/python -m pytest tests/test_resume.py tests/test_cli_run.py tests/test_suite_planner.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/resume.py src/mai_bench2/cli.py src/mai_bench2/suites/*.py tests/test_resume.py
git commit -m "feat: resume retries transport failures into the same stamp"
```

---

### Task 9: TTY picker, docs, full suite

**Files:**
- Modify: `src/mai_bench2/resume.py` (`pick_stamp`)
- Modify: `README.md`, `README.zh-CN.md`
- Modify: `tests/test_docs.py`, `tests/test_resume.py`, `tests/test_cli_help.py` (epilog already)

**Interfaces:**
- Consumes: `list_resumable`, `report.grid`
- Produces: `def pick_stamp(candidates: list[Checkpoint], *, stdin, stdout) -> str` — print numbered grid; `input()`; empty / EOF → raise `ResumeError` with exit 130 handled in `_resume_console` (`except ResumeError as exc: if str(exc)=="cancelled": return 130`). Use `ResumeCancelled` subclass for picker cancel.
- No TTY (`stdin.isatty()` false) and `stamp is None`: print grid to **stderr**, `ResumeError("no resumable runs in ...")` if empty else `ResumeError("specify --stamp")` with the list already printed. Exit 1.
- Empty candidates: `no resumable runs in {output_dir}`
- README: under Run, `mai-bench-2 resume` / `mai-bench-2 resume --stamp ...`. Short paragraph: SIGINT drain, same stamp, model gate, no keys. Chinese mirror.
- `test_docs.py`: `assert "mai-bench-2 resume" in text` and `"--stamp"` in both READMEs.

Picker columns: `#`, stamp, mode, planner, replyer, judge, `ok/pending/fail/aband` counts.

- [ ] **Step 1: Failing tests**

```python
def test_pick_stamp_reads_number(capsys):
    from io import StringIO
    ckpt = _ckpt(stamp="2026-08-25T000000Z", state="incomplete")
    chosen = pick_stamp([ckpt], stdin=StringIO("1\n"), stdout=StringIO())
    assert chosen == "2026-08-25T000000Z"

def test_no_tty_without_stamp_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    ...
```

Docs tests as above.

- [ ] **Step 2: Run** those tests — FAIL

- [ ] **Step 3: Implement picker + README**

- [ ] **Step 4: Full pytest**

Run: `.venv/bin/python -m pytest -q`

Expected: all PASS (today’s suite size plus new tests; do not fail on uncommitted compare tests if they are collected — they should pass if present)

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/resume.py README.md README.zh-CN.md tests/test_docs.py tests/test_resume.py
git commit -m "feat: interactive resume picker and docs"
```

---

## Spec coverage (self-review)

| Spec section | Task |
|---|---|
| checkpoint.json schema, atomic write, classify | 1 |
| planned items, legacy synthesize, list resumable | 2 |
| map_items queue, drain, abandon, on_item | 3 |
| `resume` CLI parse, no `--full` | 4 |
| only_ids, skip unstarted, Abandoned | 5 |
| stamp first, signals, incomplete exit 1/130, redacted config early | 6 |
| gates, already complete, unknown stamp, base_url warning | 7 |
| execute merge in place, cold suite via payload None | 8 (cold: `only_ids=None` when any payload is None) |
| picker TTY / no TTY, README | 9 |
| compare grouping unchanged | no task (non-goal) |
| no API keys stored | 6 uses existing redaction |
| `judge_fail` not retryable | 1 classify test |
| SIGTERM drain only | 6 `install_run_signals` |
| prefer ok over abandoned if future done | 3 |

Cold-suite explicit test is spec item 9 — add it in Task 8 Step 1 if the merge test doesn’t cover `payload is None` → `only_ids is None`. Add:

```python
def test_cold_suite_replays_all_ids(monkeypatch):
    # ok row payload None + transport_fail hole → fake_planner only_ids is None
```

in Task 8.
