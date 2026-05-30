from types import SimpleNamespace

from hse_lms_harvest.capture import (
    build_links,
    capture_has_network_disconnect,
    network_idle_timeout_for_url,
    restore_inline_link_markdown,
    should_queue_link,
)
from hse_lms_harvest.model import Link, PageCapture


def test_network_idle_wait_defaults_only_for_course_overview_pages() -> None:
    args = SimpleNamespace(network_idle_timeout_ms=0, course_network_idle_timeout_ms=1000)

    assert network_idle_timeout_for_url("https://edu.hse.ru/course/view.php?id=1", args) == 1000
    assert network_idle_timeout_for_url("https://edu.hse.ru/mod/page/view.php?id=1", args) == 0


def test_explicit_network_idle_wait_applies_to_all_pages() -> None:
    args = SimpleNamespace(network_idle_timeout_ms=750, course_network_idle_timeout_ms=1000)

    assert network_idle_timeout_for_url("https://edu.hse.ru/mod/page/view.php?id=1", args) == 750


def test_network_disconnect_errors_are_fatal_for_current_subject() -> None:
    assert capture_has_network_disconnect(
        PageCapture(
            index=1,
            url="https://edu.hse.ru/mod/page/view.php?id=1",
            final_url="https://edu.hse.ru/mod/page/view.php?id=1",
            title="",
            heading="",
            text_lines=[],
            errors=["navigation: Page.goto: net::ERR_INTERNET_DISCONNECTED"],
        )
    )


def test_direct_course_crawl_does_not_escape_to_courses_list_or_other_courses() -> None:
    args = SimpleNamespace(course_title=None, visit_action_pages=True, include_external=False)
    start = "https://edu.hse.ru/course/view.php?id=278994"

    assert not should_queue_link(
        Link("Мои курсы", "https://edu.hse.ru/my/courses.php", "page"), start, args
    )
    assert not should_queue_link(
        Link("Другой курс", "https://edu.hse.ru/course/view.php?id=269485", "page"),
        start,
        args,
    )
    assert should_queue_link(
        Link("Задание", "https://edu.hse.ru/mod/assign/view.php?id=1", "action"),
        start,
        args,
    )


def test_crawl_skips_state_changing_and_filtered_moodle_links() -> None:
    args = SimpleNamespace(course_title=None, visit_action_pages=True, include_external=False)
    start = "https://edu.hse.ru/course/view.php?id=253814"

    assert not should_queue_link(
        Link(
            "Подписаться",
            "https://edu.hse.ru/mod/forum/subscribe.php?id=1&sesskey=secret",
            "page",
        ),
        start,
        args,
    )
    assert not should_queue_link(
        Link(
            "А",
            "https://edu.hse.ru/mod/glossary/view.php?id=1&mode=letter&hook=A",
            "page",
        ),
        start,
        args,
    )
    assert not should_queue_link(
        Link(
            "Термин",
            "https://edu.hse.ru/mod/glossary/showentry.php?eid=93217",
            "page",
        ),
        start,
        args,
    )
    assert not should_queue_link(
        Link(
            "Просмотр попытки",
            "https://edu.hse.ru/mod/quiz/review.php?attempt=1&cmid=2",
            "page",
        ),
        start,
        args,
    )
    assert not should_queue_link(
        Link(
            "Отчёт H5P",
            "https://edu.hse.ru/mod/h5pactivity/report.php?a=1&userid=2",
            "page",
        ),
        start,
        args,
    )
    assert should_queue_link(
        Link("Глоссарий", "https://edu.hse.ru/mod/glossary/view.php?id=1", "page"),
        start,
        args,
    )


def test_restore_inline_link_markdown_preserves_hyperlinks_in_text() -> None:
    text = "Материал: ⟦HSE_LMS_LINK_0⟧"
    raw_links = [{"text": "Презентация", "href": "/mod/resource/view.php?id=10", "title": ""}]

    assert restore_inline_link_markdown(
        text, raw_links, "https://edu.hse.ru/course/view.php?id=1"
    ) == ("Материал: [Презентация](https://edu.hse.ru/mod/resource/view.php?id=10)")


def test_restore_inline_link_markdown_keeps_legacy_range_markers() -> None:
    text = "Материал: ⟦HSE_LMS_LINK_0_START⟧Презентация\nMSF⟦HSE_LMS_LINK_0_END⟧"
    raw_links = [{"text": "Презентация MSF", "href": "/mod/resource/view.php?id=10", "title": ""}]

    assert restore_inline_link_markdown(
        text, raw_links, "https://edu.hse.ru/course/view.php?id=1"
    ) == ("Материал: [Презентация MSF](https://edu.hse.ru/mod/resource/view.php?id=10)")


def test_restore_inline_link_markdown_uses_title_for_icon_only_links() -> None:
    text = "Материал: ⟦HSE_LMS_LINK_0⟧"
    raw_links = [{"text": "", "href": "/mod/resource/view.php?id=10", "title": "Скачать файл"}]

    assert restore_inline_link_markdown(
        text, raw_links, "https://edu.hse.ru/course/view.php?id=1"
    ) == ("Материал: [Скачать файл](https://edu.hse.ru/mod/resource/view.php?id=10)")


def test_restore_inline_link_markdown_redacts_sensitive_link_queries() -> None:
    text = "Ссылка: ⟦HSE_LMS_LINK_0⟧"
    raw_links = [
        {
            "text": "Материал",
            "href": "https://edu.hse.ru/mod/page/view.php?sesskey=secret&id=1",
            "title": "",
        }
    ]

    restored = restore_inline_link_markdown(
        text, raw_links, "https://edu.hse.ru/course/view.php?id=1"
    )

    assert "secret" not in restored
    assert restored == (
        "Ссылка: [Материал](https://edu.hse.ru/mod/page/view.php?sesskey=%5BREDACTED%5D&id=1)"
    )


def test_restore_inline_link_markdown_omits_logout_links() -> None:
    text = "Выход: ⟦HSE_LMS_LINK_0⟧"
    raw_links = [
        {
            "text": "Выйти",
            "href": "https://edu.hse.ru/login/logout.php?sesskey=secret&id=1",
            "title": "",
        }
    ]

    assert (
        restore_inline_link_markdown(
            text,
            raw_links,
            "https://edu.hse.ru/course/view.php?id=1",
        )
        == "Выход: "
    )


def test_restore_inline_link_markdown_omits_personal_submission_file_links() -> None:
    text = "Ответ: ⟦HSE_LMS_LINK_0⟧"
    raw_links = [
        {
            "text": "personal_submission.docx",
            "href": (
                "https://edu.hse.ru/pluginfile.php/1/assignsubmission_file/"
                "submission_files/2/my-answer.docx?forcedownload=1"
            ),
            "title": "",
        }
    ]

    restored = restore_inline_link_markdown(
        text,
        raw_links,
        "https://edu.hse.ru/mod/assign/view.php?id=1",
    )

    assert restored == "Ответ: "


def test_capture_omits_grade_and_attempt_report_links() -> None:
    text = "Оценки: ⟦HSE_LMS_LINK_0⟧ Просмотр: ⟦HSE_LMS_LINK_1⟧ Материал: ⟦HSE_LMS_LINK_2⟧"
    raw_links = [
        {"text": "Оценки", "href": "/grade/report/index.php?id=1", "title": ""},
        {"text": "Просмотр", "href": "/mod/quiz/review.php?attempt=1&cmid=2", "title": ""},
        {"text": "Презентация", "href": "/mod/resource/view.php?id=10", "title": ""},
    ]

    restored = restore_inline_link_markdown(
        text,
        raw_links,
        "https://edu.hse.ru/course/view.php?id=1",
    )
    links = build_links(raw_links, "https://edu.hse.ru/course/view.php?id=1")

    assert restored == (
        "Оценки:  Просмотр:  Материал: "
        "[Презентация](https://edu.hse.ru/mod/resource/view.php?id=10)"
    )
    assert [link.text for link in links] == ["Презентация"]
