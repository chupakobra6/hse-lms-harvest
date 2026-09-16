import asyncio
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from hse_lms_harvest.cli_args import build_parser
from hse_lms_harvest.coverage import capture_contract
from hse_lms_harvest.debug import DiagnosticRecorder, RunLogger, ScreenshotPolicy
from hse_lms_harvest.downloads import download_one_file
from hse_lms_harvest.file_cache import FileCache
from hse_lms_harvest.manifest import page_content_fingerprint
from hse_lms_harvest.model import Link, PageCapture
from hse_lms_harvest.page_cache import PageReuseIndex, maybe_reuse_page


@pytest.fixture
def local_source():
    state = SimpleNamespace(
        body=b"first",
        etag='"v1"',
        head_ok=True,
        counts=Counter(),
        bodies=0,
        content_type="application/pdf",
    )

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.respond(False)

        def do_GET(self):
            self.respond(True)

        def respond(self, include_body):
            state.counts[(self.command, self.path)] += 1
            is_page = self.path.startswith("/course/")
            etag = '"html"' if is_page else state.etag
            body = b"<h1>Course</h1>" if is_page else state.body
            status = 200
            if self.command == "HEAD" and not state.head_ok:
                status = 405
            elif include_body and etag and self.headers.get("If-None-Match") == etag:
                status = 304
            self.send_response(status)
            self.send_header("Content-Type", "text/html" if is_page else state.content_type)
            self.send_header("Content-Length", str(len(body)))
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            if include_body and status == 200:
                if not is_page:
                    state.bodies += 1
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def diagnostic(tmp_path):
    logger = RunLogger(tmp_path / "harvest.log")
    return logger, DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )


@pytest.mark.parametrize(
    "head_ok,etag,changed,expected_bodies",
    [
        (True, True, False, 1),
        (True, True, True, 2),
        (False, True, False, 1),
        (False, True, True, 2),
        (True, False, True, 2),
        (False, False, True, 2),
    ],
)
def test_files_require_freshness_not_same_length_or_type(
    tmp_path,
    local_source,
    head_ok,
    etag,
    changed,
    expected_bodies,
):
    source = local_source
    source.head_ok = head_ok
    if not etag:
        source.etag = ""
    cache = FileCache(tmp_path / "cache")
    logger, diagnostics = diagnostic(tmp_path)

    async def run():
        async with async_playwright() as playwright:
            context = SimpleNamespace(request=await playwright.request.new_context())
            try:
                for iteration in range(2):
                    if iteration and changed:
                        source.body = b"newer"  # Same length and content type.
                        if etag:
                            source.etag = '"v2"'
                    files = tmp_path / str(iteration) / "files"
                    result = await download_one_file(
                        context,
                        files,
                        Link("File", source.url + "/task.pdf", "file"),
                        files / "task.pdf",
                        logger,
                        False,
                        10000,
                        1000,
                        1000,
                        False,
                        cache,
                        diagnostics,
                    )
                    assert not result.startswith("ERROR")
                    assert (files / "task.pdf").read_bytes() == source.body
            finally:
                await context.request.dispose()

    asyncio.run(run())
    assert source.bodies == expected_bodies
    if not head_ok and etag and not changed:
        assert source.counts[("GET", "/task.pdf")] == 2  # Second GET is conditional 304.


def test_corrupted_hardlink_is_repaired_even_when_remote_validator_matches(tmp_path, local_source):
    cache = FileCache(tmp_path / "cache")
    logger, diagnostics = diagnostic(tmp_path)
    url = local_source.url + "/task.pdf"

    async def run():
        async with async_playwright() as playwright:
            context = SimpleNamespace(request=await playwright.request.new_context())
            try:
                for iteration in range(2):
                    files = tmp_path / str(iteration) / "files"
                    await download_one_file(
                        context,
                        files,
                        Link("File", url, "file"),
                        files / "task.pdf",
                        logger,
                        False,
                        10000,
                        1000,
                        1000,
                        False,
                        cache,
                        diagnostics,
                    )
                    assert (files / "task.pdf").read_bytes() == b"first"
                    if not iteration:
                        (files / "task.pdf").write_bytes(b"wrong")
            finally:
                await context.request.dispose()

    asyncio.run(run())
    assert local_source.bodies == 2


def test_page_reuse_validates_attachments_and_repairs_missing_files(tmp_path, local_source):
    source = local_source
    url = source.url + "/course/view.php?id=1"
    args = build_parser().parse_args(["harvest", "--url", url, "--download-files"])
    cache = FileCache(tmp_path / "cache")
    previous = PageCapture(
        1,
        url,
        url,
        "Course",
        "Course",
        ["Assignment"],
        links=[Link("File", source.url + "/task.pdf", "file")],
        capture_contract=capture_contract(args),
        source_metadata={"etag": '"html"'},
        downloaded_files=["files/missing.pdf sha256:123 source:" + source.url + "/task.pdf"],
    )
    previous.content_fingerprint = page_content_fingerprint(previous)
    reuse = PageReuseIndex(tmp_path / "old/manifest.json", tmp_path / "old", {url: previous})
    logger, diagnostics = diagnostic(tmp_path)

    async def run():
        async with async_playwright() as playwright:
            context = SimpleNamespace(request=await playwright.request.new_context())
            try:
                for iteration in range(3):
                    if iteration == 2:
                        source.body = b"newer"
                        source.etag = '"v2"'
                    output = tmp_path / f"new-{iteration}"
                    capture = await maybe_reuse_page(
                        context,
                        url,
                        1,
                        args,
                        reuse,
                        output,
                        logger,
                        diagnostics,
                        file_cache=cache,
                    )
                    assert capture is not None
                    assert capture.text_lines == ["Assignment"]
                    assert (output / "files/task.pdf").read_bytes() == source.body
                    assert "missing.pdf" not in " ".join(capture.downloaded_files)
                for invalidation in ("errors", "settings", "version", "content"):
                    previous.errors = ["snapshot failed"] if invalidation == "errors" else []
                    previous.capture_contract = capture_contract(args)
                    if invalidation == "settings":
                        previous.capture_contract["visit_action_pages"] = False
                    if invalidation == "version":
                        previous.capture_contract["capture_version"] = 0
                    if invalidation == "content":
                        previous.text_lines = ["corrupted local capture"]
                    assert (
                        await maybe_reuse_page(
                            context,
                            url,
                            1,
                            args,
                            reuse,
                            tmp_path / invalidation,
                            logger,
                            diagnostics,
                            file_cache=cache,
                        )
                        is None
                    )
            finally:
                await context.request.dispose()

    asyncio.run(run())
    assert source.bodies == 2  # Missing old artifact fetched once, then changed attachment once.
    assert source.counts[("GET", "/course/view.php?id=1")] == 0


def test_expected_attachment_returning_html_remains_unconfirmed(tmp_path, local_source):
    local_source.content_type = "text/html"
    logger, diagnostics = diagnostic(tmp_path)

    async def run():
        async with async_playwright() as playwright:
            request = await playwright.request.new_context()
            try:
                result = await download_one_file(
                    SimpleNamespace(request=request),
                    tmp_path / "files",
                    Link("PDF", local_source.url + "/task.pdf", "file"),
                    tmp_path / "files/task.pdf",
                    logger,
                    False,
                    10000,
                    1000,
                    1000,
                    False,
                    FileCache(tmp_path / "cache"),
                    diagnostics,
                )
                assert result.startswith("ERROR attachment returned HTML")
                assert diagnostics.errors[-1]["code"] == "file_download_html"
                assert not (tmp_path / "files/task.pdf").exists()
            finally:
                await request.dispose()

    asyncio.run(run())
