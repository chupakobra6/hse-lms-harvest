import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from hse_lms_harvest.cli import build_parser
from hse_lms_harvest.netology import (
    is_course_page,
    is_expected_final_page,
    is_netology_url,
    open_assignment_for_reading,
    prepare_page,
)

START = "https://netology.ru/profile/program/bhebfs-25-abcos-3/schedule"
TASK = "https://netology.ru/profile/program/bhebfs-25-abcos-3/lessons/659457/lesson_items/3552339"


class FakeButton:
    def __init__(self, present: bool) -> None:
        self.present = present
        self.clicked = False

    async def count(self) -> int:
        return int(self.present)

    async def click(self) -> None:
        self.clicked = True


class FakeMarkdown:
    def __init__(self) -> None:
        self.waited = False

    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert (state, timeout) == ("visible", 15_000)
        self.waited = True


class FakePage:
    def __init__(self, present: bool) -> None:
        self.button = FakeButton(present)
        self.markdown = FakeMarkdown()

    def get_by_role(self, role: str, *, name: str, exact: bool) -> FakeButton:
        assert (role, name, exact) == ("button", "Приступить к заданию", True)
        return self.button

    def locator(self, selector: str) -> "FakePage":
        assert selector == '[id^="markdown-"]'
        return self

    @property
    def first(self) -> FakeMarkdown:
        return self.markdown


def test_netology_module_traversal_never_leaves_selected_module() -> None:
    assert is_netology_url(START)
    assert not is_netology_url(START.replace("https://", "http://"))
    assert is_course_page(TASK, START)
    assert is_course_page(START.removesuffix("schedule") + "execution", START)
    assert not is_course_page(TASK.replace("abcos-3", "ks-3"), START)
    assert not is_course_page("https://netology.ru/profile/9953343", START)
    assert not is_course_page(TASK + "?next=other", START)


def test_netology_final_page_stays_in_requested_module_and_view() -> None:
    practice = START.removesuffix("schedule") + "execution"
    assert is_expected_final_page(practice, practice + "/tasks")
    assert is_expected_final_page(TASK, TASK)
    assert not is_expected_final_page(practice, practice.replace("abcos-3", "ks-3") + "/tasks")
    assert not is_expected_final_page(practice, "https://netology.ru/login")
    assert not is_expected_final_page(TASK, START)


def test_starting_assignment_is_explicit_and_never_submits() -> None:
    parser = build_parser()
    assert not parser.parse_args(["harvest", "--url", START]).open_netology_assignments
    assert parser.parse_args(
        ["harvest", "--url", START, "--open-netology-assignments"]
    ).open_netology_assignments
    page = FakePage(True)
    assert asyncio.run(open_assignment_for_reading(page, TASK))
    assert page.button.clicked and page.markdown.waited
    other = FakePage(True)
    assert not asyncio.run(open_assignment_for_reading(other, START))
    assert not other.button.clicked
    started = FakePage(False)
    assert not asyncio.run(open_assignment_for_reading(started, TASK))
    assert not started.button.clicked


def test_practice_readiness_accepts_loaded_empty_state_but_not_loading() -> None:
    async def check() -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=True)
            page = await browser.new_page()
            url = START.removesuffix("schedule") + "execution/tasks"

            class ShortTimeoutPage:
                def __init__(self, current_url: str) -> None:
                    self.url = current_url

                async def wait_for_function(self, expression: str, *, timeout: int) -> None:
                    assert timeout == 15_000
                    await page.wait_for_function(expression, timeout=200)

            await page.set_content(
                "<title>Практика test</title><main>На данном курсе нет практических заданий</main>"
            )
            await prepare_page(ShortTimeoutPage(url), url)
            await page.set_content("<title>Практика test</title><main>Загрузка</main>")
            with pytest.raises(PlaywrightTimeoutError):
                await prepare_page(ShortTimeoutPage(url), url)
            with pytest.raises(PlaywrightError, match="unexpected page"):
                await prepare_page(ShortTimeoutPage(url.replace("abcos-3", "ks-3")), url)
            with pytest.raises(PlaywrightError, match="unexpected page"):
                await prepare_page(ShortTimeoutPage("https://netology.ru/login"), url)
            await browser.close()

    asyncio.run(check())
