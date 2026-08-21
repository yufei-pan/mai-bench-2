# Terminal Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the numeric table, always print a short Chinese terminal report (meanings + worst items in MaiBot terms) built from a Python digest; optionally polish that digest with the judge LLM.

**Architecture:** Split today’s `narrative.py` blob (dump every prediction + scoring lecture → four-section Markdown) into `digest.py` (deterministic facts) and a gloss-only `narrative.py`. CLI always prints `format_digest(digest)` unless a valid gloss returns. Artifacts: `digest.json` plus `narrative.md` equal to the printed body.

**Tech Stack:** Existing package: Python ≥ 3.11, pytest, `mai_bench2`. No new dependencies. Judge HTTP client unchanged.

**Spec:** `docs/superpowers/specs/2026-08-20-terminal-report-design.md`

## Global Constraints

- Language of the report: Chinese. Two blocks: `含义` then `最差样本`. No `##` headers.
- Length target: 15–25 lines after the table; wrap near 88 columns.
- Digest is the source of truth. The judge LLM formats it; it must not see raw traces, `assistant_text` dumps, or the six-clause scoring lecture.
- Always print a report. No `[judge]` → template, no skip line. Gloss failure → `narrative skipped: …` then the template. Exit code unchanged.
- `narrative.py` / `narrative.md` names stay. Add `digest.json`.
- Do not call `mai_bench2.judge.stricter_retry` from the reporter (that prefix demands JSON).
- Do not change headline formulas, suite scoring, `judge.py` item rubrics, or table columns.
- Do not add a `[narrator]` seat.
- `build_digest(results, headlines, *, smoke: bool) -> dict`. `generate_narrative(client, digest) -> NarrativeResult`.
- Commits: do not run `git config`. Do not `--no-verify`. Do not push.
- Run only the tests named in the task until that task’s end; full `.venv/bin/python -m pytest` in Task 6.

## File map

| Path | Responsibility |
|---|---|
| `src/mai_bench2/digest.py` | `build_digest`, `format_digest`, canned meanings/tags |
| `src/mai_bench2/narrative.py` | Gloss prompt + report retry from digest only |
| `src/mai_bench2/cli.py` | Always digest; optional gloss; fallback print |
| `src/mai_bench2/report.py` | Write `digest.json` |
| `src/mai_bench2/suites/planner.py` | Stash `accepted` on prediction extra |
| `src/mai_bench2/suites/e2e.py` | Stash `accepted` on prediction extra |
| `tests/test_digest.py` | Digest + template tests |
| `tests/test_narrative.py` | Gloss prompt/retry tests |
| `tests/test_cli_run.py`, `tests/test_report.py`, `tests/test_docs.py`, suite tests | Wiring, artifacts, README |
| `README.md` | Report paragraph |

---

### Task 1: Digest meaning lines

**Files:**
- Create: `src/mai_bench2/digest.py`
- Create: `tests/test_digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `SuiteResult`, `HeadlineOutcome` from `mai_bench2.types`; `result.native`, `result.n_items`, `result.name`, `result.status`
- Produces: `build_digest(results, headlines, *, smoke: bool) -> dict` with keys `smoke`, `headline_reasons`, `meanings` (list[str], max 8), `suites` (list[dict]), `worst` (always `[]` in this task)

Canned meaning strings (pin these in tests and implementation):

- Smoke: `这是 smoke（planner {n} / replyer {n} / e2e {n}），不能当正式 headline。` Include only suites that ran, in planner/replyer/e2e order, joined with ` / `.
- wait_band == 0 only: `wait_band=0：该等待的样本没有原生 wait（或总等待时长未落入金标区间），真实麦麦不会为后续消息停住。`
- action < 1: `action={V}：{N} 条里约 {K} 条首次动作正确。` K = `round(V * N)`. Prefer the planner suite’s native/n_items; if planner has no `action` key, use e2e.
- e2e gap: if both `replyer_v1` and `joint` exist on the e2e suite and `replyer_v1 - joint >= 20`: `joint 远低于 replyer_v1：端到端损失在规划门控，不在文案。`
- If a replyer suite ran: `回复器分数评价的是已经决定回复之后的文案，不说明规划器该不该说话。`
- contract_fail == 0: `contract_fail=0：没有空正文 / 畸形工具 / reply 缺 msg_id。正文里的 JSON 不是契约失败。`
- contract_fail > 0: `contract_fail={N}：{N} 条契约失败；真实麦麦不会执行这些动作。` N = `int(value)`. Prefer planner, else e2e.
- tool_f1 / tool_hit / briefing if key exists and value < 1 (planner then e2e, no duplicates): `tool_f1={V}：信息工具名与金标不匹配。` / `tool_hit={V}：信息工具没有取回夹具。` / `briefing={V}：reply 简报缺少金标事实。`
- If any suite `status == "ok"` or native `failed_items == 0`: `status=ok / failed_items=0 只表示评测跑完，不是行为全对。`
- Replyer dims `in_character`, `style`, `grounding`, `group_chat`, `no_planner_voice` if present and ≠ 10, last: `{dim}={V}：已决定回复之后的文案分项。`

Emit in that order. Slice to 8. Format floats like the table: integer if `.is_integer()`, else `f"{value:.4f}".rstrip("0").rstrip(".")`.

Do not emit wait_band when `0 < wait_band < 1`. Do not emit action when value >= 1. `worst` is `[]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_digest.py`:

```python
from mai_bench2.digest import build_digest
from mai_bench2.types import HeadlineOutcome, SuiteResult, UsageSplit


def _suite(name, *, native, n=8, subscore=50.0, status="ok"):
    return SuiteResult(name, status, native, subscore, UsageSplit(), 1.0, n)


def test_smoke_meaning_lists_ran_suites_only():
    digest = build_digest(
        [
            _suite("planner", native={"action": 1.0, "contract_fail": 0.0}, n=8),
            _suite("e2e", native={"action": 1.0, "joint": 50.0, "replyer_v1": 90.0}, n=4),
        ],
        HeadlineOutcome({}, ["smoke"]),
        smoke=True,
    )
    assert digest["smoke"] is True
    assert digest["headline_reasons"] == ["smoke"]
    assert digest["worst"] == []
    assert digest["meanings"][0] == "这是 smoke（planner 8 / e2e 4），不能当正式 headline。"
    names = [row["name"] for row in digest["suites"]]
    assert names == ["planner", "e2e"]
    assert digest["suites"][0]["native"]["action"] == 1.0
    assert "assistant_text" not in str(digest)


def test_wait_band_zero_and_contract_fail_zero():
    digest = build_digest(
        [_suite("planner", native={"action": 1.0, "wait_band": 0.0, "contract_fail": 0.0}, n=8)],
        HeadlineOutcome({}, ["smoke"]),
        smoke=False,
    )
    assert "wait_band=0：该等待的样本没有原生 wait（或总等待时长未落入金标区间），真实麦麦不会为后续消息停住。" in digest["meanings"]
    assert "contract_fail=0：没有空正文 / 畸形工具 / reply 缺 msg_id。正文里的 JSON 不是契约失败。" in digest["meanings"]
    assert all("wait_band=0.5" not in line for line in digest["meanings"])


def test_wait_band_partial_is_omitted():
    digest = build_digest(
        [_suite("planner", native={"action": 1.0, "wait_band": 0.5, "contract_fail": 0.0}, n=8)],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert not any(line.startswith("wait_band=") for line in digest["meanings"])


def test_action_line_uses_round_and_prefers_planner():
    digest = build_digest(
        [
            _suite("planner", native={"action": 0.625, "contract_fail": 0.0}, n=8, subscore=62.5),
            _suite("e2e", native={"action": 0.5, "joint": 50.0, "replyer_v1": 98.125}, n=4),
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert "action=0.625：8 条里约 5 条首次动作正确。" in digest["meanings"]
    assert "joint 远低于 replyer_v1：端到端损失在规划门控，不在文案。" in digest["meanings"]
    action_lines = [line for line in digest["meanings"] if line.startswith("action=")]
    assert len(action_lines) == 1


def test_contract_fail_count_and_tool_f1():
    digest = build_digest(
        [_suite("planner", native={"action": 1.0, "contract_fail": 2.0, "tool_f1": 0.0}, n=8)],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert "contract_fail=2：2 条契约失败；真实麦麦不会执行这些动作。" in digest["meanings"]
    assert "tool_f1=0：信息工具名与金标不匹配。" in digest["meanings"]


def test_replyer_post_gating_and_meaning_cap():
    digest = build_digest(
        [
            _suite(
                "planner",
                native={"action": 0.5, "wait_band": 0.0, "contract_fail": 0.0, "tool_f1": 0.0, "tool_hit": 0.0, "briefing": 0.0},
                n=8,
            ),
            _suite(
                "replyer",
                native={
                    "in_character": 8.625,
                    "style": 10.0,
                    "grounding": 9.875,
                    "group_chat": 9.875,
                    "no_planner_voice": 10.0,
                    "failed_items": 0,
                },
                n=8,
                subscore=95.9,
            ),
            _suite("e2e", native={"action": 0.5, "joint": 50.0, "replyer_v1": 98.0, "wait_band": 0.0}, n=4),
        ],
        HeadlineOutcome({}, ["smoke"]),
        smoke=True,
    )
    assert len(digest["meanings"]) <= 8
    assert "回复器分数评价的是已经决定回复之后的文案，不说明规划器该不该说话。" in digest["meanings"]
    assert "status=ok / failed_items=0 只表示评测跑完，不是行为全对。" in digest["meanings"] or len(digest["meanings"]) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_digest.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'mai_bench2.digest'` (or `build_digest` import error).

- [ ] **Step 3: Write minimal implementation**

Create `src/mai_bench2/digest.py`:

```python
from __future__ import annotations

from mai_bench2.types import SuiteResult

_MAX_MEANINGS = 8
_SUITE_ORDER = ("planner", "replyer", "e2e")
_REPLYER_DIMS = ("in_character", "style", "grounding", "group_chat", "no_planner_voice")


def build_digest(results, headlines, *, smoke: bool) -> dict:
    rows = list(results or [])
    reasons = list(getattr(headlines, "reasons", None) or [])
    return {
        "smoke": bool(smoke),
        "headline_reasons": reasons,
        "meanings": _meaning_lines(rows, smoke=bool(smoke)),
        "suites": [_suite_entry(result) for result in rows],
        "worst": [],
    }


def _suite_entry(result: SuiteResult) -> dict:
    return {
        "name": result.name,
        "status": result.status,
        "n_items": result.n_items,
        "subscore": result.subscore,
        "native": dict(result.native or {}),
    }


def _fmt(value) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _by_name(results: list[SuiteResult]) -> dict[str, SuiteResult]:
    return {result.name: result for result in results}


def _native_from(results: list[SuiteResult], names: tuple[str, ...], key: str):
    lookup = _by_name(results)
    for name in names:
        result = lookup.get(name)
        if result is None:
            continue
        native = result.native or {}
        if key in native:
            return result, native[key]
    return None, None


def _meaning_lines(results: list[SuiteResult], *, smoke: bool) -> list[str]:
    lines: list[str] = []
    lookup = _by_name(results)

    if smoke:
        parts = []
        for name in _SUITE_ORDER:
            result = lookup.get(name)
            if result is not None:
                parts.append(f"{name} {result.n_items}")
        lines.append(f"这是 smoke（{' / '.join(parts)}），不能当正式 headline。")

    _, wait_band = _native_from(results, ("planner", "e2e"), "wait_band")
    if wait_band is not None and float(wait_band) == 0.0:
        lines.append(
            "wait_band=0：该等待的样本没有原生 wait（或总等待时长未落入金标区间），真实麦麦不会为后续消息停住。"
        )

    action_suite, action = _native_from(results, ("planner", "e2e"), "action")
    if action_suite is not None and float(action) < 1.0:
        n = action_suite.n_items
        k = round(float(action) * n)
        lines.append(f"action={_fmt(action)}：{n} 条里约 {k} 条首次动作正确。")

    e2e = lookup.get("e2e")
    if e2e is not None:
        native = e2e.native or {}
        if "replyer_v1" in native and "joint" in native:
            if float(native["replyer_v1"]) - float(native["joint"]) >= 20:
                lines.append("joint 远低于 replyer_v1：端到端损失在规划门控，不在文案。")

    if "replyer" in lookup:
        lines.append("回复器分数评价的是已经决定回复之后的文案，不说明规划器该不该说话。")

    fail_suite, fail = _native_from(results, ("planner", "e2e"), "contract_fail")
    if fail_suite is not None:
        count = int(float(fail))
        if count == 0:
            lines.append(
                "contract_fail=0：没有空正文 / 畸形工具 / reply 缺 msg_id。正文里的 JSON 不是契约失败。"
            )
        else:
            lines.append(f"contract_fail={count}：{count} 条契约失败；真实麦麦不会执行这些动作。")

    for key, text in (
        ("tool_f1", "信息工具名与金标不匹配。"),
        ("tool_hit", "信息工具没有取回夹具。"),
        ("briefing", "reply 简报缺少金标事实。"),
    ):
        _, value = _native_from(results, ("planner", "e2e"), key)
        if value is not None and float(value) < 1.0:
            lines.append(f"{key}={_fmt(value)}：{text}")

    if any(
        result.status == "ok" or float((result.native or {}).get("failed_items", 1) or 1) == 0
        for result in results
    ):
        lines.append("status=ok / failed_items=0 只表示评测跑完，不是行为全对。")

    replyer = lookup.get("replyer")
    if replyer is not None:
        native = replyer.native or {}
        for dim in _REPLYER_DIMS:
            if dim in native and float(native[dim]) != 10.0:
                lines.append(f"{dim}={_fmt(native[dim])}：已决定回复之后的文案分项。")

    return lines[:_MAX_MEANINGS]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_digest.py -v`

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/digest.py tests/test_digest.py
git commit -m "feat: build report digest meaning lines"
```

---

### Task 2: Worst-item tags and ranking

**Files:**
- Modify: `src/mai_bench2/digest.py`
- Modify: `tests/test_digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `build_digest` from Task 1; `Prediction.id/gold/pred/extra`
- Produces: `digest["worst"]` — list of at most 5 dicts with keys `suite`, `id`, `gold`, `pred`, `tag`, `meaning`, `tools_called`, `quote`. `pred` is the first committed action for planner/e2e (`pred` / `extra["planner_action"]`), not the visible reply string.

Tag canned `meaning` values:

| tag | meaning |
|---|---|
| `contract_fail` | `契约失败（空正文 / 畸形工具 / reply 缺 msg_id）。真实麦麦不会执行该动作。` |
| `json_in_text` | `把工具 JSON 写在正文里，没有原生 tool_calls。真实麦麦不会执行这些调用。` |
| `spoke_instead_of_wait` | `该等待却原生 reply。真实麦麦不会为后续消息停住。` |
| `spoke_instead_of_idle` | `本应保持沉默（none），规划器却原生 reply。真实群里麦麦会抢话。` |
| `idle_instead_of_reply` | `本应原生 reply，规划器却 none。真实麦麦不会说话。` |
| `waited_instead_of_reply` | `本应原生 reply，规划器却 wait。真实麦麦不会发言，只会停住。` |
| `waited_instead_of_idle` | `本应 none，规划器却 wait。真实麦麦会无谓等待。` |
| `low_in_character` | `回复人设贴合偏低。` |
| `low_style` | `回复风格偏低。` |
| `low_grounding` | `回复有缺依据的发挥。` |
| `low_group_chat` | `回复不太像群聊里的一句话。` |
| `planner_voice` | `回复混入了规划器/工具口吻。` |

Rules:

- Accept list: `extra.get("accepted")` if it is a non-empty list, else `[gold]`. First action in accepted → not an action miss.
- Planner first action = `pred.pred`. e2e first action = `extra.get("planner_action")`; if missing, skip action tags (json_in_text may still apply).
- First matching tag: `contract_fail`, `json_in_text`, then the spoke/idle/wait tags. `json_in_text` when `native_tool_call_count == 0` (key present) and assistant body looks like tool JSON: stripped text starts with `{`, or contains both `"name"` and `"arguments"`, or contains ` ```json ` (case-insensitive).
- Do not list wait↔none flavour misses (first action `wait` with `none` in accepted and `wait` in accepted, or the reverse).
- Replyer: pick lowest dim among `in_character/style/grounding/group_chat` that is ≤ 7; else `planner_voice` if `no_planner_voice < 10`.
- Rank: contract_fail 0, json_in_text 1, speak-when-silent 2, silent-when-should-speak 3, replyer 4. Cap 5.
- `tools_called` = `list(extra.get("tools_called") or [])`.
- `quote`: 80-char clip (`…` suffix if clipped). Planner action misses: `None`. `json_in_text`: clip of `assistant_text`. e2e when `planner_action == "reply"` and `Prediction.pred` is not in `{wait,reply,none,contract_fail}`: clip of that pred. Replyer: clip of `pred`.
- Never put an `assistant_text` **key** in the digest dict (quotes are `quote`). Missing extras must not raise.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_digest.py` (keep the Task 1 imports; add `Prediction`):

```python
from mai_bench2.types import HeadlineOutcome, Prediction, SuiteResult, UsageSplit


def _pred(id, gold, pred, extra=None):
    return Prediction(id, gold, pred, extra or {})


def test_worst_ranking_contract_fail_before_spoke_before_replyer():
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 0.0, "contract_fail": 1.0},
                0.0,
                UsageSplit(),
                1.0,
                3,
                predictions=[
                    _pred("p-wait-001", "wait", "reply", {"accepted": ["wait"], "tools_called": ["reply"]}),
                    _pred("p-fail-001", "reply", "contract_fail", {"accepted": ["reply"], "tools_called": []}),
                    _pred("p-ok-001", "reply", "reply", {"accepted": ["reply"], "tools_called": ["reply"]}),
                ],
            ),
            SuiteResult(
                "replyer",
                "ok",
                {"in_character": 6.0, "style": 10.0, "grounding": 9.0, "group_chat": 9.0, "no_planner_voice": 10.0},
                80.0,
                UsageSplit(),
                1.0,
                1,
                predictions=[
                    _pred("r-low-001", "reply", "嗯", {"in_character": 6, "style": 10, "grounding": 9, "group_chat": 9, "no_planner_voice": 10}),
                ],
            ),
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    tags = [row["tag"] for row in digest["worst"]]
    assert tags[:3] == ["contract_fail", "spoke_instead_of_wait", "low_in_character"]
    assert digest["worst"][0]["id"] == "p-fail-001"
    assert digest["worst"][1]["meaning"] == "该等待却原生 reply。真实麦麦不会为后续消息停住。"
    assert digest["worst"][2]["quote"] == "嗯"


def test_accepted_list_is_not_an_action_miss():
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 1.0, "contract_fail": 0.0},
                100.0,
                UsageSplit(),
                1.0,
                1,
                predictions=[
                    _pred("p-acc-001", "reply", "none", {"accepted": ["reply", "none"], "tools_called": []}),
                ],
            )
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert digest["worst"] == []


def test_json_in_text_and_no_assistant_text_key():
    blob = '{"name": "wait", "arguments": {"duration": 8}}'
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 0.0, "contract_fail": 0.0},
                0.0,
                UsageSplit(),
                1.0,
                1,
                predictions=[
                    _pred(
                        "p-json-001",
                        "wait",
                        "none",
                        {
                            "accepted": ["wait"],
                            "tools_called": [],
                            "native_tool_call_count": 0,
                            "assistant_text": blob,
                        },
                    )
                ],
            )
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert digest["worst"][0]["tag"] == "json_in_text"
    assert digest["worst"][0]["quote"] == blob
    dumped = json.dumps(digest)
    assert "assistant_text" not in dumped


def test_e2e_uses_planner_action_not_visible_reply():
    digest = build_digest(
        [
            SuiteResult(
                "e2e",
                "ok",
                {"action": 0.0, "joint": 0.0, "replyer_v1": 90.0},
                10.0,
                UsageSplit(),
                1.0,
                1,
                predictions=[
                    _pred(
                        "e-wait-001",
                        "wait",
                        "好，你先忙。",
                        {
                            "accepted": ["wait"],
                            "planner_action": "reply",
                            "tools_called": ["reply"],
                            "native_tool_call_count": 1,
                        },
                    )
                ],
            )
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    row = digest["worst"][0]
    assert row["tag"] == "spoke_instead_of_wait"
    assert row["pred"] == "reply"
    assert row["quote"] == "好，你先忙。"


def test_worst_cap_five_and_skip_wait_none_flavour():
    preds = [
        _pred(f"p-miss-{i}", "reply", "none", {"accepted": ["reply"], "tools_called": []})
        for i in range(6)
    ]
    preds.append(_pred("p-flavour", "none", "wait", {"accepted": ["none", "wait"], "tools_called": ["wait"]}))
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 0.0, "contract_fail": 0.0},
                0.0,
                UsageSplit(),
                1.0,
                7,
                predictions=preds,
            )
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert len(digest["worst"]) == 5
    assert all(row["id"] != "p-flavour" for row in digest["worst"])
    assert all(row["tag"] == "idle_instead_of_reply" for row in digest["worst"])


def test_missing_extras_do_not_raise():
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 0.0},
                0.0,
                UsageSplit(),
                1.0,
                1,
                predictions=[_pred("p-bare", "reply", "none")],
            )
        ],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    assert digest["worst"][0]["id"] == "p-bare"
    assert digest["worst"][0]["tools_called"] == []
```

Add `import json` at the top of `tests/test_digest.py`.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_digest.py::test_worst_ranking_contract_fail_before_spoke_before_replyer tests/test_digest.py::test_accepted_list_is_not_an_action_miss tests/test_digest.py::test_json_in_text_and_no_assistant_text_key tests/test_digest.py::test_e2e_uses_planner_action_not_visible_reply tests/test_digest.py::test_worst_cap_five_and_skip_wait_none_flavour tests/test_digest.py::test_missing_extras_do_not_raise -v`

Expected: FAIL (`digest["worst"] == []` so `tags[:3]` raises or assertion fails).

- [ ] **Step 3: Write minimal implementation**

In `src/mai_bench2/digest.py`, add imports and helpers, and change `build_digest` so `"worst": _worst_items(rows)` instead of `"worst": []`.

```python
_MAX_WORST = 5
_QUOTE = 80
_ACTIONS = frozenset({"wait", "reply", "none", "contract_fail"})
_TAG_MEANING = {
    "contract_fail": "契约失败（空正文 / 畸形工具 / reply 缺 msg_id）。真实麦麦不会执行该动作。",
    "json_in_text": "把工具 JSON 写在正文里，没有原生 tool_calls。真实麦麦不会执行这些调用。",
    "spoke_instead_of_wait": "该等待却原生 reply。真实麦麦不会为后续消息停住。",
    "spoke_instead_of_idle": "本应保持沉默（none），规划器却原生 reply。真实群里麦麦会抢话。",
    "idle_instead_of_reply": "本应原生 reply，规划器却 none。真实麦麦不会说话。",
    "waited_instead_of_reply": "本应原生 reply，规划器却 wait。真实麦麦不会发言，只会停住。",
    "waited_instead_of_idle": "本应 none，规划器却 wait。真实麦麦会无谓等待。",
    "low_in_character": "回复人设贴合偏低。",
    "low_style": "回复风格偏低。",
    "low_grounding": "回复有缺依据的发挥。",
    "low_group_chat": "回复不太像群聊里的一句话。",
    "planner_voice": "回复混入了规划器/工具口吻。",
}
_TAG_RANK = {
    "contract_fail": 0,
    "json_in_text": 1,
    "spoke_instead_of_wait": 2,
    "spoke_instead_of_idle": 2,
    "idle_instead_of_reply": 3,
    "waited_instead_of_reply": 3,
    "waited_instead_of_idle": 3,
    "low_in_character": 4,
    "low_style": 4,
    "low_grounding": 4,
    "low_group_chat": 4,
    "planner_voice": 4,
}


def _clip(value, limit: int = _QUOTE) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _looks_like_tool_json(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    if body.startswith("{"):
        return True
    if '"name"' in body and '"arguments"' in body:
        return True
    if "```json" in body.lower():
        return True
    return False


def _accepted(pred) -> list[str]:
    extra = pred.extra or {}
    raw = extra.get("accepted")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw]
    gold = str(pred.gold or "")
    return [gold] if gold else []


def _first_action(suite_name: str, pred) -> str | None:
    extra = pred.extra or {}
    if suite_name == "e2e":
        action = extra.get("planner_action")
        return str(action) if action is not None else None
    if suite_name == "planner":
        return str(pred.pred or "")
    return None


def _action_tag(first: str, accepted: list[str]) -> str | None:
    if first in accepted:
        return None
    if first == "reply" and "wait" in accepted and "reply" not in accepted:
        return "spoke_instead_of_wait"
    if first == "reply" and "none" in accepted and "reply" not in accepted:
        return "spoke_instead_of_idle"
    if first == "none" and "reply" in accepted and "none" not in accepted:
        return "idle_instead_of_reply"
    if first == "wait" and "reply" in accepted and "wait" not in accepted:
        return "waited_instead_of_reply"
    if first == "wait" and "none" in accepted and "wait" not in accepted:
        return "waited_instead_of_idle"
    return None


def _replyer_tag(extra: dict) -> str | None:
    dims = {
        dim: extra[dim]
        for dim in ("in_character", "style", "grounding", "group_chat")
        if dim in extra
    }
    if dims:
        lowest = min(dims, key=lambda dim: float(dims[dim]))
        if float(dims[lowest]) <= 7:
            return f"low_{lowest}"
    if "no_planner_voice" in extra and float(extra["no_planner_voice"]) < 10:
        return "planner_voice"
    return None


def _tag_for(suite_name: str, pred) -> str | None:
    extra = pred.extra or {}
    if suite_name == "replyer":
        return _replyer_tag(extra)
    first = _first_action(suite_name, pred)
    if first == "contract_fail":
        return "contract_fail"
    count = extra.get("native_tool_call_count")
    if count == 0 and _looks_like_tool_json(str(extra.get("assistant_text") or "")):
        return "json_in_text"
    if first is None:
        return None
    return _action_tag(first, _accepted(pred))


def _quote_for(suite_name: str, pred, tag: str) -> str | None:
    extra = pred.extra or {}
    if tag == "json_in_text":
        return _clip(extra.get("assistant_text"))
    if suite_name == "replyer":
        return _clip(pred.pred)
    if suite_name == "e2e" and extra.get("planner_action") == "reply" and str(pred.pred) not in _ACTIONS:
        return _clip(pred.pred)
    return None


def _worst_entry(suite_name: str, pred, tag: str) -> dict:
    extra = pred.extra or {}
    first = _first_action(suite_name, pred)
    pred_field = first if suite_name in {"planner", "e2e"} and first is not None else pred.pred
    tools = extra.get("tools_called") or []
    return {
        "suite": suite_name,
        "id": pred.id,
        "gold": pred.gold,
        "pred": pred_field,
        "tag": tag,
        "meaning": _TAG_MEANING[tag],
        "tools_called": list(tools),
        "quote": _quote_for(suite_name, pred, tag),
    }


def _worst_items(results: list[SuiteResult]) -> list[dict]:
    found: list[dict] = []
    for result in results:
        for pred in result.predictions or []:
            tag = _tag_for(result.name, pred)
            if tag is None:
                continue
            found.append(_worst_entry(result.name, pred, tag))
    found.sort(key=lambda row: (_TAG_RANK.get(row["tag"], 9), row["suite"], row["id"]))
    return found[:_MAX_WORST]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_digest.py -v`

Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/digest.py tests/test_digest.py
git commit -m "feat: rank worst items in the report digest"
```

---

### Task 3: `format_digest` terminal template

**Files:**
- Modify: `src/mai_bench2/digest.py`
- Modify: `tests/test_digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: digest dict from `build_digest`
- Produces: `format_digest(digest: dict) -> str` ending in `\n`. Labels `含义` / `最差样本`. No `##`. Wrap bullets at 88 columns (`textwrap.fill`, subsequent indent two spaces). Empty worst: a single line `最差样本：没有需要点名的失败项。`

Planner/e2e action lines: `- {id}  {gold}→{pred}  {meaning}`  
Replyer (or e2e with a quote): `- {id}  {tag}  {meaning}` and if quote: append `  {quote}` on the same wrapped bullet.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_digest.py`:

```python
from mai_bench2.digest import build_digest, format_digest


def test_format_digest_two_blocks_no_markdown_headers():
    digest = build_digest(
        [
            SuiteResult(
                "planner",
                "ok",
                {"action": 0.625, "wait_band": 0.0, "contract_fail": 0.0},
                62.5,
                UsageSplit(),
                1.0,
                8,
                predictions=[
                    _pred("p-amb-002", "none", "reply", {"accepted": ["none"], "tools_called": ["reply"]}),
                ],
            )
        ],
        HeadlineOutcome({}, ["smoke"]),
        smoke=True,
    )
    text = format_digest(digest)
    assert text.endswith("\n")
    assert "##" not in text
    assert "含义" in text.splitlines()[0]
    assert "最差样本" in text
    assert "- p-amb-002  none→reply  本应保持沉默（none），规划器却原生 reply。真实群里麦麦会抢话。" in text
    assert text.count("\n") <= 25
    assert all(len(line) <= 88 or line.startswith("  ") for line in text.splitlines())


def test_format_digest_empty_worst():
    digest = build_digest(
        [_suite("planner", native={"action": 1.0, "contract_fail": 0.0}, n=3)],
        HeadlineOutcome({}, []),
        smoke=False,
    )
    text = format_digest(digest)
    assert "最差样本：没有需要点名的失败项。" in text


def test_format_digest_replyer_uses_tag_not_full_pred():
    digest = {
        "smoke": False,
        "headline_reasons": [],
        "meanings": ["回复器分数评价的是已经决定回复之后的文案，不说明规划器该不该说话。"],
        "suites": [],
        "worst": [
            {
                "suite": "replyer",
                "id": "r-low-001",
                "gold": "reply",
                "pred": "一段很长的回复" * 10,
                "tag": "low_in_character",
                "meaning": "回复人设贴合偏低。",
                "tools_called": [],
                "quote": "一段很长的回复",
            }
        ],
    }
    text = format_digest(digest)
    assert "- r-low-001  low_in_character  回复人设贴合偏低。  一段很长的回复" in text
    assert "一段很长的回复" * 10 not in text
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_digest.py::test_format_digest_two_blocks_no_markdown_headers tests/test_digest.py::test_format_digest_empty_worst tests/test_digest.py::test_format_digest_replyer_uses_tag_not_full_pred -v`

Expected: FAIL with `ImportError: cannot import name 'format_digest'` (or `AttributeError`).

- [ ] **Step 3: Write minimal implementation**

Add to `src/mai_bench2/digest.py`:

```python
import textwrap

_WRAP = 88


def format_digest(digest: dict) -> str:
    lines = ["含义"]
    for meaning in digest.get("meanings") or []:
        lines.append(_wrap_bullet(f"- {meaning}"))
    worst = list(digest.get("worst") or [])
    if not worst:
        lines.append("最差样本：没有需要点名的失败项。")
    else:
        lines.append("最差样本")
        for row in worst:
            lines.append(_wrap_bullet(_worst_line(row)))
    return "\n".join(lines) + "\n"


def _wrap_bullet(line: str) -> str:
    return textwrap.fill(line, width=_WRAP, subsequent_indent="  ")


def _worst_line(row: dict) -> str:
    tag = row.get("tag") or ""
    if tag.startswith("low_") or tag == "planner_voice" or row.get("suite") == "replyer":
        line = f"- {row.get('id')}  {tag}  {row.get('meaning')}"
    else:
        line = f"- {row.get('id')}  {row.get('gold')}→{row.get('pred')}  {row.get('meaning')}"
    if row.get("quote"):
        line += f"  {row['quote']}"
    return line
```

Planner/e2e action tags use `gold→pred` (`pred` is the first action). Replyer uses `{tag}`. Any row may append `quote` (json_in_text clip, e2e visible reply, replyer text).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_digest.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/digest.py tests/test_digest.py
git commit -m "feat: format digest as a short terminal report"
```

---

### Task 4: Stash `accepted` on planner and e2e extras

**Files:**
- Modify: `src/mai_bench2/suites/planner.py` (the `Prediction(... extra={...})` block around the `accepted` field — currently extra starts at `final_action` / `stop_reason` / `tools_called`)
- Modify: `src/mai_bench2/suites/e2e.py` (the `extra: dict = { "planner_action": ...}` block)
- Modify: `tests/test_suite_planner.py` (`test_planner_predictions_include_assistant_text`)
- Modify: `tests/test_suite_e2e.py` (`test_e2e_predictions_include_assistant_text`)
- Test: those two tests

**Interfaces:**
- Consumes: `accepted_actions(gold: dict) -> list[str]` from `mai_bench2.metrics` (already imported in `e2e.py`; add the import in `planner.py`)
- Produces: `prediction.extra["accepted"]` is that list. Scoring formulas unchanged.

- [ ] **Step 1: Write the failing assertions**

In `tests/test_suite_planner.py`, inside `test_planner_predictions_include_assistant_text` after the existing extra asserts:

```python
    assert extra["native_tool_call_count"] == 0
    assert extra["tools_called"] == []
    assert "wait" in extra["assistant_text"]
    assert result.predictions[0].gold in extra["accepted"]
    assert isinstance(extra["accepted"], list)
```

In `tests/test_suite_e2e.py`, inside `test_e2e_predictions_include_assistant_text`:

```python
    assert extra["planner_action"] == "none"
    assert extra["native_tool_call_count"] == 0
    assert "reply" in extra["assistant_text"]
    assert result.predictions[0].gold in extra["accepted"]
    assert isinstance(extra["accepted"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_suite_planner.py::test_planner_predictions_include_assistant_text tests/test_suite_e2e.py::test_e2e_predictions_include_assistant_text -v`

Expected: FAIL `KeyError: 'accepted'` (or assertion).

- [ ] **Step 3: Write minimal implementation**

`src/mai_bench2/suites/planner.py` — add `accepted_actions` to the metrics import:

```python
from mai_bench2.metrics import accepted_actions, planner_native, planner_v1
```

In the `extra={...}` dict, add `"accepted": accepted_actions(gold),` next to the other keys.

`src/mai_bench2/suites/e2e.py` — `accepted_actions` is already imported. In the `extra: dict = { ... }` literal, add `"accepted": accepted_actions(gold),`.

Do not change scoring.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_suite_planner.py::test_planner_predictions_include_assistant_text tests/test_suite_e2e.py::test_e2e_predictions_include_assistant_text tests/test_digest.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/suites/planner.py src/mai_bench2/suites/e2e.py tests/test_suite_planner.py tests/test_suite_e2e.py
git commit -m "feat: stash accepted actions on planner extras"
```

---

### Task 5: Gloss-only `narrative.py`

**Files:**
- Replace: `src/mai_bench2/narrative.py`
- Replace: `tests/test_narrative.py`
- Test: `tests/test_narrative.py`

**Interfaces:**
- Consumes: `generate_narrative(client, digest) -> NarrativeResult` with fields `text`, `skip_reason`, `error_message` (keep the dataclass).
- Produces: one user message containing the digest JSON (`json.dumps(digest, ensure_ascii=False)`) and the style prompt below. Retry prefix is **not** `judge.stricter_retry`. `tools=None`.

Prompt body (exact; concatenate before the JSON):

```
你是评测报告的中文润色员，不是打分员。只根据下面的 JSON 写一份给终端看的短报告。
硬性限制：15 到 25 行；每行尽量不超过 88 个字符；不要 Markdown 标题（不要 ##）；不要输出 JSON；不要编造 JSON 里没有的分数、样本或工具；不要把表格数字再抄一遍。
结构：先写「含义」再用短破折号列表；然后写「最差样本」。JSON 里的 meaning 字段已经是标准说法，可以压缩，不要改事实。
证据：
```

Then the digest JSON.

Retry prefix (prepended to the first message content on attempt 1):

```
上一份不是短中文终端报告。重写：15–25 行，先含义后最差样本，不要 JSON，不要 ##。
```

Validity: nonempty after strip; does not start with `{`; nonempty line count ≤ 30. Retry once on invalid or on `chat` exception, then `error_message`. `client is None` → `skip_reason="no_judge"`. Do not raise.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_narrative.py` entirely with:

```python
from mai_bench2.narrative import generate_narrative
from mai_bench2.types import ChatResult, TokenCounts

_DIGEST = {
    "smoke": True,
    "headline_reasons": ["smoke"],
    "meanings": ["这是 smoke（planner 1），不能当正式 headline。"],
    "suites": [
        {"name": "planner", "status": "ok", "n_items": 1, "subscore": 13.3, "native": {"action": 0.0}}
    ],
    "worst": [
        {
            "suite": "planner",
            "id": "gold-001",
            "gold": "reply",
            "pred": "none",
            "tag": "idle_instead_of_reply",
            "meaning": "本应原生 reply，规划器却 none。真实麦麦不会说话。",
            "tools_called": [],
            "quote": None,
        }
    ],
}

_GLOSS = "含义\n- 规划器没有原生 tool_calls。\n\n最差样本\n- gold-001  reply→none  本应原生 reply。\n"


class ScriptClient:
    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self._texts:
            raise RuntimeError("no scripted replies")
        return ChatResult(self._texts.pop(0), TokenCounts(), False, True, [])


def test_generate_narrative_skips_without_judge_client():
    result = generate_narrative(None, _DIGEST)
    assert result.text is None
    assert result.skip_reason == "no_judge"
    assert result.error_message is None


def test_generate_narrative_returns_judge_text():
    client = ScriptClient([_GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 1
    assert client.calls[0]["tools"] is None


def test_generate_narrative_prompt_is_gloss_not_contract_lecture():
    client = ScriptClient([_GLOSS])
    generate_narrative(client, _DIGEST)
    blob = "\n".join(message["content"] for message in client.calls[0]["messages"])
    assert "润色员" in blob
    assert "15" in blob and "25" in blob
    assert "不要编造" in blob
    assert "含义" in blob
    assert "最差样本" in blob
    assert "gold-001" in blob
    assert "idle_instead_of_reply" in blob
    assert "view_forward_message" not in blob
    assert "不会被执行" not in blob
    assert "只输出一个 JSON 对象" not in blob


def test_generate_narrative_retries_empty_then_succeeds():
    client = ScriptClient(["", _GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 2
    second = client.calls[1]["messages"][0]["content"]
    assert second.startswith("上一份不是短中文终端报告")
    assert "只输出一个 JSON 对象" not in second


def test_generate_narrative_retries_json_looking_then_succeeds():
    client = ScriptClient(['{"in_character": 8}', _GLOSS])
    result = generate_narrative(client, _DIGEST)
    assert result.text == _GLOSS
    assert len(client.calls) == 2


def test_generate_narrative_retries_too_long_then_errors():
    long_text = "\n".join(f"行{i}" for i in range(31))
    client = ScriptClient([long_text, long_text])
    result = generate_narrative(client, _DIGEST)
    assert result.text is None
    assert result.error_message is not None
    assert len(client.calls) == 2


class BoomClient:
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        raise RuntimeError("network down")


def test_generate_narrative_chat_error_does_not_raise():
    result = generate_narrative(BoomClient(), _DIGEST)
    assert result.text is None
    assert "network down" in result.error_message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_narrative.py -v`

Expected: FAIL (`generate_narrative() got an unexpected keyword argument` or missing `润色员` in the old prompt). Do not keep the old contract-lecture tests.

- [ ] **Step 3: Write minimal implementation**

Replace `src/mai_bench2/narrative.py` with:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

_RETRY_PREFIX = (
    "上一份不是短中文终端报告。重写：15–25 行，先含义后最差样本，不要 JSON，不要 ##。\n"
)
_PROMPT = (
    "你是评测报告的中文润色员，不是打分员。只根据下面的 JSON 写一份给终端看的短报告。"
    "硬性限制：15 到 25 行；每行尽量不超过 88 个字符；不要 Markdown 标题（不要 ##）；"
    "不要输出 JSON；不要编造 JSON 里没有的分数、样本或工具；不要把表格数字再抄一遍。"
    "结构：先写「含义」再用短破折号列表；然后写「最差样本」。"
    "JSON 里的 meaning 字段已经是标准说法，可以压缩，不要改事实。\n"
    "证据：\n"
)


@dataclass
class NarrativeResult:
    text: str | None = None
    skip_reason: str | None = None
    error_message: str | None = None


def generate_narrative(client, digest) -> NarrativeResult:
    if client is None:
        return NarrativeResult(skip_reason="no_judge")
    messages = _gloss_messages(digest)
    last_error = None
    for attempt in range(2):
        payload = messages if attempt == 0 else _report_retry(messages)
        try:
            result = client.chat(payload, tools=None)
        except Exception as exc:
            last_error = str(exc) or type(exc).__name__
            continue
        text = result.text or ""
        if _valid_gloss(text):
            return NarrativeResult(text=result.text)
        last_error = "empty narrative" if not text.strip() else "invalid narrative"
    return NarrativeResult(error_message=last_error)


def _gloss_messages(digest) -> list[dict]:
    evidence = json.dumps(digest, ensure_ascii=False)
    return [{"role": "user", "content": _PROMPT + evidence}]


def _report_retry(messages: list[dict]) -> list[dict]:
    retried = [dict(message) for message in messages]
    retried[0]["content"] = _RETRY_PREFIX + str(retried[0].get("content") or "")
    return retried


def _valid_gloss(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("{"):
        return False
    lines = [line for line in stripped.splitlines() if line.strip()]
    return len(lines) <= 30
```

Do not import `stricter_retry`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_narrative.py tests/test_digest.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/narrative.py tests/test_narrative.py
git commit -m "feat: gloss the report digest instead of dumping traces"
```

---

### Task 6: CLI fallback, artifacts, README

**Files:**
- Modify: `src/mai_bench2/cli.py` (`console()` after `render_table`)
- Modify: `src/mai_bench2/report.py` (`write_artifacts`)
- Modify: `tests/test_cli_run.py` (`_FakeClient`, `test_console_run_writes_redacted_artifacts`, `test_console_prints_narrative_when_judge_configured`, `test_console_narrative_failure_keeps_exit_0`)
- Modify: `tests/test_report.py` (`test_write_artifacts_writes_narrative_md` plus a digest test)
- Modify: `README.md` (the paragraph starting `If \`[judge]\` is configured, a Chinese Markdown narrative`)
- Modify: `tests/test_docs.py` (`test_readme_covers_install_suites_gating_and_warnings`)
- Test: the files above

**Interfaces:**
- Consumes: `build_digest`, `format_digest` (Tasks 1–3); `generate_narrative(client, digest)` (Task 5); `write_artifacts(..., narrative=None, digest=None)`
- Produces: stdout = table + optional skip line + body. Body = valid gloss or `format_digest`. `digest.json` pretty JSON `ensure_ascii=False` `indent=2` `sort_keys=True` plus trailing newline. `narrative.md` = printed body.

CLI sequence inside the existing `try` after `headlines = compute_headlines(...)`:

```python
        table = render_table(
            results, headlines, persona=persona, smoke=cfg.run.smoke, prompts=prompts
        )
        digest = build_digest(results, headlines, smoke=cfg.run.smoke)
        body = format_digest(digest)
        skip_line = None
        judge = clients.get("judge")
        if judge is not None:
            narrative = generate_narrative(judge, digest)
            if narrative.text:
                text = narrative.text
                body = text if text.endswith("\n") else f"{text}\n"
            elif narrative.error_message:
                skip_line = f"narrative skipped: {narrative.error_message}"
        print(table, end="")
        if skip_line:
            print()
            print(skip_line)
        print()
        print(body, end="")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        out_dir = Path(cfg.run.output_dir).expanduser() / stamp
        write_artifacts(
            out_dir,
            cfg=cfg,
            persona=persona,
            prompts=prompts,
            results=results,
            headlines=headlines,
            table=table,
            narrative=body,
            digest=digest,
        )
```

`write_artifacts`: after writing `narrative.md` when `narrative` is set, if `digest is not None`:

```python
    if digest is not None:
        (out_dir / "digest.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
```

README: replace the paragraph at “If `[judge]` is configured, a Chinese Markdown narrative…” with:

```
After the table the harness always prints a short Chinese terminal report
built from a digest: what the numbers mean in real MaiBot, then the worst
items. If `[judge]` is configured, that model only polishes the digest into
15-25 lines; it does not re-score. If the gloss call fails, the templated
digest still prints and the run still exits 0. The printed body is
`narrative.md`; the structured object is `digest.json`. It is not a headline.
```

Leave the Scoring section’s idle / JSON-in-body wording in place.

- [ ] **Step 1: Write the failing tests**

In `tests/test_report.py`, add (same `_P` / `AppConfig` helpers already in that file):

```python
def test_write_artifacts_writes_digest_json(tmp_path: Path):
    cfg = AppConfig(
        EndpointConfig("http://p/v1", "SUPER_SECRET", "m"),
        None,
        None,
        RunConfig(),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        str(tmp_path / "c.toml"),
    )
    digest = {"smoke": True, "meanings": ["这是 smoke（planner 3），不能当正式 headline。"], "worst": []}
    write_artifacts(
        tmp_path,
        cfg=cfg,
        persona=_P(),
        results=[SuiteResult("planner", "ok", {"action": 1.0}, 50.0, UsageSplit(), 1.0, 3)],
        headlines=HeadlineOutcome({}, ["smoke"]),
        table="hello-table",
        narrative="含义\n- 这是 smoke。\n",
        digest=digest,
    )
    payload = json.loads((tmp_path / "digest.json").read_text(encoding="utf-8"))
    assert payload["smoke"] is True
    assert "SUPER_SECRET" not in (tmp_path / "digest.json").read_text(encoding="utf-8")
```

In `tests/test_cli_run.py`:

1. Change `_FakeClient.narrative_text` default to:

```python
    narrative_text = "含义\n- 规划器没有原生 tool_calls。\n\n最差样本：没有需要点名的失败项。\n"
```

2. In `test_console_run_writes_redacted_artifacts`, after the summary asserts, add:

```python
    assert "含义" in captured.out
    assert (runs[0] / "digest.json").is_file()
    assert (runs[0] / "narrative.md").is_file()
    assert "含义" in (runs[0] / "narrative.md").read_text(encoding="utf-8")
```

3. In `test_console_prints_narrative_when_judge_configured`, keep asserting `规划器没有原生 tool_calls` in stdout and `narrative.md`. Also assert the prompt the fake client received does not contain `view_forward_message` if you capture messages; not required if FakeClient does not record them.

4. Change `test_console_narrative_failure_keeps_exit_0` so that after `narrative skipped:` / `network down`:

```python
    assert "含义" in captured.out
    assert (runs[0] / "narrative.md").is_file()
    assert "含义" in (runs[0] / "narrative.md").read_text(encoding="utf-8")
```

Delete the assertion `assert not (runs[0] / "narrative.md").exists()`.

In `tests/test_docs.py` `test_readme_covers_install_suites_gating_and_warnings`, keep `narrative.md`, scoring JSON-in-body asserts (`including tool JSON written`, `into the assistant body`), and `writes tool JSON in text instead of native` not in text. Replace `assert "Tool JSON written in text is idle" in text` with:

```python
    assert "digest.json" in lower
    assert "15-25" in text
    assert "polishes the digest" in text
```

Keep `still \`none\`` coverage via the scoring paragraph (`including tool JSON written` + `into the assistant body`).

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_report.py::test_write_artifacts_writes_digest_json tests/test_cli_run.py::test_console_run_writes_redacted_artifacts tests/test_cli_run.py::test_console_narrative_failure_keeps_exit_0 tests/test_docs.py::test_readme_covers_install_suites_gating_and_warnings -v`

Expected: FAIL (`write_artifacts() got an unexpected keyword argument 'digest'` and/or missing `含义` / `digest.json` in README).

- [ ] **Step 3: Write minimal implementation**

`cli.py`: add `from mai_bench2.digest import build_digest, format_digest` and replace the current `generate_narrative(...)` / print / `write_artifacts` block with the sequence in this task’s Interfaces.

`report.py`: add `digest: dict | None = None` to `write_artifacts` and the `digest.json` write shown above.

`README.md`: replace the narrative paragraph as specified.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_digest.py tests/test_narrative.py tests/test_report.py tests/test_cli_run.py tests/test_docs.py tests/test_suite_planner.py tests/test_suite_e2e.py -v`

Then: `.venv/bin/python -m pytest`

Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/mai_bench2/cli.py src/mai_bench2/report.py tests/test_cli_run.py tests/test_report.py tests/test_docs.py README.md
git commit -m "feat: print digest report after the table"
```

---

## Self-review (plan vs spec)

| Spec section | Task |
|---|---|
| Digest unit + gloss unit + CLI flow | 1, 5, 6 |
| Meaning lines, max 8, wait_band==0 only, planner-preferred action, e2e gap ≥ 20 | 1 |
| Worst tags, accept-list, json_in_text, e2e `planner_action`, cap 5, no wait↔none flavour, no `assistant_text` key | 2 |
| Terminal layout, 88 col, empty-worst line | 3 |
| Stash `accepted` | 4 |
| Gloss prompt, no contract lecture, no `stricter_retry`, validity/retry | 5 |
| Always print; skip line + template; `digest.json`; `narrative.md` = body; README | 6 |
| Scoring/table/headlines/`judge.py` unchanged | Global + Task 4 note |
| Out of scope (English, narrator seat, full traces) | Global Constraints |
