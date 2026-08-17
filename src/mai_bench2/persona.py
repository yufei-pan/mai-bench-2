from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

_HEX_KEYS = (
    "nickname",
    "behavior_style",
    "personality",
    "reply_style",
    "group_chat_prompt",
    "private_chat_prompt",
)


@dataclass(frozen=True)
class Persona:
    id: str
    path: str
    nickname: str
    behavior_style: str
    personality: str
    reply_style: str
    group_chat_prompt: str
    private_chat_prompt: str
    hex: str  # 12 lowercase hex


def persona_hex(persona: Persona) -> str:
    """SHA-256 of UTF-8 canonical JSON sort_keys, separators (',', ':'), ensure_ascii=False,
    keys nickname, behavior_style, personality, reply_style, group_chat_prompt, private_chat_prompt,
    each value text.strip('\\n'). First 12 lowercase hex chars."""
    payload = {key: getattr(persona, key).strip("\n") for key in _HEX_KEYS}
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_persona(spec: str, *, root: Path) -> Persona:
    """spec is an id or a filesystem path. Search root/personas/<id>.toml then root/personas/classic/<id>.toml."""
    path = _resolve_persona_path(spec, root=root)
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    planner = data["planner"]
    replyer = data["replyer"]
    persona = Persona(
        id=data["id"],
        path=str(path),
        nickname=data["nickname"],
        behavior_style=planner["behavior_style"],
        personality=replyer["personality"],
        reply_style=replyer["reply_style"],
        group_chat_prompt=replyer["group_chat_prompt"],
        private_chat_prompt=replyer["private_chat_prompt"],
        hex="",
    )
    return replace(persona, hex=persona_hex(persona))


def _resolve_persona_path(spec: str, *, root: Path) -> Path:
    direct = Path(spec)
    if direct.is_file():
        return direct
    for candidate in (
        root / "personas" / f"{spec}.toml",
        root / "personas" / "classic" / f"{spec}.toml",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(spec)
