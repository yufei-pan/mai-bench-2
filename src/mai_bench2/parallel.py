from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Callable, TypeVar

from mai_bench2.progress import item_span

T = TypeVar("T")


class Abandoned:
    """Marker for an in-flight item that was abandoned before it finished."""


class RunControl:
    def __init__(self) -> None:
        self.drain = threading.Event()
        self.abandon = threading.Event()

    def request_drain(self) -> None:
        self.drain.set()

    def request_abandon(self) -> None:
        self.drain.set()
        self.abandon.set()


def map_items(
    fn: Callable[[dict], T],
    items: list[dict],
    *,
    concurrency: int,
    progress=None,
    suite: str,
    control: RunControl | None = None,
    on_item=None,
) -> list:
    workers = max(1, int(concurrency))
    if workers == 1 or len(items) <= 1:
        return _serial(fn, items, progress, suite, control, on_item)
    return _pool(fn, items, workers, progress, suite, control, on_item)


def _stop_starting(control: RunControl | None) -> bool:
    return control is not None and (control.drain.is_set() or control.abandon.is_set())


def _abandoning(control: RunControl | None) -> bool:
    return control is not None and control.abandon.is_set()


def _notify(on_item, item: dict, result: object) -> None:
    if on_item is not None:
        on_item(item, result)


def _serial(fn, items, progress, suite, control, on_item) -> list:
    out: list = [None] * len(items)
    for index, item in enumerate(items):
        if _stop_starting(control):
            break
        with item_span(progress, suite, str(item.get("id") or "")):
            try:
                result = fn(item)
            except Exception as exc:
                result = exc
        out[index] = result
        _notify(on_item, item, result)
    return out


def _fill_slot(
    slots: list,
    items: list[dict],
    index: int,
    fut,
    progress,
    suite: str,
    on_item,
    *,
    pending_is_abandoned: bool,
) -> None:
    item = items[index]
    if pending_is_abandoned and (not fut.done() or fut.cancelled()):
        result: object = Abandoned()
    else:
        try:
            result = fut.result()
        except Exception as exc:
            result = exc
    slots[index] = result
    if progress is not None:
        progress.complete(suite, str(item.get("id") or ""))
    _notify(on_item, item, result)


def _pool(fn, items, workers: int, progress, suite, control, on_item) -> list:
    slots: list = [None] * len(items)
    next_index = 0
    in_flight: dict = {}
    pool = ThreadPoolExecutor(max_workers=workers)
    abandoned = False
    try:
        while True:
            while (
                len(in_flight) < workers
                and next_index < len(items)
                and not _stop_starting(control)
            ):
                fut = pool.submit(fn, items[next_index])
                in_flight[fut] = next_index
                next_index += 1

            if _abandoning(control):
                abandoned = True
                pool.shutdown(wait=False, cancel_futures=True)
                done, not_done = wait(list(in_flight), timeout=0)
                for fut in done:
                    index = in_flight.pop(fut)
                    _fill_slot(
                        slots,
                        items,
                        index,
                        fut,
                        progress,
                        suite,
                        on_item,
                        pending_is_abandoned=False,
                    )
                for fut in not_done:
                    index = in_flight.pop(fut)
                    _fill_slot(
                        slots,
                        items,
                        index,
                        fut,
                        progress,
                        suite,
                        on_item,
                        pending_is_abandoned=True,
                    )
                break

            if not in_flight:
                break

            done, _ = wait(
                in_flight.keys(),
                return_when=FIRST_COMPLETED,
                timeout=0.05,
            )
            for fut in done:
                index = in_flight.pop(fut)
                _fill_slot(
                    slots,
                    items,
                    index,
                    fut,
                    progress,
                    suite,
                    on_item,
                    pending_is_abandoned=False,
                )
    finally:
        if not abandoned:
            pool.shutdown(wait=True)
    return slots
