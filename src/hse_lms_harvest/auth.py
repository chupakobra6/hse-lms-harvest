from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from .debug import (
    DiagnosticRecorder,
    RunLogger,
    ScreenshotPolicy,
    safe_error,
    safe_url,
    save_screenshot,
)

USERNAME_SELECTORS = (
    'input[type="email"]',
    'input[name*="user" i]',
    'input[name*="login" i]',
    'input[name*="email" i]',
    'input[id*="user" i]',
    'input[id*="login" i]',
    'input[id*="email" i]',
    'input[type="text"]',
)

SUBMIT_SELECTORS = (
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Войти")',
    'button:has-text("Далее")',
    'button:has-text("Продолжить")',
    'button:has-text("Sign in")',
    'button:has-text("Next")',
)


async def auto_login(
    context: BrowserContext,
    start_url: str,
    username: str,
    password: str,
    timeout_seconds: int,
    debug_dir: Path,
    logger: RunLogger,
    screenshots: ScreenshotPolicy,
    page_looks_logged_in,
    diagnostics: DiagnosticRecorder,
) -> Page | None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_url = ""
    submitted = False

    while asyncio.get_running_loop().time() < deadline:
        for page in context.pages:
            if await page_looks_logged_in(page, start_url):
                logger.log(f"auto-login detected logged-in page: {safe_url(page.url)}")
                return page

            if page.url != last_url:
                last_url = page.url
                logger.log(f"auto-login inspecting: {safe_url(page.url)}")
                await save_screenshot(page, debug_dir, "auth-page", logger, screenshots)

            if await fill_login_form(page, username, password, logger, diagnostics):
                submitted = True
                await save_screenshot(page, debug_dir, "auth-submitted", logger, screenshots)

        await asyncio.sleep(2 if submitted else 1)

    logger.log("auto-login timeout")
    if context.pages:
        await diagnostics.error(
            "auto_login_timeout",
            "Auto-login did not reach a logged-in LMS page before timeout",
            page=context.pages[0],
            url=start_url,
            details={"timeout_seconds": timeout_seconds},
        )
    else:
        await diagnostics.error(
            "auto_login_timeout",
            "Auto-login did not reach a logged-in LMS page before timeout",
            url=start_url,
            details={"timeout_seconds": timeout_seconds},
        )
    return None


async def fill_login_form(
    page: Page,
    username: str,
    password: str,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
) -> bool:
    try:
        password_fields = page.locator('input[type="password"]')
        password_count = await password_fields.count()

        filled = False
        username_field = await first_visible_locator(page, USERNAME_SELECTORS)
        if username_field is not None:
            await username_field.fill(username, timeout=3_000)
            logger.log("auto-login filled username")
            filled = True

        if password_count > 0:
            password_field = password_fields.first
            if await password_field.is_visible(timeout=1_000):
                await password_field.fill(password, timeout=3_000)
                logger.log("auto-login filled password")
                filled = True

        if not filled:
            return False

        submit = await first_visible_locator(page, SUBMIT_SELECTORS)
        if submit is not None:
            await submit.click(timeout=3_000)
            logger.log("auto-login clicked submit")
        else:
            await page.keyboard.press("Enter")
            logger.log("auto-login pressed Enter")

        with contextlib_suppress_playwright():
            await page.wait_for_load_state("domcontentloaded", timeout=8_000)
        return True
    except PlaywrightError as exc:
        logger.log(f"auto-login form attempt failed: {safe_error(exc)}")
        await diagnostics.error(
            "auto_login_form_failed",
            "Auto-login form interaction failed",
            page=page,
            exc=exc,
        )
        return False


async def first_visible_locator(page: Page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector)
        count = await locator.count()
        if count == 0:
            continue
        for index in range(min(count, 5)):
            item = locator.nth(index)
            try:
                if await item.is_visible(timeout=500):
                    return item
            except PlaywrightError:
                continue
    return None


class contextlib_suppress_playwright:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, traceback):
        return exc_type is not None and issubclass(exc_type, PlaywrightError)
