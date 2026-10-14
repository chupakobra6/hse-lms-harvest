"""Read-only export of the current subjects on club404.ru."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, async_playwright

from .credentials import load_default_username, load_password

BASE_URL = "https://club404.ru"
SUBJECT_PATH = "/course/2/subjects/"
ITEM_PATHS = {"task": "/course/2/tasks/", "material": "/course/2/materials/"}


@dataclass(frozen=True)
class Club404Item:
    kind: str
    url: str
    title: str
    subject: str
    deadline: str
    body: str
    links: list[dict[str, str]]
    description_missing: bool = False


def normalize_text(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def strip_relative_badges(value: str) -> str:
    # Linked assignment cards append a live countdown directly after HH:MM.
    return re.sub(
        r"(?<=\d{2}:\d{2})\d+\s+(?:сек\.|мин\.|час\.|дн\.|день|дня|дней)",
        "",
        value,
    )


def validate_item_url(url: str, kind: str) -> str:
    parsed = urlparse(urljoin(BASE_URL, url))
    if parsed.scheme != "https" or parsed.netloc != "club404.ru":
        raise ValueError("club404 item has an unexpected host")
    path = parsed.path
    prefix = ITEM_PATHS[kind]
    if not path.startswith(prefix) or not path[len(prefix) :] or "/" in path[len(prefix) :]:
        raise ValueError("club404 item is outside the configured course")
    return BASE_URL + path


def validate_subject(slug: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        raise ValueError("invalid club404 subject slug")
    return slug


async def open_page(page: Page, url: str, heading: str | None = None) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    if urlparse(page.url).netloc != "club404.ru" or "/login" in urlparse(page.url).path:
        raise RuntimeError("club404 login is required in its dedicated browser profile")
    await page.locator("main h1").first.wait_for(timeout=12_000)
    if heading and normalize_text(await page.locator("main h1").first.inner_text()) != heading:
        raise RuntimeError(f"unexpected club404 page at {url}")


async def ensure_login(page: Page, first_subject: str, env_file: Path) -> None:
    target = BASE_URL + SUBJECT_PATH + first_subject
    await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
    if urlparse(page.url).path != "/login":
        await open_page(page, target)
        return
    username = load_default_username("club404", env_file)
    password = load_password("club404", username, env_file)
    if not username or not password:
        raise RuntimeError("club404 login is required; configure its Keychain credential")
    await page.locator('input[name="login"]').fill(username)
    await page.locator('input[name="password"]').fill(password)
    await page.get_by_role("button", name="Войти").click()
    await page.wait_for_url(re.compile(r"https://club404\.ru/course/2(?:/.*)?$"), timeout=15_000)
    await open_page(page, target)


async def subject_links(page: Page, slug: str, kind: str) -> list[str]:
    label = "Задания" if kind == "task" else "Материалы"
    button = page.get_by_role("button", name=re.compile(rf"^{label}(?:\s+\d+)?$"))
    if await button.count() != 1:
        raise RuntimeError(f"club404 {slug}: missing {label} tab")
    expected_text = normalize_text(await button.inner_text())
    match = re.search(r"\b(\d+)\b", expected_text)
    if match is None:
        raise RuntimeError(f"club404 {slug}: missing {label} count")
    expected = int(match.group(1))
    await button.click()
    prefix = ITEM_PATHS[kind]
    links = await page.locator(f'main a[href^="{prefix}"]').evaluate_all(
        "els => els.map(el => el.getAttribute('href'))"
    )
    urls = sorted({validate_item_url(url, kind) for url in links})
    if len(urls) != expected:
        raise RuntimeError(f"club404 {slug}: {label} count {expected}, found {len(urls)} links")
    return urls


async def read_item(page: Page, url: str, kind: str, expected_subject: str) -> Club404Item:
    await open_page(page, url)
    subject_path = await page.locator(
        f'main nav[aria-label="Хлебные крошки"] a[href^="{SUBJECT_PATH}"]'
    ).first.get_attribute("href")
    if subject_path != SUBJECT_PATH + expected_subject:
        raise RuntimeError(f"club404 item belongs to another subject: {url}")
    title = normalize_text(await page.locator("main h1").first.inner_text())
    header = normalize_text(await page.locator("main header").first.inner_text())
    article = page.locator("main article")
    article_count = await article.count()
    description_missing = article_count == 0
    if description_missing:
        # A fully loaded task may contain only its card header and solutions.
        # Keep those source facts and mark the absent description explicitly.
        solutions = page.locator("main section").get_by_role(
            "heading", name=re.compile(r"^Решения(?:\s+\d+)?$")
        )
        if kind != "task" or await solutions.count() != 1:
            raise RuntimeError(f"club404 item has no unique article: {url}")
        body = strip_relative_badges(header)
    elif article_count == 1:
        body = strip_relative_badges(normalize_text(await article.text_content() or ""))
    else:
        raise RuntimeError(f"club404 item has no unique article: {url}")
    if not body:
        raise RuntimeError(f"club404 item is empty: {url}")
    match = re.search(r"Дедлайн\s+([^\n]+)", header)
    deadline = match.group(1).strip() if kind == "task" and match else ""
    raw_links = await page.locator(
        'main article a[href], main a[href^="/api/media/"]'
    ).evaluate_all("els => els.map(el => ({label: (el.textContent || '').trim(), url: el.href}))")
    links = sorted(
        {
            (strip_relative_badges(normalize_text(link["label"])), link["url"].split("#", 1)[0])
            for link in raw_links
            if link["url"].startswith(("https://", "http://"))
        }
    )
    return Club404Item(
        kind=kind,
        url=url,
        title=title,
        subject=expected_subject,
        deadline=deadline,
        body=body,
        links=[{"label": label, "url": link_url} for label, link_url in links],
        description_missing=description_missing,
    )


async def export(
    profile: Path,
    subjects: list[str],
    out: Path,
    *,
    headless: bool,
    env_file: Path = Path(".env"),
) -> dict:
    if not subjects or len(subjects) != len(set(subjects)):
        raise ValueError("club404 needs unique configured subject slugs")
    subjects = [validate_subject(slug) for slug in subjects]
    profile = profile.expanduser().resolve()
    out = out.expanduser().resolve()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=headless,
            viewport={"width": 1440, "height": 1000},
            accept_downloads=False,
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await ensure_login(page, subjects[0], env_file)
            links_by_url: dict[str, tuple[str, str]] = {}
            counts = {"subjects": 0, "tasks": 0, "materials": 0}
            for slug in subjects:
                await open_page(page, BASE_URL + SUBJECT_PATH + slug)
                counts["subjects"] += 1
                for kind in ("task", "material"):
                    links = await subject_links(page, slug, kind)
                    counts["tasks" if kind == "task" else "materials"] += len(links)
                    for url in links:
                        if url in links_by_url and links_by_url[url] != (kind, slug):
                            raise RuntimeError(f"club404 item has conflicting ownership: {url}")
                        links_by_url[url] = (kind, slug)
            items = []
            for url, (kind, slug) in sorted(links_by_url.items()):
                items.append(asdict(await read_item(page, url, kind, slug)))
            result = {
                "version": 1,
                "base_url": BASE_URL,
                "subjects": subjects,
                "counts": counts,
                "items": items,
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            temp = out.with_name(out.name + ".tmp")
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            temp.replace(out)
            return result
        finally:
            await context.close()


def run(
    profile: Path,
    subjects: list[str],
    out: Path,
    *,
    headless: bool = True,
    env_file: Path = Path(".env"),
) -> dict:
    return asyncio.run(export(profile, subjects, out, headless=headless, env_file=env_file))
