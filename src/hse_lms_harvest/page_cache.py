from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from .coverage import capture_contract
from .debug import DiagnosticRecorder, RunLogger, safe_url
from .downloads import concise_error, download_files
from .file_cache import FileCache
from .manifest import (
    clone_page_for_reuse,
    latest_manifest_path,
    load_manifest,
    metadata_has_validators,
    metadata_matches,
    page_content_fingerprint,
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
    coverage: dict = field(default_factory=dict)

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
    if getattr(args, "resume_dump", None) or args.reuse_dump:
        reuse_target = (
            Path(getattr(args, "resume_dump", None) or args.reuse_dump).expanduser().resolve()
        )
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
    manifest = load_manifest(manifest_path)
    for page in pages_from_manifest(manifest):
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
        coverage=manifest.get("coverage") or {},
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
    *,
    file_cache: FileCache | None = None,
    downloaded_urls: set[str] | None = None,
) -> PageCapture | None:
    if reuse is None:
        return None

    previous = reuse.get(url)
    if (
        previous is None
        or previous.errors
        or previous.capture_contract != capture_contract(args)
        or previous.content_fingerprint != page_content_fingerprint(previous)
    ):
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
    # HTML validity says nothing about attachment contents. Revalidate files through
    # their own cache; missing blobs are downloaded without repeating browser capture.
    page.downloaded_files = []
    if args.download_files:
        page.downloaded_files = await download_files(
            context,
            out_dir / "files",
            page.links,
            args.url,
            logger,
            downloaded_urls if downloaded_urls is not None else set(),
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
