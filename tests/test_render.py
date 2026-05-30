import json

from hse_lms_harvest.model import Button, Link, PageCapture
from hse_lms_harvest.render import (
    common_link_keys,
    link_key,
    write_navigation,
    write_page_files,
    write_summary,
)


def make_page(index: int, links: list[Link]) -> PageCapture:
    return PageCapture(
        index=index,
        url=f"https://edu.hse.ru/page/{index}",
        final_url=f"https://edu.hse.ru/page/{index}",
        title=f"Page {index} | Smart LMS",
        heading=f"Page {index}",
        text_lines=[f"Content {index}"],
        links=links,
    )


def test_common_link_keys_detects_navigation_noise_only() -> None:
    nav = Link("Мои курсы", "https://edu.hse.ru/my/courses.php", "page")
    pages = [
        make_page(1, [nav, Link("Task 1", "https://edu.hse.ru/mod/assign/view.php?id=1")]),
        make_page(2, [nav, Link("Task 2", "https://edu.hse.ru/mod/assign/view.php?id=2")]),
        make_page(3, [nav, Link("Task 3", "https://edu.hse.ru/mod/assign/view.php?id=3")]),
    ]

    repeated = common_link_keys(pages)

    assert repeated == {link_key(nav)}


def test_write_page_files_suppresses_common_links_only_in_markdown(tmp_path) -> None:
    nav = Link("Мои курсы", "https://edu.hse.ru/my/courses.php", "page")
    task = Link("Task 1", "https://edu.hse.ru/mod/assign/view.php?id=1", "action")
    page = make_page(1, [nav, task])

    write_page_files(tmp_path, page, {link_key(nav)})

    markdown = (tmp_path / "pages" / "0001-page-1.md").read_text(encoding="utf-8")
    page_json = json.loads((tmp_path / "pages" / "0001-page-1.json").read_text(encoding="utf-8"))

    assert "Мои курсы" not in markdown
    assert "Task 1" in markdown
    assert [item["text"] for item in page_json["links"]] == ["Мои курсы", "Task 1"]


def test_write_page_files_uses_compact_markdown_without_url_noise(tmp_path) -> None:
    page = make_page(
        1,
        [
            Link("Task 1", "https://edu.hse.ru/mod/assign/view.php?id=1", "action"),
            Link(
                "https://edu.hse.ru/mod/assign/view.php?id=1",
                "https://edu.hse.ru/mod/assign/view.php?id=1",
            ),
        ],
    )
    page.text_lines = [
        "Материал: [Презентация](https://edu.hse.ru/pluginfile.php/123/mod_resource/content/0/p.pdf)",
        "Source-only https://edu.hse.ru/mod/assign/view.php?id=1",
        "Активные элементы: 2",
        "Перейти в секцию Домашние задания",
        "Редактировать ответ",
        "Комментарии (0)",
    ]
    page.downloaded_files = [
        "CACHED files/презентация.pdf sha256:123456789abc source:https://edu.hse.ru/pluginfile.php/123/p.pdf",
        "SKIP media: https://edu.hse.ru/course/section.php?id=5",
    ]

    write_page_files(tmp_path, page)

    markdown = (tmp_path / "pages" / "0001-page-1.md").read_text(encoding="utf-8")
    page_json = json.loads((tmp_path / "pages" / "0001-page-1.json").read_text(encoding="utf-8"))

    assert "- Source:" not in markdown
    assert "- Title:" not in markdown
    assert "https://edu.hse.ru" not in markdown
    assert "sha256:" not in markdown
    assert "pluginfile.php" not in markdown
    assert "Материал: Презентация" in markdown
    assert "Активные элементы" not in markdown
    assert "Перейти в секцию" not in markdown
    assert "Домашние задания" in markdown
    assert "Редактировать ответ" not in markdown
    assert "- files/презентация.pdf" in markdown
    assert page_json["links"][0]["url"] == "https://edu.hse.ru/mod/assign/view.php?id=1"


def test_write_page_files_keeps_button_labels_without_action_urls(tmp_path) -> None:
    page = make_page(1, [])
    page.buttons = [
        Button(
            "Добавить ответ на задание",
            "button",
            action_url="https://edu.hse.ru/mod/assign/view.php?id=1",
        ),
        Button(
            "Закрыть оглавление курса",
            "button",
            action_url="https://edu.hse.ru/course/view.php?id=1",
        ),
    ]

    write_page_files(tmp_path, page)

    markdown = (tmp_path / "pages" / "0001-page-1.md").read_text(encoding="utf-8")

    assert "- Добавить ответ на задание" in markdown
    assert "Закрыть оглавление курса" not in markdown
    assert "https://edu.hse.ru" not in markdown


def test_write_summary_omits_page_indices_and_urls(tmp_path) -> None:
    nav = Link("Мои курсы", "https://edu.hse.ru/my/courses.php", "page")
    page = make_page(1, [nav])
    page.downloaded_files = [
        "files/задание.docx sha256:123456789abc source:https://edu.hse.ru/pluginfile.php/123/file.docx",
    ]

    write_summary(tmp_path, [page], set(), {link_key(nav)})

    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "1. Page 1" not in summary
    assert "URL:" not in summary
    assert "https://edu.hse.ru" not in summary
    assert "- Page 1 (files: 1)" in summary


def test_write_navigation_builds_local_tree_and_file_index(tmp_path) -> None:
    child = make_page(2, [])
    child.heading = "Task"
    child.downloaded_files = [
        "files/task.pdf sha256:123456789abc source:https://edu.hse.ru/pluginfile.php/123/task.pdf",
    ]
    parent = make_page(
        1,
        [
            Link("Task", child.final_url, "action"),
            Link("Мои курсы", "https://edu.hse.ru/my/courses.php", "page"),
        ],
    )
    parent.heading = "Course"

    write_navigation(tmp_path, [parent, child])

    navigation_md = (tmp_path / "navigation.md").read_text(encoding="utf-8")
    navigation_json = json.loads((tmp_path / "navigation.json").read_text(encoding="utf-8"))

    assert "[Course](<pages/0001-course.md>)" in navigation_md
    assert "  - [Task](<pages/0002-task.md>)" in navigation_md
    assert "[task.pdf](<files/task.pdf>)" in navigation_md
    assert "https://edu.hse.ru" not in navigation_md
    assert "sha256:" not in navigation_md
    assert navigation_json["tree"][0]["md"] == "pages/0001-course.md"
    assert navigation_json["tree"][0]["children"][0]["files"] == ["files/task.pdf"]


def test_write_navigation_keeps_suppressed_common_link_as_root(tmp_path) -> None:
    child = make_page(2, [])
    child.heading = "Common Nav"
    parent = make_page(1, [Link("Common Nav", child.final_url, "page")])
    parent.heading = "Course"

    write_navigation(tmp_path, [parent, child], {link_key(parent.links[0])})

    navigation_json = json.loads((tmp_path / "navigation.json").read_text(encoding="utf-8"))

    assert [node["title"] for node in navigation_json["tree"]] == ["Course", "Common Nav"]
