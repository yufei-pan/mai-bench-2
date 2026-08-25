# mai-bench-2 Resume and Checkpoints

**Date:** 2026-08-25  
**Status:** Draft (brainstorming); awaiting spec review  
**Author:** Yufei Pan / AI-assisted design  
**Repo:** mai-bench-2

This spec adds **in-place resume** of a run stamp: a `checkpoint.json` written from the first gold item, `mai-bench-2 resume` with a TTY picker, SIGINT drain/abandon, and seat-model gates. It does **not** change scoring formulas, gold, prompts, headline gates, the LLM cache key, or `compare` grouping.

## Purpose

API blips and Ctrl-C currently either drop an item (`failed_items`, headlines gated `subset`) or leave **no** folder (artifacts are written only at process end). The user should be able to pick that attempt, confirm the live config is the same model, retry only the seats that never answered, and publish into the **same** `results/<stamp>/`.

## Locked decisions

| Topic | Decision |
|---|---|
| Where numbers live | In place: same `results/<stamp>/`. No second stamp |
| Checkpoint | `checkpoint.json` in that directory. Atomic replace (temp file + rename) |
| When to write | Create the stamp **before** any gold item. Update after **each** item returns |
| Retry set | `pending`, `transport_fail`, `abandoned`. Not wrong scores, not `judge_fail` parse |
| SIGINT / SIGTERM first | Drain: stop starting new items; wait for in-flight; exit 130 |
| SIGINT second | Abandon in-flight (do not wait); those ids stay retryable; exit 130 |
| Picker | TTY list, newest first. `--stamp <UTC>` always works. No TTY and no `--stamp`: list on stderr, exit 1 |
| Seat match (block) | Per used seat: `model`, `reasoning_effort`, `temperature`, `assistant_prefill`, `extra_body`. Ignore `api_key` |
| `base_url` | Warn if different; do not block |
| Identity match (block) | `rubric_hash`, `persona_hex`, `prompts_hex`, gold id set per suite |
| Incomplete run exit | Retryable leftovers → exit **1** (not today's silent 0 + subset). SIGINT → **130** |
| Complete run | Usual artifacts, headlines as today, exit 0 |
| Keys | Still never stored. Redacted `config.toml` unchanged |
| Cache | Unchanged. Resume re-calls item `fn`; hits skip HTTP |
| Out of scope | Asyncio, multiprocessing, overlapping suites, Elo, rewriting old stamps that already `complete` |

## 1. Architecture

Four units:

1. **`checkpoint.py`** — schema, load, atomic save, classify retryable, synthesize from a legacy folder.
2. **`parallel.py`** — stop submitting the whole suite up front. Queue of size `concurrency`. Honour a shared drain/abandon flag.
3. **`resume.py`** — list stamps, picker, gates, drive `run_suites` restricted to retryable ids, merge, rewrite artifacts in place.
4. **`cli.py`** — create stamp + checkpoint at start of a live run; install SIGINT/SIGTERM; `resume` subcommand next to `compare`.

```
live run
  mkdir results/<stamp>/
  write checkpoint.json (all planned items pending)
  map_items (queue) → update checkpoint per item
  if retryable leftovers: partial snapshot, exit 1 or 130
  else: write_artifacts as today, checkpoint complete, exit 0

resume [--stamp]
  pick / load checkpoint
  gate identity + seats
  map_items only retryable ids
  merge payloads → same stamp
```

Suites still fold on the main thread. Workers only run `fn`. The checkpoint stores the **fn result** (JSON), not a second scoring implementation.

## 2. `checkpoint.json`

```json
{
  "version": 1,
  "stamp": "2026-08-25T004133Z",
  "state": "running",
  "smoke": false,
  "suite_flag": null,
  "rubric_hash": "cd59c46d6f4e",
  "persona_id": "official",
  "persona_hex": "77be5c59f150",
  "prompts_id": "official",
  "prompts_hex": "621b20d80f43",
  "gold_ids": {
    "planner": ["p-001", "p-002"],
    "replyer": ["r-001"],
    "e2e": ["e-001"]
  },
  "seats": {
    "planner": {
      "model": "cliproxyapi/gpt-5.6-terra(max)",
      "reasoning_effort": "xhigh",
      "temperature": 0.0,
      "assistant_prefill": true,
      "extra_body": {},
      "base_url": "http://127.0.0.1:8470/v1"
    }
  },
  "items": [
    {
      "suite": "planner",
      "id": "p-001",
      "sample": 0,
      "status": "ok",
      "error": null,
      "payload": {}
    }
  ]
}
```

**`state`:** `running` | `incomplete` | `complete`. `complete` means every item is `ok`. `running` is a live process (also left behind after a crash or SSH drop). `incomplete` is a finished process with retryable leftovers (including SIGINT). A picker lists `running` and `incomplete`, never `complete`. Resume of `state=running` is allowed — the process may be dead — but prints `warning: checkpoint state is running; if a live process still owns this stamp, stop it first` on stderr. Two writers on one stamp are user error.

**`items`:** one row per `(suite, id, sample)` the run planned. `sample` is the `--repeats` index (`0` when `repeats=1`). Suites that were not requested are absent (a planner-only run has no replyer rows).

**`status`:**

| Status | Meaning | Retry? |
|---|---|---|
| `pending` | Not started, or queued after drain | yes |
| `ok` | `fn` returned a scorable result, including `judge_fail` parse miss | no |
| `transport_fail` | API/transport exception, or judge HTTP failure (`model_fail`, `judge_transport`, e2e `judge_error`, raw `Exception` from `fn`) | yes |
| `abandoned` | In-flight when the second SIGINT fired | yes |

**`payload`:** JSON form of that suite's `fn` return, only when `status=ok`. Planner: `asdict(PlannerTrace)`. Replyer: `{kind, visible, row}` (`kind` is `ok`; `judge_fail` lives on `row`). E2e: `asdict` of the per-item result, including nested trace. Tuples (`tool_hits`) become JSON lists; load reconstructs what fold needs.

`payload` is omitted or `null` on non-`ok` rows. `error` is a short `TypeName: message` on `transport_fail`.

Atomic write: write `checkpoint.json.tmp` in the same directory, `fsync`, rename over `checkpoint.json`. Readers treat a missing/partial file as corrupt.

### 2.1 Classifying a `fn` result

Do this on the main thread when the slot is filled, not inside the worker.

- **Planner:** raised `Exception` → `transport_fail`. `PlannerTrace` → `ok`.
- **Replyer:** `kind in {model_fail, judge_transport}` → `transport_fail`. `kind==ok` (including `row.judge_fail`) → `ok`. A worker-raised `Exception` → `transport_fail`.
- **E2e:** raised `Exception` → `transport_fail`. Result with `judge_error` set → `transport_fail` (retry the whole item `fn`; cache hits skip planner/replyer HTTP). `judge_unparsed` without `judge_error` → `ok`.

Wrong action, `contract_fail`, emote-only, and judge parse-fail are `ok`.

### 2.2 Planned item list

At stamp creation, after gold is selected (same `select_items` as the suites, including smoke), write every selected id for every suite this process will run, times `repeats`. Probe happens before items; if a seat probe fails, those suite rows stay `pending` (resume retries when the seat is up). Do not invent rows for skipped seats.

### 2.3 Legacy folders (no `checkpoint.json`)

A pre-this-spec stamp with `summary.json` is resumable when at least one requested suite has gold ids missing from that suite's predictions (today's `failed_items` holes). Synthesize:

- Present prediction ids → `ok` with `payload: null`.
- Missing ids → `transport_fail`.
- Copy identity and seats from `summary.json` + redacted `config.toml`.

On resume, if any `ok` row in a suite has `payload` null, that suite is **cold**: `map_items` runs **all** selected items in that suite once (LLM cache serves the old successes). Then write real payloads. Do not try to reconstruct `PlannerTrace` from `items.tsv`.

A legacy stamp with every gold id present and no retryable hole is **not** listed (it already finished). Smoke-complete and planner-only-complete stamps are not listed.

## 3. Live run and signals

`console()` for `command==run`:

1. Resolve config, persona, prompts, clients, probes (unchanged).
2. Allocate `stamp = UTC now`, `out_dir = output_dir/stamp`, mkdir, write initial `checkpoint.json` (`state=running`) and redacted `config.toml` (so a crash still has models for the picker).
3. Install handlers: SIGINT and SIGTERM share one path. First delivery sets `drain`. Second SIGINT sets `abandon`. SIGTERM does not abandon (one drain only).
4. `run_suites` as today, but `map_items` sees the flag and a checkpoint callback.
5. After suites: if retryable leftovers, `state=incomplete`, write a **partial snapshot** (`summary.json`, `table.txt`, per-suite JSON, `items.tsv` from whatever is `ok`), skip publishable headlines if `n_items` ≠ gold (existing gate). Exit **130** if a signal was received, else **1**. If a signal was received but every item is already `ok`, write the usual artifacts and exit **0** (the interrupt lost the race to a finished run).
6. If every item `ok`: `state=complete`, `write_artifacts` as today (overwrite the early `config.toml`), exit 0 or 1 only from today's suite `error` status (all-calls-failed). A complete checkpoint with all `ok` is a normal publishable run.

Partial snapshot uses the same `write_artifacts` / `render_table` / digest path on the fold of `ok` payloads only. `n_items` is the `ok` count. That is intentional: `compare` can show the stamp; headlines stay gated.

Do not wrap `fn` in the signal handler. The handler only sets flags.

## 4. `map_items` queue

Replace “submit every future at once” with a queue:

- At most `concurrency` items in flight.
- Main thread (or the executor’s completion path) starts the next `pending` item only if **not** `drain` and **not** `abandon`.
- `concurrency==1` is the same policy: drain waits for the current item.
- Return list still **aligned with the input list**. Slots never started stay a distinguished empty; the suite fold must not treat empty as `ok`. The checkpoint already knows `pending`. Practical implementation: `map_items` accepts an optional `skip` predicate / only receives the retryable sublist; the suite zip is over that sublist. **Resume** therefore passes only retryable gold items into `map_items`. **Live drain** stops feeding the remaining selected items; they stay `pending` in the checkpoint without a slot in the returned list — the suite fold only sees items that were started. After drain, the suite must not mark unstarted items as failures.

**Abandon:** do not wait for in-flight workers. `ThreadPoolExecutor.shutdown(wait=False, cancel_futures=True)` for queued work. In-flight threads may still finish and write the LLM cache; if a result arrives before process exit, **prefer `ok` / `transport_fail` over `abandoned`** (the call was paid for). Otherwise mark in-flight ids `abandoned`.

Progress ticks once per item that started (success, transport, abandon). Unstarted pending items do not tick.

`KeyboardInterrupt` / `SystemExit` inside `fn` still propagate (not stored as `transport_fail`).

## 5. Resume CLI

```
mai-bench-2 resume
mai-bench-2 resume --stamp 2026-08-25T004133Z
mai-bench-2 resume --config path.toml
```

`--smoke` / `--full` are **not** on the resume parser (`mai-bench-2 resume --full` is an argparse error). Smoke vs full is whatever the checkpoint recorded. After loading config for keys and seats, set `cfg.run.smoke` from the checkpoint before selecting gold or running suites. `--persona` / `--prompts` are not on the resume parser either; identity must match the checkpoint hexes using the same persona/prompts ids stored there (load those ids from the checkpoint, not from a live `--persona` override).

**Picker (TTY):** `report.grid` rows: stamp, mode (`smoke`/`full`), models (planner / replyer / judge, effort if set), counts `ok/pending/transport_fail/abandoned`. Numbered. Read a line; empty or Ctrl-C → exit 130. Invalid index → stderr, exit 1.

**`--stamp`:** exact folder name under `output_dir`. Missing folder → `unknown group` style error, exit 1. Complete checkpoint → print `already complete: <stamp>` on stdout, exit 0. Corrupt JSON → exit 1.

**`output_dir`:** same resolver as `compare` (TOML `[run] output_dir` without interpolating API keys).

### 5.1 Gates (in order)

1. Load checkpoint (or synthesize legacy).
2. Load persona/prompts from the **checkpoint’s** `persona_id` / `prompts_id` (path ids, same as a normal run). Recompute hexes and `rubric_hash`. If any hex differs from the checkpoint → abort, name which hex. Live `config.toml` `[run] persona` is not used.
3. Gold ids per suite in the checkpoint must equal `select_items` for that suite **with the checkpoint's `smoke` flag**, not the live `--full`. If the set differs → abort (`gold changed`).
4. For each seat key present in `checkpoint.seats`: live `model`, `reasoning_effort`, `temperature`, `assistant_prefill`, `extra_body` must equal. Extra live seats the attempt did not use are ignored. Missing live seat that the attempt used → abort. Mismatch message names the seat and both values.
5. If live `base_url` differs on a used seat → print `warning: <seat> base_url differs (... → ...); resume continues` on stderr. Do not abort.

After gates, build clients from **live** config (keys come from the environment, not the stamp).

### 5.2 Merge

For each suite with retryable rows, call the existing suite runner **restricted to those gold ids** (keep selected order among them). Update checkpoint rows as results arrive (same per-item callback as a live run). Then fold **all** `ok` payloads in original gold order into `SuiteResult` (existing `planner_native` / `replyer_v1` / `pair_v1`). `failed_items` counts remaining retryable rows plus `judge_fail` parse misses already in `ok` payloads (same as today: parse miss is dropped from the mean, counted in `failed_items`, and is **not** retryable).

If any retryable row remains: `state=incomplete`, partial snapshot, exit 1.

If all `ok`: `state=complete`, full `write_artifacts` in that stamp (overwrite), print table + digest as a normal run, exit 0.

Resume uses the same SIGINT drain/abandon against **this** stamp.

`--repeats`: only retry rows whose `sample` is retryable; do not start new samples.

## 6. Error handling and exit codes

| Situation | Exit | Stderr / stdout |
|---|---|---|
| Live run, all `ok` | 0, or 1 if a suite `status==error` as today | table + digest |
| Live run, retryable leftovers, no signal | 1 | table of `ok` so far; checkpoint `incomplete` |
| First SIGINT drain with leftovers | 130 | same snapshot |
| Second SIGINT abandon | 130 | |
| Resume, all `ok` after merge | 0 | table + digest |
| Resume, leftovers remain | 1 | |
| Resume, already complete | 0 | `already complete: <stamp>` |
| No resumable stamps (picker) | 1 | `no resumable runs in <output_dir>` |
| No TTY, no `--stamp` | 1 | the list, then that message |
| Unknown `--stamp` | 1 | `unknown stamp: ...` |
| Corrupt checkpoint | 1 | `corrupt checkpoint: ...` |
| Hex / gold / seat mismatch | 1 | which field, expected vs live |
| Picker Ctrl-C / empty input | 130 | no mutation |

`compare` does not require `state=complete`. It already skips folders without `summary.json`; a partial snapshot has `summary.json`, so interrupted stamps appear. Smoke vs full still split.

## 7. Files

| Path | Role |
|---|---|
| `src/mai_bench2/checkpoint.py` | Schema, atomic IO, classify, legacy synthesize |
| `src/mai_bench2/resume.py` | Picker, gates, merge drive |
| `src/mai_bench2/parallel.py` | Queue, drain, abandon |
| `src/mai_bench2/cli.py` | Stamp at start, signals, `resume` parse/dispatch |
| `src/mai_bench2/suites/*.py` | Per-item callback into checkpoint; optional id filter for resume |
| `tests/test_checkpoint.py` | Atomic write, classify, legacy synthesize, gold-id gate helpers |
| `tests/test_resume.py` | Picker/`--stamp`, seat/`base_url`, hex abort, merge, SIGINT drain vs abandon |
| `tests/test_parallel.py` | Queue width, drain does not start the rest, abandon marks in-flight |
| `tests/test_cli_help.py` | `resume` is not a suite; `--stamp` on resume |
| `README.md`, `README.zh-CN.md` | `mai-bench-2 resume` |

Do not add a `mai-bench-2-resume` wrapper.

## 8. Tests (no live HTTP)

1. After two fake items, `checkpoint.json` has two `ok` payloads; a third `Exception` is `transport_fail`.
2. Drain: concurrency 2, four items, `fn` blocks; first flag lets two finish, the other two stay `pending`.
3. Second flag: in-flight become `abandoned` unless they return first (then `ok`).
4. Resume `--stamp` with one `transport_fail` calls `fn` once, rewrites the same stamp, `state=complete`.
5. Seat `model` mismatch aborts; `base_url` mismatch warns and continues (capsys).
6. `rubric_hash` / gold id mismatch aborts.
7. Replyer `judge_fail` row is `ok`, not in the retry set.
8. Replyer `judge_transport` is retryable; second call with a row completes.
9. Legacy folder: missing id in predictions → listed; resume cold-replays that suite with a cache hit on the kept id (create_fn call count).
10. `parse_args(["resume", "--stamp", "x"])` is `command==resume`; `parse_args(["planner"])` still a run.
11. `parse_args(["resume", "-h"])` mentions `--stamp`, not `--repeats`.
12. Complete checkpoint + `--stamp` exits 0 with `already complete`.

## Non-goals

- Patching `compare` grouping rules
- Storing API keys, or requiring `base_url` equality
- Retrying `judge_fail` parse misses or wrong gold actions
- Interactive picker in tests (use `--stamp`)
- Changing `_RETRYABLE_STATUS_CODES` or `max_attempts`
- Resuming into a new stamp
- Making `mai-bench-2-smoke resume` a thing
