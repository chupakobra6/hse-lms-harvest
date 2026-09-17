"""Course traversal within selected Netology modules; tasks may open for reading."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

MODULE_PATH = re.compile(r"^/profile/program/([^/]+)(?:/(.*))?$")
LESSON_ITEM_PATH = re.compile(r"^lessons/\d+/lesson_items/\d+$")

EXPAND_SCHEDULE_SCRIPT = """
() => {
  let clicked = 0;
  for (const title of document.querySelectorAll('[data-testid="program-lesson-title"]')) {
    const header = title.parentElement?.parentElement;
    if (!header || !String(header.className).includes('Lesson--header')) continue;
    if (String(header.className).includes('Lesson--expanded')) continue;
    if (header.parentElement?.innerText?.includes('Откроется') &&
        !header.parentElement?.querySelector('a[href*="/lesson_items/"]')) continue;
    header.click();
    clicked += 1;
  }
  return clicked;
}
"""


def is_netology_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "netology.ru"


def is_course_page(url: str, start_url: str) -> bool:
    """Never leave the explicitly selected program module."""
    parsed, start = urlparse(url), urlparse(start_url)
    if parsed.scheme != "https" or parsed.netloc != start.netloc or parsed.query:
        return False
    target_match, start_match = (
        MODULE_PATH.fullmatch(parsed.path),
        MODULE_PATH.fullmatch(start.path),
    )
    if not target_match or not start_match or target_match.group(1) != start_match.group(1):
        return False
    suffix = target_match.group(2) or ""
    return suffix in {"schedule", "execution", "execution/tasks"} or bool(
        LESSON_ITEM_PATH.fullmatch(suffix)
    )


async def prepare_page(page: Page, url: str) -> None:
    """Wait for the client-rendered view; expand schedule accordions only."""
    if not is_netology_url(url):
        return
    path = urlparse(url).path
    if path.endswith("/schedule"):
        await page.locator('[data-testid="program-lesson-title"]').first.wait_for(timeout=15_000)
        await page.wait_for_function("document.title.startsWith('Расписание ')", timeout=15_000)
        await page.evaluate(EXPAND_SCHEDULE_SCRIPT)
    elif path.endswith("/execution") or path.endswith("/execution/tasks"):
        await page.wait_for_function(
            "document.title.startsWith('Практика ') && document.body.innerText.includes('На проверке')",
            timeout=15_000,
        )
    elif (match := MODULE_PATH.fullmatch(path)) and LESSON_ITEM_PATH.fullmatch(
        match.group(2) or ""
    ):
        await page.wait_for_function(
            "document.title !== 'Нетология — образовательная платформа' && "
            "document.querySelector('[id^=\"lesson-item-id-\"]') !== null",
            timeout=15_000,
        )
        if (await page.title()).startswith("Задание:"):
            await page.wait_for_function(
                "document.querySelector('[id^=\"markdown-\"]') !== null || "
                "Array.from(document.querySelectorAll('button')).some(button => "
                "button.textContent?.trim() === 'Приступить к заданию')",
                timeout=15_000,
            )


async def course_page_visible(page: Page) -> bool:
    if not is_netology_url(page.url) or not MODULE_PATH.fullmatch(urlparse(page.url).path):
        return False
    try:
        return await page.get_by_role("link", name="Основной курс").count() > 0
    except PlaywrightError:
        return False


async def open_assignment_for_reading(page: Page, url: str) -> bool:
    """Only start an explicitly selected Netology task; never touch its answer form."""
    match = MODULE_PATH.fullmatch(urlparse(url).path)
    if not match or not LESSON_ITEM_PATH.fullmatch(match.group(2) or ""):
        return False
    button = page.get_by_role("button", name="Приступить к заданию", exact=True)
    if not await button.count():
        return False
    await button.click()
    await page.locator('[id^="markdown-"]').first.wait_for(state="visible", timeout=15_000)
    return True
