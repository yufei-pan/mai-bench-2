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
