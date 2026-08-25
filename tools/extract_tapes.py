#!/usr/bin/env python3
"""Extract anonymized chat tapes from maiGoLLMRouter planner/replyer logs.

Usage: extract_tapes.py <maiGoLLMRouter-logs-dir>
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from goldkit import M, SELF_MAX_CHARS, counted  # noqa: E402

LOGS = Path()
OUT = ROOT / "tools" / "tapes"

CAST = (
    "小徐 阿岚 团团 老周 咪咪 大鹏 芋圆 蓝莓 三三 "
    "小满 阿KEN 老白 北北 阿年 可乐 饺子 豆豆 花生"
).split()
SHORT_SELF = ("嗯", "行", "好", "我看看", "那行", "知道了", "哦", "确实")
BOT = frozenset({"菜包", "麦麦"})
DENY = ("菜包", "demonte", "地上补课", "技校配不上", "群臭狗", "HydroBlue", "http://", "https://")
ALLOWED_FORWARD_SPEAKERS = frozenset({"麦麦", "QQ用户", *CAST})

ATTR_RE = re.compile(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\')')
MSG_RE = re.compile(
    r"<message\s+([^>]+)>(.*?)(?=\n<message\s|\n<plugin_proactive_task|\n<system-reminder|\Z)",
    re.S,
)
URL_RE = re.compile(r"https?://[^\s<>\"']+")
ID_RE = re.compile(r"\d{8,}")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")

CUT_MARKERS = (
    "\n[已折叠的历史工具调用]",
    "[已折叠的历史工具调用]",
    "\n【RSS 订阅",
    "【人物画像",
    "【表达习惯",
    "【菜包的自我回馈",
    "<plugin_proactive_task",
    "<system-reminder",
    "印象卡片",
    "[黑话参考]",
    "「分析」",
    "分析：",
)


def _unescape(value: str) -> str:
    return (
        value.replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def _sha_int(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:2], "big")


def fake_card(user: str, card: str | None) -> str:
    return CAST[_sha_int(f"{user}\0{card or ''}") % len(CAST)]


def fake_user(user: str, card: str | None) -> str:
    digest = hashlib.sha256(f"{user}\0{card or ''}".encode("utf-8")).hexdigest()[:4]
    return f"q_{digest}"


def flatten_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        typ = str(content.get("type") or "")
        if "image" in typ or "image_url" in content:
            text = flatten_content(content.get("text") or content.get("content"))
            if "[图片" in (text or ""):
                return text
            return (text + "\n" if text else "") + "[图片]"
        if content.get("text"):
            return str(content["text"])
        return flatten_content(content.get("content"))
    if isinstance(content, list):
        texts: list[str] = []
        has_image = False
        has_pic_text = False
        for part in content:
            piece = flatten_content(part)
            if isinstance(part, dict):
                typ = str(part.get("type") or "")
                if "image" in typ or "image_url" in part:
                    has_image = True
            if "[图片" in piece:
                has_pic_text = True
            if piece:
                texts.append(piece)
        blob = "\n".join(texts)
        if has_image and not has_pic_text and "[图片" not in blob:
            blob = (blob + "\n" if blob else "") + "[图片]"
        return blob
    return str(content)


def iter_blobs(request: dict) -> list[str]:
    blobs: list[str] = []
    incoming = request.get("input")
    if isinstance(incoming, str):
        blobs.append(incoming)
    elif isinstance(incoming, list):
        for item in incoming:
            if isinstance(item, str):
                blobs.append(item)
            elif isinstance(item, dict):
                if item.get("type") in {"function_call", "function_call_output"}:
                    continue
                if item.get("role") in {"system", "assistant"}:
                    continue
                blobs.append(flatten_content(item.get("content")))
    for message in request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") in {"system", "assistant"}:
            continue
        blobs.append(flatten_content(message.get("content")))
    return blobs


def parse_time(stamp: str) -> int | None:
    match = TIME_RE.match((stamp or "").strip())
    if not match:
        return None
    hour, minute, second = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def clean_body(text: str) -> str:
    body = text.replace("\r\n", "\n")
    if body.startswith("\n"):
        body = body[1:]
    for marker in CUT_MARKERS:
        idx = body.find(marker)
        if idx >= 0:
            body = body[:idx]
    return body.strip()


def parse_messages(request: dict) -> list[dict]:
    rows: list[dict] = []
    for blob in iter_blobs(request):
        if "<message" not in blob:
            continue
        for match in MSG_RE.finditer(blob):
            attrs = {key: _unescape(a or b) for key, a, b in ATTR_RE.findall(match.group(1))}
            msg_id = attrs.get("msg_id") or ""
            if not msg_id or "{" in msg_id or msg_id in {"id", "{id}"}:
                continue
            user = attrs.get("user") or ""
            card = attrs.get("group_card") or None
            body = clean_body(_unescape(match.group(2)))
            rows.append(
                {
                    "msg_id": msg_id,
                    "quote": attrs.get("quote") or None,
                    "time": attrs.get("time") or "",
                    "user": user,
                    "card": card,
                    "self": attrs.get("is_self_message") == "true" or user in BOT,
                    "text": body,
                }
            )
    return rows


def assign_times(rows: list[dict]) -> None:
    last_abs: int | None = None
    last_sec: int | None = None
    day = 0
    for row in rows:
        sec = parse_time(row["time"])
        if sec is None:
            abs_t = (last_abs + 1) if last_abs is not None else 0
            row["t"] = abs_t
            last_abs = abs_t
            continue
        if last_sec is not None and sec + 6 * 3600 < last_sec:
            day += 1
        abs_t = day * 86400 + sec
        if last_abs is not None and abs_t < last_abs:
            abs_t = last_abs
        row["t"] = abs_t
        last_abs = abs_t
        last_sec = sec


def rewrite_forward_preview_speakers(text: str) -> str:
    if "转发消息" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_forward = False
    for line in lines:
        if "转发消息" in line:
            in_forward = True
            out.append(line)
            continue
        if in_forward:
            stripped = line.strip()
            if stripped.startswith("预览") or stripped.startswith("[消息类型]"):
                out.append(line)
                continue
            idx = line.find("：")
            if idx > 0:
                name = line[:idx]
                if name not in ALLOWED_FORWARD_SPEAKERS and not name.startswith("["):
                    line = fake_card(name, None) + line[idx:]
        out.append(line)
    return "\n".join(out)


def masquerade(rows: list[dict]) -> None:
    group = any(row["card"] for row in rows)
    nick_map: dict[str, str] = {}
    for row in rows:
        if row["user"] in BOT or row["self"]:
            continue
        fake = fake_card(row["user"], row["card"])
        if row["card"]:
            nick_map[row["card"]] = fake
        if row["user"] and len(row["user"]) >= 2:
            nick_map.setdefault(row["user"], fake)

    nick_keys = sorted(nick_map, key=len, reverse=True)

    def rewrite(text: str) -> str:
        out = text.replace("菜包", "麦麦")
        for old in nick_keys:
            fake = nick_map[old]
            if len(old) == 1:
                out = out.replace(f"@{old}", f"@{fake}")
            else:
                out = out.replace(f"@{old}", f"@{fake}")
                out = out.replace(old, fake)
        out = rewrite_forward_preview_speakers(out)
        out = URL_RE.sub("[链接]", out)
        out = ID_RE.sub("[id]", out)
        for needle in DENY:
            if needle in {"http://", "https://"}:
                continue
            if needle in out:
                out = out.replace(needle, fake_card(needle, None))
        return out

    for row in rows:
        original_text = row["text"]
        row["text"] = rewrite(row["text"])
        if row["self"] or row["user"] in BOT:
            row["user"] = "麦麦"
            row["card"] = None
            row["self"] = True
            if len(original_text) > SELF_MAX_CHARS:
                row["text"] = SHORT_SELF[_sha_int(original_text) % len(SHORT_SELF)]
            continue
        card = fake_card(row["user"], row["card"])
        if group:
            row["user"] = fake_user(row["user"], row["card"])
            row["card"] = card
        else:
            row["user"] = card
            row["card"] = None


def to_messages(rows: list[dict]) -> list[M]:
    assign_times(rows)
    masquerade(rows)
    id_map: dict[str, str] = {}
    n = 0
    for row in rows:
        n += 1
        new_id = f"m{n}"
        id_map[row["msg_id"]] = new_id
        row["msg_id"] = new_id
    for row in rows:
        if row["quote"]:
            row["quote"] = id_map.get(row["quote"])
    msgs = [
        M(
            t=int(row["t"]),
            msg_id=row["msg_id"],
            user=row["user"],
            text=row["text"],
            card=row["card"],
            quote=row["quote"],
            self_msg=bool(row["self"]),
        )
        for row in rows
        if (row["text"] or row["self"])
    ]
    visible = counted(msgs)
    if not visible:
        return []
    origin = visible[0].t
    for message in msgs:
        message.t -= origin
    return msgs


def decode_request(stem: str) -> dict | None:
    path = LOGS / f"{stem}.json.zst"
    if not path.exists():
        return None
    try:
        raw = subprocess.check_output(["zstd", "-dc", str(path)], stderr=subprocess.DEVNULL)
        data = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    req = data.get("request")
    return req if isinstance(req, dict) else None


def index_rows() -> list[tuple[int, str, str, str]]:
    path = LOGS / "index.tsv"
    rows: list[tuple[int, str, str, str]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        handle.readline()
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            preview = parts[11]
            if "你是规划器模块" not in preview and "你是回复器模块" not in preview:
                continue
            try:
                tokens = int(parts[7] or 0)
            except ValueError:
                tokens = 0
            kind = "planner" if "你是规划器模块" in preview else "replyer"
            rows.append((tokens, parts[0], parts[1][:10], kind))
    return rows


def sample_stems(rows: list[tuple[int, str, str, str]]) -> list[tuple[str, str]]:
    by_date: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for row in rows:
        if row[3] != "planner":
            continue
        by_date[row[2]].append(row)
    chosen: list[tuple[str, str]] = []
    seen: set[str] = set()
    for date, items in sorted(by_date.items()):
        items = sorted(items, reverse=True)
        sized: list[tuple[int, str, str, str]] = []
        for item in items:
            zst = LOGS / f"{item[1]}.json.zst"
            try:
                size = zst.stat().st_size
            except OSError:
                continue
            if 0 < size < 2_000_000:
                sized.append(item)
        mid = [x for x in sized if 12_000 <= x[0] <= 100_000]
        small = [x for x in sized if 8_000 <= x[0] <= 25_000]
        picks = mid[:5] + small[:2] + sized[:2]
        for item in picks:
            if item[1] in seen:
                continue
            seen.add(item[1])
            chosen.append((item[1], item[2]))
    return chosen


def near_dup(ids: list[str], pools: list[set[str]]) -> bool:
    current = set(ids)
    if not current:
        return True
    for other in pools:
        overlap = len(current & other) / max(1, min(len(current), len(other)))
        if overlap >= 0.8:
            return True
    return False


def pick_channel(cands: list[tuple[int, str, list[M]]], want: int, min_sum: int) -> list[tuple[str, list[M]]]:
    cands = sorted(cands, key=lambda item: -item[0])
    chosen: list[tuple[str, list[M]]] = []
    per_date: Counter[str] = Counter()
    for n, date, msgs in cands:
        if per_date[date] >= 2:
            continue
        chosen.append((date, msgs))
        per_date[date] += 1
        total = sum(len(counted(m)) for _, m in chosen)
        if len(chosen) >= want and total >= min_sum:
            return chosen
    for n, date, msgs in cands:
        if any(m is msgs for _, m in chosen):
            continue
        chosen.append((date, msgs))
        total = sum(len(counted(m)) for _, m in chosen)
        if len(chosen) >= max(4, want) and total >= min_sum:
            break
    return chosen


def assert_clean(tapes: list[tuple[str, str, list[M]]]) -> None:
    blob = json.dumps(
        [m.to_json() for _, _, msgs in tapes for m in msgs],
        ensure_ascii=False,
    )
    for needle in DENY:
        if needle in blob:
            raise SystemExit(f"deny-list leak: {needle}")


def write_tapes(tapes: list[tuple[str, str, list[M]]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for tape_id, channel, msgs in tapes:
        payload = {
            "id": tape_id,
            "channel": channel,
            "messages": [m.to_json() for m in msgs],
        }
        (OUT / f"{tape_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: extract_tapes.py <maiGoLLMRouter-logs-dir>", file=sys.stderr)
        return 2
    global LOGS
    LOGS = Path(args[0])
    if not LOGS.is_dir():
        print(f"not a directory: {LOGS}", file=sys.stderr)
        return 2
    stems = sample_stems(index_rows())
    groups: list[tuple[int, str, list[M]]] = []
    privates: list[tuple[int, str, list[M]]] = []
    seen_group: list[set[str]] = []
    seen_private: list[set[str]] = []
    for stem, date in stems:
        request = decode_request(stem)
        if not request:
            continue
        rows = parse_messages(request)
        if len(rows) < 20:
            continue
        ids = [row["msg_id"] for row in rows]
        channel = "group" if any(row["card"] for row in rows) else "private"
        pool = seen_group if channel == "group" else seen_private
        if near_dup(ids, pool):
            continue
        msgs = to_messages(rows)
        n = len(counted(msgs))
        if n < 20:
            continue
        pool.append(set(ids))
        bucket = groups if channel == "group" else privates
        bucket.append((n, date, msgs))

    picked_g = pick_channel(groups, want=8, min_sum=80)
    picked_p = pick_channel(privates, want=8, min_sum=120)
    tapes: list[tuple[str, str, list[M]]] = []
    for i, (date, msgs) in enumerate(picked_g, start=1):
        stamp = date.replace("-", "")
        tapes.append((f"group-{stamp}-{i:02d}", "group", msgs))
    for i, (date, msgs) in enumerate(picked_p, start=1):
        stamp = date.replace("-", "")
        tapes.append((f"private-{stamp}-{i:02d}", "private", msgs))
    if len([t for t in tapes if t[1] == "group"]) < 4 or len([t for t in tapes if t[1] == "private"]) < 4:
        raise SystemExit(
            f"not enough tapes: group={sum(1 for t in tapes if t[1]=='group')} "
            f"private={sum(1 for t in tapes if t[1]=='private')} "
            f"(raw unique group={len(groups)} private={len(privates)})"
        )
    assert_clean(tapes)
    write_tapes(tapes)
    gsum = sum(len(counted(m)) for _, ch, m in tapes if ch == "group")
    psum = sum(len(counted(m)) for _, ch, m in tapes if ch == "private")
    print(
        f"wrote {len(tapes)} tapes "
        f"({sum(1 for t in tapes if t[1]=='group')} group / {gsum} counted, "
        f"{sum(1 for t in tapes if t[1]=='private')} private / {psum} counted) "
        f"from {len(stems)} sampled logs"
    )
    for tape_id, channel, msgs in tapes:
        lengths = [len(m.text) for m in msgs if not m.self_msg]
        hi = max(lengths) if lengths else 0
        print(f"  {tape_id:28} {channel:7} n={len(counted(msgs)):3} max_human={hi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
