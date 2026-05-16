from __future__ import annotations

import re
from collections.abc import Iterable

SPACE_RE = re.compile(r"[ \t\u00a0]+")
BLANK_RE = re.compile(r"\n{3,}")


def normalize_line(value: str) -> str:
    value = value.replace("\r", "\n")
    value = SPACE_RE.sub(" ", value)
    return value.strip()


def split_visible_text(value: str) -> list[str]:
    normalized = BLANK_RE.sub("\n\n", value.replace("\r", "\n"))
    lines: list[str] = []
    seen_consecutive = ""
    for raw_line in normalized.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue
        if line == seen_consecutive:
            continue
        lines.append(line)
        seen_consecutive = line
    return lines


def stable_slug(value: str, *, fallback: str = "page", max_len: int = 80) -> str:
    value = value.lower().strip()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^0-9a-zа-яё._-]+", "-", value, flags=re.IGNORECASE)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    if not value:
        value = fallback
    return value[:max_len].strip("-._") or fallback


def common_lines(page_lines: Iterable[Iterable[str]], *, min_share: float = 0.6) -> set[str]:
    pages = [set(lines) for lines in page_lines]
    if len(pages) < 3:
        return set()

    counts: dict[str, int] = {}
    for lines in pages:
        for line in lines:
            counts[line] = counts.get(line, 0) + 1

    threshold = max(3, int(len(pages) * min_share))
    return {
        line
        for line, count in counts.items()
        if count >= threshold and not looks_like_task_specific_line(line)
    }


def looks_like_task_specific_line(line: str) -> bool:
    lower = line.lower()
    important_markers = (
        "срок сдачи",
        "требуемые условия",
        "состояние ответа",
        "состояние оценивания",
        "последнее изменение",
        "задание",
        "вариантный сектор",
        "парные сравнения",
        "тз",
        "техническое задание",
    )
    return any(marker in lower for marker in important_markers)
