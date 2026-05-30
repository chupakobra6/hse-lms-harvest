from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .debug import (
    DiagnosticRecorder,
    RunLogger,
    ScreenshotPolicy,
    safe_error,
    safe_url,
    save_screenshot,
)

COURSE_LINK_SCRIPT = """
(needle) => {
  const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
  const lowerNeedle = needle.toLowerCase();
  return Array.from(document.querySelectorAll('a[href]'))
    .map((element) => ({
      text: clean(element.innerText || element.textContent || element.getAttribute('title') || element.getAttribute('aria-label')),
      href: element.href,
    }))
    .filter((item) => item.href && item.text.toLowerCase().includes(lowerNeedle));
}
"""


async def resolve_course_url(
    page: Page,
    start_url: str,
    course_title: str | None,
    debug_dir: Path,
    logger: RunLogger,
    screenshots: ScreenshotPolicy,
    diagnostics: DiagnosticRecorder,
) -> str:
    if not course_title:
        return start_url

    parsed = urlparse(start_url)
    courses_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/my/courses.php")
    logger.log(f"resolving course title {course_title!r} from {safe_url(courses_url)}")

    try:
        await page.goto(courses_url, wait_until="commit", timeout=30_000)
        with suppress_playwright():
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except PlaywrightError as exc:
        await diagnostics.error(
            "course_list_navigation_failed",
            "Failed to open LMS courses list",
            page=page,
            url=courses_url,
            exc=exc,
        )
        raise
    with suppress_playwright():
        await page.wait_for_load_state("networkidle", timeout=8_000)
    matches = []
    deadline = asyncio.get_running_loop().time() + 45
    while asyncio.get_running_loop().time() < deadline:
        try:
            matches = await page.evaluate(COURSE_LINK_SCRIPT, course_title)
        except PlaywrightError as exc:
            logger.log(f"course resolution failed: {safe_error(exc)}")
            await diagnostics.error(
                "course_resolution_failed",
                "Failed to inspect LMS courses list",
                page=page,
                url=courses_url,
                exc=exc,
                details={"course_title": course_title},
            )
            raise RuntimeError(f"Could not resolve course title {course_title!r}.") from exc
        if matches:
            break
        await asyncio.sleep(1)

    await save_screenshot(page, debug_dir, "courses-list", logger, screenshots)

    if not matches:
        logger.log("course title was not found; stopping harvest")
        await diagnostics.error(
            "course_title_not_found",
            "Course title was not found in LMS courses list",
            page=page,
            url=courses_url,
            details={"course_title": course_title},
        )
        raise RuntimeError(f"Course title was not found: {course_title!r}.")

    target = matches[0]["href"]
    logger.log(f"course resolved: {matches[0]['text']!r} -> {safe_url(target)}")
    return target


class suppress_playwright:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return exc_type is not None and issubclass(exc_type, PlaywrightError)
