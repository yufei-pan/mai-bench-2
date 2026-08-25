"""Checkpoint schema, atomic IO, and per-item result classification."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from mai_bench2.config import EndpointConfig

RETRYABLE = frozenset({"pending", "transport_fail", "abandoned"})

CHECKPOINT_VERSION = 1


class CheckpointError(Exception):
    pass


@dataclass
class SeatSnapshot:
    model: str
    reasoning_effort: str | None
    temperature: float | None
    assistant_prefill: bool
    extra_body: dict
    base_url: str


@dataclass
class ItemRecord:
    suite: str
    id: str
    sample: int
    status: str
    error: str | None = None
    payload: dict | None = None


@dataclass
class Checkpoint:
    version: int
    stamp: str
    state: str
    smoke: bool
    suite_flag: str | None
    rubric_hash: str
    persona_id: str
    persona_hex: str
    prompts_id: str
    prompts_hex: str
    gold_ids: dict[str, list[str]]
    seats: dict[str, SeatSnapshot]
    items: list[ItemRecord]


def checkpoint_to_dict(ckpt: Checkpoint) -> dict:
    return asdict(ckpt)


def checkpoint_from_dict(data: dict) -> Checkpoint:
    if not isinstance(data, dict):
        raise CheckpointError(f"bad version: {data!r}")
    if "version" not in data:
        raise CheckpointError("missing version")
    version = data["version"]
    if version != CHECKPOINT_VERSION:
        raise CheckpointError(f"bad version: {version!r}")
    if "items" not in data:
        raise CheckpointError("missing items")
    items_raw = data["items"]
    if not isinstance(items_raw, list):
        raise CheckpointError(f"bad items: {type(items_raw).__name__}")
    seats_raw = data["seats"]
    seats = {
        name: seat if isinstance(seat, SeatSnapshot) else SeatSnapshot(**seat)
        for name, seat in seats_raw.items()
    }
    items = [
        item if isinstance(item, ItemRecord) else ItemRecord(**item)
        for item in items_raw
    ]
    return Checkpoint(
        version=version,
        stamp=data["stamp"],
        state=data["state"],
        smoke=data["smoke"],
        suite_flag=data["suite_flag"],
        rubric_hash=data["rubric_hash"],
        persona_id=data["persona_id"],
        persona_hex=data["persona_hex"],
        prompts_id=data["prompts_id"],
        prompts_hex=data["prompts_hex"],
        gold_ids=data["gold_ids"],
        seats=seats,
        items=items,
    )


def save_checkpoint(directory: Path, ckpt: Checkpoint) -> None:
    path = directory / "checkpoint.json"
    tmp = directory / "checkpoint.json.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(checkpoint_to_dict(ckpt), fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_checkpoint(directory: Path) -> Checkpoint:
    path = directory / "checkpoint.json"
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return checkpoint_from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, CheckpointError) as exc:
        raise CheckpointError(f"corrupt checkpoint: {exc}") from exc


def _plain(obj: object) -> dict:
    return {
        "kind": getattr(obj, "kind", None),
        "visible": getattr(obj, "visible", None),
        "row": getattr(obj, "row", None),
        "error": getattr(obj, "error", None),
    }


def _as_payload(obj: object) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return _plain(obj)


def classify_item(suite: str, result: object) -> tuple[str, dict | None, str | None]:
    if isinstance(result, Exception):
        return "transport_fail", None, f"{type(result).__name__}: {result}"
    if suite == "planner":
        return "ok", asdict(result), None
    if suite == "replyer":
        kind = getattr(result, "kind", None)
        if kind in {"model_fail", "judge_transport"}:
            return "transport_fail", None, getattr(result, "error", None)
        return "ok", _as_payload(result), None
    if suite == "e2e":
        judge_error = getattr(result, "judge_error", None)
        if judge_error:
            return "transport_fail", None, judge_error
        return "ok", asdict(result), None
    raise CheckpointError(f"unknown suite: {suite}")


def update_item(
    ckpt: Checkpoint,
    *,
    suite: str,
    id: str,
    sample: int,
    status: str,
    payload: dict | None,
    error: str | None,
) -> None:
    for row in ckpt.items:
        if row.suite == suite and row.id == id and row.sample == sample:
            row.status = status
            row.payload = payload
            row.error = error
            return
    raise CheckpointError(f"missing item {suite}/{id}/{sample}")


def retryable_items(ckpt: Checkpoint) -> list[ItemRecord]:
    return [row for row in ckpt.items if row.status in RETRYABLE]


def is_complete(ckpt: Checkpoint) -> bool:
    return all(row.status == "ok" for row in ckpt.items)


def seat_snapshot(endpoint: EndpointConfig) -> SeatSnapshot:
    return SeatSnapshot(
        model=endpoint.model,
        reasoning_effort=endpoint.reasoning_effort,
        temperature=endpoint.temperature,
        assistant_prefill=endpoint.assistant_prefill,
        extra_body=dict(endpoint.extra_body),
        base_url=endpoint.base_url,
    )
