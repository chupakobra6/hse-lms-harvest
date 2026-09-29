import asyncio

import pytest
from playwright.async_api import async_playwright

from hse_lms_harvest.club404 import read_item, subject_links, validate_item_url


def test_rejects_item_outside_current_course() -> None:
    with pytest.raises(ValueError, match="outside the configured course"):
        validate_item_url("/course/1/tasks/985", "task")
    with pytest.raises(ValueError, match="unexpected host"):
        validate_item_url("https://example.com/course/2/tasks/985", "task")


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
