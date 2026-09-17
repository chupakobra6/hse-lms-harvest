import asyncio

from hse_lms_harvest.cli import build_parser
from hse_lms_harvest.netology import is_course_page, is_netology_url, open_assignment_for_reading

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
