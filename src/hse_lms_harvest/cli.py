from __future__ import annotations

import argparse
import asyncio
import sys
from collections import deque
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .auth import auto_login
from .capture import capture_has_network_disconnect, capture_page, should_queue_link
from .cleanup import run_cleanup
from .cli_args import build_parser
from .course import resolve_course_url
from .credentials import (
    credentials_status,
    delete_password,
    load_default_username,
    load_password,
    read_password_from_user,
    store_password,
)
from .debug import (
    DiagnosticRecorder,
    RunLogger,
    ScreenshotPolicy,
    safe_error,
    safe_url,
    save_screenshot,
)
from .downloads import block_heavy_page_resource
from .file_cache import FileCache
from .manifest import (
    FORMAT_VERSION,
    manifest_paths,
    render_dump,
    render_dump_from_manifest,
)
from .model import PageCapture
from .page_cache import load_page_reuse_index, mark_reused_downloads, maybe_reuse_page
from .privacy import redact_url, strip_fragment
from .text import stable_slug


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            asyncio.run(run_login(args))
        elif args.command == "harvest":
            asyncio.run(run_harvest(args))
        elif args.command == "credentials":
            run_credentials(args)
        elif args.command == "cleanup":
            run_cleanup(args)
        elif args.command == "migrate":
            run_migrate(args)
        else:
            parser.error("unknown command")
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


def run_credentials(args: argparse.Namespace) -> None:
    env_file = Path(args.env_file)
    if args.credentials_command == "set":
        password = read_password_from_user(password_stdin=args.password_stdin)
        store_password(args.username, password, env_file)
        print(credentials_status(env_file))
        return
    if args.credentials_command == "status":
        print(credentials_status(env_file))
        return
    if args.credentials_command == "delete":
        delete_password(env_file)
        print("Credentials deleted.")
        return
    raise RuntimeError(f"unknown credentials command: {args.credentials_command}")


def run_migrate(args: argparse.Namespace) -> None:
    target = Path(args.out)
    paths = manifest_paths(target)
    if args.latest_only and paths:
        latest = max(paths, key=lambda path: path.stat().st_mtime)
        paths = [latest]
    if not paths:
        raise RuntimeError(f"no manifest.json files found under {target}")

    for manifest_path in paths:
        render_dump_from_manifest(manifest_path)
        print(f"migrated {manifest_path.parent}")


async def run_login(args: argparse.Namespace) -> None:
    profile = Path(args.profile).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        context = await launch_context(playwright, args, profile)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        await maybe_click_login(page)
        print(f"Opened {args.url}")
        print("Log in manually in the opened browser window.")

        if args.manual_confirm:
            print("Press Enter here when the profile is logged in and ready.")
            await asyncio.to_thread(input)
        else:
            print("Waiting until LMS looks logged in...")
            logged_in_page = await wait_for_logged_in(context, args.url, args.auth_timeout)
            if logged_in_page is None:
                await context.close()
                raise RuntimeError(
                    f"Login was not detected within {args.auth_timeout} seconds. "
                    "Run again with --manual-confirm if auto-detection is too strict."
                )
            print(f"Login detected at {logged_in_page.url}. Browser profile is ready.")

        await context.close()


async def wait_for_logged_in(
    context: BrowserContext, start_url: str, timeout_seconds: int
) -> Page | None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        for page in context.pages:
            if await page_looks_logged_in(page, start_url):
                return page
        await asyncio.sleep(2)
    return None


async def maybe_click_login(page: Page) -> None:
    try:
        link = page.get_by_role("link", name="Войти")
        if await link.count() == 1:
            await link.click()
            with suppress(PlaywrightError):
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except PlaywrightError:
        return


async def page_looks_logged_in(page: Page, start_url: str) -> bool:
    start_host = urlparse(start_url).netloc
    try:
        current_url = page.url
        if urlparse(current_url).netloc and urlparse(current_url).netloc != start_host:
            return False

        lower_url = current_url.lower()
        if (
            "login" in lower_url
            or "/auth/" in lower_url
            or "openid" in lower_url
            or "sso" in lower_url
        ):
            return False

        text = await page.locator("body").inner_text(timeout=1_000)
    except PlaywrightError:
        return False

    lower_text = text.lower()
    logged_in_markers = (
        "мои курсы",
        "вы зашли под именем",
        "основные блоки контента",
        "требуемые условия завершения",
        "состояние ответа",
    )
    if any(marker in lower_text for marker in logged_in_markers):
        return True

    return any(path in lower_url for path in ("/my/", "/course/view.php", "/mod/"))


async def run_harvest(args: argparse.Namespace) -> None:
    profile = Path(args.profile).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dump_name = f"{stable_slug(urlparse(args.url).netloc)}-{stamp}"
    out_dir = out_root / dump_name
    files_dir = out_dir / "files"
    debug_dir = out_dir / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir / "harvest.log")
    screenshots = ScreenshotPolicy(
        mode=args.screenshot_mode,
        quality=args.screenshot_quality,
        retain=args.screenshot_retain,
    )
    diagnostics = DiagnosticRecorder(
        debug_dir,
        logger,
        screenshots,
        dump_mode=args.debug_dump_mode,
        text_limit=args.debug_text_limit,
    )
    logger.log(f"harvest started source={safe_url(args.url)}")
    diagnostics.event(
        "info",
        "harvest_started",
        "Harvest started",
        url=args.url,
        details={
            "max_pages": args.max_pages,
            "download_files": args.download_files,
            "debug_dump_mode": args.debug_dump_mode,
            "screenshot_mode": args.screenshot_mode,
        },
    )

    pages: list[PageCapture] = []
    visited: set[str] = set()
    queued: set[str] = set()
    queue: deque[str] = deque()
    downloaded_urls: set[str] = set()
    errors: list[str] = []
    resolved_start_url = args.url
    file_cache = None if args.no_file_cache else FileCache(Path(args.file_cache_dir))
    page_reuse = load_page_reuse_index(args, out_root, out_dir, logger)
    reused_page_count = 0

    async with async_playwright() as playwright:
        try:
            context = await launch_context(playwright, args, profile)
        except PlaywrightError as exc:
            await diagnostics.error(
                "browser_launch_failed",
                "Browser context launch failed",
                url=args.url,
                exc=exc,
                details={"profile": str(profile), "browser_channel": args.browser_channel},
            )
            raise
        if not args.load_page_assets:
            await context.route("**/*", block_heavy_page_resource)
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(12_000)

        if args.ensure_login or args.auto_login:
            await ensure_logged_in(
                context, page, args.url, args, debug_dir, logger, screenshots, diagnostics
            )

        resolved_start_url = await resolve_course_url(
            page, args.url, args.course_title, debug_dir, logger, screenshots, diagnostics
        )
        live_refresh_urls = (
            {strip_fragment(resolved_start_url)} if args.refresh_start_page else set()
        )
        queue.append(resolved_start_url)
        queued.add(strip_fragment(resolved_start_url))

        while queue and len(pages) < args.max_pages:
            url = queue.popleft()
            url_key = strip_fragment(url)
            if url_key in visited:
                continue
            visited.add(url_key)
            index = len(pages) + 1

            capture = None
            if url_key in live_refresh_urls:
                logger.log(f"[{index}/{args.max_pages}] refresh start page {safe_url(url)}")
            else:
                capture = await maybe_reuse_page(
                    context,
                    url,
                    index,
                    args,
                    page_reuse,
                    out_dir,
                    logger,
                    diagnostics,
                )
            if capture is not None:
                reused_page_count += 1
                mark_reused_downloads(capture, downloaded_urls)
                pages.append(capture)
                for link in capture.links:
                    link_key = strip_fragment(link.url)
                    if link_key == strip_fragment(capture.final_url):
                        continue
                    if link_key in visited or link_key in queued:
                        continue
                    if should_queue_link(link, resolved_start_url, args):
                        queue.append(link.url)
                        queued.add(link_key)
                continue

            logger.log(f"[{index}/{args.max_pages}] capture {safe_url(url)}")

            capture = await capture_page(
                context,
                page,
                url,
                index,
                args,
                files_dir,
                debug_dir,
                logger,
                downloaded_urls,
                screenshots,
                file_cache,
                diagnostics,
            )
            pages.append(capture)
            if capture_has_network_disconnect(capture):
                message = f"network disconnected while capturing {safe_url(url)}"
                errors.append(message)
                logger.log(f"{message}; aborting current subject")
                diagnostics.warning(
                    "network_disconnected_abort",
                    "Network disconnected; aborting current subject to avoid noisy partial dump",
                    page_index=index,
                    url=url,
                )
                break

            for link in capture.links:
                link_key = strip_fragment(link.url)
                if link_key == strip_fragment(capture.final_url):
                    continue
                if link_key in visited or link_key in queued:
                    continue
                if should_queue_link(link, resolved_start_url, args):
                    queue.append(link.url)
                    queued.add(link_key)

        await close_context(context, logger, diagnostics)

    manifest = {
        "format_version": FORMAT_VERSION,
        "source_url": redact_url(resolved_start_url),
        "requested_url": redact_url(args.url),
        "course_title": args.course_title,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "profile_dir": str(profile),
        "errors": errors,
        "page_cache": {
            "mode": args.page_cache,
            "source": str(page_reuse.manifest_path) if page_reuse is not None else "",
            "reused_pages": reused_page_count,
        },
        "debug": {
            "error_count": len(diagnostics.errors),
            "events": "debug/events.jsonl",
            "errors": "debug/errors.json",
            "errors_markdown": "debug/errors.md",
        },
    }
    render_dump(out_dir, manifest, pages)

    logger.log(f"saved LMS dump: {out_dir}")
    logger.log(f"summary: {out_dir / 'summary.md'}")
    logger.log(f"navigation: {out_dir / 'navigation.md'}")
    logger.log(f"manifest: {out_dir / 'manifest.json'}")
    if errors:
        raise RuntimeError("; ".join(errors))


async def close_context(
    context: BrowserContext,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
    *,
    timeout_seconds: int = 10,
) -> None:
    logger.log("closing browser context")
    close_task = asyncio.create_task(context.close())
    try:
        await asyncio.wait_for(close_task, timeout=timeout_seconds)
    except (TimeoutError, PlaywrightError) as exc:
        close_task.cancel()
        with suppress(BaseException):
            await close_task
        exception = safe_error(exc) or type(exc).__name__
        logger.log(f"browser context close warning: {exception}")
        diagnostics.warning(
            "browser_context_close_warning",
            "Browser context close did not finish before timeout; continuing to save dump",
            details={"timeout_seconds": timeout_seconds, "exception": exception},
        )


async def ensure_logged_in(
    context: BrowserContext,
    page: Page,
    start_url: str,
    args: argparse.Namespace,
    debug_dir: Path,
    logger: RunLogger,
    screenshots: ScreenshotPolicy,
    diagnostics: DiagnosticRecorder,
) -> None:
    try:
        await page.goto(start_url, wait_until="commit", timeout=30_000)
        with suppress(PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
    except PlaywrightError as exc:
        logger.log(f"login start navigation warning for {safe_url(start_url)}: {safe_error(exc)}")
        diagnostics.warning(
            "login_start_navigation_warning",
            "Start URL navigation failed before login check; continuing with current page state",
            url=start_url,
            details={"exception": safe_error(exc)},
        )
        with suppress(PlaywrightError):
            await page.wait_for_load_state("domcontentloaded", timeout=2_000)
    if await page_looks_logged_in(page, start_url):
        logger.log(f"already logged in at {safe_url(page.url)}")
        return

    await maybe_click_login(page)
    await save_screenshot(page, debug_dir, "login-start", logger, screenshots)

    if args.auto_login:
        env_file = Path(args.env_file)
        username = args.username or load_default_username(env_file)
        password = load_password(username, env_file)
        if username and password:
            logger.log(f"auto-login using stored credentials for {username}")
            logged_in_page = await auto_login(
                context,
                start_url,
                username,
                password,
                args.auth_timeout,
                debug_dir,
                logger,
                screenshots,
                page_looks_logged_in,
                diagnostics,
            )
            if logged_in_page is not None:
                logger.log(f"login detected at {safe_url(logged_in_page.url)}")
                return
        logger.log("auto-login requested but stored credentials were not available")

    logger.log("manual login is needed; waiting in the same browser session")
    logged_in_page = await wait_for_logged_in(context, start_url, args.auth_timeout)
    if logged_in_page is None:
        await diagnostics.error(
            "manual_login_timeout",
            "Manual login was not detected before timeout",
            page=page,
            url=start_url,
            details={"timeout_seconds": args.auth_timeout},
        )
        raise RuntimeError(f"Login was not detected within {args.auth_timeout} seconds.")

    logger.log(f"login detected at {safe_url(logged_in_page.url)}")


async def launch_context(
    playwright: Any, args: argparse.Namespace, profile: Path
) -> BrowserContext:
    launch_args = {
        "headless": args.headless,
        "slow_mo": args.slow_mo,
        "accept_downloads": True,
        "viewport": {"width": 1440, "height": 1000},
    }
    channel = None if args.browser_channel == "chromium" else args.browser_channel
    if channel:
        launch_args["channel"] = channel
    return await playwright.chromium.launch_persistent_context(str(profile), **launch_args)


if __name__ == "__main__":
    raise SystemExit(main())
