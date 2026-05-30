from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from .classify import (
    FILE_EXTENSIONS,
    MEDIA_EXTENSIONS,
    filename_from_url,
    is_media_content_type,
    is_media_link,
    is_moodle_artifact_link,
    is_submission_file_link,
)
from .debug import DiagnosticRecorder, RunLogger, safe_error, safe_url
from .file_cache import FileCache, FileMetadata, metadata_from_headers
from .model import Link
from .privacy import strip_fragment
from .sizes import format_bytes
from .text import stable_slug

BLOCKED_PAGE_RESOURCE_TYPES = {"font", "image", "media"}
MOODLE_FILESERVER_PATHS = ("/pluginfile.php", "/webservice/pluginfile.php")
EXTRA_DOWNLOAD_EXTENSIONS = {".djvu"}
KNOWN_DOWNLOAD_EXTENSIONS = FILE_EXTENSIONS | EXTRA_DOWNLOAD_EXTENSIONS
CONTENT_TYPE_EXTENSIONS = {
    "application/msword": ".doc",
    "application/pdf": ".pdf",
    "application/rtf": ".rtf",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/zip": ".zip",
    "image/vnd.djvu": ".djvu",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/plain": ".txt",
}


async def download_files(
    context: BrowserContext,
    files_dir: Path,
    links: list[Link],
    start_url: str,
    logger: RunLogger,
    downloaded_urls: set[str],
    download_media: bool,
    skip_lms_file_server: bool,
    max_file_mb: int,
    download_concurrency: int,
    file_head_timeout_ms: int,
    file_download_timeout_ms: int,
    trust_file_cache: bool,
    file_cache: FileCache | None,
    diagnostics: DiagnosticRecorder,
) -> list[str]:
    downloaded: list[str] = []
    start_host = urlparse(start_url).netloc
    max_file_bytes = max_file_mb * 1024 * 1024 if max_file_mb > 0 else 0
    reserved_targets: set[Path] = set()
    candidates: list[tuple[Link, Path]] = []

    for link in links:
        if link.kind not in {"file", "maybe-file"}:
            continue
        if urlparse(link.url).netloc != start_host:
            continue
        source_key = strip_fragment(link.url)
        if source_key in downloaded_urls:
            continue
        downloaded_urls.add(source_key)

        if not download_media and is_media_link(link.text, link.url):
            downloaded.append(f"SKIP media: {safe_url(link.url)}")
            logger.log(f"skipped media {safe_url(link.url)}")
            continue
        if is_moodle_artifact_link(link.text, link.url):
            downloaded.append(f"SKIP artifact: {safe_url(link.url)}")
            logger.log(f"skipped artifact {safe_url(link.url)}")
            continue
        if is_submission_file_link(link.url):
            logger.log(f"skipped submission-file {safe_url(link.url)}")
            continue
        if skip_lms_file_server and is_moodle_fileserver_url(link.url):
            downloaded.append(f"SKIP lms-file-server: {safe_url(link.url)}")
            logger.log(f"skipped lms-file-server {safe_url(link.url)}")
            continue

        fallback = filename_from_url(link.url, stable_slug(link.text, fallback="file"))
        target = reserve_unique_path(files_dir / safe_filename(fallback), reserved_targets)
        candidates.append((link, target))

    semaphore = asyncio.Semaphore(max(1, download_concurrency))

    async def run_candidate(link: Link, target: Path) -> str:
        async with semaphore:
            return await download_one_file(
                context,
                files_dir,
                link,
                target,
                logger,
                download_media,
                max_file_bytes,
                file_head_timeout_ms,
                file_download_timeout_ms,
                trust_file_cache,
                file_cache,
                diagnostics,
            )

    results = await asyncio.gather(*(run_candidate(link, target) for link, target in candidates))
    downloaded.extend(result for result in results if result)
    return downloaded


async def download_one_file(
    context: BrowserContext,
    files_dir: Path,
    link: Link,
    target: Path,
    logger: RunLogger,
    download_media: bool,
    max_file_bytes: int,
    file_head_timeout_ms: int,
    file_download_timeout_ms: int,
    trust_file_cache: bool,
    file_cache: FileCache | None,
    diagnostics: DiagnosticRecorder,
) -> str:
    try:
        if trust_file_cache and file_cache is not None:
            cached = file_cache.get(link.url)
            if cached:
                target = target_with_metadata_extension(target, metadata_from_cache_entry(cached))
                materialized = file_cache.materialize(cached, target)
                if materialized is not None:
                    digest = str(cached.get("sha256") or "")[:12]
                    logger.log(f"trusted cache {materialized.relative_to(files_dir.parent)}")
                    return (
                        f"CACHED {materialized.relative_to(files_dir.parent)} sha256:{digest} "
                        f"source:{safe_url(link.url)}"
                    )

        head_metadata = await fetch_head_metadata(
            context, link.url, logger, diagnostics, timeout_ms=file_head_timeout_ms
        )
        skip_reason = download_skip_reason(
            link.url,
            head_metadata,
            download_media=download_media,
            max_file_bytes=max_file_bytes,
        )
        if skip_reason:
            logger.log(skip_reason.lower())
            return skip_reason

        if file_cache is not None:
            cached = file_cache.get_validated(link.url, head_metadata)
            if cached:
                cached_metadata = metadata_from_cache_entry(cached)
                if cache_entry_can_name_target(cached, target, file_cache):
                    target = target_with_metadata_extension(
                        target, head_metadata or cached_metadata
                    )
                    materialized = file_cache.materialize(cached, target)
                    if materialized is not None:
                        digest = str(cached.get("sha256") or "")[:12]
                        logger.log(f"cached {materialized.relative_to(files_dir.parent)}")
                        return (
                            f"CACHED {materialized.relative_to(files_dir.parent)} sha256:{digest} "
                            f"source:{safe_url(link.url)}"
                        )
                else:
                    logger.log(f"cache metadata incomplete; refreshing {safe_url(link.url)}")

        response = await context.request.get(link.url, timeout=max(1, file_download_timeout_ms))
        if not response.ok:
            message = f"Download returned HTTP {response.status}"
            logger.log(f"{message}: {safe_url(link.url)}")
            await diagnostics.error(
                "file_download_http_error",
                message,
                url=link.url,
                details={"status": response.status, "label": link.text},
            )
            return f"ERROR {response.status}: {safe_url(link.url)}"

        metadata = metadata_from_headers(response.headers)
        skip_reason = download_skip_reason(
            link.url,
            metadata,
            download_media=download_media,
            max_file_bytes=max_file_bytes,
        )
        if skip_reason:
            logger.log(skip_reason.lower())
            return skip_reason

        body = await response.body()
        target = target_with_metadata_extension(target, metadata)

        if file_cache is not None:
            cache_path = file_cache.store(link.url, body, target.suffix, metadata)
            materialized = file_cache.materialize(
                {"path": str(cache_path.relative_to(file_cache.root))}, target
            )
            if materialized is None:
                files_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                materialized = target
        else:
            files_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            materialized = target

        digest = hashlib_short(body)
        logger.log(f"downloaded {materialized.relative_to(files_dir.parent)}")
        return (
            f"{materialized.relative_to(files_dir.parent)} sha256:{digest} "
            f"source:{safe_url(link.url)}"
        )
    except PlaywrightError as exc:
        logger.log(f"download failed for {safe_url(link.url)}: {safe_error(exc)}")
        await diagnostics.error(
            "file_download_failed",
            "File download failed",
            url=link.url,
            exc=exc,
            details={"label": link.text},
        )
        return f"ERROR {safe_error(exc)}: {safe_url(link.url)}"


async def fetch_head_metadata(
    context: BrowserContext,
    url: str,
    logger: RunLogger,
    diagnostics: DiagnosticRecorder,
    *,
    timeout_ms: int,
) -> FileMetadata | None:
    if timeout_ms <= 0:
        logger.log(f"HEAD skipped by config {safe_url(url)}")
        return None
    try:
        response = await context.request.head(url, timeout=timeout_ms)
    except PlaywrightError as exc:
        message = concise_error(exc)
        logger.log(f"HEAD failed for {safe_url(url)}: {message}")
        diagnostics.warning(
            "file_head_failed",
            "HEAD metadata request failed; GET validation may still continue",
            url=url,
            details={"exception": message},
        )
        return None
    if not response.ok:
        logger.log(f"HEAD skipped validation status={response.status} {safe_url(url)}")
        diagnostics.warning(
            "file_head_http_error",
            "HEAD metadata request returned non-OK status; GET validation may still continue",
            url=url,
            details={"status": response.status},
        )
        return None
    return metadata_from_headers(response.headers)


async def block_heavy_page_resource(route: Any) -> None:
    request = route.request
    if should_block_page_resource(request.resource_type, request.url, load_page_assets=False):
        await route.abort()
        return
    await route.continue_()


def should_block_page_resource(resource_type: str, url: str, *, load_page_assets: bool) -> bool:
    if load_page_assets:
        return False
    if resource_type in BLOCKED_PAGE_RESOURCE_TYPES:
        return True
    path = urlparse(url).path.lower()
    return Path(path).suffix in MEDIA_EXTENSIONS


def download_skip_reason(
    url: str,
    metadata: FileMetadata | None,
    *,
    download_media: bool,
    max_file_bytes: int,
) -> str | None:
    if metadata is None:
        return None
    if not download_media and is_media_content_type(metadata.content_type):
        return f"SKIP media: {safe_url(url)}"
    if not download_media and is_media_link(
        metadata.content_disposition, metadata.content_disposition
    ):
        return f"SKIP media: {safe_url(url)}"
    if "text/html" in metadata.content_type:
        return f"SKIP html: {safe_url(url)}"
    if max_file_bytes and metadata.content_length and metadata.content_length > max_file_bytes:
        return f"SKIP large ({format_bytes(metadata.content_length)}): {safe_url(url)}"
    return None


def is_moodle_fileserver_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(marker in path for marker in MOODLE_FILESERVER_PATHS)


def metadata_from_cache_entry(entry: dict[str, object]) -> FileMetadata:
    content_length = entry.get("content_length")
    return FileMetadata(
        content_type=str(entry.get("content_type") or ""),
        content_disposition=str(entry.get("content_disposition") or ""),
        content_length=content_length if isinstance(content_length, int) else None,
        etag=str(entry.get("etag") or ""),
        last_modified=str(entry.get("last_modified") or ""),
    )


def target_with_metadata_extension(target: Path, metadata: FileMetadata | None) -> Path:
    if has_known_download_extension(target):
        return target
    extension = extension_from_metadata(metadata)
    if not extension:
        return target
    return unique_path(target.with_name(f"{target.name}{extension}"))


def cache_entry_can_name_target(
    entry: dict[str, object], target: Path, file_cache: FileCache
) -> bool:
    if has_known_download_extension(target):
        return True
    if file_cache.entry_path(entry).suffix:
        return True
    return bool(extension_from_metadata(metadata_from_cache_entry(entry)))


def extension_from_metadata(metadata: FileMetadata | None) -> str:
    if metadata is None:
        return ""
    disposition_extension = extension_from_content_disposition(metadata.content_disposition)
    if disposition_extension:
        return disposition_extension
    content_type = metadata.content_type.split(";", 1)[0].strip().lower()
    if not content_type:
        return ""
    mapped = CONTENT_TYPE_EXTENSIONS.get(content_type, "")
    if mapped:
        return mapped
    guessed = mimetypes.guess_extension(content_type) or ""
    if guessed == ".jpe":
        return ".jpg"
    if guessed in KNOWN_DOWNLOAD_EXTENSIONS:
        return guessed
    return ""


def extension_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)", value, flags=re.IGNORECASE)
    if not match:
        return ""
    filename = unquote(match.group(1).strip().strip('"'))
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in KNOWN_DOWNLOAD_EXTENSIONS else ""


def has_known_download_extension(path: Path) -> bool:
    return path.suffix.lower() in KNOWN_DOWNLOAD_EXTENSIONS


def hashlib_short(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()[:12]


def concise_error(exc: Exception) -> str:
    return safe_error(exc).splitlines()[0].strip()


def safe_filename(value: str) -> str:
    name = stable_slug(value, fallback="file", max_len=120)
    if "." in value and "." not in name:
        suffix = Path(value).suffix
        if suffix.lower() in KNOWN_DOWNLOAD_EXTENSIONS:
            name = f"{name}{suffix.lower()}"
    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate unique filename for {path}")


def reserve_unique_path(path: Path, reserved: set[Path]) -> Path:
    candidate = unique_path(path)
    if candidate not in reserved:
        reserved.add(candidate)
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(2, 10_000):
        next_candidate = candidate.with_name(f"{stem}-{index}{suffix}")
        if next_candidate.exists() or next_candidate in reserved:
            continue
        reserved.add(next_candidate)
        return next_candidate
    raise RuntimeError(f"cannot reserve unique filename for {path}")
