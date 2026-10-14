import asyncio

import pytest
from playwright.async_api import async_playwright

from hse_lms_harvest.club404 import (
    read_item,
    strip_relative_badges,
    subject_links,
    validate_item_url,
)


def test_rejects_item_outside_current_course() -> None:
    with pytest.raises(ValueError, match="outside the configured course"):
        validate_item_url("/course/1/tasks/985", "task")
    with pytest.raises(ValueError, match="unexpected host"):
        validate_item_url("https://example.com/course/2/tasks/985", "task")


def test_live_countdown_does_not_change_saved_content() -> None:
    assert strip_relative_badges("Дедлайн 30 сен 2026, 23:5922 час.") == (
        "Дедлайн 30 сен 2026, 23:59"
    )
    assert strip_relative_badges("Время выполнения: 120 минут") == "Время выполнения: 120 минут"


def test_reads_task_and_checks_subject_counts() -> None:
    async def exercise() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    "<main><button>Задания 2</button>"
                    '<a href="/course/2/tasks/985">one</a>'
                    '<a href="/course/2/tasks/985">duplicate</a></main>'
                )
                with pytest.raises(RuntimeError, match="count 2, found 1"):
                    await subject_links(page, "net", "task")
                await page.set_content(
                    '<main><nav aria-label="Хлебные крошки">'
                    '<a href="/course/2/subjects/net">Сети</a></nav>'
                    "<header><h1>Задание 1</h1><p>Дедлайн 1 окт 2026, 23:59</p></header>"
                    "<article><p>Точного дедлайна пока что нет, стоит произвольная дата</p>"
                    '<a href="https://netology.ru/task">Условие</a></article></main>'
                )
                # read_item navigates; supply the local fixture through an intercepted response.
                html = await page.content()
                await page.route(
                    "https://club404.ru/course/2/tasks/985",
                    lambda route: route.fulfill(
                        status=200, body=html, content_type="text/html; charset=utf-8"
                    ),
                )
                item = await read_item(page, "https://club404.ru/course/2/tasks/985", "task", "net")
                assert item.deadline == "1 окт 2026, 23:59"
                assert "произвольная дата" in item.body
                assert item.links == [{"label": "Условие", "url": "https://netology.ru/task"}]
            finally:
                await browser.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "kind,solutions,accepted",
    [("task", True, True), ("task", False, False), ("material", True, False)],
)
def test_task_without_description_keeps_header_and_marks_gap(kind, solutions, accepted) -> None:
    async def exercise() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = await browser.new_page()
                url = f"https://club404.ru/course/2/{kind}s/1200"
                html = (
                    '<main><nav aria-label="Хлебные крошки">'
                    '<a href="/course/2/subjects/net">Сети</a></nav>'
                    "<header><h1>Согласовать тему</h1><p>Дедлайн 10 ноя 2026, 23:59</p></header>"
                    + (
                        "<section><h2>Решения</h2><button>Прикрепить решение</button></section>"
                        if solutions
                        else ""
                    )
                    + "</main>"
                )
                await page.route(
                    url,
                    lambda route: route.fulfill(
                        status=200, body=html, content_type="text/html; charset=utf-8"
                    ),
                )
                if accepted:
                    item = await read_item(page, url, kind, "net")
                    assert item.description_missing is True
                    assert item.title == "Согласовать тему"
                    assert item.deadline == "10 ноя 2026, 23:59"
                    assert "Согласовать тему" in item.body
                    assert "Дедлайн 10 ноя 2026, 23:59" in item.body
                    assert "Прикрепить решение" not in item.body
                else:
                    with pytest.raises(RuntimeError, match="no unique article"):
                        await read_item(page, url, kind, "net")
            finally:
                await browser.close()

    asyncio.run(exercise())
