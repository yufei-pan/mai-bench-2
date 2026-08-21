# Item Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a suite keep up to N independent gold items in flight (`run.concurrency` / `--concurrency`, default 1) without changing scores, gold order, or intra-item sequencing.

**Architecture:** Shared `map_items(fn, items, *, concurrency, progress, suite)` serializes when N==1 and uses `ThreadPoolExecutor` when N>1. Suites extract one-item `fn`, fold results on the main thread in selected-gold order. Progress ticks on the main thread. Shared `ChatClient` per seat; cache writes take a lock.

**Tech Stack:** Python ≥ 3.11, stdlib `concurrent.futures`, existing `pytest`, `rich` progress from the progress-bar work. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-20-item-concurrency-design.md`

## Global Constraints

- Requires the terminal progress bar already on the branch: `RunProgress.item`, `item_span`, `tests/test_progress.py` `RecordingProgress`. Land on `feat/progress-bar` (or merge it first). Copy this plan and the spec into that worktree if they only exist on `main`.
- Unit of parallelism: gold items in the current suite. Intra-item calls stay serial. Suites stay serial. Repeats stay sequential passes.
- Default concurrency is 1. `max(1, int(N))` for CLI and for `map_items`. CLI override uses `args.concurrency is not None` (so `--concurrency 0` still applies).
- Fold in selected-gold order. `first_error` is the first failure in that order, not finish order.
- One item `Exception` does not cancel others. Do not catch `KeyboardInterrupt` or `SystemExit`.
- No asyncio, no process pool, no overlapping suites, no per-suite concurrency keys, no HTTP semaphore, no singleflight.
- Do not change scoring formulas, headlines, table, digest/gloss, cache keys, or `set_sample` (main thread, before the pass).
- Commits: do not run `git config`. Do not `--no-verify`. Do not push.
- Run only the tests named in the task until that task’s end; full pytest in Task 7.
- Tests: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest <args>` from the worktree. On a checkout whose `.venv` is local: `PYTHONPATH=src .venv/bin/python -m pytest <args>`.

## File map

| Path | Responsibility |
|---|---|
| `src/mai_bench2/config.py` | `RunConfig.concurrency`, `_RUN_KEYS`, `apply_overrides` |
| `src/mai_bench2/cli.py` | `--concurrency` |
| `src/mai_bench2/progress.py` | `RunProgress.complete(suite, id)` |
| `src/mai_bench2/parallel.py` | **New.** `map_items` |
| `src/mai_bench2/client.py` | Cache write lock |
| `src/mai_bench2/suites/planner.py` | Item `fn` + `map_items` + fold |
| `src/mai_bench2/suites/replyer.py` | Tagged item result + `map_items` + fold |
| `src/mai_bench2/suites/e2e.py` | Item payload + `map_items` + fold |
| `tests/test_parallel.py` | **New.** `map_items` overlap / order / isolation |
| `tests/test_config.py`, `tests/test_cli_help.py`, `tests/test_cli_run.py` | Load, clamp, flag |
| `tests/test_progress.py` | `RecordingProgress.complete`; suite ticks at N=2 |
| `tests/test_client.py` | Threaded cache writes |
| `tests/test_docs.py` | README + example config |
| `config.example.toml`, `README.md` | User-facing |

---

### Task 1: Config and CLI knob

**Files:**
- Modify: `src/mai_bench2/config.py` (`_RUN_KEYS`, `RunConfig`, `apply_overrides`)
- Modify: `src/mai_bench2/cli.py` (`_add_run_flags`)
- Modify: `config.example.toml` (`[run]` block)
- Test: `tests/test_config.py`, `tests/test_cli_help.py`, `tests/test_cli_run.py`

**Interfaces:**
- Consumes: existing `RunConfig`, `apply_overrides(cfg, args)`, `parse_args`
- Produces: `RunConfig.concurrency: int = 1`; `args.concurrency: int | None`; `apply_overrides` sets `run.concurrency = max(1, int(args.concurrency))` when `args.concurrency is not None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_help.py` inside `test_parse_run_defaults`:

```python
    assert ns.concurrency is None
```

Add to `tests/test_cli_help.py`:

```python
def test_parse_concurrency_flag():
    ns = parse_args(["run", "--concurrency", "8"])
    assert ns.concurrency == 8


def test_parse_concurrency_on_full_command():
    ns = parse_args(["full", "--concurrency", "4"])
    assert ns.concurrency == 4
```

Add to `tests/test_config.py`:

```python
def test_load_run_concurrency(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text("[run]\nconcurrency = 8\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.run.concurrency == 8


def test_load_run_concurrency_defaults_to_1(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text("[run]\nsmoke = true\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.run.concurrency == 1
```

Add to `tests/test_cli_run.py` next to `test_apply_overrides_from_parse_args`:

```python
def test_apply_overrides_concurrency_from_flag():
    args = parse_args(["run", "--concurrency", "8"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 8


def test_apply_overrides_concurrency_zero_becomes_one():
    args = parse_args(["run", "--concurrency", "0"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 1


def test_apply_overrides_concurrency_negative_becomes_one():
    args = parse_args(["run", "--concurrency", "-3"])
    cfg = apply_overrides(_cfg(planner=_PLANNER), args)
    assert cfg.run.concurrency == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_cli_help.py tests/test_config.py tests/test_cli_run.py::test_apply_overrides_concurrency_from_flag tests/test_cli_run.py::test_apply_overrides_concurrency_zero_becomes_one tests/test_cli_run.py::test_apply_overrides_concurrency_negative_becomes_one -v`

Expected: FAIL — `Namespace` has no `concurrency` and/or `RunConfig` has no `concurrency`.

- [ ] **Step 3: Minimal implementation**

In `src/mai_bench2/config.py`: add `"concurrency"` to `_RUN_KEYS` after `"repeats"`; add `concurrency: int = 1` on `RunConfig` after `repeats`; in `apply_overrides` after the repeats block:

```python
    if getattr(args, "concurrency", None) is not None:
        run = replace(run, concurrency=max(1, int(args.concurrency)))
```

In `src/mai_bench2/cli.py` `_add_run_flags`, after `--repeats`:

```python
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Gold items in flight per suite (default 1)",
    )
```

In `config.example.toml` `[run]`, after `repeats = 1`:

```toml
concurrency = 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_cli_help.py tests/test_config.py tests/test_cli_run.py::test_apply_overrides_from_parse_args tests/test_cli_run.py::test_apply_overrides_concurrency_from_flag tests/test_cli_run.py::test_apply_overrides_concurrency_zero_becomes_one tests/test_cli_run.py::test_apply_overrides_concurrency_negative_becomes_one -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/config.py src/mai_bench2/cli.py config.example.toml tests/test_cli_help.py tests/test_config.py tests/test_cli_run.py
git commit -m "$(cat <<'EOF'
feat: add run.concurrency and --concurrency

EOF
)"
```

---

### Task 2: `map_items` and `progress.complete`

**Files:**
- Create: `src/mai_bench2/parallel.py`
- Create: `tests/test_parallel.py`
- Modify: `src/mai_bench2/progress.py` (`RunProgress.complete`)
- Modify: `tests/test_progress.py` (`RecordingProgress.complete`)
- Test: `tests/test_parallel.py`, `tests/test_progress.py::test_complete_is_item_then_advance`

**Interfaces:**
- Consumes: `item_span(progress, suite, item_id)` from `mai_bench2.progress`; `RunProgress.item`
- Produces: `map_items(fn: Callable[[dict], T], items: list[dict], *, concurrency: int, progress=None, suite: str) -> list[T | Exception]`; `RunProgress.complete(self, suite: str, item_id: str) -> None`. Return list aligned with `items`. Slot `i` is `fn(items[i])` or the `Exception` it raised. `concurrency = max(1, int(concurrency))`. `N == 1` or `len(items) <= 1`: serial `with item_span(...):` around `fn`. `N > 1`: `ThreadPoolExecutor(max_workers=N)`; main thread `as_completed` then `progress.complete(suite, id)` if `progress is not None`. Do not catch `KeyboardInterrupt` or `SystemExit`.

- [ ] **Step 1: Write the failing tests**

Add to `RecordingProgress` in `tests/test_progress.py`:

```python
    def complete(self, suite: str, item_id: str) -> None:
        with self.item(suite, item_id):
            pass
```

Add:

```python
def test_complete_is_item_then_advance():
    progress = RecordingProgress()
    progress.complete("planner", "p-amb-002")
    assert progress.ticks == [("planner", "p-amb-002")]


def test_run_progress_complete_is_silent_when_not_a_tty():
    stream = StringIO()
    progress = make_progress(8, stream=stream, repeats=1)
    with progress:
        progress.complete("planner", "p-amb-002")
    assert stream.getvalue() == ""
```

Create `tests/test_parallel.py`:

```python
import threading

from mai_bench2.parallel import map_items


class RecordingProgress:
    def __init__(self):
        self.ticks: list[tuple[str, str]] = []

    def complete(self, suite: str, item_id: str) -> None:
        self.ticks.append((suite, item_id))

    def item(self, suite: str, item_id: str):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            try:
                yield
            finally:
                self.ticks.append((suite, item_id))

        return _cm()


def test_map_items_serial_preserves_order_and_ticks():
    progress = RecordingProgress()
    items = [{"id": "a"}, {"id": "b"}]
    out = map_items(
        lambda item: item["id"].upper(),
        items,
        concurrency=1,
        progress=progress,
        suite="planner",
    )
    assert out == ["A", "B"]
    assert progress.ticks == [("planner", "a"), ("planner", "b")]


def test_map_items_pool_returns_input_order_when_b_finishes_first():
    b_done = threading.Event()

    def fn(item):
        if item["id"] == "b":
            try:
                return "B"
            finally:
                b_done.set()
        assert b_done.wait(timeout=2)
        return "A"

    items = [{"id": "a"}, {"id": "b"}]
    progress = RecordingProgress()
    out = map_items(fn, items, concurrency=2, progress=progress, suite="planner")
    assert out == ["A", "B"]
    assert set(progress.ticks) == {("planner", "a"), ("planner", "b")}
    assert len(progress.ticks) == 2


def test_map_items_pool_overlaps():
    barrier = threading.Barrier(2)

    def fn(item):
        barrier.wait(timeout=2)
        return item["id"]

    out = map_items(
        fn, [{"id": "a"}, {"id": "b"}], concurrency=2, progress=None, suite="planner"
    )
    assert out == ["a", "b"]


def test_map_items_keeps_other_item_when_one_raises():
    progress = RecordingProgress()

    def fn(item):
        if item["id"] == "boom":
            raise RuntimeError("down")
        return 1

    out = map_items(
        fn,
        [{"id": "ok"}, {"id": "boom"}],
        concurrency=2,
        progress=progress,
        suite="planner",
    )
    assert out[0] == 1
    assert isinstance(out[1], RuntimeError)
    assert str(out[1]) == "down"
    assert len(progress.ticks) == 2


def test_map_items_clamps_non_positive_concurrency():
    out = map_items(
        lambda item: item["id"], [{"id": "a"}], concurrency=0, progress=None, suite="planner"
    )
    assert out == ["a"]
```

`test_map_items_pool_overlaps` deadlocks (Barrier timeout) if `concurrency=2` still runs serially.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_parallel.py tests/test_progress.py::test_complete_is_item_then_advance tests/test_progress.py::test_run_progress_complete_is_silent_when_not_a_tty -v`

Expected: FAIL — `mai_bench2.parallel` missing, and/or `RunProgress` has no `complete`.

- [ ] **Step 3: Minimal implementation**

`RunProgress.complete` in `src/mai_bench2/progress.py`:

```python
    def complete(self, suite: str, item_id: str) -> None:
        with self.item(suite, item_id):
            pass
```

Create `src/mai_bench2/parallel.py`:

```python
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, TypeVar

from mai_bench2.progress import item_span

T = TypeVar("T")


def map_items(
    fn: Callable[[dict], T],
    items: list[dict],
    *,
    concurrency: int,
    progress=None,
    suite: str,
) -> list[T | Exception]:
    workers = max(1, int(concurrency))
    if workers == 1 or len(items) <= 1:
        return _serial(fn, items, progress, suite)
    return _pool(fn, items, workers, progress, suite)


def _serial(fn, items, progress, suite) -> list:
    out: list = []
    for item in items:
        with item_span(progress, suite, str(item.get("id") or "")):
            try:
                out.append(fn(item))
            except Exception as exc:
                out.append(exc)
    return out


def _pool(fn, items, workers: int, progress, suite) -> list:
    slots: list = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): index for index, item in enumerate(items)}
        for fut in as_completed(futures):
            index = futures[fut]
            item = items[index]
            try:
                slots[index] = fut.result()
            except Exception as exc:
                slots[index] = exc
            if progress is not None:
                progress.complete(suite, str(item.get("id") or ""))
    return slots
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_parallel.py tests/test_progress.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/parallel.py src/mai_bench2/progress.py tests/test_parallel.py tests/test_progress.py
git commit -m "$(cat <<'EOF'
feat: map gold items with a bounded thread pool

EOF
)"
```

---

### Task 3: Cache write lock

**Files:**
- Modify: `src/mai_bench2/client.py` (`__init__`, cache write in `chat`)
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: existing `ChatClient.chat`, `_cache_path`, `_usage_lock`
- Produces: `self._cache_lock = threading.Lock()`; `mkdir` + `write_text` of a cache file happen while holding `_cache_lock`. Usage stays on `_usage_lock`. No singleflight.

- [ ] **Step 1: Write the test**

Append to `tests/test_client.py` (reuse `_resp` in that file):

```python
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
```

This pins concurrent `chat` + two cache files. It may pass without a lock (different keys). Still add the lock in Step 3; do not skip it.

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_client.py::test_cache_writes_from_two_threads -v`

Expected: FAIL if usage updates race badly, otherwise PASS — then still implement the lock.

- [ ] **Step 3: Add the cache lock**

In `ChatClient.__init__`, next to `_usage_lock`:

```python
        self._cache_lock = threading.Lock()
```

Replace the post-call cache write (`if not self._no_cache:` after building `result`) with:

```python
        if not self._no_cache:
            payload = json.dumps(
                {
                    "text": text,
                    "usage": asdict(usage),
                    "tool_calls": [asdict(call) for call in tool_calls],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            with self._cache_lock:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(payload)
```

Leave the cache **read** path unchanged.

- [ ] **Step 4: Re-run client tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_client.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/client.py tests/test_client.py
git commit -m "$(cat <<'EOF'
fix: lock LLM cache writes for concurrent items

EOF
)"
```

---

### Task 4: Planner suite uses `map_items`

**Files:**
- Modify: `src/mai_bench2/suites/planner.py` (item loop)
- Test: `tests/test_progress.py`, `tests/test_suite_planner.py`

**Interfaces:**
- Consumes: `map_items(fn, items, *, concurrency, progress, suite) -> list[PlannerTrace | Exception]`; `cfg.run.concurrency`
- Produces: same `SuiteResult` as today for `concurrency=1`. `fn(item)` calls `run_planner_loop` and may raise. Fold walks `zip(selected, results)` in that order.

- [ ] **Step 1: Write the tests**

Add to `tests/test_progress.py` (existing `_cfg`, `BoomClient`, `SilentClient`, `load_persona`, `ROOT`):

```python
def test_planner_concurrency_two_ticks_all_failures():
    progress = RecordingProgress()
    cfg = _cfg(planner=_PLANNER)
    cfg.run.concurrency = 2
    run_planner_suite(
        cfg,
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 8
    assert {suite for suite, _ in progress.ticks} == {"planner"}


def test_planner_concurrency_two_matches_serial_subscore():
    serial = run_planner_suite(
        _cfg(planner=_PLANNER),
        SilentClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
    )
    cfg = _cfg(planner=_PLANNER)
    cfg.run.concurrency = 2
    parallel = run_planner_suite(
        cfg,
        SilentClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
    )
    assert parallel.n_items == serial.n_items
    assert parallel.subscore == serial.subscore
    assert [pred.id for pred in parallel.predictions] == [pred.id for pred in serial.predictions]
```

These may pass on the old serial loop. Keep them. Replacing the loop with `map_items` is this task’s production change; Task 2 already proved the pool.

- [ ] **Step 2: Run tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_progress.py::test_planner_concurrency_two_ticks_all_failures tests/test_progress.py::test_planner_concurrency_two_matches_serial_subscore tests/test_suite_planner.py -v`

Expected: PASS (characterization) or FAIL if `concurrency` is missing (Task 1 not done).

- [ ] **Step 3: Replace the planner item loop**

Remove `from mai_bench2.progress import item_span`. Add `from mai_bench2.parallel import map_items`.

Replace the `for item in selected:` block (including the `scored` / `failures` init that only served that loop) with:

```python
    def _one(item: dict) -> PlannerTrace:
        return run_planner_loop(client, persona, item, prompts=prompts)

    mapped = map_items(
        _one,
        selected,
        concurrency=cfg.run.concurrency,
        progress=progress,
        suite="planner",
    )
    scored: list[tuple[dict, PlannerTrace]] = []
    predictions: list[Prediction] = []
    failures = 0
    first_error: str | None = None
    for item, result in zip(selected, mapped):
        if isinstance(result, Exception):
            failures += 1
            first_error = first_error or f"{type(result).__name__}: {result}"
            continue
        scored.append((item, result))
        gold = item["gold"] if isinstance(item.get("gold"), dict) else item
        predictions.append(
            Prediction(
                id=str(item.get("id") or ""),
                gold=str(gold.get("action") or ""),
                pred=result.action,
                extra={
                    "final_action": result.final_action,
                    "stop_reason": result.stop_reason,
                    "tools_called": list(result.tools_called),
                    "wait_seconds": result.wait_seconds,
                    "total_waited": result.total_waited,
                    "assistant_text": result.assistant_text,
                    "native_tool_call_count": result.native_tool_call_count,
                    "accepted": accepted_actions(gold),
                },
            )
        )
```

- [ ] **Step 4: Run planner tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_suite_planner.py tests/test_progress.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/planner.py tests/test_progress.py
git commit -m "$(cat <<'EOF'
feat: run planner gold items concurrently

EOF
)"
```

---

### Task 5: Replyer suite uses `map_items`

**Files:**
- Modify: `src/mai_bench2/suites/replyer.py`
- Test: `tests/test_progress.py`, `tests/test_suite_replyer.py`

**Interfaces:**
- Consumes: `map_items`; `cfg.run.concurrency`
- Produces: private `@dataclass` `_ReplyerOne` with `kind: str` (`"model_fail"` | `"judge_transport"` | `"ok"`), `error: str | None`, `visible: str`, `row: dict | None`. `fn` catches model/judge exceptions and returns those kinds (does not raise). Fold preserves `all model calls failed` vs `all judge calls failed`.

- [ ] **Step 1: Write the test**

Add to `tests/test_progress.py`:

```python
def test_replyer_concurrency_two_ticks_all_failures():
    progress = RecordingProgress()
    cfg = _cfg(replyer=_REPLYER, judge=_JUDGE)
    cfg.run.concurrency = 2
    run_replyer_suite(
        cfg,
        BoomClient(),
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 8
    assert {suite for suite, _ in progress.ticks} == {"replyer"}
```

- [ ] **Step 2: Run the test plus replyer suite tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_progress.py::test_replyer_concurrency_two_ticks_all_failures tests/test_suite_replyer.py -v`

Expected: PASS on the old loop. Still do Step 3.

- [ ] **Step 3: Replace the replyer item loop**

Add `from dataclasses import dataclass` and `from mai_bench2.parallel import map_items`. Remove `item_span`.

```python
@dataclass
class _ReplyerOne:
    kind: str
    error: str | None = None
    visible: str = ""
    row: dict | None = None
```

Replace the `for item in selected:` loop with:

```python
    def _one(item: dict) -> _ReplyerOne:
        try:
            visible = generate_reply(replyer_client, persona, item, prompts)
        except Exception as exc:
            return _ReplyerOne("model_fail", error=f"{type(exc).__name__}: {exc}")
        try:
            row = judge_reply(judge_client, persona, item, visible)
        except Exception as exc:
            return _ReplyerOne(
                "judge_transport",
                error=f"{type(exc).__name__}: {exc}",
                visible=visible,
            )
        return _ReplyerOne("ok", visible=visible, row=row)

    mapped = map_items(
        _one,
        selected,
        concurrency=cfg.run.concurrency,
        progress=progress,
        suite="replyer",
    )
    rows: list[dict] = []
    predictions: list[Prediction] = []
    failures = 0
    first_error: str | None = None
    judge_transport = 0
    judge_unparsed = 0
    for item, result in zip(selected, mapped):
        if isinstance(result, Exception):
            failures += 1
            first_error = first_error or f"{type(result).__name__}: {result}"
            continue
        if result.kind == "model_fail":
            failures += 1
            first_error = first_error or result.error
            continue
        if result.kind == "judge_transport":
            judge_transport += 1
            first_error = first_error or result.error
            continue
        row = result.row or {}
        if row.get("judge_fail"):
            judge_unparsed += 1
        else:
            rows.append(row)
        gold = item["gold"] if isinstance(item.get("gold"), dict) else item
        predictions.append(
            Prediction(
                id=str(item.get("id") or ""),
                gold=str(gold.get("action") or ""),
                pred=result.visible,
                extra=dict(row),
            )
        )
```

Keep the `dropped` / status / `replyer_v1` tail unchanged.

- [ ] **Step 4: Run replyer tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_suite_replyer.py tests/test_progress.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/replyer.py tests/test_progress.py
git commit -m "$(cat <<'EOF'
feat: run replyer gold items concurrently

EOF
)"
```

---

### Task 6: E2E suite uses `map_items`

**Files:**
- Modify: `src/mai_bench2/suites/e2e.py`
- Test: `tests/test_progress.py`, `tests/test_suite_e2e.py`

**Interfaces:**
- Consumes: `map_items`; `cfg.run.concurrency`
- Produces: private `@dataclass` `_E2eOne` with fields `trace`, `visible`, `produced`, `row`, `gold_action`, `accepted`, `extra`, `pred`, `joint`, `mute_reply`, `judge_unparsed`. `fn` raises on the same failures as today’s outer `try` (no prediction). Fold walks selected order.

- [ ] **Step 1: Write the test**

Add to `tests/test_progress.py`:

```python
def test_e2e_concurrency_two_ticks_all_failures():
    progress = RecordingProgress()
    cfg = _cfg(planner=_PLANNER, replyer=_REPLYER, judge=_JUDGE)
    cfg.run.concurrency = 2
    run_e2e_suite(
        cfg,
        BoomClient(),
        BoomClient(),
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 4
    assert {suite for suite, _ in progress.ticks} == {"e2e"}
```

- [ ] **Step 2: Run the test plus e2e suite tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_progress.py::test_e2e_concurrency_two_ticks_all_failures tests/test_suite_e2e.py -v`

Expected: PASS on the old loop. Still do Step 3.

- [ ] **Step 3: Replace the e2e item loop**

Add `from dataclasses import dataclass` if needed, `from mai_bench2.parallel import map_items`, remove `item_span`.

```python
@dataclass
class _E2eOne:
    trace: PlannerTrace
    visible: str
    produced: bool
    row: dict | None
    gold_action: str
    accepted: list
    extra: dict
    pred: str
    joint: float
    mute_reply: bool
    judge_unparsed: bool
```

Replace `for item in selected:` with:

```python
    def _one(item: dict) -> _E2eOne:
        trace = run_planner_loop(planner_client, persona, item, prompts=prompts)
        visible = ""
        produced = False
        row: dict | None = None
        unparsed = False
        if trace.replied:
            work = dict(item)
            work["target_t"] = int(item.get("target_t") or 0) + int(trace.total_waited)
            work["oracle_handoff"] = _handoff_from_trace(trace)
            visible = generate_reply(replyer_client, persona, work, prompts)
            produced = True
            row = judge_reply(judge_client, persona, work, visible)
            unparsed = bool(row.get("judge_fail"))
        gold = item["gold"] if isinstance(item.get("gold"), dict) else item
        gold_action = str(gold.get("action") or "")
        accepted = accepted_actions(gold)
        mute_reply = accepted == ["reply"] and not produced
        joint = joint_item(
            accepted,
            produced,
            visible,
            list(gold.get("required_facts") or []),
            first_action=trace.action,
        )
        extra: dict = {
            "planner_action": trace.action,
            "planner_final_action": trace.final_action,
            "stop_reason": trace.stop_reason,
            "tools_called": list(trace.tools_called),
            "wait_seconds": trace.wait_seconds,
            "total_waited": trace.total_waited,
            "assistant_text": trace.assistant_text,
            "native_tool_call_count": trace.native_tool_call_count,
            "accepted": accepted_actions(gold),
        }
        if row is not None:
            extra.update(row)
        return _E2eOne(
            trace=trace,
            visible=visible,
            produced=produced,
            row=row,
            gold_action=gold_action,
            accepted=accepted,
            extra=extra,
            pred=visible if produced else trace.action,
            joint=joint,
            mute_reply=mute_reply,
            judge_unparsed=unparsed,
        )

    mapped = map_items(
        _one,
        selected,
        concurrency=cfg.run.concurrency,
        progress=progress,
        suite="e2e",
    )
    scored: list[tuple[dict, PlannerTrace]] = []
    joints: list[float] = []
    judge_rows: list[dict] = []
    predictions: list[Prediction] = []
    failures = 0
    first_error: str | None = None
    judge_unparsed = 0
    for item, result in zip(selected, mapped):
        if isinstance(result, Exception):
            failures += 1
            first_error = first_error or f"{type(result).__name__}: {result}"
            continue
        if result.judge_unparsed:
            judge_unparsed += 1
        elif result.row is not None:
            judge_rows.append(result.row)
        if result.mute_reply:
            judge_rows.append(silent_row())
        joints.append(result.joint)
        scored.append((item, result.trace))
        predictions.append(
            Prediction(
                id=str(item.get("id") or ""),
                gold=result.gold_action,
                pred=result.pred,
                extra=result.extra,
            )
        )
```

Keep the native / `pair_v1` / `n_items=len(scored) - judge_unparsed` tail unchanged.

- [ ] **Step 4: Run e2e tests**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_suite_e2e.py tests/test_progress.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/e2e.py tests/test_progress.py
git commit -m "$(cat <<'EOF'
feat: run e2e gold items concurrently

EOF
)"
```

---

### Task 7: Docs and full suite

**Files:**
- Modify: `README.md` (after the `--repeats` paragraph)
- Modify: `tests/test_docs.py`
- Test: `tests/test_docs.py`; full pytest

**Interfaces:**
- Consumes: Task 1 already added `concurrency = 1` to `config.example.toml`
- Produces: README documents `--concurrency` / `run.concurrency`

- [ ] **Step 1: Write the failing docs tests**

In `tests/test_docs.py` `test_readme_covers_install_suites_gating_and_warnings`, add:

```python
    assert "--concurrency" in text
    assert "concurrency" in (ROOT / "config.example.toml").read_text(encoding="utf-8")
```

In `test_example_config_seats_env_smoke_and_temps`, add:

```python
    assert "concurrency = 1" in text
```

- [ ] **Step 2: Run docs tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_docs.py -v`

Expected: FAIL on `--concurrency` missing from README (`concurrency = 1` in the example already from Task 1).

- [ ] **Step 3: README**

Immediately after the `--repeats N` paragraph in `README.md`, add:

```markdown
Pass `--concurrency N` (or `run.concurrency` in `config.toml`) to keep up to N gold
items in flight inside a suite. Default is 1. Planner steps, reply-then-judge, and
the e2e chain stay serial per item; suites and `--repeats` passes stay one after
another. Example: `mai-bench-2 run --full --concurrency 8`.
```

- [ ] **Step 4: Full pytest**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest -q`

Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_docs.py
git commit -m "$(cat <<'EOF'
docs: document item concurrency

EOF
)"
```

---

## Spec coverage (self-review)

| Spec section | Task |
|---|---|
| `run.concurrency` / `--concurrency` / default 1 / `max(1,N)` / `is not None` | 1 |
| `map_items` serial vs pool, input order, Exception slots | 2 |
| `progress.complete` on pool; `item` wrap on serial | 2 |
| Cache write lock, no singleflight, shared client | 3 |
| Planner fold selected order | 4 |
| Replyer tagged kinds, error strings unchanged | 5 |
| E2E payload fold, outer except → Exception slot | 6 |
| README + example + `test_docs` | 1 (toml) + 7 |
| No asyncio / no overlapping suites / scores unchanged | constraints + Task 4 characterization |
| Progress ticks including failures at N=2 | 4–6 |
