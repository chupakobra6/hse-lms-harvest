from __future__ import annotations

import argparse
import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from .classify import classify_link, is_ignored_capture_url, looks_like_course_link
from .debug import DiagnosticRecorder, RunLogger, ScreenshotPolicy, safe_error, save_screenshot
from .downloads import download_files
from .file_cache import FileCache
from .manifest import page_content_fingerprint, response_metadata
from .model import Button, Link, PageCapture
from .privacy import has_sensitive_query, redact_url, strip_fragment
from .text import normalize_line, split_visible_text

EXPAND_TEXTS = (
    "Показать ещё",
    "Показать еще",
    "Развернуть",
    "Раскрыть",
    "Expand all",
    "Show more",
)
COMPLETION_TOGGLE_TEXTS = (
    "Отметить как выполненное",
    "Отметить как выполнено",
    "Mark as done",
)
SUBMISSION_FORM_TEXTS = (
    "Добавить ответ на задание",
    "Добавить ответ",
    "Add submission",
)
STATE_CHANGING_PATHS = {
    "/login/logout.php",
    "/mod/forum/subscribe.php",
    "/mod/forum/unsubscribe.php",
}
GLOSSARY_FILTER_QUERY_KEYS = {"hook", "mode", "sortkey", "sortorder"}
NOISY_DETAIL_PATHS = {
    "/mod/glossary/showentry.php",
    "/mod/h5pactivity/report.php",
    "/mod/quiz/review.php",
}
NETWORK_DISCONNECT_MARKERS = (
    "net::ERR_INTERNET_DISCONNECTED",
    "net::ERR_NETWORK_CHANGED",
)

PAGE_SNAPSHOT_SCRIPT = """
() => {
  const isVisible = (element) => {
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const heading =
    clean(document.querySelector('h1')?.innerText) ||
    clean(document.querySelector('[role="heading"]')?.innerText) ||
    clean(document.title);
  const root = document.body || document.documentElement;

  const linkElements = Array.from(document.querySelectorAll('a[href]')).filter(isVisible);
  const links = linkElements.map((element) => ({
    text: clean(element.innerText || element.textContent || element.getAttribute('aria-label')),
    href: element.href,
    title: clean(element.getAttribute('title')),
    role: clean(element.getAttribute('role')),
  }));

  const textWalker = root
    ? document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      const parent = node.parentElement;
      if (!parent || !isVisible(parent)) return NodeFilter.FILTER_REJECT;
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
      })
    : null;
  const chunks = [];
  let current;
  while (textWalker && (current = textWalker.nextNode())) {
    const parentLink = current.parentElement?.closest('a[href]');
    const linkIndex = parentLink ? linkElements.indexOf(parentLink) : -1;
    if (linkIndex >= 0) {
      chunks.push(`⟦HSE_LMS_LINK_${linkIndex}_START⟧${current.nodeValue}⟦HSE_LMS_LINK_${linkIndex}_END⟧`);
    } else {
      chunks.push(current.nodeValue);
    }
  }

  const iconLinks = links
    .map((link, index) => ({...link, index}))
    .filter((link) => !link.text && link.title)
    .map((link) => `⟦HSE_LMS_LINK_${link.index}⟧`);

  const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a[role="button"]'))
    .filter(isVisible)
    .map((element) => ({
      text: clean(element.innerText || element.value || element.getAttribute('aria-label') || element.getAttribute('title')),
      type: clean(element.getAttribute('type')),
      disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
      action: element.href || element.formAction || '',
    }));

  return {title: document.title, heading, text: chunks.concat(iconLinks).join('\\n'), links, buttons};
}
"""

INLINE_LINK_RANGE_RE = re.compile(
    r"⟦HSE_LMS_LINK_(?P<index>\d+)_START⟧(?P<label>.*?)⟦HSE_LMS_LINK_(?P=index)_END⟧",
    flags=re.DOTALL,
)
INLINE_LINK_TOKEN_RE = re.compile(r"⟦HSE_LMS_LINK_(?P<index>\d+)⟧")

EXPAND_SCRIPT = """
(texts) => {
  const wanted = texts.map((value) => value.toLowerCase());
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  let clicked = 0;
  for (const element of candidates) {
    const text = clean(element.innerText || element.textContent || element.getAttribute('aria-label') || element.getAttribute('title'));
    const lowerText = text.toLowerCase();
    if (!text || !wanted.some((needle) => lowerText.includes(needle))) continue;
    if (element.disabled || element.getAttribute('aria-disabled') === 'true') continue;
    element.click();
    clicked += 1;
  }
  return clicked;
}
"""

CLICK_COMPLETION_SCRIPT = """
(texts) => {
  const wanted = texts.map((value) => value.toLowerCase());
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const candidates = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"]'));
  let clicked = 0;
  for (const element of candidates) {
    const text = clean(element.innerText || element.value || element.getAttribute('aria-label') || element.getAttribute('title'));
    const lowerText = text.toLowerCase();
    if (!text || !wanted.some((needle) => lowerText.includes(needle))) continue;
    if (element.disabled || element.getAttribute('aria-disabled') === 'true') continue;
    element.click();
    clicked += 1;
  }
  return clicked;
}
"""

OPEN_SUBMISSION_FORM_SCRIPT = """
(texts) => {
  const wanted = texts.map((value) => value.toLowerCase());
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const blocked = ['edit submission', 'редактировать ответ'];
  const candidates = Array.from(document.querySelectorAll('a[href], button, input[type="button"], input[type="submit"]'));
  for (const element of candidates) {
    const text = clean(element.innerText || element.value || element.getAttribute('aria-label') || element.getAttribute('title'));
    const lowerText = text.toLowerCase();
    if (!text || !wanted.some((needle) => lowerText.includes(needle))) continue;
    if (blocked.some((needle) => lowerText.includes(needle))) continue;
    if (element.disabled || element.getAttribute('aria-disabled') === 'true') continue;
    element.click();
    return {clicked: true, text, href: element.href || element.formAction || ''};
  }
  return {clicked: false};
}
"""


async def capture_page(
    context: BrowserContext,
    page: Page,
    url: str,
    index: int,
    args: argparse.Namespace,
    files_dir: Path,
    debug_dir: Path,
    logger: RunLogger,
    downloaded_urls: set[str],
    screenshots: ScreenshotPolicy,
    file_cache: FileCache | None,
    diagnostics: DiagnosticRecorder,
) -> PageCapture:
    capture = PageCapture(
        index=index,
        url=url,
        final_url=url,
        title="",
        heading="",
        text_lines=[],
    )

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        if response is not None:
            capture.source_metadata = response_metadata(response.headers)
    except PlaywrightError as exc:
        capture.errors.append(f"navigation: {safe_error(exc)}")
        logger.log(f"navigation warning for {redact_url(url)}: {safe_error(exc)}")
        await diagnostics.error(
            "page_navigation_failed",
            "Page navigation failed",
            page=page,
            page_index=index,
            url=url,
            exc=exc,
        )

    network_idle_timeout_ms = network_idle_timeout_for_url(url, args)
    if network_idle_timeout_ms > 0:
        try:
            await page.wait_for_load_state("networkidle", timeout=network_idle_timeout_ms)
        except PlaywrightError as exc:
            logger.log(f"network idle wait skipped for {redact_url(url)}")
            diagnostics.warning(
                "network_idle_timeout",
                "Network idle wait timed out and was skipped",
                page_index=index,
                url=url,
                details={
                    "timeout_ms": network_idle_timeout_ms,
                    "exception": safe_error(exc),
                },
            )

    await expand_read_only_controls(page, network_idle_timeout_ms, logger, diagnostics, index)
    if args.visit_action_pages:
        await open_submission_form_for_reading(
            page, logger, network_idle_timeout_ms, diagnostics, index
        )
        await expand_read_only_controls(page, network_idle_timeout_ms, logger, diagnostics, index)
    if args.allow_state_changes:
        await click_completion_toggles(page, logger, diagnostics, index)
    await wait_for_body(page)
    await save_screenshot(page, debug_dir, f"page-{index:04d}", logger, screenshots, kind="page")

    try:
        data = await page.evaluate(PAGE_SNAPSHOT_SCRIPT)
    except PlaywrightError as exc:
        capture.errors.append(f"snapshot: {safe_error(exc)}")
        logger.log(f"snapshot warning for {redact_url(url)}: {safe_error(exc)}")
        await diagnostics.error(
            "page_snapshot_failed",
            "Page snapshot extraction failed",
            page=page,
            page_index=index,
            url=url,
            exc=exc,
        )
        data = {"title": await page.title(), "heading": "", "text": "", "links": [], "buttons": []}

    final_url = page.url
    capture.final_url = redact_url(final_url) if has_sensitive_query(final_url) else final_url
    capture.title = data.get("title") or await page.title()
    capture.heading = data.get("heading") or capture.title
    raw_links = data.get("links") or []
    linked_text = restore_inline_link_markdown(data.get("text") or "", raw_links, final_url)
    capture.text_lines = split_visible_text(linked_text)
    capture.links = build_links(raw_links, final_url)
    capture.buttons = build_buttons(data.get("buttons") or [])
    capture.content_fingerprint = page_content_fingerprint(capture)

    if args.download_files:
        capture.downloaded_files = await download_files(
            context,
            files_dir,
            capture.links,
            args.url,
            logger,
            downloaded_urls,
            args.download_media,
            args.skip_lms_file_server,
            args.max_file_mb,
            args.download_concurrency,
            args.file_head_timeout_ms,
            args.file_download_timeout_ms,
            args.trust_file_cache,
            file_cache,
            diagnostics,
        )

    return capture


def restore_inline_link_markdown(text: str, raw_links: list[dict[str, str]], base_url: str) -> str:
    def markdown_link(index: int, label: str = "") -> str:
        if index >= len(raw_links):
            return label

        raw = raw_links[index]
        href = strip_fragment(urljoin(base_url, raw.get("href") or ""))
        if not href:
            return label
        if is_ignored_capture_url(href):
            return ""

        label = label or normalize_line(raw.get("text") or raw.get("title") or href)
        kind = classify_link(label, href)
        stored_href = redact_url(href) if kind == "unsafe" or has_sensitive_query(href) else href
        return f"[{escape_markdown_link_label(label)}]({stored_href})"

    def replace_range(match: re.Match[str]) -> str:
        label = normalize_line(match.group("label").replace("\n", " "))
        return markdown_link(int(match.group("index")), label)

    def replace_token(match: re.Match[str]) -> str:
        return markdown_link(int(match.group("index")))

    return INLINE_LINK_TOKEN_RE.sub(replace_token, INLINE_LINK_RANGE_RE.sub(replace_range, text))


def network_idle_timeout_for_url(url: str, args: argparse.Namespace) -> int:
    if args.network_idle_timeout_ms > 0:
        return args.network_idle_timeout_ms
    if urlparse(url).path == "/course/view.php":
        return max(0, args.course_network_idle_timeout_ms)
    return 0


def escape_markdown_link_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


async def expand_read_only_controls(
    page: Page,
    network_idle_timeout_ms: int,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
    page_index: int,
) -> None:
    for _ in range(3):
        try:
            clicked = await page.evaluate(EXPAND_SCRIPT, list(EXPAND_TEXTS))
        except PlaywrightError as exc:
            message = safe_error(exc)
            if is_navigation_churn(message):
                logger.log("read-only expand retried after page navigation")
                await wait_for_optional_page_settle(page, network_idle_timeout_ms)
                continue
            logger.log(f"read-only expand attempt failed: {safe_error(exc)}")
            await diagnostics.error(
                "expand_read_only_failed",
                "Read-only control expansion failed",
                page=page,
                page_index=page_index,
                exc=exc,
            )
            return
        if not clicked:
            return
        await wait_for_optional_network_idle(page, network_idle_timeout_ms)


async def click_completion_toggles(
    page: Page, logger: RunLogger, diagnostics: DiagnosticRecorder, page_index: int
) -> None:
    try:
        clicked = await page.evaluate(CLICK_COMPLETION_SCRIPT, list(COMPLETION_TOGGLE_TEXTS))
    except PlaywrightError as exc:
        logger.log(f"completion toggle attempt failed: {safe_error(exc)}")
        await diagnostics.error(
            "completion_toggle_failed",
            "Completion toggle click attempt failed",
            page=page,
            page_index=page_index,
            exc=exc,
        )
        return
    if clicked:
        logger.log(f"clicked completion toggles: {clicked}")
        diagnostics.event(
            "info",
            "completion_toggle_clicked",
            "Clicked completion toggles",
            page_index=page_index,
            url=page.url,
            details={"clicked": clicked},
        )
        with suppress(PlaywrightError):
            await page.wait_for_load_state("networkidle", timeout=2_000)


async def open_submission_form_for_reading(
    page: Page,
    logger: RunLogger,
    network_idle_timeout_ms: int,
    diagnostics: DiagnosticRecorder,
    page_index: int,
) -> None:
    try:
        result = await page.evaluate(OPEN_SUBMISSION_FORM_SCRIPT, list(SUBMISSION_FORM_TEXTS))
    except PlaywrightError as exc:
        logger.log(f"submission form open attempt failed: {safe_error(exc)}")
        await diagnostics.error(
            "submission_form_open_failed",
            "Submission form open attempt failed",
            page=page,
            page_index=page_index,
            exc=exc,
        )
        return

    if not result or not result.get("clicked"):
        return

    target = result.get("href") or page.url
    logger.log(f"opened submission form for reading: {result.get('text')} -> {redact_url(target)}")
    diagnostics.event(
        "info",
        "submission_form_opened",
        "Opened submission form for read-only capture",
        page_index=page_index,
        url=target,
        details={"text": result.get("text")},
    )
    await wait_for_optional_page_settle(page, network_idle_timeout_ms)


async def wait_for_optional_network_idle(page: Page, timeout_ms: int) -> None:
    if timeout_ms <= 0:
        return
    with suppress(PlaywrightError):
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)


async def wait_for_body(page: Page) -> None:
    with suppress(PlaywrightError):
        await page.wait_for_selector("body", state="attached", timeout=1_000)


async def wait_for_optional_page_settle(page: Page, timeout_ms: int) -> None:
    timeout = max(timeout_ms, 1_000)
    with suppress(PlaywrightError):
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    await wait_for_optional_network_idle(page, timeout_ms)


def is_navigation_churn(message: str) -> bool:
    lower = message.lower()
    return (
        "execution context was destroyed" in lower or "most likely because of a navigation" in lower
    )


def is_network_disconnect_error(message: str) -> bool:
    lower = message.lower()
    return any(marker.lower() in lower for marker in NETWORK_DISCONNECT_MARKERS)


def capture_has_network_disconnect(capture: PageCapture) -> bool:
    return any(is_network_disconnect_error(error) for error in capture.errors)


def build_links(raw_links: list[dict[str, str]], base_url: str) -> list[Link]:
    result: list[Link] = []
    seen: set[str] = set()
    for raw in raw_links:
        href = strip_fragment(urljoin(base_url, raw.get("href") or ""))
        text = normalize_line(raw.get("text") or raw.get("title") or href)
        if not href or href in seen:
            continue
        if is_ignored_capture_url(href):
            continue
        seen.add(href)
        kind = classify_link(text, href)
        stored_href = redact_url(href) if kind == "unsafe" or has_sensitive_query(href) else href
        result.append(Link(text=text, url=stored_href, kind=kind))
    return result


def build_buttons(raw_buttons: list[dict[str, Any]]) -> list[Button]:
    result: list[Button] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_buttons:
        text = normalize_line(raw.get("text") or "")
        if not text:
            continue
        action = raw.get("action") or ""
        stored_action = redact_url(action) if action and has_sensitive_query(action) else action
        key = (text, stored_action)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            Button(
                text=text,
                kind=raw.get("type") or "",
                disabled=bool(raw.get("disabled")),
                action_url=stored_action,
            )
        )
    return result


def should_queue_link(link: Link, start_url: str, args: argparse.Namespace) -> bool:
    if "[REDACTED]" in link.url:
        return False
    if has_sensitive_query(link.url):
        return False

    parsed = urlparse(link.url)
    query = parse_qs(parsed.query)
    if parsed.path in STATE_CHANGING_PATHS:
        return False
    if parsed.path in NOISY_DETAIL_PATHS:
        return False
    if parsed.path == "/mod/glossary/view.php" and GLOSSARY_FILTER_QUERY_KEYS.intersection(query):
        return False

    if args.course_title and urlparse(link.url).path == "/my/courses.php":
        return False
    if link.kind == "unsafe":
        return False
    if link.kind == "file":
        return False
    if link.kind == "action" and not args.visit_action_pages:
        return False

    start = urlparse(start_url)
    if start.path == "/course/view.php":
        if parsed.path == "/my/courses.php":
            return False
        if parsed.path == "/course/view.php":
            start_id = parse_qs(start.query).get("id")
            link_id = parse_qs(parsed.query).get("id")
            if start_id != link_id:
                return False

    if parsed.netloc != start.netloc:
        return args.include_external and link.kind == "page"
    return looks_like_course_link(link.url, start_url)
