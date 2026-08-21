"""Progress bar: one tick per gold item, silent when stderr is not a TTY."""

from contextlib import contextmanager
from io import StringIO
from pathlib import Path

import pytest

from conftest import ROOT
from mai_bench2.config import AppConfig, EndpointConfig, RunConfig, SuiteConfig
from mai_bench2.persona import load_persona
from mai_bench2.progress import item_description, make_progress, planned_total
from mai_bench2.suites.e2e import run_e2e_suite
from mai_bench2.suites.planner import run_planner_suite
from mai_bench2.suites.replyer import run_replyer_suite
from mai_bench2.types import ChatResult, TokenCounts


_PLANNER = EndpointConfig("http://p/v1", "k", "m")
_REPLYER = EndpointConfig("http://r/v1", "k", "m")
_JUDGE = EndpointConfig("http://j/v1", "k", "m")


def _cfg(**kwargs):
    run = kwargs.get("run", RunConfig())
    return AppConfig(
        kwargs.get("planner", None),
        kwargs.get("replyer", None),
        kwargs.get("judge", None),
        run,
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        "x",
        suite_flag=kwargs.get("suite_flag", None),
    )


class RecordingProgress:
    def __init__(self):
        self.ticks: list[tuple[str, str]] = []
        self.samples: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_sample(self, sample: int) -> None:
        self.samples.append(sample)

    @contextmanager
    def item(self, suite: str, item_id: str):
        try:
            yield
        finally:
            self.ticks.append((suite, item_id))

    def complete(self, suite: str, item_id: str) -> None:
        with self.item(suite, item_id):
            pass


class BoomClient:
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        raise RuntimeError("network down")


class SilentClient:
    def chat(self, messages, *, max_tokens=None, temperature=None, tools=None):
        return ChatResult("", TokenCounts(), False, True, [])


def test_item_description_is_suite_space_id():
    # Break: dropping the item id, or joining with a slash.
    assert item_description("planner", "p-amb-002", sample=1, repeats=1) == "planner p-amb-002"


def test_item_description_adds_pass_index_when_repeated():
    assert (
        item_description("planner", "p-amb-002", sample=2, repeats=3)
        == "planner p-amb-002  2/3"
    )


def test_planned_total_smoke_planner_is_eight():
    # Break: counting full gold (124) or ignoring smoke_n.
    total = planned_total(_cfg(planner=_PLANNER, run=RunConfig(smoke=True)), ["planner"], ROOT)
    assert total == 8


def test_planned_total_full_planner_is_124():
    total = planned_total(
        _cfg(planner=_PLANNER, run=RunConfig(smoke=False)), ["planner"], ROOT
    )
    assert total == 124


def test_planned_total_multiplies_repeats():
    total = planned_total(
        _cfg(planner=_PLANNER, run=RunConfig(smoke=True, repeats=3)),
        ["planner"],
        ROOT,
    )
    assert total == 24


def test_planned_total_three_smoke_suites():
    total = planned_total(
        _cfg(planner=_PLANNER, replyer=_REPLYER, judge=_JUDGE, run=RunConfig(smoke=True)),
        ["planner", "replyer", "e2e"],
        ROOT,
    )
    assert total == 20


def test_planned_total_skip_drops_a_suite():
    total = planned_total(
        _cfg(planner=_PLANNER, replyer=_REPLYER, judge=_JUDGE, run=RunConfig(smoke=True)),
        ["planner", "replyer", "e2e"],
        ROOT,
        skip=("replyer", "e2e"),
    )
    assert total == 8


def test_planned_total_invalid_gold_counts_zero(tmp_path: Path):
    gold = tmp_path / "data" / "gold"
    gold.mkdir(parents=True)
    (gold / "planner.jsonl").write_text("{not json}\n", encoding="utf-8")
    total = planned_total(_cfg(planner=_PLANNER), ["planner"], tmp_path)
    assert total == 0


def test_make_progress_writes_nothing_when_not_a_tty():
    # Break: writing a bar (or any text) to a piped/captured stream.
    stream = StringIO()
    progress = make_progress(8, stream=stream, repeats=1)
    with progress:
        progress.set_sample(1)
        with progress.item("planner", "p-amb-002"):
            pass
    assert stream.getvalue() == ""


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


def test_planner_ticks_every_item_when_all_calls_fail():
    # Break: wrapping only the success path, so failures freeze the bar.
    progress = RecordingProgress()
    run_planner_suite(
        _cfg(planner=_PLANNER),
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 8
    assert {suite for suite, _ in progress.ticks} == {"planner"}
    assert all(item_id for _, item_id in progress.ticks)


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


def test_replyer_ticks_every_item_when_all_calls_fail():
    progress = RecordingProgress()
    run_replyer_suite(
        _cfg(replyer=_REPLYER, judge=_JUDGE),
        BoomClient(),
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 8
    assert {suite for suite, _ in progress.ticks} == {"replyer"}


def test_e2e_ticks_every_item_when_all_calls_fail():
    progress = RecordingProgress()
    run_e2e_suite(
        _cfg(planner=_PLANNER, replyer=_REPLYER, judge=_JUDGE),
        BoomClient(),
        BoomClient(),
        BoomClient(),
        load_persona("official", root=ROOT),
        root=ROOT,
        progress=progress,
    )
    assert len(progress.ticks) == 4
    assert {suite for suite, _ in progress.ticks} == {"e2e"}


def test_run_suites_ticks_each_planner_smoke_item():
    from mai_bench2.cli import run_suites

    progress = RecordingProgress()
    run_suites(
        _cfg(planner=_PLANNER, suite_flag="planner"),
        root=ROOT,
        clients={"planner": SilentClient()},
        progress=progress,
    )
    assert len(progress.ticks) == 8
    assert {suite for suite, _ in progress.ticks} == {"planner"}
    assert progress.samples == [1]


def test_run_suites_repeats_tick_each_item_twice():
    from mai_bench2.cli import run_suites

    progress = RecordingProgress()
    run_suites(
        _cfg(planner=_PLANNER, suite_flag="planner", run=RunConfig(smoke=True, repeats=2)),
        root=ROOT,
        clients={"planner": SilentClient()},
        progress=progress,
    )
    assert len(progress.ticks) == 16
    assert progress.samples == [1, 2]


def test_run_suites_make_progress_total_excludes_unseated_suites(
    monkeypatch: pytest.MonkeyPatch,
):
    from mai_bench2.cli import run_suites
    from mai_bench2.types import SuiteResult, UsageSplit

    seen: dict[str, int] = {}

    def fake_make(total, **kwargs):
        seen["total"] = total
        return RecordingProgress()

    monkeypatch.setattr("mai_bench2.cli.make_progress", fake_make)
    monkeypatch.setattr(
        "mai_bench2.cli.run_planner_suite",
        lambda *a, **k: SuiteResult("planner", "ok", {}, 1.0, UsageSplit(), 0.0, 1),
    )
    run_suites(_cfg(planner=_PLANNER), root=ROOT, clients={"planner": SilentClient()})
    # planner smoke 8; replyer/e2e are enabled but have no seats
    assert seen["total"] == 8


def test_run_suites_probe_failure_does_not_tick():
    from mai_bench2.cli import run_suites

    class Dead:
        def probe(self, messages, *, max_tokens=1):
            raise RuntimeError("down")

    progress = RecordingProgress()
    results, code = run_suites(
        _cfg(planner=_PLANNER, suite_flag="planner"),
        root=ROOT,
        clients={"planner": Dead()},
        progress=progress,
    )
    assert code == 1
    assert results[0].status == "error"
    assert progress.ticks == []


def test_run_suites_probe_failure_asks_for_zero_total(monkeypatch: pytest.MonkeyPatch):
    from mai_bench2.cli import run_suites

    class Dead:
        def probe(self, messages, *, max_tokens=1):
            raise RuntimeError("down")

    seen: dict[str, int] = {}

    def fake_make(total, **kwargs):
        seen["total"] = total
        return RecordingProgress()

    monkeypatch.setattr("mai_bench2.cli.make_progress", fake_make)
    run_suites(
        _cfg(planner=_PLANNER, suite_flag="planner"),
        root=ROOT,
        clients={"planner": Dead()},
    )
    assert seen["total"] == 0
