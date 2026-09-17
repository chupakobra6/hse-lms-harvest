from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from .credentials import CredentialError
from .debug import (
    DiagnosticRecorder,
    RunLogger,
    ScreenshotPolicy,
    safe_error,
    safe_url,
    save_screenshot,
)
from .netology import is_netology_url

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

HSE_SSO_LOGIN_SELECTORS = (
    'button:has-text("Войти через ЕЛК")',
    'a:has-text("Войти через ЕЛК")',
    'input[value*="Войти через ЕЛК"]',
    'button:has-text("ЕЛК")',
    'a:has-text("ЕЛК")',
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
    last_recovery_at = 0.0
    submitted = False
    netology_methods_opened = False
    netology_email_selected = False

    while asyncio.get_running_loop().time() < deadline:
        for page in context.pages:
            if await page_looks_logged_in(page, start_url):
                logger.log(f"auto-login detected logged-in page: {safe_url(page.url)}")
                return page

            if page.url != last_url:
                last_url = page.url
                logger.log(f"auto-login inspecting: {safe_url(page.url)}")
                await save_screenshot(page, debug_dir, "auth-page", logger, screenshots)

            if is_netology_url(start_url) and await netology_captcha_visible(page):
                await diagnostics.error(
                    "netology_captcha_required",
                    "Netology SmartCaptcha requires manual login in the dedicated browser profile",
                    page=page,
                    url=start_url,
                )
                raise CredentialError(
                    "Netology requires manual SmartCaptcha; sign in with the dedicated browser profile."
                )
            if is_netology_url(start_url) and submitted:
                continue

            # The Netology modal offers third-party SSO; never send its password there.
            login_form_ready = not is_netology_url(start_url) or (
                is_netology_url(page.url)
                and netology_email_selected
                and "modal=sign_in" in page.url
            )
            if login_form_ready and await fill_login_form(
                page, username, password, logger, diagnostics
            ):
                submitted = True
                await save_screenshot(page, debug_dir, "auth-submitted", logger, screenshots)
                continue

            if (
                is_netology_url(start_url)
                and "modal=sign_in" in page.url
                and not netology_methods_opened
                and await maybe_open_netology_methods(page, logger)
            ):
                netology_methods_opened = True
                await save_screenshot(page, debug_dir, "auth-netology-methods", logger, screenshots)
                continue

            if (
                is_netology_url(start_url)
                and netology_methods_opened
                and not netology_email_selected
                and await maybe_choose_netology_email(page, logger)
            ):
                netology_email_selected = True
                await save_screenshot(page, debug_dir, "auth-netology-email", logger, screenshots)
                continue

            if is_netology_url(start_url) and await maybe_click_netology_login(page, logger):
                await save_screenshot(page, debug_dir, "auth-netology-opened", logger, screenshots)
                continue

            if await maybe_click_hse_sso_login(page, logger):
                submitted = True
                await save_screenshot(page, debug_dir, "auth-sso-clicked", logger, screenshots)
                continue

            now = asyncio.get_running_loop().time()
            if is_stuck_smart_lms_login(page.url) and now - last_recovery_at > 8:
                last_recovery_at = now
                logger.log(
                    f"auto-login recovering from intermediate login page: {safe_url(page.url)}"
                )
                with contextlib_suppress_playwright():
                    await page.goto(start_url, wait_until="commit", timeout=10_000)
                    await maybe_click_smart_lms_login(page, logger)

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


async def maybe_click_netology_login(page: Page, logger: RunLogger) -> bool:
    if not is_netology_url(page.url) or "modal=sign_in" in page.url:
        return False
    for selector in ('a[href*="modal=sign_in"]:has-text("Войти")',):
        locator = page.locator(selector)
        try:
            if await locator.count() and await locator.first.is_visible(timeout=500):
                await locator.first.click(timeout=2_000)
                logger.log("auto-login opened Netology sign-in")
                return True
        except PlaywrightError:
            continue
    return False


async def maybe_open_netology_methods(page: Page, logger: RunLogger) -> bool:
    try:
        choice = page.get_by_text("Другие способы входа", exact=True)
        if await choice.count() and await choice.first.is_visible(timeout=500):
            await choice.first.click(timeout=2_000)
            logger.log("auto-login opened Netology sign-in methods")
            return True
    except PlaywrightError:
        pass
    return False


async def maybe_choose_netology_email(page: Page, logger: RunLogger) -> bool:
    try:
        choice = page.get_by_text("Войти по почте", exact=True)
        if await choice.count() and await choice.first.is_visible(timeout=500):
            await choice.first.click(timeout=2_000)
            logger.log("auto-login selected Netology email sign-in")
            return True
    except PlaywrightError:
        pass
    return False


async def netology_captcha_visible(page: Page) -> bool:
    try:
        return (
            await page.locator(
                '[data-testid="advanced-container"].SmartCaptcha-Overlay_visible'
            ).count()
            > 0
        )
    except PlaywrightError:
        return False


def is_stuck_smart_lms_login(url: str) -> bool:
    lower = url.lower()
    return "/login/hselogin.php" in lower


async def maybe_click_smart_lms_login(page: Page, logger: RunLogger) -> None:
    for selector in ('button:has-text("Войти")', 'a:has-text("Войти")', 'input[value*="Войти" i]'):
        locator = page.locator(selector)
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=1_000):
                await locator.first.click(timeout=2_000)
                logger.log("auto-login clicked Smart LMS login")
                return
        except PlaywrightError:
            continue


async def maybe_click_hse_sso_login(page: Page, logger: RunLogger) -> bool:
    for selector in HSE_SSO_LOGIN_SELECTORS:
        locator = page.locator(selector)
        try:
            if await locator.count() > 0 and await locator.first.is_visible(timeout=1_000):
                await locator.first.click(timeout=2_000)
                logger.log("auto-login clicked HSE SSO login")
                with contextlib_suppress_playwright():
                    await page.wait_for_load_state("domcontentloaded", timeout=8_000)
                return True
        except PlaywrightError:
            continue
    return False


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
