import asyncio
from pathlib import Path

from hse_lms_harvest.debug import DiagnosticRecorder, RunLogger, ScreenshotPolicy
from hse_lms_harvest.downloads import (
    cache_entry_can_name_target,
    download_files,
    extension_from_metadata,
    is_moodle_fileserver_url,
    metadata_from_cache_entry,
    should_block_page_resource,
    target_with_metadata_extension,
)
from hse_lms_harvest.file_cache import FileCache, FileMetadata
from hse_lms_harvest.model import Link


def test_extension_from_metadata_prefers_real_course_file_types() -> None:
    assert (
        extension_from_metadata(
            FileMetadata(
                content_type=(
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                )
            )
        )
        == ".pptx"
    )
    assert extension_from_metadata(FileMetadata(content_type="image/vnd.djvu")) == ".djvu"
    assert (
        extension_from_metadata(
            FileMetadata(content_disposition='attachment; filename="task.docx"')
        )
        == ".docx"
    )
    assert (
        extension_from_metadata(
            FileMetadata(content_disposition="attachment; filename*=UTF-8''task%2Epdf")
        )
        == ".pdf"
    )


def test_target_with_metadata_extension_appends_after_title_dots(tmp_path) -> None:
    target = tmp_path / "презентация-02.02"

    fixed = target_with_metadata_extension(
        target,
        FileMetadata(
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )

    assert fixed.name == "презентация-02.02.pptx"


def test_detects_moodle_fileserver_urls_without_affecting_resource_pages() -> None:
    assert is_moodle_fileserver_url("https://edu.hse.ru/pluginfile.php/1/material.pdf")
    assert is_moodle_fileserver_url("https://edu.hse.ru/webservice/pluginfile.php/1/a.docx")
    assert not is_moodle_fileserver_url("https://edu.hse.ru/mod/resource/view.php?id=1941152")


def test_download_files_skips_personal_submission_files(tmp_path) -> None:
    logger = RunLogger(tmp_path / "harvest.log")
    diagnostics = DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )
    url = (
        "https://edu.hse.ru/pluginfile.php/1/assignsubmission_file/"
        "submission_files/2/my-answer.docx"
    )

    results = asyncio.run(
        download_files(
            object(),
            tmp_path / "files",
            [Link("Мой ответ", url, "file")],
            "https://edu.hse.ru/course/view.php?id=1",
            logger,
            set(),
            False,
            False,
            80,
            1,
            0,
            1000,
            False,
            None,
            diagnostics,
        )
    )

    assert results == []
    assert not (tmp_path / "files").exists()


def test_blocks_heavy_page_assets_by_default() -> None:
    assert should_block_page_resource(
        "image", "https://edu.hse.ru/theme/image.php/logo.png", load_page_assets=False
    )
    assert should_block_page_resource(
        "document", "https://edu.hse.ru/video/lecture.mp4", load_page_assets=False
    )
    assert not should_block_page_resource(
        "document", "https://edu.hse.ru/course/view.php?id=1", load_page_assets=False
    )
    assert not should_block_page_resource(
        "image", "https://edu.hse.ru/theme/image.php/logo.png", load_page_assets=True
    )


def test_metadata_from_cache_entry_supports_trusted_cache_extension() -> None:
    metadata = metadata_from_cache_entry(
        {
            "content_type": "application/pdf",
            "content_disposition": "",
            "content_length": 123,
            "etag": '"v1"',
            "last_modified": "Wed, 13 May 2026 10:00:00 GMT",
        }
    )

    assert metadata.content_type == "application/pdf"
    assert metadata.content_length == 123
    assert target_with_metadata_extension(Path("lecture"), metadata).name == "lecture.pdf"


def test_cache_without_extension_metadata_is_refreshed_instead_of_materialized(
    tmp_path,
) -> None:
    cache = FileCache(tmp_path / "cache")
    incomplete = {"path": "files/aa/hash"}
    source = cache.entry_path(incomplete)
    source.parent.mkdir(parents=True)
    source.write_bytes(b"data")

    assert not cache_entry_can_name_target(incomplete, tmp_path / "lecture", cache)
    assert cache_entry_can_name_target(incomplete, tmp_path / "lecture.pdf", cache)
    assert cache_entry_can_name_target(
        {"path": "files/aa/hash", "content_type": "application/pdf"},
        tmp_path / "lecture",
        cache,
    )
