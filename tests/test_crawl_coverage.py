import asyncio
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from hse_lms_harvest.cli import build_parser, run_harvest
from hse_lms_harvest.manifest import latest_manifest_path, render_dump_from_manifest


@pytest.fixture
def course_source():
    state = SimpleNamespace(links=[1, 2], failed=set(), validators=True, counts=Counter())

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.respond(False)

        def do_GET(self):
            self.respond(True)

        def respond(self, body):
            state.counts[(self.command, self.path)] += 1
            root = self.path.startswith("/course/")
            status = 503 if self.path in state.failed else 200
            html = (
                "<h1>Course</h1>"
                + "".join(
                    f'<a href="/mod/page/view.php?id={item}">Assignment {item}</a>'
                    for item in state.links
                )
                if root
                else "<h1>Assignment</h1><p>Required work</p>"
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html.encode())))
            if state.validators:
                self.send_header("ETag", '"' + str(hash(html)) + '"')
            self.end_headers()
            if body:
                self.wfile.write(html.encode())

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


def args_for(tmp_path, source, *extra):
    return build_parser().parse_args(
        [
            "harvest",
            "--url",
            source.url + "/course/view.php?id=1",
            "--profile",
            str(tmp_path / "profile"),
            "--out",
            str(tmp_path / "dumps"),
            "--file-cache-dir",
            str(tmp_path / "cache"),
            "--headless",
            "--course-network-idle-timeout-ms",
            "0",
            "--screenshot-mode",
            "off",
            "--debug-dump-mode",
            "off",
            "--skip-action-pages",
            *extra,
        ]
    )


def harvest(tmp_path, source, *extra):
    exit_code = asyncio.run(run_harvest(args_for(tmp_path, source, *extra)))
    path = latest_manifest_path(tmp_path / "dumps")
    return exit_code, path, json.loads(path.read_text())


@pytest.mark.parametrize("validators", [True, False])
def test_partial_resume_progresses_and_validates_the_full_current_scope(
    tmp_path, course_source, validators
):
    source = course_source
    source.validators = validators
    code, path, partial = harvest(tmp_path, source, "--max-pages", "1")
    assert code == 1
    coverage = partial["coverage"]
    assert coverage["status"] == "partial"
    assert coverage["stop_reason"] == "max_pages"
    assert coverage["confirmed_urls"] == [source.url + "/course/view.php?id=1"]
    assert len(coverage["remaining_urls"]) == 2
    code, path, second = harvest(tmp_path, source, "--max-pages", "1", "--resume-dump", str(path))
    assert code == 1
    assert len(second["coverage"]["confirmed_urls"]) == 2
    code, _, complete = harvest(tmp_path, source, "--max-pages", "1", "--resume-latest")
    assert code == 0
    assert complete["coverage"]["status"] == "complete"
    assert complete["coverage"]["remaining_urls"] == []
    assert len(complete["pages"]) == 3
    assert complete["coverage"]["scope"] == partial["coverage"]["scope"]
    if validators:
        assert (
            sum(
                value
                for (method, path), value in source.counts.items()
                if method == "GET" and path.startswith(("/course/", "/mod/"))
            )
            == 3
        )
        assert complete["page_cache"]["reused_pages"] == 2
    else:
        assert complete["page_cache"]["reused_pages"] == 0


def test_error_is_partial_then_retry_completes_and_new_crawl_sees_real_removal(
    tmp_path, course_source
):
    source = course_source
    source.failed = {"/mod/page/view.php?id=2"}
    code, path, partial = harvest(tmp_path, source)
    assert code == 1
    assert partial["coverage"]["stop_reason"] == "capture_error"
    assert source.url + "/mod/page/view.php?id=2" not in partial["coverage"]["confirmed_urls"]
    source.failed.clear()
    code, _, complete = harvest(tmp_path, source, "--resume-dump", str(path), "--max-pages", "1")
    assert code == 0
    assert len(complete["coverage"]["confirmed_urls"]) == 3
    source.links = [1]
    code, _, removed = harvest(tmp_path, source)
    assert code == 0
    assert len(removed["pages"]) == 2
    assert source.url + "/mod/page/view.php?id=2" not in removed["coverage"]["confirmed_urls"]


def test_interruption_checkpoint_can_continue_and_migration_does_not_certify_old_dump(
    tmp_path, course_source, monkeypatch
):
    import hse_lms_harvest.cli as cli

    original = cli.capture_page
    count = 0

    async def interrupt_once(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("fixture browser interruption")
        return await original(*args, **kwargs)

    monkeypatch.setattr(cli, "capture_page", interrupt_once)
    code, path, partial = harvest(tmp_path, course_source)
    assert code == 1
    assert partial["coverage"]["stop_reason"] == "interrupted"
    assert len(partial["coverage"]["remaining_urls"]) == 2
    monkeypatch.setattr(cli, "capture_page", original)
    code, _, complete = harvest(tmp_path, course_source, "--resume-dump", str(path))
    assert code == 0
    assert len(complete["pages"]) == 3
    before = partial["coverage"]
    render_dump_from_manifest(path)
    assert json.loads(path.read_text())["coverage"] == before
    partial.pop("coverage")
    path.write_text(json.dumps(partial))
    render_dump_from_manifest(path)
    assert "coverage" not in json.loads(path.read_text())


def test_trust_and_changed_scope_cannot_silently_certify_or_resume(tmp_path, course_source):
    code, path, _ = harvest(tmp_path, course_source, "--max-pages", "1")
    assert code == 1
    with pytest.raises(RuntimeError, match="same capture scope"):
        harvest(tmp_path, course_source, "--resume-dump", str(path), "--download-files")
    code, _, changed = harvest(
        tmp_path, course_source, "--resume-latest", "--download-files", "--max-pages", "1"
    )
    assert code == 1
    assert changed["resumed_from"] == ""
    assert len(changed["pages"]) == 1
    code, _, unverified = harvest(tmp_path, course_source, "--page-cache", "trust")
    assert code == 1
    assert unverified["coverage"]["status"] == "unknown"
    assert unverified["coverage"]["confirmed_urls"] == []


def test_resume_moves_past_persistently_broken_first_link(tmp_path, course_source):
    source = course_source
    source.failed = {"/mod/page/view.php?id=1"}
    _, _, first = harvest(tmp_path, source, "--max-pages", "1")
    assert len(first["pages"]) == 1
    _, _, failed = harvest(tmp_path, source, "--max-pages", "1", "--resume-latest")
    assert failed["coverage"]["remaining_urls"] == [
        source.url + "/mod/page/view.php?id=2",
        source.url + "/mod/page/view.php?id=1",
    ]
    code, _, progressed = harvest(tmp_path, source, "--max-pages", "1", "--resume-latest")
    assert code == 1
    assert source.url + "/mod/page/view.php?id=2" in progressed["coverage"]["confirmed_urls"]
    assert source.url + "/mod/page/view.php?id=1" not in progressed["coverage"]["confirmed_urls"]
    source.failed.clear()
    code, _, complete = harvest(tmp_path, source, "--max-pages", "1", "--resume-latest")
    assert code == 0
    assert len(complete["coverage"]["confirmed_urls"]) == 3
