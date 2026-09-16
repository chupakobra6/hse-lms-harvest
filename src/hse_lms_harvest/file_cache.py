from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .privacy import strip_fragment


@dataclass(frozen=True)
class FileMetadata:
    content_type: str = ""
    content_disposition: str = ""
    content_length: int | None = None
    etag: str = ""
    last_modified: str = ""


class FileCache:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        self.index: dict[str, dict[str, object]] = self._load_index()
        self._verified: dict[Path, tuple[tuple[int, ...], str]] = {}

    def get(self, url: str) -> dict[str, object] | None:
        entry = self.index.get(cache_key(url))
        if not entry:
            return None
        path = self.entry_path(entry)
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            checked = self._verified.get(path)
            digest = checked[1] if checked and checked[0] == signature else file_digest(path)
        except FileNotFoundError:
            return None
        self._verified[path] = (signature, digest)
        if digest != entry.get("sha256"):
            return None
        return entry

    def get_validated(self, url: str, metadata: FileMetadata | None) -> dict[str, object] | None:
        if metadata is None:
            return None
        entry = self.get(url)
        if not entry:
            return None
        if cache_entry_matches(entry, metadata):
            return entry
        return None

    def store(self, url: str, body: bytes, suffix: str, metadata: FileMetadata) -> Path:
        digest = sha256_hex(body)
        suffix = suffix if suffix.startswith(".") else ""
        cache_path = self.files_dir / f"{digest[:2]}" / f"{digest}{suffix}"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not cache_path.exists() or file_digest(cache_path) != digest:
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_bytes(body)
            temporary.replace(cache_path)

        self.index[cache_key(url)] = {
            "path": str(cache_path.relative_to(self.root)),
            "sha256": digest,
            "content_type": metadata.content_type,
            "content_disposition": metadata.content_disposition,
            "content_length": metadata.content_length,
            "etag": metadata.etag,
            "last_modified": metadata.last_modified,
        }
        self.save()
        return cache_path

    def materialize(self, entry: dict[str, object], target: Path) -> Path | None:
        source = self.entry_path(entry)
        if not source.is_file():
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix and not target.suffix:
            target = target.with_suffix(source.suffix)
            target = unique_path_for_cache(target)
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
        return target

    def entry_path(self, entry: dict[str, object]) -> Path:
        path = str(entry.get("path") or "")
        return self.root / path

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def _load_index(self) -> dict[str, dict[str, object]]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


def metadata_from_headers(headers: dict[str, str]) -> FileMetadata:
    normalized = {key.lower(): value for key, value in headers.items()}
    return FileMetadata(
        content_type=normalized.get("content-type", ""),
        content_disposition=normalized.get("content-disposition", ""),
        content_length=parse_content_length(normalized.get("content-length", "")),
        etag=normalized.get("etag", ""),
        last_modified=normalized.get("last-modified", ""),
    )


def cache_entry_matches(entry: dict[str, object], metadata: FileMetadata) -> bool:
    entry_etag = str(entry.get("etag") or "")
    if entry_etag and metadata.etag:
        return entry_etag == metadata.etag

    entry_last_modified = str(entry.get("last_modified") or "")
    entry_length = entry.get("content_length")
    if (
        entry_last_modified
        and metadata.last_modified
        and entry_length is not None
        and metadata.content_length is not None
    ):
        return (
            entry_last_modified == metadata.last_modified
            and entry_length == metadata.content_length
        )

    return False


def parse_content_length(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cache_key(url: str) -> str:
    return sha256_hex(strip_fragment(url).encode("utf-8"))


def sha256_hex(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def unique_path_for_cache(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot allocate unique filename for {path}")


def file_digest(path: Path) -> str:
    import hashlib

    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
