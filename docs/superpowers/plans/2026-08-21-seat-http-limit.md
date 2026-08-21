# Per-Seat HTTP Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional `http_limit` on each seat caps in-flight logical HTTP calls on that seat’s `ChatClient` without changing `run.concurrency`, scores, or cache keys.

**Architecture:** `EndpointConfig.http_limit: int | None`. Omitted stays `None` (unlimited). Present values `max(1, int(N))`. `ChatClient` takes a `threading.BoundedSemaphore` when the limit is set and wraps miss-path `_create_with_retries` plus `probe()` with unbounded `acquire()` / `finally: release()`. Cache hits skip the gate. `timeout_s` remains the OpenAI HTTP timeout and must not be passed to `acquire`.

**Tech Stack:** Python ≥ 3.11, stdlib `threading`, existing `pytest`. No new dependencies. No CLI flags.

**Spec:** `docs/superpowers/specs/2026-08-21-seat-http-limit-design.md`

## Global Constraints

- Two limiters stay independent: `run.concurrency` / `map_items` (gold items) vs `http_limit` (HTTP on that seat). Do not change `map_items`, scoring, cache keys, or `set_sample`.
- Omitted `http_limit` is `None` — no semaphore. If the key is present, `max(1, int(N))` (`0` and negatives become `1`).
- Cache lookup is unlocked. Hits do not acquire. Misses and `probe()` acquire, run `_create_with_retries` (retries + backoff still hold the slot), release in `finally`.
- Queue wait is unbounded. Do not `acquire(timeout=timeout_s)` or fail because the seat is busy. `timeout_s` starts when `_create` runs.
- Per seat, not per model string. No `--judge-http-limit`, no `[run] judge_http_limit`, no asyncio.
- Do not wrap cache `write_text` in the HTTP semaphore. Do not nest the usage lock with the HTTP semaphore.
- Do not catch `KeyboardInterrupt` or `SystemExit`. `finally` still releases if acquire succeeded.
- Commits: do not run `git config`. Do not `--no-verify`. Do not push.
- Run only the tests named in the task until that task’s end; full pytest in Task 3.
- Tests: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest <args>` from the worktree. On a checkout whose `.venv` is local: `PYTHONPATH=src .venv/bin/python -m pytest <args>`.

## File map

| Path | Responsibility |
|---|---|
| `src/mai_bench2/config.py` | `http_limit` on `EndpointConfig` and `_ENDPOINT_KEYS`; clamp in `_endpoint` |
| `src/mai_bench2/client.py` | `BoundedSemaphore` + `_http_slot` around miss HTTP and `probe` |
| `config.example.toml` | Commented `# http_limit = 2` on each seat |
| `README.md` | One sentence after the concurrency paragraph |
| `tests/test_config.py` | Load omitted / `2` / `0` → 1 / `-3` → 1 |
| `tests/test_client.py` | Overlap, unlimited overlap, cache skip, wait ≠ timeout |
| `tests/test_docs.py` | README + example mention `http_limit` |

---

### Task 1: Config `http_limit`

**Files:**
- Modify: `src/mai_bench2/config.py` (`_ENDPOINT_KEYS`, `EndpointConfig`, `_endpoint`)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `_endpoint(raw)` / `load_config`
- Produces: `EndpointConfig.http_limit: int | None = None`. `_ENDPOINT_KEYS` includes `"http_limit"`. When the TOML key is present, `http_limit = max(1, int(value))`. When absent, `None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def _planner_toml(**extra: str) -> str:
    body = '[planner]\nbase_url="http://p/v1"\napi_key="k"\nmodel="m"\n'
    if extra:
        body += "\n".join(extra.values()) + "\n"
    return body


def test_load_http_limit_omitted_is_none(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner is not None
    assert cfg.planner.http_limit is None


def test_load_http_limit_positive(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = 2\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 2


def test_load_http_limit_zero_becomes_one(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = 0\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 1


def test_load_http_limit_negative_becomes_one(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(_planner_toml(limit="http_limit = -3\n"), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.planner.http_limit == 1
```

If `_planner_toml(**extra)` feels awkward, inline the TOML strings; the asserts above are the contract.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_config.py::test_load_http_limit_omitted_is_none tests/test_config.py::test_load_http_limit_positive tests/test_config.py::test_load_http_limit_zero_becomes_one tests/test_config.py::test_load_http_limit_negative_becomes_one -v`

Expected: FAIL — `EndpointConfig` has no `http_limit`.

- [ ] **Step 3: Implement config**

In `src/mai_bench2/config.py`:

1. Add `"http_limit"` at the end of `_ENDPOINT_KEYS`.
2. On `EndpointConfig`, after `max_attempts`:

```python
    http_limit: int | None = None
```

3. In `_endpoint`, after building `kwargs` and copying `extra_body`, clamp:

```python
    if "http_limit" in kwargs and kwargs["http_limit"] is not None:
        kwargs["http_limit"] = max(1, int(kwargs["http_limit"]))
```

Do not add a `RunConfig` field. Do not add a CLI flag.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_config.py -v`

Expected: PASS (including the four new tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/config.py tests/test_config.py
git commit -m "feat: load optional per-seat http_limit"
```

---

### Task 2: `ChatClient` HTTP gate

**Files:**
- Modify: `src/mai_bench2/client.py` (`ChatClient.__init__`, `chat`, `probe`; add `_http_slot`)
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `EndpointConfig.http_limit: int | None`
- Produces: `ChatClient` with `_http_sema: threading.BoundedSemaphore | None`. `_http_slot()` is a context manager: no-op when `_http_sema is None`; otherwise unbounded `acquire()`, `yield`, `release()` in `finally`. `chat` cache hits skip it. `chat` misses wrap only `_create_with_retries` (usage + cache write stay outside the HTTP semaphore). `probe` wraps `_create_with_retries` the same way. `ChatClient` also uses `max(1, int(http_limit))` when the field is not `None`, so a constructed `EndpointConfig(http_limit=0)` does not create `BoundedSemaphore(0)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py` (it already imports `threading` and `ThreadPoolExecutor`):

```python
def test_http_limit_one_serializes_create(tmp_path: Path):
    inside = 0
    max_inside = 0
    lock = threading.Lock()
    first_in = threading.Event()
    release = threading.Event()

    def create(**kwargs):
        nonlocal inside, max_inside
        with lock:
            inside += 1
            max_inside = max(max_inside, inside)
        first_in.set()
        assert release.wait(timeout=2)
        with lock:
            inside -= 1
        return _resp(text=kwargs["messages"][0]["content"])

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", http_limit=1),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "a"}])
        assert first_in.wait(timeout=2)
        fb = pool.submit(client.chat, [{"role": "user", "content": "b"}])
        assert not fb.done()
        with lock:
            assert max_inside == 1
        release.set()
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert {ra.text, rb.text} == {"a", "b"}
    assert max_inside == 1


def test_http_limit_omitted_allows_overlap(tmp_path: Path):
    barrier = threading.Barrier(2)
    inside = 0
    max_inside = 0
    lock = threading.Lock()

    def create(**kwargs):
        nonlocal inside, max_inside
        with lock:
            inside += 1
            max_inside = max(max_inside, inside)
        barrier.wait(timeout=2)
        with lock:
            inside -= 1
        return _resp(text=kwargs["messages"][0]["content"])

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m"),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "a"}])
        fb = pool.submit(client.chat, [{"role": "user", "content": "b"}])
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert {ra.text, rb.text} == {"a", "b"}
    assert max_inside == 2


def test_http_limit_cache_hit_skips_gate(tmp_path: Path):
    first_in = threading.Event()
    release = threading.Event()
    live_calls = {"n": 0}

    def create(**kwargs):
        live_calls["n"] += 1
        first_in.set()
        assert release.wait(timeout=2)
        return _resp(text=kwargs["messages"][0]["content"])

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", http_limit=1),
        "judge",
        tmp_path,
        no_cache=False,
        create_fn=create,
    )
    primed = client.chat([{"role": "user", "content": "cached"}])
    assert primed.cached is False
    live_calls["n"] = 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        live = pool.submit(client.chat, [{"role": "user", "content": "live"}])
        assert first_in.wait(timeout=2)
        hit = pool.submit(client.chat, [{"role": "user", "content": "cached"}])
        cached = hit.result(timeout=2)
        assert cached.cached is True
        assert cached.text == "cached"
        assert live_calls["n"] == 1
        assert not live.done()
        release.set()
        live.result(timeout=5)
    assert live_calls["n"] == 1


def test_http_limit_wait_is_not_a_timeout(tmp_path: Path):
    first_in = threading.Event()
    release = threading.Event()
    second_create = threading.Event()

    def create(**kwargs):
        text = kwargs["messages"][0]["content"]
        if text == "first":
            first_in.set()
            assert release.wait(timeout=2)
        else:
            second_create.set()
        return _resp(text=text)

    client = ChatClient(
        EndpointConfig("http://x/v1", "k", "m", timeout_s=0.05, http_limit=1),
        "judge",
        tmp_path,
        no_cache=True,
        create_fn=create,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(client.chat, [{"role": "user", "content": "first"}])
        assert first_in.wait(timeout=2)
        fb = pool.submit(client.chat, [{"role": "user", "content": "second"}])
        assert not second_create.wait(timeout=0.15)
        assert not fb.done()
        try:
            assert fb.exception(timeout=0.01) is None
        except TimeoutError:
            pass  # still waiting on the semaphore — not a chat timeout
        release.set()
        ra, rb = fa.result(timeout=5), fb.result(timeout=5)
    assert ra.text == "first"
    assert rb.text == "second"
    assert second_create.is_set()
```

`TimeoutError` here is `concurrent.futures.TimeoutError` (alias of `TimeoutError` on 3.11+). Do not treat it as an HTTP failure.

`test_http_limit_one_serializes_create` deadlocks if `http_limit=1` still overlaps inside `create` (the second call would also enter `create` and wait on `release` before the first finishes).

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_client.py::test_http_limit_one_serializes_create tests/test_client.py::test_http_limit_omitted_allows_overlap tests/test_client.py::test_http_limit_cache_hit_skips_gate tests/test_client.py::test_http_limit_wait_is_not_a_timeout -v`

Expected: FAIL — `test_http_limit_one_serializes_create` sees `max_inside == 2` (or `http_limit` unexpected kwargs if Task 1 is missing). `test_http_limit_omitted_allows_overlap` may already PASS (characterization). Still add the gate.

- [ ] **Step 3: Implement the gate**

At top of `src/mai_bench2/client.py`, add `import contextlib` (keep `import threading`).

In `ChatClient.__init__`, after `_cache_lock`:

```python
        raw_limit = endpoint.http_limit
        if raw_limit is None:
            self._http_sema = None
        else:
            self._http_sema = threading.BoundedSemaphore(max(1, int(raw_limit)))
```

Add:

```python
    @contextlib.contextmanager
    def _http_slot(self):
        sema = self._http_sema
        if sema is None:
            yield
            return
        sema.acquire()  # blocking, no timeout
        try:
            yield
        finally:
            sema.release()
```

In `chat`, wrap only the miss-path `_create_with_retries` call (kwargs stay built outside the slot; cache write stays after, under `_cache_lock` only):

```python
        kwargs = self._request_kwargs(
            messages,
            max_tokens=effective_max_tokens,
            temperature=effective_temperature,
            tools=tools,
        )
        with self._http_slot():
            response = self._create_with_retries(kwargs)
```

In `probe`:

```python
        kwargs = self._request_kwargs(
            messages,
            max_tokens=max_tokens,
            temperature=self._endpoint.temperature,
            tools=None,
        )
        with self._http_slot():
            self._create_with_retries(kwargs)
```

Do not pass `timeout=` to `acquire`. Do not call `acquire` on the cache-hit return path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_client.py -v`

Expected: PASS (including the four new tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/client.py tests/test_client.py
git commit -m "feat: cap in-flight HTTP per seat client"
```

---

### Task 3: Docs and full suite

**Files:**
- Modify: `config.example.toml` (commented `# http_limit = 2` on each of `[planner]`, `[replyer]`, `[judge]`, after `max_attempts`)
- Modify: `README.md` (one sentence immediately after the `--concurrency` paragraph)
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: Task 1 already loads `http_limit`; example file currently has no such key (commented → still `None` when loaded)
- Produces: README and `config.example.toml` mention `http_limit`. `test_example_config_loads_with_env` still sees `cfg.planner.http_limit is None`.

- [ ] **Step 1: Write the failing docs assertions**

In `tests/test_docs.py`, inside `test_readme_covers_install_suites_gating_and_warnings` after the concurrency asserts:

```python
    assert "http_limit" in text
    assert "http_limit" in (ROOT / "config.example.toml").read_text(encoding="utf-8")
```

In `test_example_config_seats_env_smoke_and_temps`:

```python
    assert "# http_limit = 2" in text
```

In `test_example_config_loads_with_env`, after the temperature asserts:

```python
    assert cfg.planner.http_limit is None
    assert cfg.replyer.http_limit is None
    assert cfg.judge.http_limit is None
```

- [ ] **Step 2: Run docs tests to verify they fail**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_docs.py::test_readme_covers_install_suites_gating_and_warnings tests/test_docs.py::test_example_config_seats_env_smoke_and_temps tests/test_docs.py::test_example_config_loads_with_env -v`

Expected: FAIL on `http_limit` missing from README / example (the `is None` asserts PASS once Task 1 is in — they fail only if the example uncommented a live value).

- [ ] **Step 3: Docs**

In `config.example.toml`, after each seat’s `max_attempts = 5`:

```toml
# http_limit = 2
```

Three copies, one per `[planner]` / `[replyer]` / `[judge]`. Leave them commented so `load_config` still yields `None`.

In `README.md`, immediately after the `--concurrency` paragraph (after the `mai-bench-2 run --full --concurrency 8` sentence), add:

```markdown
Optional `http_limit` on `[planner]` / `[replyer]` / `[judge]` caps in-flight HTTP
on that seat (omitted means unlimited). Waiting for a slot is not a client timeout.
Example: `[judge] http_limit = 2` with `--concurrency 7`.
```

Do not add argparse flags.

- [ ] **Step 4: Run docs tests, then the full suite**

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest tests/test_docs.py -v`

Expected: PASS.

Run: `PYTHONPATH=src /mnt/klein/work/mai-bench-2/.venv/bin/python -m pytest -q`

Expected: PASS (count at least as high as before this plan; report the number).

- [ ] **Step 5: Commit**

```bash
git add config.example.toml README.md tests/test_docs.py
git commit -m "docs: document per-seat http_limit"
```

---

## Self-review (spec coverage)

| Spec requirement | Task |
|---|---|
| `EndpointConfig.http_limit: int \| None`; `_ENDPOINT_KEYS`; omitted `None`; present `max(1, int(N))` | 1 |
| `BoundedSemaphore`; cache hit skip; miss + `probe` wrap `_create_with_retries`; hold through retries | 2 |
| Unbounded `acquire`; `timeout_s` not used as acquire timeout | 2 (`test_http_limit_wait_is_not_a_timeout`) |
| Overlap 1 vs omitted overlap 2; cache hit skips gate | 2 |
| Commented example; README sentence; no CLI | 3 |
| Do not change `map_items` / scoring / cache keys | Global constraint (no suite tasks) |
