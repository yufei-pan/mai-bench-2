from __future__ import annotations

from datetime import datetime, timedelta

_ORIGIN = datetime(2026, 1, 1, 12, 0, 0)


def identity(nickname: str, personality: str) -> str:
    return f"你的名字是{nickname}。\n{personality}".strip()


def attention_block(chat_prompt: str) -> str:
    prompt = (chat_prompt or "").strip()
    if not prompt:
        return ""
    return "在该聊天中的注意事项：\n通用注意事项：\n" + prompt + "\n"


def stamp(t: int) -> str:
    return (_ORIGIN + timedelta(seconds=int(t))).strftime("%Y-%m-%d %H:%M:%S")


def clock_time(t: int) -> str:
    return (_ORIGIN + timedelta(seconds=int(t))).strftime("%H:%M:%S")


def deferred_reminder(locked: list[tuple[str, str]]) -> str:
    if not locked:
        return ""
    lines = [
        "<system-reminder>",
        "以下工具当前未直接暴露给你，但可以通过 tool_search 工具发现并在后续轮次中使用：",
    ]
    for index, (name, description) in enumerate(locked, start=1):
        desc = description.strip()
        lines.append(f"{index}. {name}: {desc}" if desc else f"{index}. {name}")
    lines.extend([
        "",
        "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行。",
        "</system-reminder>",
    ])
    return "\n".join(lines)


def replyer_history(messages: list[dict]) -> list[dict]:
    rows = []
    for message in messages:
        if message.get("kind") and message.get("kind") != "message":
            continue
        role = "assistant" if message.get("is_self_message") else "user"
        rows.append({"role": role, "content": str(message.get("text") or "")})
    return rows


def target_block(messages: list[dict], msg_id: str, nickname: str) -> str:
    target_id = (msg_id or "").strip()
    if not target_id:
        return ""
    row = next((m for m in messages if str(m.get("msg_id") or "") == target_id), None)
    if row is None:
        return ""
    text = str(row.get("text") or "").strip() or "[无可见文本内容]"
    if row.get("is_self_message"):
        return "\n".join([
            f"你想要补充说明你自己（{nickname}） 发送的 msg_id为 {target_id} 的消息，"
            "你可以在这条目标消息的基础上补充发言，不要把你自己的发言当成别人的发言。",
            f"- 你之前的发言内容：{text}",
        ])
    sender = str(row.get("group_card") or row.get("user") or "")
    lines = [
        f"你想要回复的消息是 {sender} 发送的 msg_id为 {target_id} 的消息，你这次要回复的就是这条目标消息，不要把其他历史消息当成当前回复对象。",
    ]
    if row.get("quote"):
        lines.append(f"- quote={row['quote']}")
    lines.append(f"- 发言内容：{text}")
    return "\n".join(lines)
