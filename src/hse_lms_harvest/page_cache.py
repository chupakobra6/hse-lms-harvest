from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from .debug import DiagnosticRecorder, RunLogger, safe_url
from .downloads import concise_error
from .manifest import (
    clone_page_for_reuse,
    copy_reused_files,
    latest_manifest_path,
    load_manifest,
    metadata_has_validators,
    metadata_matches,
    pages_from_manifest,
    response_metadata,
)
from .model import PageCapture
from .privacy import strip_fragment


@dataclass
class PageReuseIndex:
    manifest_path: Path
    dump_dir: Path
    pages_by_url: dict[str, PageCapture]

    def get(self, url: str) -> PageCapture | None:
        return self.pages_by_url.get(strip_fragment(url))


def load_page_reuse_index(
    args: argparse.Namespace,
    out_root: Path,
    out_dir: Path,
    logger: RunLogger,
) -> PageReuseIndex | None:
    if args.page_cache == "off":
        logger.log("page cache disabled")
        return None

    manifest_path: Path | None
    if args.reuse_dump:
        reuse_target = Path(args.reuse_dump).expanduser().resolve()
        manifest_path = (
            reuse_target if reuse_target.name == "manifest.json" else reuse_target / "manifest.json"
        )
        if not manifest_path.is_file():
            raise RuntimeError(f"reuse dump manifest not found: {manifest_path}")
    else:
        manifest_path = latest_manifest_path(out_root, exclude=out_dir)

    if manifest_path is None:
        logger.log("page cache has no previous manifest")
        return None

    pages_by_url: dict[str, PageCapture] = {}
    for page in pages_from_manifest(load_manifest(manifest_path)):
        for url in (page.url, page.final_url):
            key = strip_fragment(url)
            if key:
                pages_by_url.setdefault(key, page)

    if not pages_by_url:
        logger.log(f"page cache empty in {manifest_path}")
        return None

    logger.log(f"page cache loaded {len(pages_by_url)} URLs from {manifest_path.parent}")
    return PageReuseIndex(
        manifest_path=manifest_path,
        dump_dir=manifest_path.parent,
        pages_by_url=pages_by_url,
    )


async def maybe_reuse_page(
    context: BrowserContext,
    url: str,
    index: int,
    args: argparse.Namespace,
    reuse: PageReuseIndex | None,
    out_dir: Path,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
) -> PageCapture | None:
    if reuse is None:
        return None

    previous = reuse.get(url)
    if previous is None:
        return None

    if args.page_cache == "validate":
        previous_metadata = previous.source_metadata
        if not metadata_has_validators(previous_metadata):
            return None
        current_metadata = await fetch_page_head_metadata(
            context,
            url,
            timeout_ms=args.page_head_timeout_ms,
            logger=logger,
            diagnostics=diagnostics,
            page_index=index,
        )
        if current_metadata is None:
            logger.log(f"[{index}/{args.max_pages}] page cache miss no validators {safe_url(url)}")
            return None
        if not metadata_matches(previous_metadata, current_metadata):
            logger.log(f"[{index}/{args.max_pages}] page cache miss changed {safe_url(url)}")
            return None

    page = clone_page_for_reuse(previous, index=index, reused_from=reuse.dump_dir)
    copy_reused_files(page, reuse.dump_dir, out_dir)
    logger.log(f"[{index}/{args.max_pages}] reused page cache {safe_url(url)}")
    diagnostics.event(
        "info",
        "page_cache_reused",
        "Reused page from previous manifest",
        page_index=index,
        url=url,
        details={"mode": args.page_cache, "source": str(reuse.manifest_path)},
    )
    return page


async def fetch_page_head_metadata(
    context: BrowserContext,
    url: str,
    *,
    timeout_ms: int,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
    page_index: int,
) -> dict[str, str] | None:
    if timeout_ms <= 0:
        return None
    try:
        response = await context.request.head(url, timeout=timeout_ms)
    except PlaywrightError as exc:
        logger.log(f"page HEAD failed for {safe_url(url)}: {concise_error(exc)}")
        diagnostics.warning(
            "page_head_failed",
            "Page HEAD validation failed; live capture will continue",
            page_index=page_index,
            url=url,
            details={"exception": concise_error(exc)},
        )
    else:
        if response.ok:
            metadata = response_metadata(response.headers)
            if metadata_has_validators(metadata):
                return metadata
            logger.log(f"page HEAD had no validators for {safe_url(url)}")
        else:
            logger.log(f"page HEAD skipped validation status={response.status} {safe_url(url)}")

    try:
        response = await context.request.get(url, timeout=max(timeout_ms, 3_000))
    except PlaywrightError as exc:
        logger.log(f"page GET metadata probe failed for {safe_url(url)}: {concise_error(exc)}")
        diagnostics.warning(
            "page_get_probe_failed",
            "Page GET metadata probe failed; live capture will continue",
            page_index=page_index,
            url=url,
            details={"exception": concise_error(exc)},
        )
        return None
    if not response.ok:
        logger.log(f"page GET metadata probe status={response.status} {safe_url(url)}")
        return None
    metadata = response_metadata(response.headers)
    return metadata if metadata_has_validators(metadata) else None


def mark_reused_downloads(page: PageCapture, downloaded_urls: set[str]) -> None:
    for item in page.downloaded_files:
        if " source:" not in item:
            continue
        source = item.split(" source:", 1)[1].strip()
        if source:
            downloaded_urls.add(strip_fragment(source))
