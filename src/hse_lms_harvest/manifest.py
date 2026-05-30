from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .classify import is_ignored_capture_url
from .model import Button, Link, PageCapture
from .render import (
    common_link_keys,
    compact_downloaded_files,
    write_navigation,
    write_page_files,
    write_summary,
)
from .text import common_lines

FORMAT_VERSION = 2
PAGE_METADATA_KEYS = ("etag", "last-modified", "content-length", "content-type")
MARKDOWN_LINK_TARGET_RE = re.compile(r"\[(?P<label>[^\]\n]*)\]\((?P<url>[^)\n]*)\)")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_paths(target: Path) -> list[Path]:
    target = target.expanduser().resolve()
    if target.is_file() and target.name == "manifest.json":
        return [target]
    if (target / "manifest.json").is_file():
        return [target / "manifest.json"]
    if not target.exists():
        return []
    return sorted(target.glob("**/manifest.json"))


def latest_manifest_path(root: Path, *, exclude: Path | None = None) -> Path | None:
    root = root.expanduser().resolve()
    exclude = exclude.expanduser().resolve() if exclude is not None else None
    candidates = []
    for path in manifest_paths(root):
        if exclude is not None and exclude in path.parents:
            continue
        candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def pages_from_manifest(manifest: dict[str, Any]) -> list[PageCapture]:
    pages = manifest.get("pages") or []
    return [page_from_data(item) for item in pages if isinstance(item, dict)]


def page_from_data(data: dict[str, Any]) -> PageCapture:
    return PageCapture(
        index=int(data.get("index") or 0),
        url=str(data.get("url") or ""),
        final_url=str(data.get("final_url") or data.get("url") or ""),
        title=str(data.get("title") or ""),
        heading=str(data.get("heading") or ""),
        text_lines=prune_ignored_text_lines(data.get("text_lines") or []),
        unique_text_lines=prune_ignored_text_lines(data.get("unique_text_lines") or []),
        links=[
            link_from_data(item)
            for item in data.get("links") or []
            if isinstance(item, dict) and not should_prune_link_data(item)
        ],
        buttons=[
            button_from_data(item) for item in data.get("buttons") or [] if isinstance(item, dict)
        ],
        downloaded_files=[
            str(item)
            for item in data.get("downloaded_files") or []
            if not should_prune_downloaded_file(str(item))
        ],
        errors=[str(item) for item in data.get("errors") or []],
        source_metadata={
            str(key): str(value)
            for key, value in (data.get("source_metadata") or {}).items()
            if value is not None
        },
        content_fingerprint=str(data.get("content_fingerprint") or ""),
        reused_from=str(data.get("reused_from") or ""),
    )


def prune_ignored_text_lines(items: list[Any]) -> list[str]:
    lines: list[str] = []
    for item in items:
        line = prune_ignored_markdown_links(str(item))
        if line:
            lines.append(line)
    return lines


def prune_ignored_markdown_links(value: str) -> str:
    def replace_link(match: re.Match[str]) -> str:
        target = match.group("url").strip("<>")
        return "" if is_ignored_capture_url(target) else match.group(0)

    cleaned = MARKDOWN_LINK_TARGET_RE.sub(replace_link, value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.rstrip(" :-")


def should_prune_link_data(data: dict[str, Any]) -> bool:
    return is_ignored_capture_url(str(data.get("url") or ""))


def should_prune_downloaded_file(item: str) -> bool:
    if " source:" not in item:
        return is_ignored_capture_url(item)
    source = item.rsplit(" source:", 1)[1].strip()
    return is_ignored_capture_url(source)


def link_from_data(data: dict[str, Any]) -> Link:
    return Link(
        text=str(data.get("text") or ""),
        url=str(data.get("url") or ""),
        kind=str(data.get("kind") or "page"),
        title=str(data.get("title") or ""),
    )


def button_from_data(data: dict[str, Any]) -> Button:
    return Button(
        text=str(data.get("text") or ""),
        kind=str(data.get("kind") or "button"),
        disabled=bool(data.get("disabled")),
        action_url=str(data.get("action_url") or ""),
    )


def page_content_fingerprint(page: PageCapture) -> str:
    payload = {
        "title": page.title,
        "heading": page.heading,
        "text_lines": page.text_lines,
        "links": [asdict(link) for link in page.links],
        "buttons": [asdict(button) for button in page.buttons],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def response_metadata(headers: dict[str, str]) -> dict[str, str]:
    normalized = {key.lower(): value for key, value in headers.items()}
    return {
        key: str(normalized[key])
        for key in PAGE_METADATA_KEYS
        if normalized.get(key) not in {None, ""}
    }


def metadata_has_validators(metadata: dict[str, str]) -> bool:
    return bool(metadata.get("etag") or metadata.get("last-modified"))


def metadata_matches(previous: dict[str, str], current: dict[str, str]) -> bool:
    if previous.get("etag") and current.get("etag"):
        return previous["etag"] == current["etag"]
    if previous.get("last-modified") and current.get("last-modified"):
        if previous["last-modified"] != current["last-modified"]:
            return False
        previous_length = previous.get("content-length")
        current_length = current.get("content-length")
        return not previous_length or not current_length or previous_length == current_length
    return False


def clone_page_for_reuse(page: PageCapture, *, index: int, reused_from: Path) -> PageCapture:
    return replace(
        page,
        index=index,
        unique_text_lines=[],
        reused_from=str(reused_from),
    )


def copy_reused_files(page: PageCapture, previous_dump: Path, current_dump: Path) -> None:
    for item in compact_downloaded_files(page.downloaded_files):
        source = previous_dump / item
        target = current_dump / item
        if not source.is_file() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.hardlink_to(source)
        except OSError:
            shutil.copy2(source, target)


def render_dump_from_manifest(manifest_path: Path) -> None:
    manifest = load_manifest(manifest_path)
    dump_dir = manifest_path.parent
    pages = pages_from_manifest(manifest)
    render_dump(dump_dir, manifest, pages, migrated=True)


def render_dump(
    out_dir: Path,
    manifest: dict[str, Any],
    pages: list[PageCapture],
    *,
    migrated: bool = False,
) -> None:
    repeated = common_lines(page.text_lines for page in pages)
    repeated_links = common_link_keys(pages)
    for page_capture in pages:
        page_capture.unique_text_lines = [
            line for line in page_capture.text_lines if line not in repeated
        ]
        if not page_capture.content_fingerprint:
            page_capture.content_fingerprint = page_content_fingerprint(page_capture)
        write_page_files(out_dir, page_capture, repeated_links)

    manifest_update = {
        "format_version": FORMAT_VERSION,
        "page_count": len(pages),
        "common_line_count": len(repeated),
        "common_link_count": len(repeated_links),
        "common_lines": sorted(repeated),
        "pages": [asdict(page_capture) for page_capture in pages],
    }
    if migrated:
        manifest_update["migrated_at"] = datetime.now().isoformat(timespec="seconds")
    manifest.update(manifest_update)
    write_manifest(out_dir / "manifest.json", manifest)
    write_summary(out_dir, pages, repeated, repeated_links)
    write_navigation(out_dir, pages, repeated_links)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
