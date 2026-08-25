# mai-bench-2 Per-Seat HTTP Limits

**Date:** 2026-08-21  
**Status:** Draft (brainstorming); awaiting spec review  
**Author:** Yufei Pan / AI-assisted design  
**Repo:** mai-bench-2

This spec adds an optional **in-flight HTTP cap per seat** on the shared `ChatClient`. It does **not** change `run.concurrency` (gold items in the current suite), scoring, gold order, cache keys, or intra-item sequencing. It sits on top of item concurrency (`docs/superpowers/specs/2026-08-20-item-concurrency-design.md`). That spec’s “no HTTP semaphore” non-goal is replaced by this document for **per-seat** limits only.

## Purpose

`--concurrency 7` can put seven items on the judge at once. The planner may tolerate that; the judge may not. The user should cap HTTP on one seat without shrinking the item pool for the others.

## Locked decisions

| Topic | Decision |
|---|---|
| Unit | Concurrent **logical HTTP calls** on that seat’s `ChatClient` |
| Where | Optional `http_limit` on `[planner]` / `[replyer]` / `[judge]` |
| Omitted | `None` — no extra cap; only `run.concurrency` limits work |
| Present | `max(1, int(N))` (`0` and negatives become `1`) |
| CLI | None. `--concurrency` stays the item pool |
| Cache hit | Does **not** acquire |
| Cache miss and `probe()` | Acquire → `_create_with_retries` → release in `finally` |
| Retries | Hold the slot for the whole logical call, including backoff |
| Queue wait | **Unbounded.** Do not `acquire(timeout=timeout_s)` or otherwise fail because the seat is busy |
| `timeout_s` | OpenAI **HTTP** timeout. Starts when `_create` is called, not when the thread starts waiting |
| Sharing | Per **seat**, not per model string. Two seats with the same `model` have independent gates |
| Out of scope | `--judge-http-limit`, `[run] judge_http_limit`, asyncio, per-model-string pooling, changing `map_items` |

## 1. Architecture

Two independent limiters:

1. **Item pool** — `run.concurrency` / `map_items`. Unchanged.
2. **Seat HTTP gate** — `ChatClient` semaphore when `endpoint.http_limit` is set.

A replyer or e2e item occupies one item slot the whole time. Inside that slot it may wait on the replyer gate, then later on the judge gate. A planner item’s loop may `chat()` more than once; those calls are serial in the item, so one item holds at most one planner HTTP slot at a time.

`concurrency = 7` and `[judge] http_limit = 2` means seven items may be in the suite, but only two judge HTTP calls at a time. The other five wait on the judge semaphore while still occupying item slots. That is intended.

## 2. Config

Add `http_limit` to `_ENDPOINT_KEYS` and `EndpointConfig`:

```python
http_limit: int | None = None
```

Load: if the key is absent, `None`. If present, `max(1, int(value))`.

`config.example.toml` — commented on each seat (default remains unlimited):

```toml
# http_limit = 2
```

README: one sentence next to the concurrency paragraph. Example: cap the judge at two in-flight HTTP calls with `http_limit = 2` under `[judge]` while `--concurrency` stays 7.

No argparse flags. No `RunConfig` field.

## 3. `ChatClient`

If `http_limit` is `None`, do not create a semaphore; `chat` / `probe` stay as today.

If set, one `threading.BoundedSemaphore(http_limit)` on the client.

**`chat`:** cache lookup first, **unlocked**. Hit: return as today. Miss: `acquire()` (blocking, no timeout) → `try: _create_with_retries(...)` → `finally: release()`.

**`probe`:** same acquire/try/finally around `_create_with_retries`. Probes run before the suite and are already serial; using the same gate keeps one code path.

Do not wrap cache `write_text` in the HTTP semaphore (cache lock stays separate). Do not nest the usage lock with the HTTP semaphore.

`KeyboardInterrupt` / `SystemExit` while waiting or during HTTP still propagate; `finally` still releases if acquire succeeded.

## 4. Timeouts

Waiting for a slot is not a client timeout and must not become one.

- Do not pass `timeout_s` (or any other deadline) to `acquire`.
- Do not start the OpenAI client timeout until `_create` runs.
- A thread can wait longer than `timeout_s` for a slot and still succeed when the slot frees. A real HTTP 408/timeout from the server or SDK during `_create` still fails that attempt as today (and may retry under the existing policy **while still holding the slot**).

## 5. Testing

Client tests, not suite tests:

1. **Serializes.** `http_limit = 1`, two threads, a barrier so both enter miss-path `chat()` before either `create` returns. Fake `create` records overlap; max overlap is 1.
2. **Unlimited still overlaps.** Same harness, `http_limit` omitted: overlap is 2.
3. **Cache hit skips the gate.** Thread A is in a miss holding the only slot; thread B cache-hits and returns without calling `create`.
4. **Clamp.** TOML `http_limit = 0` loads as `1`; omitted key loads as `None`.
5. **Wait is not a timeout.** Fake `create` for the first caller blocks; the second waits longer than `timeout_s` on the semaphore; when the first finishes, the second `create` runs and succeeds. No timeout exception.

Docs tests: example config mentions `http_limit`; README mentions `http_limit`.

## 6. Files

| File | Role |
|---|---|
| `src/mai_bench2/config.py` | `http_limit` on `EndpointConfig` and `_ENDPOINT_KEYS` |
| `src/mai_bench2/client.py` | Semaphore around miss-path HTTP and `probe` |
| `config.example.toml` | Commented `# http_limit = 2` on each seat |
| `README.md` | One sentence next to concurrency |
| `tests/test_config.py` | Load omitted / `0` → 1 / positive |
| `tests/test_client.py` | Overlap, cache skip, wait ≠ timeout |
| `tests/test_docs.py` | README + example mention `http_limit` |

## 7. Non-goals

- CLI overrides
- One semaphore shared by seats that happen to use the same `model`
- Changing `run.concurrency`, `map_items`, scoring, or cache keys
- Singleflight on cache misses
- Cancelling waiters on Ctrl+C beyond normal `KeyboardInterrupt` propagation
