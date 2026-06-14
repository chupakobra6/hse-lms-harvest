import asyncio

from hse_lms_harvest.auth import HSE_SSO_LOGIN_SELECTORS, maybe_click_hse_sso_login


class FakeLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        self.lines.append(message)


class FakeLocator:
    def __init__(self, *, visible: bool = False) -> None:
        self.visible = visible
        self.clicked = False

    @property
    def first(self) -> "FakeLocator":
        return self

    async def count(self) -> int:
        return 1 if self.visible else 0

    async def is_visible(self, timeout: int) -> bool:
        return self.visible

    async def click(self, timeout: int) -> None:
        self.clicked = True


class FakePage:
    def __init__(self, visible_selector: str | None) -> None:
        self.visible_selector = visible_selector
        self.locators: dict[str, FakeLocator] = {}
        self.waited = False

    def locator(self, selector: str) -> FakeLocator:
        locator = FakeLocator(visible=selector == self.visible_selector)
        self.locators[selector] = locator
        return locator

    async def wait_for_load_state(self, state: str, timeout: int) -> None:
        self.waited = True


def test_maybe_click_hse_sso_login_clicks_elk_button() -> None:
    page = FakePage('button:has-text("Войти через ЕЛК")')
    logger = FakeLogger()

    clicked = asyncio.run(maybe_click_hse_sso_login(page, logger))

    assert clicked is True
    assert page.locators['button:has-text("Войти через ЕЛК")'].clicked is True
    assert page.waited is True
    assert logger.lines == ["auto-login clicked HSE SSO login"]


def test_maybe_click_hse_sso_login_ignores_page_without_elk_button() -> None:
    page = FakePage(None)
    logger = FakeLogger()

    clicked = asyncio.run(maybe_click_hse_sso_login(page, logger))

    assert clicked is False
    assert set(page.locators) == set(HSE_SSO_LOGIN_SELECTORS)
    assert logger.lines == []
