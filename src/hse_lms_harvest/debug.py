from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .privacy import redact_url
from .text import stable_slug

URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\"']+")
SENSITIVE_HEADER_RE = re.compile(
    r"(?im)^(\s*-\s*)?(cookie|set-cookie|authorization|proxy-authorization):.*$"
)
MOODLE_SESSION_RE = re.compile(r"MoodleSession=[^;\s]+")


@dataclass(frozen=True)
class ScreenshotPolicy:
    mode: str = "key"
    image_type: str = "jpeg"
    quality: int = 55
    retain: int = 40

    def should_save(self, kind: str) -> bool:
        if self.mode == "off":
            return False
        if self.mode == "every-page":
            return True
        if self.mode == "key":
            return kind in {"key", "error"}
        if self.mode == "on-error":
            return kind == "error"
        return False


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def log(self, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


class DiagnosticRecorder:
    def __init__(
        self,
        debug_dir: Path,
        logger: RunLogger,
        screenshots: ScreenshotPolicy,
        *,
        dump_mode: str = "on-error",
        text_limit: int = 6_000,
    ) -> None:
        self.debug_dir = debug_dir
        self.logger = logger
        self.screenshots = screenshots
        self.dump_mode = dump_mode
        self.text_limit = max(0, text_limit)
        self.events_path = debug_dir / "events.jsonl"
        self.errors_path = debug_dir / "errors.json"
        self.errors_markdown_path = debug_dir / "errors.md"
        self.errors_dir = debug_dir / "errors"
        self.errors: list[dict[str, Any]] = []
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.events_path.write_text("", encoding="utf-8")
        self.write_errors()

    def event(
        self,
        level: str,
        code: str,
        message: str,
        *,
        page_index: int | None = None,
        url: str = "",
        details: dict[str, Any] | None = None,
        artifacts: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "code": code,
            "message": message,
            "page_index": page_index,
            "url": safe_url(url) if url else "",
            "details": sanitize_debug_value(details or {}),
            "artifacts": artifacts or {},
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    async def error(
        self,
        code: str,
        message: str,
        *,
        page: Page | None = None,
        page_index: int | None = None,
        url: str = "",
        exc: BaseException | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error_slug = stable_slug(code.replace("_", "-"), fallback="error")
        error_id = f"{len(self.errors) + 1:04d}-{error_slug}"
        error_dir = self.errors_dir / error_id
        error_dir.mkdir(parents=True, exist_ok=True)

        artifacts: dict[str, str] = {}
        resolved_url = url
        if page is not None:
            resolved_url = resolved_url or page.url
            screenshot = await save_screenshot(
                page,
                self.debug_dir,
                error_id,
                self.logger,
                self.screenshots,
                kind="error",
            )
            if screenshot:
                artifacts["screenshot"] = relative_debug_path(self.debug_dir, Path(screenshot))

            if self.dump_mode in {"on-error", "verbose"}:
                state_path = error_dir / "page-state.json"
                state = await capture_page_state(page, self.text_limit)
                write_debug_json(state_path, sanitize_debug_value(state))
                artifacts["page_state"] = relative_debug_path(self.debug_dir, state_path)

            if self.dump_mode == "verbose":
                html_path = error_dir / "page.html"
                html = await capture_page_html(page)
                html_path.write_text(html, encoding="utf-8")
                artifacts["html"] = relative_debug_path(self.debug_dir, html_path)

        error_json_path = error_dir / "error.json"
        artifacts["error"] = relative_debug_path(self.debug_dir, error_json_path)
        entry = {
            "id": error_id,
            "time": datetime.now().isoformat(timespec="seconds"),
            "code": code,
            "message": message,
            "page_index": page_index,
            "url": safe_url(resolved_url) if resolved_url else "",
            "exception": exception_summary(exc),
            "details": sanitize_debug_value(details or {}),
            "artifacts": artifacts,
        }
        write_debug_json(error_json_path, entry)
        self.errors.append(entry)
        self.write_errors()
        self.event(
            "error",
            code,
            message,
            page_index=page_index,
            url=resolved_url,
            details=entry["details"],
            artifacts=artifacts,
        )
        self.logger.log(f"error recorded {error_id}: {message}")
        return entry

    def warning(
        self,
        code: str,
        message: str,
        *,
        page_index: int | None = None,
        url: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.event(
            "warning",
            code,
            message,
            page_index=page_index,
            url=url,
            details=details,
        )

    def write_errors(self) -> None:
        write_debug_json(self.errors_path, self.errors)
        lines = [
            "# Debug Errors",
            "",
            "This file is a compact index. Full error bundles live under `debug/errors/`.",
            "",
        ]
        if not self.errors:
            lines.append("No errors recorded.")
        else:
            for item in self.errors:
                lines.append(f"## {item['id']}")
                lines.append(f"- Code: {item['code']}")
                if item.get("page_index") is not None:
                    lines.append(f"- Page: {item['page_index']}")
                if item.get("url"):
                    lines.append(f"- URL: {item['url']}")
                lines.append(f"- Message: {item['message']}")
                exception = item.get("exception") or {}
                if exception:
                    lines.append(
                        f"- Exception: {exception.get('type')}: {exception.get('message')}"
                    )
                artifacts = item.get("artifacts") or {}
                if artifacts:
                    artifact_links = ", ".join(
                        f"[{name}](<{path}>)" for name, path in sorted(artifacts.items())
                    )
                    lines.append(f"- Artifacts: {artifact_links}")
                lines.append("")
        self.errors_markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def save_screenshot(
    page: Page,
    debug_dir: Path,
    label: str,
    logger: RunLogger,
    policy: ScreenshotPolicy,
    *,
    kind: str = "key",
) -> str | None:
    if not policy.should_save(kind):
        return None

    screenshots_dir = debug_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    extension = "jpg" if policy.image_type == "jpeg" else "png"
    name = f"{stamp}-{stable_slug(label, fallback='page')}.{extension}"
    path = screenshots_dir / name
    try:
        kwargs = {
            "path": str(path),
            "full_page": False,
            "type": policy.image_type,
            "timeout": 5_000,
        }
        if policy.image_type == "jpeg":
            kwargs["quality"] = policy.quality
        await page.screenshot(**kwargs)
    except PlaywrightError as exc:
        logger.log(f"screenshot failed: {safe_error(exc)}")
        return None
    logger.log(f"screenshot saved: {path}")
    trim_screenshots(screenshots_dir, policy.retain)
    return str(path)


async def capture_page_state(page: Page, text_limit: int) -> dict[str, Any]:
    script = """
    (textLimit) => {
      const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
      };
      const active = document.activeElement;
      return {
        url: location.href,
        title: document.title,
        ready_state: document.readyState,
        heading: clean(document.querySelector('h1')?.innerText || document.querySelector('[role="heading"]')?.innerText),
        visible_text: (document.body?.innerText || '').slice(0, textLimit),
        active_element: active ? {
          tag: active.tagName?.toLowerCase(),
          id: active.id || '',
          name: active.getAttribute('name') || '',
          type: active.getAttribute('type') || '',
          text: clean(active.innerText || active.value || active.getAttribute('aria-label') || active.getAttribute('title')),
        } : null,
        buttons: Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a[role="button"]'))
          .filter(visible)
          .slice(0, 50)
          .map((element) => ({
            text: clean(element.innerText || element.value || element.getAttribute('aria-label') || element.getAttribute('title')),
            tag: element.tagName.toLowerCase(),
            disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
          })),
        links: Array.from(document.querySelectorAll('a[href]'))
          .filter(visible)
          .slice(0, 80)
          .map((element) => ({
            text: clean(element.innerText || element.textContent || element.getAttribute('title') || element.getAttribute('aria-label')),
            href: element.href,
          })),
        forms: Array.from(document.querySelectorAll('form'))
          .slice(0, 20)
          .map((element) => ({
            action: element.action || '',
            method: element.method || '',
            text: clean(element.innerText).slice(0, 500),
          })),
      };
    }
    """
    try:
        return await page.evaluate(script, text_limit)
    except PlaywrightError as exc:
        return {"capture_error": exception_summary(exc), "url": safe_url(page.url)}


async def capture_page_html(page: Page) -> str:
    try:
        return await page.content()
    except PlaywrightError as exc:
        return f"<!-- page html capture failed: {exception_summary(exc)} -->\n"


def exception_summary(exc: BaseException | None) -> dict[str, str]:
    if exc is None:
        return {}
    return {"type": type(exc).__name__, "message": safe_error(exc)}


def safe_error(exc: BaseException) -> str:
    return redact_string(str(exc))


def sanitize_debug_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [sanitize_debug_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_debug_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_debug_value(item) for key, item in value.items()}
    return value


def redact_string(value: str) -> str:
    value = URL_IN_TEXT_RE.sub(lambda match: safe_url(match.group(0)), value)
    value = SENSITIVE_HEADER_RE.sub(
        lambda match: f"{match.group(1) or ''}{match.group(2)}: [REDACTED]",
        value,
    )
    return MOODLE_SESSION_RE.sub("MoodleSession=[REDACTED]", value)


def write_debug_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def relative_debug_path(debug_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(debug_dir))
    except ValueError:
        return str(path)


def trim_screenshots(screenshots_dir: Path, retain: int) -> None:
    if retain <= 0:
        return
    files = sorted(
        [path for path in screenshots_dir.iterdir() if path.is_file()],
        key=lambda path: path.stat().st_mtime,
    )
    for path in files[:-retain]:
        path.unlink(missing_ok=True)


def safe_url(url: str) -> str:
    return redact_url(url)
