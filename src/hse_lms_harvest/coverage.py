from __future__ import annotations

import argparse

from .model import PageCapture
from .privacy import redact_url, strip_fragment

CAPTURE_VERSION = 4
COVERAGE_VERSION = 1


def capture_contract(args: argparse.Namespace) -> dict[str, object]:
    """Settings that change extracted HTML, independently of attachment policy."""
    return {
        "capture_version": CAPTURE_VERSION,
        "open_netology_assignments": getattr(args, "open_netology_assignments", False),
        **{
            name: getattr(args, name)
            for name in (
                "visit_action_pages",
                "allow_state_changes",
                "load_page_assets",
                "network_idle_timeout_ms",
                "course_network_idle_timeout_ms",
            )
        },
    }


def coverage_scope(args: argparse.Namespace, start_url: str) -> dict[str, object]:
    return {
        "source_url": redact_url(strip_fragment(start_url)),
        **capture_contract(args),
        "assignments_only": getattr(args, "assignments_only", False),
        **{
            name: getattr(args, name)
            for name in (
                "include_external",
                "download_files",
                "download_media",
                "skip_lms_file_server",
                "max_file_mb",
            )
        },
    }


def page_confirmed(page: PageCapture) -> bool:
    return not page.errors and not any(item.startswith("ERROR ") for item in page.downloaded_files)


def make_coverage(
    args: argparse.Namespace,
    start_url: str,
    pages: list[PageCapture],
    remaining: list[str],
    *,
    stop_reason: str = "",
) -> dict[str, object]:
    failed = [strip_fragment(page.url) for page in pages if not page_confirmed(page)]
    pending = list(dict.fromkeys([*map(strip_fragment, remaining), *failed]))
    trusted = args.page_cache == "trust" or (args.download_files and args.trust_file_cache)
    status = "unknown" if trusted else "partial" if pending or stop_reason else "complete"
    return {
        "version": COVERAGE_VERSION,
        "status": status,
        "scope": coverage_scope(args, start_url),
        "confirmed_urls": []
        if trusted
        else [strip_fragment(page.url) for page in pages if page_confirmed(page)],
        "remaining_urls": pending,
        "stop_reason": "unverified_cache"
        if trusted
        else (
            stop_reason or ("capture_error" if failed else "max_pages" if pending else "exhausted")
        ),
    }
