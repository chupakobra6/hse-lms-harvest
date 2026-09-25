from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import deque
from contextlib import suppress
from dataclasses import asdict
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
from .coverage import coverage_scope, make_coverage
from .credentials import (
    CredentialError,
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
    write_manifest,
)
from .model import PageCapture
from .netology import course_page_visible, is_netology_url
from .page_cache import load_page_reuse_index, maybe_reuse_page
from .privacy import redact_url, strip_fragment
from .text import stable_slug


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "login":
            asyncio.run(run_login(args))
        elif args.command == "harvest":
            return asyncio.run(run_harvest(args))
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
    except CredentialError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def run_credentials(args: argparse.Namespace) -> None:
    env_file = Path(args.env_file)
    if args.credentials_command == "set":
        password = read_password_from_user(password_stdin=args.password_stdin)
        store_password(
            args.source, args.username, password, env_file, credential_helper=args.credential_helper
        )
        print(f"Credentials stored and verified for {args.username} through the configured helper.")
        return
    if args.credentials_command == "status":
        print(credentials_status(args.source, env_file))
        return
    if args.credentials_command == "delete":
        delete_password(args.source, env_file)
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
        if is_netology_url(page.url):
            return
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
        title = await page.title()
    except PlaywrightError:
        return False

    lower_text = f"{title}\n{text}".lower()
    if is_netology_url(start_url):
        return await course_page_visible(page)
    logged_in_markers = (
        "мои курсы",
        "вы зашли под именем",
        "основные блоки контента",
        "требуемые условия завершения",
        "состояние ответа",
        "мои работы (вкр/кр/проект)",
        "загрузка работы",
        "список работ",
        "файл работы",
    )
    if any(marker in lower_text for marker in logged_in_markers):
        return True

    return any(path in lower_url for path in ("/my/", "/course/view.php", "/mod/"))


async def run_harvest(args: argparse.Namespace) -> int:
    if args.max_pages < 1:
        raise RuntimeError("--max-pages must be positive")
    if not 1 <= args.page_concurrency <= 3:
        raise RuntimeError("--page-concurrency must be between 1 and 3")
    if is_netology_url(args.url) and args.page_concurrency != 1:
        raise RuntimeError("Netology requires --page-concurrency 1 for assignment navigation")
    if args.assignments_only and not is_netology_url(args.url):
        raise RuntimeError("--assignments-only is supported only for Netology course URLs")
    if args.open_netology_assignments and not is_netology_url(args.url):
        raise RuntimeError("--open-netology-assignments is supported only for Netology course URLs")
    if args.resume_dump and (args.resume_latest or args.reuse_dump):
        raise RuntimeError("--resume-dump cannot be combined with --resume-latest/--reuse-dump")
    if (args.resume_dump or args.resume_latest) and args.page_cache == "off":
        raise RuntimeError("resume requires --page-cache validate")
    profile = Path(args.profile).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
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
    resumed_from = ""
    pending: list[str] = []
    stop_reason = ""
    manifest: dict[str, Any] = {}

    def checkpoint(*, reason: str = "interrupted", render: bool = False) -> None:
        manifest.update(
            {
                "format_version": FORMAT_VERSION,
                "source_url": redact_url(resolved_start_url),
                "requested_url": redact_url(args.url),
                "course_title": args.course_title,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "profile_dir": str(profile),
                "errors": errors,
                "coverage": make_coverage(
                    args,
                    resolved_start_url,
                    pages,
                    [*pending, *queue],
                    stop_reason=reason,
                ),
                "resumed_from": resumed_from,
                "page_cache": {
                    "mode": args.page_cache,
                    "source": str(page_reuse.manifest_path) if page_reuse else "",
                    "reused_pages": reused_page_count,
                },
                "debug": {
                    "error_count": len(diagnostics.errors),
                    "events": "debug/events.jsonl",
                    "errors": "debug/errors.json",
                    "errors_markdown": "debug/errors.md",
                },
                "page_count": len(pages),
                "pages": [asdict(capture) for capture in pages],
            }
        )
        if render:
            render_dump(out_dir, manifest, pages)
        else:
            write_manifest(out_dir / "manifest.json", manifest)

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

        workers = [page]
        for _ in range(args.page_concurrency - 1):
            worker = await context.new_page()
            worker.set_default_timeout(12_000)
            workers.append(worker)
        download_lock = asyncio.Lock()

        resolved_start_url = await resolve_course_url(
            page, args.url, args.course_title, debug_dir, logger, screenshots, diagnostics
        )
        resume_confirmed: set[str] = set()
        resume_order: dict[str, int] = {}
        if args.resume_dump or args.resume_latest:
            compatible = (
                page_reuse is not None
                and page_reuse.coverage.get("version") == 1
                and page_reuse.coverage.get("status") == "partial"
                and page_reuse.coverage.get("scope") == coverage_scope(args, resolved_start_url)
            )
            if args.resume_dump and not compatible:
                await close_context(context, logger, diagnostics)
                raise RuntimeError("resume requires a partial dump with the same capture scope")
            if compatible:
                resume_confirmed = set(page_reuse.coverage.get("confirmed_urls") or [])
                resumed_from = str(page_reuse.manifest_path)
                resume_order = {
                    url: index
                    for index, url in enumerate(page_reuse.coverage.get("remaining_urls") or [])
                }
        live_refresh_urls = (
            {strip_fragment(resolved_start_url)} if args.refresh_start_page else set()
        )
        queue.append(resolved_start_url)
        queued.add(strip_fragment(resolved_start_url))
        new_page_count = 0
        checkpoint()

        async def capture_one(url: str, index: int, worker: Page) -> tuple[PageCapture, bool]:
            capture = None
            if strip_fragment(url) not in live_refresh_urls:
                # The cache may materialize attachments; serialize those writes.
                async with download_lock:
                    capture = await maybe_reuse_page(
                        context,
                        url,
                        index,
                        args,
                        page_reuse,
                        out_dir,
                        logger,
                        diagnostics,
                        file_cache=file_cache,
                        downloaded_urls=downloaded_urls,
                    )
            if capture is not None:
                return capture, True
            logger.log(f"[{index}] capture {safe_url(url)}")
            return await capture_page(
                context,
                worker,
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
                download_lock=download_lock,
            ), False

        try:
            while queue:
                if resume_order:
                    # Validate known pages first, then advance the saved unfinished frontier;
                    # a failing early link must not consume every future batch.
                    queue = deque(
                        sorted(
                            queue,
                            key=lambda url: (
                                0 if strip_fragment(url) in resume_confirmed else 1,
                                resume_order.get(strip_fragment(url), len(resume_order)),
                            ),
                        )
                    )
                batch: list[tuple[str, str, int, Page]] = []
                while queue and len(batch) < len(workers):
                    url = queue.popleft()
                    url_key = strip_fragment(url)
                    if url_key in visited:
                        continue
                    if url_key not in resume_confirmed and new_page_count >= args.max_pages:
                        pending.append(url_key)
                        continue
                    if url_key not in resume_confirmed:
                        new_page_count += 1
                    visited.add(url_key)
                    batch.append((url, url_key, len(pages) + len(batch) + 1, workers[len(batch)]))
                if not batch:
                    continue
                # Preserve every in-flight URL before starting concurrent browser work.
                pending.extend(item[1] for item in batch)
                checkpoint()
                results = await asyncio.gather(
                    *(capture_one(url, index, worker) for url, _, index, worker in batch),
                    return_exceptions=True,
                )
                failure = None
                for (url, url_key, index, _), result in zip(batch, results, strict=True):
                    if isinstance(result, BaseException):
                        failure = failure or result
                        continue
                    if failure is not None:
                        # Keep later captures pending rather than creating gaps in
                        # page indexes if an earlier worker failed.
                        continue
                    capture, reused = result
                    reused_page_count += int(reused)
                    pages.append(capture)
                    pending.remove(url_key)
                    for link in capture.links:
                        link_key = strip_fragment(link.url)
                        if link_key == strip_fragment(capture.final_url):
                            continue
                        if link_key in visited or link_key in queued:
                            continue
                        if should_queue_link(link, resolved_start_url, args):
                            queue.append(link.url)
                            queued.add(link_key)
                    checkpoint()
                    if capture_has_network_disconnect(capture):
                        stop_reason = "capture_error"
                        diagnostics.warning(
                            "network_disconnected_abort",
                            "Network disconnected; partial dump saved",
                            page_index=index,
                            url=url,
                        )
                if failure is not None:
                    raise failure
                if stop_reason:
                    break
        except (Exception, asyncio.CancelledError) as exc:
            stop_reason = "interrupted"
            errors.append(safe_error(exc) or type(exc).__name__)
            await diagnostics.error(
                "harvest_interrupted",
                "Harvest interrupted; checkpoint preserved",
                exc=exc,
            )
            checkpoint(reason=stop_reason, render=True)
            if isinstance(exc, asyncio.CancelledError):
                raise

        await close_context(context, logger, diagnostics)

    checkpoint(reason=stop_reason, render=True)
    logger.log(f"saved LMS dump: {out_dir}")
    logger.log(f"summary: {out_dir / 'summary.md'}")
    logger.log(f"navigation: {out_dir / 'navigation.md'}")
    logger.log(f"manifest: {out_dir / 'manifest.json'}")
    print(
        "HARVEST_RESULT "
        + json.dumps(
            {
                "manifest_path": str(out_dir / "manifest.json"),
                "coverage_status": manifest["coverage"]["status"],
                "coverage_version": 1,
            }
        )
    )
    return 0 if manifest["coverage"]["status"] == "complete" else 1


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
        source = "netology" if is_netology_url(start_url) else "smart_lms"
        username = args.username or load_default_username(source, env_file)
        try:
            password = load_password(source, username, env_file)
        except CredentialError as exc:
            await diagnostics.error("credential_unavailable", str(exc))
            raise
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

        if args.headless:
            await diagnostics.error(
                "auto_login_unavailable",
                "Headless auto-login did not establish a session; repair credentials or the login flow",
            )
            raise CredentialError(
                "Headless auto-login failed; manual login cannot run in background."
            )

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
