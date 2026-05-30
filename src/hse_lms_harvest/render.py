from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .model import Button, Link, PageCapture
from .text import stable_slug


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


LinkKey = tuple[str, str, str]

MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((?:https?://|mailto:|/)[^)]+\)")
RAW_URL_RE = re.compile(r"<?https?://\S+>?")
SMART_LMS_TITLE_RE = re.compile(r"\s*\|\s*Smart LMS\s*$", re.IGNORECASE)
ACTIVE_ELEMENTS_RE = re.compile(r"^активные элементы:\s*\d+$", re.IGNORECASE)
COMMENT_COUNTER_RE = re.compile(r"^комментарии\s*\(\d+\)$", re.IGNORECASE)

LOW_SIGNAL_LABELS = {
    "0 непрочитанных бесед: 0",
    "label",
    "close",
    "ки",
    "курс",
    "оценки",
    "свернуть",
    "справка",
    "искать",
    "инструкции",
    "участники",
    "компетенции",
    "учебные подразделения",
    "закрыть оглавление курса",
    "варианты оглавления курса",
    "перейти к основному содержанию",
}

LOW_SIGNAL_PREFIXES = (
    "перейти в секцию ",
    "skip to ",
)

LOW_SIGNAL_TEXT_LINES = {
    "редактировать ответ",
    "удалить ответ",
    "добавить запись",
    "... экспорт записей",
    "показать больше",
    "открыть боковую панель",
}

CONTEXT_BUTTON_PREFIXES = (
    "добавить ответ",
    "add submission",
)


def write_page_files(
    out_dir: Path, page: PageCapture, suppressed_links: set[LinkKey] | None = None
) -> None:
    suppressed_links = suppressed_links or set()
    name = page_file_stem(page)
    page_dir = out_dir / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)

    write_json(page_dir / f"{name}.json", asdict(page))

    text_lines = compact_text_lines(page.unique_text_lines or page.text_lines)
    lines = [
        f"# {page_markdown_heading(page)}",
        "",
        "## Text",
        "",
    ]
    lines.extend(text_lines)

    visible_links = visible_links_for_markdown(page, suppressed_links, text_lines)
    if visible_links:
        lines.extend(["", "## Links", ""])
        lines.extend(link_markdown_line(link) for link in visible_links)

    button_lines = button_markdown_lines(page.buttons)
    if button_lines:
        lines.extend(["", "## Buttons", ""])
        lines.extend(button_lines)

    downloaded_files = compact_downloaded_files(page.downloaded_files)
    if downloaded_files:
        lines.extend(["", "## Downloaded files", ""])
        lines.extend(f"- {item}" for item in downloaded_files)

    if page.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in page.errors)

    (page_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(
    out_dir: Path,
    pages: list[PageCapture],
    common_lines: set[str],
    common_links: set[LinkKey] | None = None,
) -> None:
    common_links = common_links or set()
    lines = [
        "# LMS Harvest Summary",
        "",
        f"- Pages: {len(pages)}",
        "",
        "## Pages",
        "",
    ]
    for page in pages:
        heading = page_markdown_heading(page)
        suffixes = []
        downloaded_files = compact_downloaded_files(page.downloaded_files)
        if downloaded_files:
            suffixes.append(f"files: {len(downloaded_files)}")
        if page.errors:
            suffixes.append(f"errors: {len(page.errors)}")
        suffix = f" ({', '.join(suffixes)})" if suffixes else ""
        lines.append(f"- {heading}{suffix}")

    assignment_pages = [
        page
        for page in pages
        if any(
            marker in " ".join(page.text_lines).lower()
            for marker in ("срок сдачи", "состояние ответа", "требуемые условия завершения")
        )
    ]
    if assignment_pages:
        lines.extend(["", "## Likely Assignments", ""])
        for page in assignment_pages:
            lines.append(f"### {page_markdown_heading(page)}")
            for text_line in compact_text_lines(page.unique_text_lines[:80]):
                lower = text_line.lower()
                if any(
                    marker in lower
                    for marker in (
                        "срок сдачи",
                        "требуемые условия",
                        "состояние ответа",
                        "состояние оценивания",
                        "последнее изменение",
                    )
                ):
                    lines.append(f"- {text_line}")

    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_navigation(
    out_dir: Path,
    pages: list[PageCapture],
    common_links: set[LinkKey] | None = None,
) -> None:
    navigation = navigation_data(pages, common_links)
    write_json(out_dir / "navigation.json", navigation)

    lines = [
        "# LMS Navigation",
        "",
        f"- Pages: {navigation['page_count']}",
        f"- Files: {navigation['file_count']}",
        "",
        "## Page Tree",
        "",
    ]
    if navigation["tree"]:
        append_navigation_nodes(lines, navigation["tree"])
    else:
        lines.append("- No captured pages")

    if navigation["files"]:
        lines.extend(["", "## Files", ""])
        current_page = ""
        for item in navigation["files"]:
            page_title = str(item["page"])
            if page_title != current_page:
                lines.append(f"### {page_title}")
                current_page = page_title
            path = str(item["path"])
            lines.append(f"- {markdown_link(Path(path).name, path)}")

    (out_dir / "navigation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def navigation_data(
    pages: list[PageCapture],
    common_links: set[LinkKey] | None = None,
) -> dict[str, object]:
    common_links = common_links or set()
    page_by_url = page_url_index(pages)
    children_by_parent: dict[int, list[int]] = {page.index: [] for page in pages}
    parent_by_child: dict[int, int] = {}

    for page in pages:
        for link in page.links:
            if link_key(link) in common_links:
                continue
            target = page_by_url.get(url_without_fragment(link.url))
            if target is None or target.index <= page.index or target.index in parent_by_child:
                continue
            parent_by_child[target.index] = page.index
            children_by_parent[page.index].append(target.index)

    page_by_index = {page.index: page for page in pages}
    roots = [page for page in pages if page.index not in parent_by_child]
    tree = [navigation_node(page, page_by_index, children_by_parent) for page in roots]
    files = [
        {"page": page_markdown_heading(page), "path": item}
        for page in pages
        for item in compact_downloaded_files(page.downloaded_files)
    ]
    return {
        "page_count": len(pages),
        "file_count": len(files),
        "tree": tree,
        "files": files,
    }


def page_url_index(pages: list[PageCapture]) -> dict[str, PageCapture]:
    indexed: dict[str, PageCapture] = {}
    for page in pages:
        for url in (page.url, page.final_url):
            key = url_without_fragment(url)
            if key:
                indexed[key] = page
    return indexed


def navigation_node(
    page: PageCapture,
    page_by_index: dict[int, PageCapture],
    children_by_parent: dict[int, list[int]],
) -> dict[str, object]:
    files = compact_downloaded_files(page.downloaded_files)
    return {
        "title": page_markdown_heading(page),
        "md": page_markdown_path(page),
        "json": page_json_path(page),
        "files": files,
        "errors": len(page.errors),
        "children": [
            navigation_node(page_by_index[index], page_by_index, children_by_parent)
            for index in children_by_parent.get(page.index, [])
        ],
    }


def append_navigation_nodes(
    lines: list[str],
    nodes: list[dict[str, object]],
    *,
    depth: int = 0,
) -> None:
    indent = "  " * depth
    for node in nodes:
        suffixes = []
        files = node["files"]
        if isinstance(files, list) and files:
            suffixes.append(f"files: {len(files)}")
        if node["errors"]:
            suffixes.append(f"errors: {node['errors']}")
        suffix = f" ({', '.join(suffixes)})" if suffixes else ""
        title = str(node["title"])
        md_path = str(node["md"])
        json_path = str(node["json"])
        lines.append(
            f"{indent}- {markdown_link(title, md_path)} · json: {markdown_link('json', json_path)}{suffix}"
        )
        children = node["children"]
        if isinstance(children, list) and children:
            append_navigation_nodes(lines, children, depth=depth + 1)


def common_link_keys(pages: list[PageCapture], *, min_share: float = 0.6) -> set[LinkKey]:
    page_link_sets = [set(link_key(link) for link in page.links) for page in pages]
    if len(page_link_sets) < 3:
        return set()

    counts: dict[LinkKey, int] = {}
    for links in page_link_sets:
        for key in links:
            counts[key] = counts.get(key, 0) + 1

    threshold = max(3, int(len(page_link_sets) * min_share))
    return {key for key, count in counts.items() if count >= threshold}


def link_key(link: Link) -> LinkKey:
    label = link.text or link.title or link.url
    return link.kind, label, link.url


def page_file_stem(page: PageCapture) -> str:
    return f"{page.index:04d}-{stable_slug(page.heading or page.title or page.final_url)}"


def page_markdown_path(page: PageCapture) -> str:
    return f"pages/{page_file_stem(page)}.md"


def page_json_path(page: PageCapture) -> str:
    return f"pages/{page_file_stem(page)}.json"


def url_without_fragment(url: str) -> str:
    return url.split("#", 1)[0]


def markdown_link(label: str, path: str) -> str:
    return f"[{escape_markdown_link_label(label)}](<{path}>)"


def escape_markdown_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def link_markdown_line(link: Link) -> str:
    _, label, _ = link_key(link)
    return f"- {clean_markdown_label(label)}"


def page_markdown_heading(page: PageCapture) -> str:
    heading = clean_markdown_label(page.heading or page.title)
    return heading or "Страница LMS"


def compact_text_lines(lines: list[str]) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for value in lines:
        line = compact_markdown_text(value)
        if not line or line in seen or is_low_signal_text_line(line):
            continue
        compacted.append(line)
        seen.add(line)
    return compacted


def compact_markdown_text(value: str) -> str:
    value = MARKDOWN_LINK_RE.sub(
        lambda match: unescape_markdown_link_label(match.group(1)),
        value,
    )
    value = RAW_URL_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(" :-")
    value = re.sub(r"^перейти в секцию\s+", "", value, flags=re.IGNORECASE)
    return value


def clean_markdown_label(value: str) -> str:
    value = compact_markdown_text(value)
    value = SMART_LMS_TITLE_RE.sub("", value).strip()
    return value


def unescape_markdown_link_label(value: str) -> str:
    return value.replace(r"\[", "[").replace(r"\]", "]").replace(r"\\", "\\")


def visible_links_for_markdown(
    page: PageCapture,
    suppressed_links: set[LinkKey],
    text_lines: list[str],
) -> list[Link]:
    text_blob = "\n".join(text_lines).casefold()
    links: list[Link] = []
    seen_labels: set[str] = set()
    for link in page.links:
        if link_key(link) in suppressed_links:
            continue
        label = clean_markdown_label(link.text or link.title or link.url)
        label_key = label.casefold()
        if not label or label_key in seen_labels or is_low_signal_label(label):
            continue
        if label_key in text_blob:
            continue
        links.append(link)
        seen_labels.add(label_key)
    return links


def is_low_signal_label(label: str) -> bool:
    lower = label.casefold().strip()
    if lower in LOW_SIGNAL_LABELS:
        return True
    if ACTIVE_ELEMENTS_RE.match(lower) or COMMENT_COUNTER_RE.match(lower):
        return True
    if lower.startswith(("http://", "https://")):
        return True
    if lower.isdigit():
        return True
    return any(lower.startswith(prefix) for prefix in LOW_SIGNAL_PREFIXES)


def is_low_signal_text_line(line: str) -> bool:
    lower = line.casefold().strip()
    if lower in LOW_SIGNAL_LABELS or lower in LOW_SIGNAL_TEXT_LINES:
        return True
    if ACTIVE_ELEMENTS_RE.match(lower) or COMMENT_COUNTER_RE.match(lower):
        return True
    return lower.startswith("skip to ")


def button_markdown_lines(buttons: list[Button]) -> list[str]:
    lines: list[str] = []
    seen_labels: set[str] = set()
    for button in buttons:
        label = clean_markdown_label(button.text)
        label_key = label.casefold()
        if (
            not label
            or label_key in seen_labels
            or is_low_signal_label(label)
            or not is_context_button(label)
        ):
            continue
        status = " (disabled)" if button.disabled else ""
        lines.append(f"- {label}{status}")
        seen_labels.add(label_key)
    return lines


def is_context_button(label: str) -> bool:
    lower = label.casefold().strip()
    return any(lower.startswith(prefix) for prefix in CONTEXT_BUTTON_PREFIXES)


def compact_downloaded_files(items: list[str]) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for item in items:
        compacted_item = compact_downloaded_file(item)
        if compacted_item and compacted_item not in seen:
            compacted.append(compacted_item)
            seen.add(compacted_item)
    return compacted


def compact_downloaded_file(item: str) -> str:
    if item.startswith("SKIP "):
        return ""
    item = item.removeprefix("CACHED ").strip()
    item = item.split(" sha256:", 1)[0].strip()
    item = item.split(" source:", 1)[0].strip()
    if item.startswith("files/"):
        return item
    return compact_markdown_text(item)
