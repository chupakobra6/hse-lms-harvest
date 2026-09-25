import asyncio

import pytest

from hse_lms_harvest.cli import build_parser, page_looks_logged_in, run_harvest


class FakeLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    async def inner_text(self, timeout: int) -> str:
        return self.text


class FakePage:
    def __init__(self, url: str, text: str, title: str = "") -> None:
        self.url = url
        self.text = text
        self.title_text = title

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "body"
        return FakeLocator(self.text)

    async def title(self) -> str:
        return self.title_text


def test_harvest_opens_action_pages_by_default() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank"])

    assert args.visit_action_pages is True


def test_harvest_can_skip_action_pages_explicitly() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank", "--skip-action-pages"])

    assert args.visit_action_pages is False


def test_netology_rejects_parallel_assignment_navigation() -> None:
    args = build_parser().parse_args(
        [
            "harvest",
            "--url",
            "https://netology.ru/profile/program/course/schedule",
            "--page-concurrency",
            "2",
            "--open-netology-assignments",
        ]
    )
    with pytest.raises(RuntimeError, match="requires --page-concurrency 1"):
        asyncio.run(run_harvest(args))


def test_harvest_debug_defaults_keep_error_bundles_compact() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank"])

    assert args.debug_dump_mode == "on-error"
    assert args.debug_text_limit == 6000


def test_lms_hse_work_list_page_counts_as_logged_in() -> None:
    page = FakePage(
        "https://lms.hse.ru/?ap_list=",
        "Мои работы (ВКР/КР/Проект)\nКалендарный период\nЗагружено\nСтатус проверки",
    )

    assert asyncio.run(page_looks_logged_in(page, "https://lms.hse.ru/?ap_list="))


def test_lms_hse_work_detail_page_counts_as_logged_in() -> None:
    page = FakePage(
        "https://lms.hse.ru/?ap=&h_id=198E69F5-3D9F-499A-A062-BE98B54D3462",
        "Список работ\nФайл работы\nФайл презентации\nФайл приложения",
        title="Загрузка работы",
    )

    assert asyncio.run(
        page_looks_logged_in(
            page,
            "https://lms.hse.ru/?ap=&h_id=198E69F5-3D9F-499A-A062-BE98B54D3462",
        )
    )
