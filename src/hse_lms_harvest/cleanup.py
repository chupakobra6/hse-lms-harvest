from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .classify import MEDIA_EXTENSIONS, is_moodle_artifact_link
from .sizes import format_bytes, path_size


def run_cleanup(args: argparse.Namespace) -> None:
    clean_screenshots = args.all or args.screenshots
    clean_media = args.all or args.media
    clean_artifacts = args.all or args.artifacts
    clean_browser_cache = args.all or args.browser_cache
    clean_file_cache = args.file_cache
    if not (
        clean_screenshots
        or clean_media
        or clean_artifacts
        or clean_browser_cache
        or clean_file_cache
    ):
        raise RuntimeError(
            "Choose at least one cleanup target: --screenshots, --media, --artifacts, "
            "--browser-cache, --file-cache, or --all."
        )

    reclaimed = 0
    removed = 0
    out_dir = Path(args.out)
    profile_dir = Path(args.profile)

    if clean_screenshots:
        count, size = cleanup_screenshots(out_dir, args.retain_screenshots, args.dry_run)
        removed += count
        reclaimed += size
    if clean_media:
        count, size = cleanup_media(out_dir, args.dry_run)
        removed += count
        reclaimed += size
    if clean_artifacts:
        count, size = cleanup_attachment_artifacts(out_dir, args.dry_run)
        removed += count
        reclaimed += size
    if clean_browser_cache:
        count, size = cleanup_browser_cache(profile_dir, args.dry_run)
        removed += count
        reclaimed += size
    if clean_file_cache:
        count, size = cleanup_file_cache(Path(args.file_cache_dir), args.dry_run)
        removed += count
        reclaimed += size

    action = "Would remove" if args.dry_run else "Removed"
    print(f"{action} {removed} paths, {format_bytes(reclaimed)}.")


def cleanup_screenshots(out_dir: Path, retain: int, dry_run: bool) -> tuple[int, int]:
    removed = 0
    reclaimed = 0
    for screenshots_dir in out_dir.rglob("debug/screenshots"):
        if not screenshots_dir.is_dir():
            continue
        files = sorted(
            [path for path in screenshots_dir.iterdir() if path.is_file()],
            key=lambda path: path.stat().st_mtime,
        )
        for path in files[: max(0, len(files) - retain)]:
            removed += 1
            reclaimed += path.stat().st_size
            if not dry_run:
                path.unlink(missing_ok=True)
    return removed, reclaimed


def cleanup_media(out_dir: Path, dry_run: bool) -> tuple[int, int]:
    removed = 0
    reclaimed = 0
    for files_dir in iter_dump_files_dirs(out_dir):
        for path in files_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            removed += 1
            reclaimed += path.stat().st_size
            if not dry_run:
                path.unlink(missing_ok=True)
    return removed, reclaimed


def cleanup_attachment_artifacts(out_dir: Path, dry_run: bool) -> tuple[int, int]:
    removed = 0
    reclaimed = 0
    for files_dir in iter_dump_files_dirs(out_dir):
        for path in files_dir.iterdir():
            if not path.is_file() or not is_moodle_artifact_link(path.name, path.name):
                continue
            removed += 1
            reclaimed += path.stat().st_size
            if not dry_run:
                path.unlink(missing_ok=True)
    return removed, reclaimed


def iter_dump_files_dirs(out_dir: Path) -> list[Path]:
    result: list[Path] = []
    for files_dir in out_dir.rglob("files"):
        if not files_dir.is_dir():
            continue
        if "_file-cache" in files_dir.parts:
            continue
        if (files_dir.parent / "manifest.json").is_file() or (
            files_dir.parent / "harvest.log"
        ).is_file():
            result.append(files_dir)
    return result


def cleanup_browser_cache(profile_dir: Path, dry_run: bool) -> tuple[int, int]:
    cache_names = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "GraphiteDawnCache",
        "GrShaderCache",
        "ShaderCache",
        "component_crx_cache",
        "extensions_crx_cache",
        "screen_ai",
    }
    cache_suffixes = {
        Path("Default") / "Service Worker" / "CacheStorage",
        Path("Default") / "CacheStorage",
        Path("Default") / "IndexedDB",
    }
    cache_file_prefixes = ("BrowserMetrics",)
    removed = 0
    reclaimed = 0
    if not profile_dir.exists():
        return removed, reclaimed
    for path in profile_dir.rglob("*"):
        if path.is_file() and path.name.startswith(cache_file_prefixes):
            removed += 1
            reclaimed += path.stat().st_size
            if not dry_run:
                path.unlink(missing_ok=True)
            continue
        if not path.is_dir():
            continue
        relative = path.relative_to(profile_dir)
        should_remove = path.name in cache_names or relative in cache_suffixes
        if not should_remove:
            continue
        removed += 1
        reclaimed += path_size(path)
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
    return removed, reclaimed


def cleanup_file_cache(cache_dir: Path, dry_run: bool) -> tuple[int, int]:
    cache_dir = cache_dir.expanduser().resolve()
    if not cache_dir.exists():
        return 0, 0
    size = path_size(cache_dir)
    if not dry_run:
        shutil.rmtree(cache_dir, ignore_errors=True)
    return 1, size
