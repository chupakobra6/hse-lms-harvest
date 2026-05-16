from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Link:
    text: str
    url: str
    kind: str = "page"
    title: str = ""


@dataclass(frozen=True)
class Button:
    text: str
    kind: str = "button"
    disabled: bool = False
    action_url: str = ""


@dataclass
class PageCapture:
    index: int
    url: str
    final_url: str
    title: str
    heading: str
    text_lines: list[str]
    unique_text_lines: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    downloaded_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_metadata: dict[str, str] = field(default_factory=dict)
    content_fingerprint: str = ""
    reused_from: str = ""
