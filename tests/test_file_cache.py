from hse_lms_harvest.file_cache import (
    FileCache,
    FileMetadata,
    cache_entry_matches,
    metadata_from_headers,
)


def test_metadata_from_headers_is_case_insensitive() -> None:
    metadata = metadata_from_headers(
        {
            "Content-Type": "application/pdf",
            "CONTENT-LENGTH": "42",
            "ETag": '"abc"',
            "Last-Modified": "Tue, 12 May 2026 10:00:00 GMT",
        }
    )

    assert metadata.content_type == "application/pdf"
    assert metadata.content_length == 42
    assert metadata.etag == '"abc"'


def test_cache_entry_matches_by_etag() -> None:
    entry = {"etag": '"abc"', "content_length": 100, "content_type": "application/pdf"}

    assert cache_entry_matches(entry, FileMetadata(etag='"abc"', content_length=200))
    assert not cache_entry_matches(entry, FileMetadata(etag='"changed"', content_length=100))


def test_cache_entry_matches_by_length_and_content_type_when_no_etag() -> None:
    entry = {"content_length": 0, "content_type": "application/pdf; charset=binary"}

    assert cache_entry_matches(
        entry, FileMetadata(content_length=0, content_type="application/pdf")
    )
    assert not cache_entry_matches(
        entry, FileMetadata(content_length=1, content_type="application/pdf")
    )


def test_file_cache_store_validate_and_materialize(tmp_path) -> None:
    cache = FileCache(tmp_path / "cache")
    source_url = "https://edu.hse.ru/pluginfile.php/1/report.pdf"
    metadata = FileMetadata(content_type="application/pdf", content_length=7, etag='"v1"')

    cache.store(source_url, b"content", ".pdf", metadata)
    entry = cache.get_validated(source_url, FileMetadata(content_length=99, etag='"v1"'))
    assert entry is not None

    materialized = cache.materialize(entry, tmp_path / "dump" / "files" / "report.pdf")

    assert materialized is not None
    assert materialized.read_bytes() == b"content"
