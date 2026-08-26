"""Compare historical run artifacts into tables."""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mai_bench2.config import ConfigError
from mai_bench2.report import _REPLYER_DIMS, _TERM_ORDER, grid

_HEADLINE = {
    "planner": "planner-v1",
    "replyer": "replyer-v1",
    "e2e": "pair-v1",
}
_SCORE100 = ("planner_v1", "joint", "replyer_v1")
_SUITE_ORDER = ("planner", "replyer", "e2e")


class CompareError(Exception):
    pass


def resolve_output_dir(explicit: str | None) -> Path:
    """`output_dir` from config, without interpolating seat secrets.

    Compare is read-only; a missing API key must not block it. No config means
    `./results`.
    """
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"config not found: {path}")
        return _output_dir_from_toml(path.resolve())
    cwd = Path("config.toml")
    if cwd.is_file():
        return _output_dir_from_toml(cwd.resolve())
    home = Path.home() / ".config" / "mai-bench-2" / "config.toml"
    if home.is_file():
        return _output_dir_from_toml(home)
    return Path("results")


def _output_dir_from_toml(path: Path) -> Path:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    raw = run.get("output_dir", "./results")
    if not isinstance(raw, str) or not raw.strip():
        raw = "./results"
    return Path(raw).expanduser()


@dataclass
class RunRecord:
    stamp: str
    smoke: bool
    rubric_hash: str
    persona_id: str
    persona_hex: str
    prompts_id: str
    prompts_hex: str
    headlines: dict
    suites: dict[str, dict]
    models: dict[str, str] = field(default_factory=dict)


def compare_runs(
    output_dir: Path,
    *,
    smoke: bool = False,
    full: bool = False,
    group: str | None = None,
) -> str:
    runs, skipped = _load_runs(Path(output_dir))
    if not runs:
        raise CompareError(f"no runs in {output_dir}")
    runs = _select(runs, group=group, smoke=smoke, full=full)
    if not runs:
        raise CompareError("no matching runs")
    buckets: dict[tuple, list[RunRecord]] = defaultdict(list)
    for run in runs:
        buckets[_group_key(run)].append(run)
    keys = sorted(buckets, key=lambda key: max(item.stamp for item in buckets[key]), reverse=True)
    blocks = [_render_group(key, buckets[key]) for key in keys]
    text = "\n\n".join(blocks)
    if skipped:
        text = f"{text}\n\nskipped {skipped} folders (no summary.json)"
    return text + "\n"


def _load_runs(output_dir: Path) -> tuple[list[RunRecord], int]:
    if not output_dir.is_dir():
        return [], 0
    runs: list[RunRecord] = []
    skipped = 0
    for path in output_dir.iterdir():
        if not path.is_dir():
            continue
        summary_path = path / "summary.json"
        if not summary_path.is_file():
            skipped += 1
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(summary, dict):
            skipped += 1
            continue
        runs.append(_record(path, summary))
    return runs, skipped


def _record(path: Path, summary: dict) -> RunRecord:
    suites: dict[str, dict] = {}
    for item in summary.get("suites") or []:
        if isinstance(item, dict) and item.get("name"):
            suites[str(item["name"])] = item
    headlines = summary.get("headlines") or {}
    if not isinstance(headlines, dict):
        headlines = {}
    return RunRecord(
        stamp=path.name,
        smoke=bool(summary.get("smoke")),
        rubric_hash=str(summary.get("rubric_hash") or ""),
        persona_id=str(summary.get("persona_id") or ""),
        persona_hex=str(summary.get("persona_hex") or ""),
        prompts_id=str(summary.get("prompts_id") or ""),
        prompts_hex=str(summary.get("prompts_hex") or ""),
        headlines=headlines,
        suites=suites,
        models=_load_models(path),
    )


def _load_models(path: Path) -> dict[str, str]:
    config = path / "config.toml"
    if not config.is_file():
        return {}
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    models: dict[str, str] = {}
    for seat in ("planner", "replyer", "judge"):
        section = data.get(seat)
        if not isinstance(section, dict):
            continue
        model = section.get("model") or ""
        if not model:
            continue
        effort = section.get("reasoning_effort")
        models[seat] = f"{model} @ {effort}" if effort else str(model)
    return models


def _select(
    runs: list[RunRecord],
    *,
    group: str | None,
    smoke: bool,
    full: bool,
) -> list[RunRecord]:
    if group:
        stamped = [run for run in runs if run.stamp == group]
        if stamped:
            triple = _triple(stamped[0])
            runs = [run for run in runs if _triple(run) == triple]
        else:
            matched = [run for run in runs if run.rubric_hash.startswith(group)]
            if not matched:
                raise CompareError(f"unknown group: {group}")
            runs = matched
    if full:
        runs = [run for run in runs if not run.smoke]
    elif smoke:
        runs = [run for run in runs if run.smoke]
    return runs


def _triple(run: RunRecord) -> tuple[str, str, str]:
    return (run.rubric_hash, run.persona_hex, run.prompts_hex)


def _group_key(run: RunRecord) -> tuple[str, str, str, bool]:
    return (*_triple(run), run.smoke)


def _render_group(key: tuple, runs: list[RunRecord]) -> str:
    rubric, persona_hex, prompts_hex, smoke_flag = key
    sample = runs[0]
    mode = "smoke" if smoke_flag else "full"
    lines = [
        f"GROUP rubric_hash={rubric} persona_id={sample.persona_id} "
        f"persona_hex={persona_hex} prompts_id={sample.prompts_id} "
        f"prompts_hex={prompts_hex}  mode={mode}  n={len(runs)}"
    ]
    ordered = sorted(runs, key=lambda run: run.stamp, reverse=True)
    for name in _SUITE_ORDER:
        block = _suite_table(name, ordered)
        if not block:
            continue
        lines.append("")
        lines.extend(block)
    return "\n".join(lines)


def _suite_table(name: str, runs: list[RunRecord]) -> list[str]:
    present = [run for run in runs if name in run.suites]
    if not present:
        return []
    header, rows = _suite_rows(name, present)
    return [name.upper(), *grid(rows, header)]


def _suite_rows(name: str, runs: list[RunRecord]) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    headline = _HEADLINE[name]
    if name == "planner":
        header = ("stamp", "planner", headline, *_TERM_ORDER)
        rows = [
            (
                run.stamp,
                run.models.get("planner", ""),
                _headline_cell(run, headline),
                *(_term_cell(run.suites[name], key) for key in _TERM_ORDER),
            )
            for run in runs
        ]
        return header, rows
    if name == "replyer":
        header = ("stamp", "replyer", "judge", headline, *_REPLYER_DIMS)
        rows = [
            (
                run.stamp,
                run.models.get("replyer", ""),
                run.models.get("judge", ""),
                _headline_cell(run, headline),
                *(_term_cell(run.suites[name], key) for key in _REPLYER_DIMS),
            )
            for run in runs
        ]
        return header, rows
    header = (
        "stamp",
        "planner",
        "replyer",
        "judge",
        headline,
        *_TERM_ORDER,
        *_SCORE100,
    )
    rows = [
        (
            run.stamp,
            run.models.get("planner", ""),
            run.models.get("replyer", ""),
            run.models.get("judge", ""),
            _headline_cell(run, headline),
            *(_term_cell(run.suites[name], key) for key in _TERM_ORDER),
            *(_term_cell(run.suites[name], key) for key in _SCORE100),
        )
        for run in runs
    ]
    return header, rows


def _headline_cell(run: RunRecord, name: str) -> str:
    if name not in run.headlines:
        return "n/a"
    return f"{float(run.headlines[name]):.1f}"


def _term_cell(suite: dict, key: str) -> str:
    native = suite.get("native") or {}
    if not isinstance(native, dict) or key not in native:
        return ""
    value = native[key]
    if key in _SCORE100 or key in _HEADLINE.values():
        return f"{float(value):.1f}"
    return f"{float(value):.2f}"
