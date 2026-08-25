from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from conftest import ROOT
from mai_bench2.checkpoint import (
    Checkpoint,
    ItemRecord,
    SeatSnapshot,
    save_checkpoint,
    seat_snapshot,
)
from mai_bench2.cli import _gold_ids_for_run, console
from mai_bench2.config import AppConfig, ConfigError, EndpointConfig, RunConfig, SuiteConfig
from mai_bench2.metrics import rubric_hash
from mai_bench2.persona import load_persona
from mai_bench2.prompts import load_prompts
from mai_bench2.resume import ResumeError, gate_resume, load_resume_target, resolve_output_dir


def _planner_cfg(**kwargs) -> AppConfig:
    endpoint = EndpointConfig(
        kwargs.pop("base_url", "http://p/v1"),
        kwargs.pop("api_key", "SECRET_KEY"),
        kwargs.pop("model", "m"),
        reasoning_effort=kwargs.pop("reasoning_effort", "high"),
        temperature=kwargs.pop("temperature", 0.0),
        assistant_prefill=kwargs.pop("assistant_prefill", False),
        extra_body=kwargs.pop("extra_body", {"z": 1, "a": 2}),
    )
    return AppConfig(
        endpoint,
        None,
        None,
        RunConfig(smoke=True, persona="official", prompts="official"),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        kwargs.pop("config_path", "x"),
        suite_flag="planner",
    )


def _official():
    persona = load_persona("official", root=ROOT)
    prompts = load_prompts("official", root=ROOT)
    return persona, prompts, rubric_hash(prompts)


def _ckpt_for(cfg: AppConfig, **kwargs) -> Checkpoint:
    persona, prompts, rh = _official()
    gold_ids = kwargs.pop("gold_ids", None)
    if gold_ids is None:
        gold_ids = _gold_ids_for_run(cfg, ROOT)
    seats = kwargs.pop("seats", None)
    if seats is None:
        seats = {}
        for role in ("planner", "replyer", "judge"):
            endpoint = getattr(cfg, role)
            if endpoint is not None:
                seats[role] = seat_snapshot(endpoint)
    planner_ids = gold_ids.get("planner") or ["p-1"]
    items = kwargs.pop(
        "items",
        [ItemRecord("planner", planner_ids[0], 0, "pending")],
    )
    return Checkpoint(
        version=1,
        stamp=kwargs.pop("stamp", "2026-08-25T000000Z"),
        state=kwargs.pop("state", "incomplete"),
        smoke=kwargs.pop("smoke", cfg.run.smoke),
        suite_flag=kwargs.pop("suite_flag", cfg.suite_flag),
        rubric_hash=kwargs.pop("rubric_hash", rh),
        persona_id=kwargs.pop("persona_id", persona.id),
        persona_hex=kwargs.pop("persona_hex", persona.hex),
        prompts_id=kwargs.pop("prompts_id", prompts.id),
        prompts_hex=kwargs.pop("prompts_hex", prompts.hex),
        gold_ids=gold_ids,
        seats=seats,
        items=items,
    )


def _write_config(
    tmp_path: Path, *, output_dir: Path | None = None, api_key: str = "SECRET_KEY"
) -> tuple[Path, Path]:
    out_dir = output_dir if output_dir is not None else tmp_path / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                f'api_key = "{api_key}"',
                'model = "m"',
                'reasoning_effort = "high"',
                "[run]",
                f'output_dir = "{out_dir}"',
                f'cache_dir = "{tmp_path / "cache"}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return cfg_path, out_dir


def _write_legacy_incomplete(stamp_dir: Path) -> None:
    stamp_dir.mkdir(parents=True, exist_ok=True)
    (stamp_dir / "summary.json").write_text(
        json.dumps(
            {
                "rubric_hash": "abc",
                "persona_id": "official",
                "persona_hex": "77be5c59f150",
                "prompts_id": "official",
                "prompts_hex": "bbbb",
                "smoke": True,
                "suite_flag": "planner",
            }
        ),
        encoding="utf-8",
    )
    (stamp_dir / "planner.json").write_text(
        json.dumps({"predictions": []}),
        encoding="utf-8",
    )
    (stamp_dir / "config.toml").write_text(
        '[planner]\nmodel = "m"\nbase_url = "http://p/v1"\n',
        encoding="utf-8",
    )


def test_gate_model_mismatch(tmp_path):
    live = _planner_cfg(model="live-model")
    ckpt = _ckpt_for(
        live,
        seats={
            "planner": SeatSnapshot(
                "ckpt-model",
                "high",
                0.0,
                False,
                {"z": 1, "a": 2},
                "http://p/v1",
            )
        },
    )
    with pytest.raises(ResumeError, match="planner"):
        gate_resume(ckpt, live, root=ROOT, package_root=ROOT)


def test_gate_base_url_warns(tmp_path):
    live = _planner_cfg(base_url="http://live/v1")
    ckpt = _ckpt_for(
        live,
        seats={
            "planner": SeatSnapshot(
                "m",
                "high",
                0.0,
                False,
                {"z": 1, "a": 2},
                "http://ckpt/v1",
            )
        },
    )
    warnings = gate_resume(ckpt, live, root=ROOT, package_root=ROOT)
    assert any("base_url" in w for w in warnings)


def test_gate_persona_hex_mismatch():
    cfg = _planner_cfg()
    persona = load_persona("official", root=ROOT)
    assert persona.hex != "deadbeefdead"
    ckpt = _ckpt_for(cfg, persona_hex="deadbeefdead")
    with pytest.raises(ResumeError, match=r"persona_hex: checkpoint=deadbeefdead live="):
        gate_resume(ckpt, cfg, root=ROOT, package_root=ROOT)


def test_gate_gold_changed():
    cfg = _planner_cfg()
    ckpt = _ckpt_for(cfg, gold_ids={"planner": ["not-a-gold-id"]})
    with pytest.raises(ResumeError, match="gold changed"):
        gate_resume(ckpt, cfg, root=ROOT, package_root=ROOT)


def test_gate_extra_body_mismatch():
    live = _planner_cfg(extra_body={"k": 1})
    ckpt = _ckpt_for(
        live,
        seats={
            "planner": SeatSnapshot("m", "high", 0.0, False, {"k": 2}, "http://p/v1"),
        },
    )
    with pytest.raises(ResumeError, match="planner"):
        gate_resume(ckpt, live, root=ROOT, package_root=ROOT)


def test_gate_extra_body_key_order_equal():
    live = _planner_cfg(extra_body={"a": 1, "b": 2})
    ckpt = _ckpt_for(
        live,
        seats={
            "planner": SeatSnapshot("m", "high", 0.0, False, {"b": 2, "a": 1}, "http://p/v1"),
        },
    )
    warnings = gate_resume(ckpt, live, root=ROOT, package_root=ROOT)
    assert warnings == []


def test_gate_missing_live_seat():
    cfg = AppConfig(
        None,
        None,
        None,
        RunConfig(smoke=True, persona="official", prompts="official"),
        SuiteConfig(),
        SuiteConfig(),
        SuiteConfig(smoke_n=4),
        "x",
        suite_flag="planner",
    )
    ckpt = _ckpt_for(
        _planner_cfg(),
        seats={"planner": SeatSnapshot("m", "high", 0.0, False, {"z": 1, "a": 2}, "http://p/v1")},
    )
    with pytest.raises(ResumeError, match="planner"):
        gate_resume(ckpt, cfg, root=ROOT, package_root=ROOT)


def test_resume_stamp_unknown(tmp_path, capsys):
    cfg_path, _out_dir = _write_config(tmp_path)
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", "nope", "--config", str(cfg_path)])
    assert exited.value.code == 1
    assert "unknown stamp" in capsys.readouterr().err


def test_resume_already_complete_without_api_key_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MISSING_RESUME_KEY", raising=False)
    cfg_path, out_dir = _write_config(tmp_path, api_key="${MISSING_RESUME_KEY}")
    stamp = "2026-08-25T000000Z"
    stamp_dir = out_dir / stamp
    stamp_dir.mkdir()
    save_checkpoint(
        stamp_dir,
        _ckpt_for(
            _planner_cfg(),
            stamp=stamp,
            state="complete",
            items=[ItemRecord("planner", "p-1", 0, "ok", payload={})],
        ),
    )
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", stamp, "--config", str(cfg_path)])
    assert exited.value.code == 0
    assert "already complete" in capsys.readouterr().out


def test_resume_unknown_stamp_without_api_key_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MISSING_RESUME_KEY", raising=False)
    cfg_path, _out_dir = _write_config(tmp_path, api_key="${MISSING_RESUME_KEY}")
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", "nope", "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "unknown stamp" in err
    assert "MISSING_RESUME_KEY" not in err


def test_resume_no_tty_empty_without_api_key_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MISSING_RESUME_KEY", raising=False)
    cfg_path, _out_dir = _write_config(tmp_path, api_key="${MISSING_RESUME_KEY}")
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "no resumable runs" in err
    assert "MISSING_RESUME_KEY" not in err


def test_resume_legacy_stamp_without_api_key_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MISSING_RESUME_KEY", raising=False)
    cfg_path, out_dir = _write_config(tmp_path, api_key="${MISSING_RESUME_KEY}")
    stamp = "2026-08-20T000000Z"
    _write_legacy_incomplete(out_dir / stamp)
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", stamp, "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "unknown stamp" not in err
    assert "Missing environment variable: MISSING_RESUME_KEY" in err


def test_resume_no_tty_lists_legacy_without_api_key_env(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MISSING_RESUME_KEY", raising=False)
    cfg_path, out_dir = _write_config(tmp_path, api_key="${MISSING_RESUME_KEY}")
    stamp = "2026-08-20T000000Z"
    _write_legacy_incomplete(out_dir / stamp)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert stamp in err
    assert "no TTY" in err
    assert "unknown stamp" not in err
    assert "MISSING_RESUME_KEY" not in err


def test_resume_already_complete(tmp_path, capsys):
    cfg_path, out_dir = _write_config(tmp_path)
    stamp = "2026-08-25T000000Z"
    stamp_dir = out_dir / stamp
    stamp_dir.mkdir()
    ckpt = _ckpt_for(
        _planner_cfg(),
        stamp=stamp,
        state="complete",
        items=[ItemRecord("planner", "p-1", 0, "ok", payload={})],
    )
    save_checkpoint(stamp_dir, ckpt)
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", stamp, "--config", str(cfg_path)])
    assert exited.value.code == 0
    assert "already complete" in capsys.readouterr().out


def test_resume_running_warns(tmp_path, capsys):
    cfg_path, out_dir = _write_config(tmp_path)
    stamp = "2026-08-25T010000Z"
    stamp_dir = out_dir / stamp
    stamp_dir.mkdir()
    ckpt = _ckpt_for(
        _planner_cfg(model="other"),
        stamp=stamp,
        state="running",
        items=[ItemRecord("planner", "p-1", 0, "pending")],
    )
    save_checkpoint(stamp_dir, ckpt)
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--stamp", stamp, "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "warning: checkpoint state is running; if a live process still owns this stamp, stop it first" in err


def test_resume_no_tty_empty(tmp_path, capsys, monkeypatch):
    cfg_path, _out_dir = _write_config(tmp_path)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--config", str(cfg_path)])
    assert exited.value.code == 1
    assert "no resumable runs" in capsys.readouterr().err


def test_resume_no_tty_lists_stamps(tmp_path, capsys, monkeypatch):
    cfg_path, out_dir = _write_config(tmp_path)
    stamp = "2026-08-25T020000Z"
    stamp_dir = out_dir / stamp
    stamp_dir.mkdir()
    save_checkpoint(
        stamp_dir,
        _ckpt_for(
            _planner_cfg(),
            stamp=stamp,
            state="incomplete",
            items=[ItemRecord("planner", "p-1", 0, "pending")],
        ),
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(SystemExit) as exited:
        console(["resume", "--config", str(cfg_path)])
    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert stamp in err
    assert "no TTY" in err


def test_load_resume_target_unknown(tmp_path):
    with pytest.raises(ResumeError, match="unknown stamp: missing"):
        load_resume_target(
            tmp_path,
            "missing",
            gold_ids={"planner": ["p-1"]},
            tty=False,
            stdin=StringIO(),
            stdout=StringIO(),
            stderr=StringIO(),
        )


def test_resolve_output_dir_explicit_missing(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        resolve_output_dir(str(tmp_path / "nope.toml"))


def test_resolve_output_dir_no_config_defaults_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert resolve_output_dir(None) == Path("results")


def test_resolve_output_dir_skips_interpolation(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "\n".join(
            [
                "[planner]",
                'base_url = "http://p/v1"',
                'api_key = "${API_KEY}"',
                'model = "m"',
                "[run]",
                'output_dir = "/tmp/mai-bench-resume-out"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert resolve_output_dir(str(cfg_path)) == Path("/tmp/mai-bench-resume-out")
