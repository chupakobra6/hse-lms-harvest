import json

from hse_lms_harvest.cli import run_migrate
from hse_lms_harvest.manifest import (
    FORMAT_VERSION,
    metadata_matches,
    page_content_fingerprint,
    page_from_data,
)


def test_page_from_data_tolerates_old_manifest_without_new_fields() -> None:
    page = page_from_data(
        {
            "index": 1,
            "url": "https://edu.hse.ru/course/view.php?id=1",
            "final_url": "https://edu.hse.ru/course/view.php?id=1",
            "title": "Course",
            "heading": "Course",
            "text_lines": ["Hello"],
        }
    )

    assert page.source_metadata == {}
    assert page.content_fingerprint == ""
    assert page_content_fingerprint(page)


def test_page_from_data_prunes_ignored_capture_links_from_old_manifests() -> None:
    page = page_from_data(
        {
            "index": 1,
            "url": "https://edu.hse.ru/course/view.php?id=1",
            "title": "Course",
            "heading": "Course",
            "text_lines": [
                "[Оценки](https://edu.hse.ru/grade/report/index.php?id=1)",
                "Материал: [Презентация](https://edu.hse.ru/mod/resource/view.php?id=10)",
            ],
            "links": [
                {"text": "Оценки", "url": "https://edu.hse.ru/grade/report/index.php?id=1"},
                {
                    "text": "Презентация",
                    "url": "https://edu.hse.ru/mod/resource/view.php?id=10",
                    "kind": "file",
                },
            ],
            "downloaded_files": [
                (
                    "files/my-answer.docx sha256:abc source:"
                    "https://edu.hse.ru/pluginfile.php/1/assignsubmission_file/"
                    "submission_files/2/my-answer.docx"
                ),
                (
                    "files/task.pdf sha256:def source:"
                    "https://edu.hse.ru/pluginfile.php/1/mod_resource/content/0/task.pdf"
                ),
            ],
        }
    )

    assert page.text_lines == [
        "Материал: [Презентация](https://edu.hse.ru/mod/resource/view.php?id=10)"
    ]
    assert [link.text for link in page.links] == ["Презентация"]
    assert page.downloaded_files == [
        "files/task.pdf sha256:def source:"
        "https://edu.hse.ru/pluginfile.php/1/mod_resource/content/0/task.pdf"
    ]


def test_metadata_matches_prefers_etag_then_last_modified() -> None:
    assert metadata_matches({"etag": '"v1"'}, {"etag": '"v1"'})
    assert not metadata_matches({"etag": '"v1"'}, {"etag": '"v2"'})
    assert metadata_matches(
        {"last-modified": "Wed, 13 May 2026 10:00:00 GMT", "content-length": "10"},
        {"last-modified": "Wed, 13 May 2026 10:00:00 GMT", "content-length": "10"},
    )
    assert not metadata_matches(
        {"last-modified": "Wed, 13 May 2026 10:00:00 GMT", "content-length": "10"},
        {"last-modified": "Wed, 13 May 2026 10:00:00 GMT", "content-length": "11"},
    )


def test_migrate_regenerates_dump_files_from_existing_manifest(tmp_path) -> None:
    dump = tmp_path / "subject" / "edu.hse.ru-20260513-121815"
    dump.mkdir(parents=True)
    (dump / "manifest.json").write_text(
        json.dumps(
            {
                "requested_url": "https://edu.hse.ru/my/courses.php",
                "source_url": "https://edu.hse.ru/course/view.php?id=1",
                "pages": [
                    {
                        "index": 1,
                        "url": "https://edu.hse.ru/course/view.php?id=1",
                        "final_url": "https://edu.hse.ru/course/view.php?id=1",
                        "title": "Course | Smart LMS",
                        "heading": "Course",
                        "text_lines": ["Материал"],
                        "links": [],
                        "buttons": [],
                        "downloaded_files": [],
                        "errors": [],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    run_migrate(type("Args", (), {"out": str(tmp_path), "latest_only": False})())

    migrated = json.loads((dump / "manifest.json").read_text(encoding="utf-8"))
    assert migrated["format_version"] == FORMAT_VERSION
    assert migrated["pages"][0]["content_fingerprint"]
    assert (dump / "pages" / "0001-course.md").is_file()
    assert (dump / "navigation.md").is_file()
