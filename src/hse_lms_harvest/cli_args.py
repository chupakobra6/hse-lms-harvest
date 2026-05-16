from __future__ import annotations

import argparse
from pathlib import Path

from .credentials import DEFAULT_ENV_FILE

DEFAULT_PROFILE_DIR = Path(".browser-profile")
DEFAULT_DUMPS_DIR = Path("dumps")
DEFAULT_FILE_CACHE_DIR = DEFAULT_DUMPS_DIR / "_file-cache"
DEFAULT_NETWORK_IDLE_TIMEOUT_MS = 0
DEFAULT_COURSE_NETWORK_IDLE_TIMEOUT_MS = 1_000
DEFAULT_PAGE_HEAD_TIMEOUT_MS = 1_000
DEFAULT_FILE_HEAD_TIMEOUT_MS = 1_500
DEFAULT_FILE_DOWNLOAD_TIMEOUT_MS = 30_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hse-lms-harvest",
        description="Harvest visible text, links, and LMS attachments from a logged-in browser profile.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser(
        "login", help="Open a browser profile so you can log in manually."
    )
    add_browser_args(login)
    login.add_argument("--url", required=True, help="LMS URL to open for manual login.")
    login.add_argument(
        "--auth-timeout",
        type=int,
        default=900,
        help="Seconds to wait until the browser looks logged in.",
    )
    login.add_argument(
        "--manual-confirm",
        action="store_true",
        help="Wait for Enter instead of detecting login automatically.",
    )

    harvest = subparsers.add_parser("harvest", help="Capture course pages and attachments.")
    add_browser_args(harvest)
    harvest.add_argument("--url", required=True, help="Course or assignment URL to start from.")
    harvest.add_argument(
        "--out", default=str(DEFAULT_DUMPS_DIR), help="Output directory for dumps."
    )
    harvest.add_argument("--max-pages", type=int, default=80, help="Maximum LMS pages to visit.")
    harvest.add_argument(
        "--network-idle-timeout-ms",
        type=int,
        default=DEFAULT_NETWORK_IDLE_TIMEOUT_MS,
        help="Optional short wait for post-load network quietness after domcontentloaded. Default skips it for speed.",
    )
    harvest.add_argument(
        "--course-network-idle-timeout-ms",
        type=int,
        default=DEFAULT_COURSE_NETWORK_IDLE_TIMEOUT_MS,
        help="Network-idle wait used only for course overview pages so lazy course content can appear.",
    )
    harvest.add_argument(
        "--page-cache",
        choices=("off", "validate", "trust"),
        default="validate",
        help=(
            "Reuse pages from the previous manifest. validate reuses only matching HTTP "
            "validators; trust reuses by URL without network validation."
        ),
    )
    harvest.add_argument(
        "--page-head-timeout-ms",
        type=int,
        default=DEFAULT_PAGE_HEAD_TIMEOUT_MS,
        help="HEAD timeout for page-cache validation. Use 0 to disable validation probes.",
    )
    harvest.add_argument(
        "--reuse-dump",
        help="Specific previous dump directory or manifest.json to use for page-cache reuse.",
    )
    harvest.add_argument(
        "--download-files",
        action="store_true",
        help="Download same-site file links using the logged-in browser session.",
    )
    harvest.add_argument(
        "--skip-lms-file-server",
        action="store_true",
        help="Record LMS file-serving URLs but do not fetch those attachments.",
    )
    harvest.add_argument(
        "--download-media",
        action="store_true",
        help="Also download audio/video files. Disabled by default to keep dumps small.",
    )
    harvest.add_argument(
        "--max-file-mb",
        type=int,
        default=80,
        help="Skip individual downloads larger than this size. Use 0 to disable the limit.",
    )
    harvest.add_argument(
        "--download-concurrency",
        type=int,
        default=6,
        help="Maximum parallel HEAD/download requests for attachments.",
    )
    harvest.add_argument(
        "--file-head-timeout-ms",
        type=int,
        default=DEFAULT_FILE_HEAD_TIMEOUT_MS,
        help="HEAD timeout for attachment metadata checks. Use 0 to skip HEAD.",
    )
    harvest.add_argument(
        "--file-download-timeout-ms",
        type=int,
        default=DEFAULT_FILE_DOWNLOAD_TIMEOUT_MS,
        help="GET timeout for individual attachment downloads.",
    )
    harvest.add_argument(
        "--file-cache-dir",
        default=str(DEFAULT_FILE_CACHE_DIR),
        help="Persistent attachment cache reused across harvest runs.",
    )
    harvest.add_argument(
        "--trust-file-cache",
        action="store_true",
        help="Reuse cached attachments without HEAD validation. Fastest for repeated local reruns.",
    )
    harvest.add_argument(
        "--no-file-cache",
        action="store_true",
        help="Disable persistent attachment cache reuse.",
    )
    harvest.add_argument(
        "--load-page-assets",
        action="store_true",
        help="Load images, media, and fonts while reading pages. Disabled by default for speed.",
    )
    harvest.add_argument(
        "--visit-action-pages",
        dest="visit_action_pages",
        action="store_true",
        default=True,
        help="Open read-only action/detail pages, e.g. 'Добавить ответ'. Enabled by default.",
    )
    harvest.add_argument(
        "--skip-action-pages",
        dest="visit_action_pages",
        action="store_false",
        help="Do not open action/detail pages such as 'Добавить ответ'.",
    )
    harvest.add_argument(
        "--allow-state-changes",
        action="store_true",
        help="Allow clicking explicit LMS completion toggles. Save/submit/delete controls are still ignored.",
    )
    harvest.add_argument(
        "--include-external",
        action="store_true",
        help="Record external pages for crawling. External files are still not downloaded.",
    )
    harvest.add_argument(
        "--ensure-login",
        action="store_true",
        help="If LMS redirects to login, wait for manual login and then harvest in the same browser session.",
    )
    harvest.add_argument(
        "--auth-timeout",
        type=int,
        default=900,
        help="Seconds to wait for manual login when --ensure-login is used.",
    )
    harvest.add_argument(
        "--auto-login",
        action="store_true",
        help="Use stored credentials to refresh LMS login automatically.",
    )
    harvest.add_argument(
        "--username",
        help="LMS username. Defaults to the username stored by the credentials command.",
    )
    harvest.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Project .env file with HSE_LMS_USERNAME and HSE_LMS_PASSWORD.",
    )
    harvest.add_argument(
        "--course-title",
        help="Resolve and harvest one course from /my/courses.php by visible course title.",
    )
    harvest.add_argument(
        "--screenshot-mode",
        choices=("off", "on-error", "key", "every-page"),
        default="key",
        help="Debug screenshot policy. Default keeps only login/course/error screenshots.",
    )
    harvest.add_argument(
        "--screenshot-retain",
        type=int,
        default=30,
        help="Maximum debug screenshots kept per dump.",
    )
    harvest.add_argument(
        "--screenshot-quality",
        type=int,
        default=50,
        help="JPEG quality for debug screenshots.",
    )
    harvest.add_argument(
        "--debug-dump-mode",
        choices=("off", "on-error", "verbose"),
        default="on-error",
        help="Error diagnostic bundle policy. Default writes compact page-state JSON on errors.",
    )
    harvest.add_argument(
        "--debug-text-limit",
        type=int,
        default=6_000,
        help="Maximum visible-text characters stored in each error page-state dump.",
    )

    credentials = subparsers.add_parser(
        "credentials", help="Store or inspect local LMS credentials."
    )
    credentials_subparsers = credentials.add_subparsers(dest="credentials_command", required=True)
    credentials_set = credentials_subparsers.add_parser(
        "set", help="Store credentials in a local 0600 env file."
    )
    credentials_set.add_argument("--username", required=True)
    credentials_set.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Project .env file to write.",
    )
    credentials_set.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read password from stdin instead of an interactive hidden prompt.",
    )
    credentials_status_parser = credentials_subparsers.add_parser(
        "status", help="Show whether credentials are configured."
    )
    credentials_status_parser.add_argument(
        "--env-file", default=str(DEFAULT_ENV_FILE), help="Project .env file to inspect."
    )
    credentials_delete = credentials_subparsers.add_parser(
        "delete", help="Delete stored credentials."
    )
    credentials_delete.add_argument(
        "--env-file", default=str(DEFAULT_ENV_FILE), help="Project .env file to update."
    )

    cleanup = subparsers.add_parser("cleanup", help="Remove generated heavy artifacts.")
    cleanup.add_argument("--out", default=str(DEFAULT_DUMPS_DIR), help="Dumps directory.")
    cleanup.add_argument(
        "--profile", default=str(DEFAULT_PROFILE_DIR), help="Browser profile directory."
    )
    cleanup.add_argument("--screenshots", action="store_true", help="Clean debug screenshots.")
    cleanup.add_argument("--media", action="store_true", help="Clean downloaded audio/video files.")
    cleanup.add_argument(
        "--artifacts",
        action="store_true",
        help="Clean service attachment artifacts such as conference chat/playback files.",
    )
    cleanup.add_argument(
        "--browser-cache",
        action="store_true",
        help="Clean rebuildable browser cache directories without deleting cookies.",
    )
    cleanup.add_argument(
        "--file-cache",
        action="store_true",
        help="Delete the persistent attachment cache. Not included in --all.",
    )
    cleanup.add_argument(
        "--file-cache-dir",
        default=str(DEFAULT_FILE_CACHE_DIR),
        help="Persistent attachment cache directory.",
    )
    cleanup.add_argument(
        "--all",
        action="store_true",
        help=(
            "Clean screenshots, media, service artifacts, and browser cache. "
            "File cache is separate."
        ),
    )
    cleanup.add_argument(
        "--retain-screenshots",
        type=int,
        default=0,
        help="Keep N newest screenshots per dump when cleaning screenshots.",
    )
    cleanup.add_argument("--dry-run", action="store_true", help="Only print what would be removed.")

    migrate = subparsers.add_parser(
        "migrate",
        help="Regenerate pages/*.md, summary, navigation, and manifest fields from existing dumps.",
    )
    migrate.add_argument(
        "--out",
        default=str(DEFAULT_DUMPS_DIR),
        help="Dump directory, manifest.json, or root containing dumps to migrate.",
    )
    migrate.add_argument(
        "--latest-only",
        action="store_true",
        help="Only migrate the newest manifest found under --out.",
    )

    return parser


def add_browser_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        default=str(DEFAULT_PROFILE_DIR),
        help="Persistent browser profile directory. Keep it out of git.",
    )
    parser.add_argument(
        "--browser-channel",
        default="chrome",
        help="Installed browser channel to use. Use 'chromium' after running playwright install.",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run without a visible browser window."
    )
    parser.add_argument(
        "--slow-mo", type=int, default=0, help="Playwright slow motion in milliseconds."
    )
