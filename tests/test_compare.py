from __future__ import annotations

import json
from pathlib import Path

import pytest

from mai_bench2.compare import CompareError, compare_runs


def test_empty_results_is_an_error(tmp_path: Path):
    with pytest.raises(CompareError, match="no runs"):
        compare_runs(tmp_path)


def _put_run(
    root: Path,
    stamp: str,
    *,
    smoke: bool = False,
    rubric_hash: str = "aaa111",
    persona_id: str = "official",
    persona_hex: str = "77be5c59f150",
    prompts_id: str = "official",
    prompts_hex: str = "bbbb222",
    headlines: dict | None = None,
    reasons: list | None = None,
    suites: list | None = None,
    models: dict | None = None,
) -> Path:
    path = root / stamp
    path.mkdir(parents=True)
    if suites is None:
        suites = [
            {
                "name": "planner",
                "status": "ok",
                "native": {
                    "action": 0.5,
                    "reply_target": 0.4,
                    "wait_band": 0.3,
                    "tool_restraint": 1.0,
                    "tool_f1": 0.2,
                    "tool_hit": 0.1,
                    "briefing": 0.8,
                },
                "subscore": 50.0,
                "n_items": 10,
            }
        ]
    if headlines is None:
        headlines = {} if smoke else {"planner-v1": float(suites[0].get("subscore") or 0)}
    summary = {
        "persona_id": persona_id,
        "persona_hex": persona_hex,
        "rubric_hash": rubric_hash,
        "prompts_id": prompts_id,
        "prompts_hex": prompts_hex,
        "smoke": smoke,
        "headlines": headlines,
        "reasons": ["smoke"] if smoke else list(reasons or []),
        "suites": suites,
    }
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    models = dict(models or {"planner": "ox-alpha", "replyer": "reply-m", "judge": "judge-m"})
    lines: list[str] = []
    for seat, spec in models.items():
        if spec is None:
            continue
        name, effort = spec if isinstance(spec, tuple) else (spec, None)
        lines.append(f"[{seat}]")
        lines.append(f'model = "{name}"')
        if effort:
            lines.append(f'reasoning_effort = "{effort}"')
        lines.append("")
    (path / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    return path


def _groups(text: str) -> list[str]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("GROUP "):
            if current:
                chunks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append(current)
    return ["\n".join(chunk) for chunk in chunks]


def test_same_hash_group_prints_planner_table_newest_first(tmp_path: Path):
    _put_run(
        tmp_path,
        "2026-08-20T100000Z",
        headlines={"planner-v1": 40.0},
        models={"planner": "old-m"},
        suites=[{"name": "planner", "status": "ok", "native": {"action": 0.40}, "subscore": 40.0}],
    )
    _put_run(
        tmp_path,
        "2026-08-21T100000Z",
        headlines={"planner-v1": 55.0},
        models={"planner": ("new-m", "xhigh")},
        suites=[{"name": "planner", "status": "ok", "native": {"action": 0.55}, "subscore": 55.0}],
    )
    text = compare_runs(tmp_path)
    groups = _groups(text)
    assert len(groups) == 1
    banner = groups[0].splitlines()[0]
    assert "rubric_hash=aaa111" in banner
    assert "persona_id=official" in banner
    assert "persona_hex=77be5c59f150" in banner
    assert "prompts_id=official" in banner
    assert "prompts_hex=bbbb222" in banner
    assert "mode=full" in banner
    assert "n=2" in banner
    assert "PLANNER" in groups[0]
    assert "REPLYER" not in groups[0]
    assert "E2E" not in groups[0]
    assert text.index("2026-08-21T100000Z") < text.index("2026-08-20T100000Z")
    assert "new-m @ xhigh" in text
    assert "old-m" in text
    assert "55.0" in text
    assert "40.0" in text
    assert "action" in text
    assert "0.55" in text
    assert "0.40" in text


def test_different_rubric_hash_is_two_groups_newest_first(tmp_path: Path):
    _put_run(tmp_path, "2026-08-20T100000Z", rubric_hash="oldhash", headlines={"planner-v1": 10.0})
    _put_run(tmp_path, "2026-08-22T100000Z", rubric_hash="newhash", headlines={"planner-v1": 90.0})
    text = compare_runs(tmp_path)
    groups = _groups(text)
    assert len(groups) == 2
    assert "rubric_hash=newhash" in groups[0].splitlines()[0]
    assert "rubric_hash=oldhash" in groups[1].splitlines()[0]


def test_smoke_and_full_never_share_a_table(tmp_path: Path):
    _put_run(
        tmp_path,
        "2026-08-21T010000Z",
        smoke=True,
        headlines={},
        suites=[{"name": "planner", "status": "ok", "native": {"action": 0.9}, "subscore": 90.0}],
    )
    _put_run(
        tmp_path,
        "2026-08-21T020000Z",
        smoke=False,
        headlines={"planner-v1": 50.0},
        suites=[{"name": "planner", "status": "ok", "native": {"action": 0.5}, "subscore": 50.0}],
    )
    text = compare_runs(tmp_path)
    groups = _groups(text)
    assert len(groups) == 2
    by_mode = {chunk.splitlines()[0].split("mode=")[1].split()[0]: chunk for chunk in groups}
    assert "full" in by_mode and "smoke" in by_mode
    assert "2026-08-21T020000Z" in by_mode["full"]
    assert "2026-08-21T010000Z" not in by_mode["full"]
    assert "2026-08-21T010000Z" in by_mode["smoke"]
    assert "2026-08-21T020000Z" not in by_mode["smoke"]
    assert "n/a" in by_mode["smoke"]


def test_full_flag_hides_smoke_groups(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z", smoke=True)
    _put_run(tmp_path, "2026-08-21T020000Z", smoke=False, headlines={"planner-v1": 50.0})
    text = compare_runs(tmp_path, full=True)
    groups = _groups(text)
    assert len(groups) == 1
    assert "mode=full" in groups[0]
    assert "2026-08-21T010000Z" not in text


def test_smoke_flag_hides_full_groups(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z", smoke=True)
    _put_run(tmp_path, "2026-08-21T020000Z", smoke=False, headlines={"planner-v1": 50.0})
    text = compare_runs(tmp_path, smoke=True)
    groups = _groups(text)
    assert len(groups) == 1
    assert "mode=smoke" in groups[0]
    assert "2026-08-21T020000Z" not in text


def test_group_stamp_pins_hash_triple(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z", rubric_hash="keepme", headlines={"planner-v1": 11.0})
    _put_run(tmp_path, "2026-08-21T020000Z", rubric_hash="keepme", headlines={"planner-v1": 22.0})
    _put_run(tmp_path, "2026-08-21T030000Z", rubric_hash="other", headlines={"planner-v1": 99.0})
    text = compare_runs(tmp_path, group="2026-08-21T010000Z")
    groups = _groups(text)
    assert len(groups) == 1
    assert "rubric_hash=keepme" in groups[0]
    assert "2026-08-21T020000Z" in text
    assert "2026-08-21T030000Z" not in text


def test_group_hash_prefix(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z", rubric_hash="abcfff", headlines={"planner-v1": 1.0})
    _put_run(tmp_path, "2026-08-21T020000Z", rubric_hash="zzz000", headlines={"planner-v1": 2.0})
    text = compare_runs(tmp_path, group="abc")
    assert "abcfff" in text
    assert "zzz000" not in text


def test_unknown_group_is_an_error(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z")
    with pytest.raises(CompareError, match="unknown group"):
        compare_runs(tmp_path, group="nope")


def test_skip_folders_without_summary(tmp_path: Path):
    _put_run(tmp_path, "2026-08-21T010000Z", headlines={"planner-v1": 50.0})
    (tmp_path / "2026-08-21T020000Z").mkdir()
    text = compare_runs(tmp_path)
    assert "2026-08-21T010000Z" in text
    assert "skipped 1 folders (no summary.json)" in text


def test_replyer_table_includes_judge_model(tmp_path: Path):
    _put_run(
        tmp_path,
        "2026-08-21T010000Z",
        headlines={"replyer-v1": 7.5},
        models={"replyer": "reply-m", "judge": "gpt-judge"},
        suites=[
            {
                "name": "replyer",
                "status": "ok",
                "native": {
                    "in_character": 8.0,
                    "style": 7.0,
                    "grounding": 6.0,
                    "group_chat": 5.0,
                    "no_planner_voice": 9.0,
                },
                "subscore": 7.5,
            }
        ],
    )
    text = compare_runs(tmp_path)
    assert "REPLYER" in text
    assert "PLANNER" not in text
    header = [line for line in text.splitlines() if "replyer-v1" in line][0]
    assert "judge" in header
    assert "gpt-judge" in text
    assert "in_character" in text
    assert "8.00" in text or "8.0" in text


def test_e2e_table_names_writer_seats_and_judge(tmp_path: Path):
    _put_run(
        tmp_path,
        "2026-08-21T010000Z",
        headlines={"pair-v1": 60.0},
        models={"planner": "p-m", "replyer": "r-m", "judge": "j-m"},
        suites=[
            {
                "name": "e2e",
                "status": "ok",
                "native": {
                    "action": 0.6,
                    "planner_v1": 50.0,
                    "joint": 70.0,
                    "replyer_v1": 80.0,
                },
                "subscore": 60.0,
            }
        ],
    )
    text = compare_runs(tmp_path)
    assert "E2E" in text
    header = [line for line in text.splitlines() if "pair-v1" in line][0]
    assert "planner" in header
    assert "replyer" in header
    assert "judge" in header
    assert "p-m" in text and "r-m" in text and "j-m" in text
    assert "60.0" in text
    assert "joint" in text


def test_console_compare_does_not_need_api_keys(tmp_path: Path, capsys):
    from mai_bench2.cli import console

    results = tmp_path / "out"
    _put_run(results, "2026-08-21T010000Z", headlines={"planner-v1": 50.0})
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "${MISSING_COMPARE_KEY}"',
                'model = "m"',
                "[run]",
                f'output_dir = "{results}"',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exited:
        console(["compare", "--config", str(cfg)])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert "PLANNER" in out
    assert "50.0" in out


def test_console_compare_empty_exits_1(tmp_path: Path, capsys):
    from mai_bench2.cli import console

    cfg = tmp_path / "config.toml"
    empty = tmp_path / "empty-results"
    empty.mkdir()
    cfg.write_text(
        "\n".join(["[run]", f'output_dir = "{empty}"']),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exited:
        console(["compare", "--config", str(cfg)])
    assert exited.value.code == 1
    assert "no runs" in capsys.readouterr().err


def test_console_compare_defaults_to_results(tmp_path: Path, monkeypatch, capsys):
    from mai_bench2.cli import console

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda *a, **k: tmp_path / "home")
    _put_run(tmp_path / "results", "2026-08-21T010000Z", headlines={"planner-v1": 41.0})
    with pytest.raises(SystemExit) as exited:
        console(["compare"])
    assert exited.value.code == 0
    assert "41.0" in capsys.readouterr().out
