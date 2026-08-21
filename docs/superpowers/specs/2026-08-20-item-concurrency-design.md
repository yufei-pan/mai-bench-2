# mai-bench-2 Item Concurrency

**Date:** 2026-08-20  
**Status:** Draft (brainstorming); awaiting spec review  
**Author:** Yufei Pan / AI-assisted design  
**Repo:** `/mnt/klein/work/mai-bench-2`

This spec adds a **gold-item concurrency** knob so a suite can keep up to N items in flight. It does **not** change scoring formulas, gold, prompts, the numeric table, or the digest/gloss report. It builds on the terminal progress bar (`RunProgress` / one tick per gold item).

## Purpose

A full run is hundreds of independent gold items, each blocked on HTTP. Today the three suites walk items one by one. The user should be able to say how many items may run at once, from config or a CLI flag, without changing results except wall time (and `first_error` remaining gold-order, not race-order).

## Locked decisions

| Topic | Decision |
|---|---|
| Unit | At most **N gold items in flight** in the **current suite** |
| Intra-item | Still serial (planner loop steps; replyer then judge; e2e chain) |
| Suites | Still one after another |
| Repeats | Still sequential passes; items **inside** a pass may be parallel |
| Default | `1` — same as today |
| Where to set | `run.concurrency` in `config.toml`; `--concurrency` overrides |
| Bad values | `max(1, int(N))`, same as `--repeats` |
| Implementation | `ThreadPoolExecutor` for `N > 1`; serial `for` loop for `N == 1` |
| Result order | Fold in **selected-gold order**, not finish order |
| Progress | Serial: wrap work in `progress.item`. Pool: main thread `progress.complete` on `as_completed` (description + tick). Failures still tick. |
| Exceptions | One item `Exception` does not cancel the others. `KeyboardInterrupt` / `SystemExit` propagate |
| HTTP client | Shared `ChatClient` per seat (no client-per-item). Usage lock already exists; add a cache write lock. No singleflight on cache misses |
| Out of scope | Asyncio, multiprocessing, overlapping suites, per-suite concurrency, in-flight HTTP semaphore, ticks on LLM calls |

## 1. Architecture

Three units:

1. **Config** — `RunConfig.concurrency: int = 1`. Suites read `cfg.run.concurrency`. `run_suites` does not grow a new argument.

2. **`map_items`** — new `src/mai_bench2/parallel.py`. Generic “run this list of gold items, return a list aligned with input.” Owns the serial vs pool split and progress ticks. Does not know planner/replyer/e2e scoring.

3. **Suite item functions** — each suite extracts “run this one gold item” (`fn`). The suite still loads gold, calls `map_items`, then **folds** on the main thread into `scored` / `predictions` / `failed_items` using the same rules as today.

```
select_items
    → map_items(fn)     # value or Exception, input order
    → fold on main thread → SuiteResult
```

Workers only call `fn`. They do not touch `SuiteResult`, progress, or `set_sample`.

## 2. `map_items`

```python
def map_items(
    fn: Callable[[dict], T],
    items: list[dict],
    *,
    concurrency: int,
    progress=None,
    suite: str,
) -> list[T | Exception]:
```

- `concurrency = max(1, int(concurrency))`.
- `fn(item)` returns a value or raises `Exception`. `KeyboardInterrupt` and `SystemExit` are not caught.
- Return length equals `len(items)`. Index `i` is the result for `items[i]`, even if item B finished first.
- A raised `Exception` is stored in that slot (the object, not re-raised). The suite fold interprets it.

**`concurrency == 1`:** serial loop, `with progress.item(suite, id):` around `fn(item)` so the bar shows the id while it runs. On `Exception`, store it and continue (the `item` context still advances).

**`concurrency > 1`:** `ThreadPoolExecutor(max_workers=concurrency)`. Submit all items. Main thread `as_completed`: `slots[i] = fut.result()` or the `Exception`; then `progress.complete(suite, id)` (set description to that item, advance 1). Do not call Rich from worker threads.

`progress.complete(suite, id)` is a small addition on `RunProgress`: set description, then `advance(1)`. Disabled/non-TTY `RunProgress` no-ops. Test doubles (`RecordingProgress`) append one tick, same as `item()`’s `finally`. Serial path keeps wrapping with `item()` so N=1 UX is unchanged.

Item id is `str(item.get("id") or "")`, same as the suite loops today.

## 3. Suite fold

Each suite’s `fn` is the current per-item body **without** the progress wrapper and **without** appending to shared lists.

**Planner.** `fn` returns a `PlannerTrace` or raises. Fold: exception → `failures += 1`, set `first_error` if unset **in selected order** (walk the result list, not `as_completed` order). Success → `scored` + `Prediction` as today.

**Replyer.** Today one item has two `try` blocks (model vs judge transport) plus `judge_fail`. `fn` returns a tagged result, not a bare exception for the judge-transport path:

```python
# kind: "model_fail" | "judge_transport" | "ok"
# "ok" still includes rows with judge_fail; fold counts those as unparsed
```

- `model_fail` — `generate_reply` raised. Fold: `failures += 1`, no prediction (same as today’s `continue`).
- `judge_transport` — reply succeeded, `judge_reply` raised. Fold: `judge_transport += 1`, no prediction.
- `ok` — append `Prediction`; if `row["judge_fail"]` then `judge_unparsed += 1`, else score the row.

Do not flatten those into a single `Exception` slot: that would collapse `all model calls failed` vs `all judge calls failed`.

**E2E.** `fn` is the current per-item `try` body (planner → maybe replyer → judge). Exception → same as today’s outer `except` (no prediction). Success payload is whatever fold needs for `scored` / `joints` / `judge_rows` / `predictions`.

`first_error` is the first failure in **selected order**, not whoever failed first in wall time.

Suite status, `n_items`, natives, and subscores stay the current formulas. With `concurrency=1`, a suite result must match today’s result for the same clients and gold (aside from `wall_s`).

## 4. Client and cache

`ChatClient` is shared across workers on a seat.

- Usage: existing `_usage_lock` (keep it).
- Cache write / `mkdir`: take a lock so two workers do not interleave `write_text` on the same path. Two cache **misses** on the same key may both hit the network; no singleflight.
- `set_sample` remains main-thread-only, once per repeat, before `map_items`.
- Do not create a new `OpenAI` client per item.

## 5. Config, CLI, docs

`RunConfig.concurrency: int = 1`. Add `"concurrency"` to `_RUN_KEYS`.

`apply_overrides`: if `args.concurrency is not None` (not truthiness — `0` must still override), set `run.concurrency = max(1, int(args.concurrency))`. Argparse `type=int`, default `None` so omitted means “use config.”

Flag on `run` / `smoke` / `full`: `--concurrency N`. Help: gold items in flight per suite (default 1).

`config.example.toml`:

```toml
concurrency = 1
```

README: next to the `--repeats` paragraph. Example: `mai-bench-2 run --full --concurrency 8`.

## 6. Progress bar

Total is still planned gold items × repeats (unchanged). Ticks remain one per gold item, including failures.

| Mode | When description is set | When the bar advances |
|---|---|---|
| `N == 1` | Item **starts** | Item **ends** (success or fail) |
| `N > 1` | Item **completes** (main thread) | Same moment |

No extra progress lines. Non-TTY still silent.

## 7. Testing

Default `concurrency=1` must keep the existing suite and CLI tests on the serial path (no new threads required).

New tests:

1. **Config/CLI.** TOML loads `run.concurrency`; `--concurrency` overrides; `0` and `-3` become `1`; help lists `--concurrency`.
2. **`map_items` overlap.** Two items, a `threading.Event` barrier so both enter `fn` before either returns. Results in **input order** even if B finishes first.
3. **`map_items` isolation.** One `fn` raises; that slot is the exception; the other value is kept; progress ticks twice.
4. **Suites at `concurrency=2`.** Planner / replyer / e2e with existing fake clients: same `n_items` and subscores as `concurrency=1`; predictions still gold-id order; all-fail still ticks 8 / 8 / 4.
5. **Client.** Two threads writing cache for **different** keys both survive; `usage_snapshot` remains consistent.
6. **Docs.** README and `config.example.toml` mention concurrency (`test_docs` / example-config tests, same style as repeats).

No live-LLM soak. No asyncio tests.

## 8. Files

| File | Role |
|---|---|
| `src/mai_bench2/parallel.py` | **New.** `map_items` |
| `src/mai_bench2/progress.py` | Add `complete(suite, id)` |
| `src/mai_bench2/client.py` | Cache write lock |
| `src/mai_bench2/config.py` | `RunConfig.concurrency`, overrides, `_RUN_KEYS` |
| `src/mai_bench2/cli.py` | `--concurrency` |
| `src/mai_bench2/suites/planner.py` | Item `fn` + `map_items` + fold |
| `src/mai_bench2/suites/replyer.py` | Same |
| `src/mai_bench2/suites/e2e.py` | Same |
| `tests/test_parallel.py` | **New.** `map_items` |
| `tests/test_progress.py` | `complete` + suite tick counts at N=2 |
| `tests/test_config.py`, `test_cli_help.py`, `test_client.py`, `test_docs.py` | Flag, example, cache lock, docs |
| `config.example.toml`, `README.md` | User-facing |

## 9. Non-goals

- Overlapping planner suite with replyer suite
- A global in-flight HTTP cap separate from item N
- Per-suite `suites.planner.concurrency`
- Process pools / asyncio
- Changing cache keys, repeats, or headline gates
