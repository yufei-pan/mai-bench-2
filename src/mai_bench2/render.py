"""Render a gold log the way MaiBot actually presents chat to a model.

Real requests carry an XML-ish envelope, not a tidy one-line-per-message dump:

    <message msg_id="…" quote="…" time="HH:MM:SS" user="…" group_card="…">
    body

with no closing tag — the next block delimits it. Media that MaiBot does not
caption arrives as a bare marker in the body ([图片] / [视频] / [文件]), and
non-chat blocks (<plugin_proactive_task>, <system-reminder>) are interleaved.
"""

from __future__ import annotations

BASE_SECONDS = 14 * 3600  # t=0 renders as 14:00:00

MESSAGE = "message"
PROACTIVE_TASK = "proactive_task"
SYSTEM_REMINDER = "system_reminder"
KINDS = frozenset({MESSAGE, PROACTIVE_TASK, SYSTEM_REMINDER})


def clock_time(t: int) -> str:
    total = (BASE_SECONDS + int(t)) % 86400
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def render_entry(entry: dict) -> str:
    kind = entry.get("kind") or MESSAGE
    body = str(entry.get("text") or "")
    if kind == PROACTIVE_TASK:
        head = _attrs(
            [("id", entry.get("msg_id")), ("plugin_id", entry.get("plugin_id") or "scheduler")]
        )
        return f"<plugin_proactive_task {head}>\n{body}"
    if kind == SYSTEM_REMINDER:
        return f"<system-reminder>\n{body}"
    head = _attrs(
        [
            ("msg_id", entry.get("msg_id")),
            ("quote", entry.get("quote")),
            ("time", clock_time(entry.get("t") or 0)),
            ("user", entry.get("user") or entry.get("speaker")),
            ("group_card", entry.get("group_card")),
            ("is_self_message", "true" if entry.get("is_self_message") else None),
        ]
    )
    return f"<message {head}>\n{body}"


def render_log(entries: list[dict]) -> str:
    return "\n".join(render_entry(entry) for entry in entries)


def speaker_of(entry: dict) -> str:
    return str(entry.get("user") or entry.get("speaker") or "")


def _attrs(pairs: list[tuple[str, object]]) -> str:
    return " ".join(
        f'{key}="{_escape(value)}"' for key, value in pairs if value is not None and value != ""
    )


def _escape(value: object) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
