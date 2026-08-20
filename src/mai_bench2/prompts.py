"""Prompt templates, selectable the same way personas are.

A run's prompts are part of what it measures, so `prompts_hex` is reported beside
`persona_hex` and folded into `rubric_hash`.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

_HEX_KEYS = (
    "planner_system",
    "planner_final_assistant_reminder",
    "query_memory_rule_group",
    "query_memory_rule_private",
    "replyer_system",
    "replyer_output_instruction",
    "replyer_final_instruction",
    "reply_style_short",
    "reply_style_long",
)


@dataclass(frozen=True)
class Prompts:
    id: str
    path: str
    planner_system: str
    planner_final_assistant_reminder: str
    query_memory_rule_group: str
    query_memory_rule_private: str
    replyer_system: str
    replyer_output_instruction: str
    replyer_final_instruction: str
    reply_style_short: str
    reply_style_long: str
    hex: str = ""


def fill(template: str, values: dict[str, str]) -> str:
    """Literal placeholder substitution. Unknown placeholders survive untouched,
    and braces in Chinese prose are not special."""
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def prompts_hex(prompts: Prompts) -> str:
    """SHA-256 over canonical JSON of the applied templates. First 12 hex chars."""
    payload = {key: getattr(prompts, key).strip("\n") for key in _HEX_KEYS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_prompts(spec: str, *, root: Path) -> Prompts:
    """spec is an id or a path. Searched as root/prompts/<id>.toml."""
    path = _resolve(spec, root=root)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    planner = data.get("planner") if isinstance(data.get("planner"), dict) else {}
    replyer = data.get("replyer") if isinstance(data.get("replyer"), dict) else {}
    query_memory = (
        planner.get("query_memory_rule")
        if isinstance(planner.get("query_memory_rule"), dict)
        else {}
    )
    styles = (
        replyer.get("reply_style_message")
        if isinstance(replyer.get("reply_style_message"), dict)
        else {}
    )
    required = {
        "planner.system": planner.get("system"),
        "planner.final_assistant_reminder": planner.get("final_assistant_reminder"),
        "planner.query_memory_rule.group": query_memory.get("group"),
        "planner.query_memory_rule.private": query_memory.get("private"),
        "replyer.system": replyer.get("system"),
        "replyer.output_instruction": replyer.get("output_instruction"),
        "replyer.final_instruction": replyer.get("final_instruction"),
        "replyer.reply_style_message.简短表达": styles.get("简短表达"),
        "replyer.reply_style_message.长回复": styles.get("长回复"),
    }
    missing = [key for key, value in required.items() if not isinstance(value, str)]
    if missing:
        raise ValueError(f"invalid prompts: {path.name}: missing {missing[0]}")
    prompts = Prompts(
        id=data.get("id") or path.stem,
        path=str(path),
        planner_system=required["planner.system"],
        planner_final_assistant_reminder=required["planner.final_assistant_reminder"],
        query_memory_rule_group=required["planner.query_memory_rule.group"],
        query_memory_rule_private=required["planner.query_memory_rule.private"],
        replyer_system=required["replyer.system"],
        replyer_output_instruction=required["replyer.output_instruction"],
        replyer_final_instruction=required["replyer.final_instruction"],
        reply_style_short=required["replyer.reply_style_message.简短表达"],
        reply_style_long=required["replyer.reply_style_message.长回复"],
    )
    return replace(prompts, hex=prompts_hex(prompts))


@lru_cache(maxsize=None)
def default_prompts() -> Prompts:
    """The shipped `official` templates, for callers with no run context."""
    return load_prompts("official", root=Path(__file__).resolve().parents[2])


def _resolve(spec: str, *, root: Path) -> Path:
    direct = Path(spec)
    if direct.is_file():
        return direct
    candidate = root / "prompts" / f"{spec}.toml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(spec)
