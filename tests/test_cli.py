import asyncio

from hse_lms_harvest.cli import build_parser, page_looks_logged_in


class FakeLocator:
    def __init__(self, text: str) -> None:
        self.text = text

    async def inner_text(self, timeout: int) -> str:
        return self.text


class FakePage:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.text = text

    def locator(self, selector: str) -> FakeLocator:
        assert selector == "body"
        return FakeLocator(self.text)


def test_harvest_opens_action_pages_by_default() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank"])

    assert args.visit_action_pages is True


def test_harvest_can_skip_action_pages_explicitly() -> None:
    parser = build_parser()

    args = parser.parse_args(["harvest", "--url", "about:blank", "--skip-action-pages"])

    assert args.visit_action_pages is False


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
