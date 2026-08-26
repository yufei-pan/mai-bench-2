import threading

from mai_bench2.parallel import Abandoned, RunControl, map_items


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


def test_abandon_returns_without_waiting_for_in_flight():
    import time

    hold = threading.Event()
    started = threading.Barrier(3)
    control = RunControl()

    def fn(item):
        started.wait(timeout=2)
        hold.wait(timeout=5)
        return item["id"]

    def killer():
        started.wait(timeout=2)
        control.request_abandon()

    threading.Thread(target=killer, daemon=True).start()
    t0 = time.monotonic()
    out = map_items(
        fn,
        [{"id": "a"}, {"id": "b"}],
        concurrency=2,
        progress=None,
        suite="planner",
        control=control,
    )
    elapsed = time.monotonic() - t0
    hold.set()
    assert elapsed < 1.0
    assert all(isinstance(slot, Abandoned) or slot in {"a", "b"} for slot in out)


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
