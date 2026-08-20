"""Prompt templates, selectable the same way personas are.

A run's prompts are part of what it measures, so `prompts_hex` is reported beside
`persona_hex` and folded into `rubric_hash`.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

_HEX_KEYS = ("planner_system", "replyer_system", "replyer_user")


@dataclass(frozen=True)
class Prompts:
    id: str
    path: str
    planner_system: str
    replyer_system: str
    replyer_user: str
    tool_lines: dict[str, str] = field(default_factory=dict)
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
    payload["tool_lines"] = dict(sorted(prompts.tool_lines.items()))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_prompts(spec: str, *, root: Path) -> Prompts:
    """spec is an id or a path. Searched as root/prompts/<id>.toml."""
    path = _resolve(spec, root=root)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    planner = data.get("planner") or {}
    replyer = data.get("replyer") or {}
    for section, key in (("planner", "system"), ("replyer", "system"), ("replyer", "user")):
        if not isinstance((data.get(section) or {}).get(key), str):
            raise ValueError(f"invalid prompts: {path.name}: missing [{section}].{key}")
    prompts = Prompts(
        id=data.get("id") or path.stem,
        path=str(path),
        planner_system=planner["system"],
        replyer_system=replyer["system"],
        replyer_user=replyer["user"],
        tool_lines=dict(planner.get("tool_line") or {}),
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
