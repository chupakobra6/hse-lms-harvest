import json
from types import SimpleNamespace

from hse_lms_harvest.debug import RunLogger
from hse_lms_harvest.model import PageCapture
from hse_lms_harvest.page_cache import load_page_reuse_index, mark_reused_downloads


def test_load_page_reuse_index_maps_url_and_final_url(tmp_path) -> None:
    previous = tmp_path / "edu.hse.ru-20260514-120000"
    previous.mkdir()
    manifest_path = previous / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "index": 1,
                        "url": "https://edu.hse.ru/mod/page/view.php?id=1#section",
                        "final_url": "https://edu.hse.ru/mod/page/view.php?id=1&redirect=0",
                        "title": "Page",
                        "heading": "Page",
                        "text_lines": ["Text"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(page_cache="validate", reuse_dump=str(manifest_path))

    reuse = load_page_reuse_index(args, tmp_path, tmp_path / "current", RunLogger(tmp_path / "log"))

    assert reuse is not None
    assert reuse.get("https://edu.hse.ru/mod/page/view.php?id=1#another") is not None
    assert reuse.get("https://edu.hse.ru/mod/page/view.php?id=1&redirect=0") is not None


def test_load_page_reuse_index_can_be_disabled(tmp_path) -> None:
    args = SimpleNamespace(page_cache="off", reuse_dump="")

    assert (
        load_page_reuse_index(args, tmp_path, tmp_path / "current", RunLogger(tmp_path / "log"))
        is None
    )


def test_mark_reused_downloads_tracks_source_urls_without_fragments() -> None:
    downloaded_urls: set[str] = set()

    mark_reused_downloads(
        PageCapture(
            index=1,
            url="https://edu.hse.ru/course/view.php?id=1",
            final_url="https://edu.hse.ru/course/view.php?id=1",
            title="Course",
            heading="Course",
            text_lines=[],
            downloaded_files=[
                "files/task.pdf sha256:abc source:https://edu.hse.ru/pluginfile.php/1/task.pdf#frag",
                "files/no-source.pdf",
            ],
        ),
        downloaded_urls,
    )

    assert downloaded_urls == {"https://edu.hse.ru/pluginfile.php/1/task.pdf"}
