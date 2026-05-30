import asyncio
import json

from hse_lms_harvest.debug import DiagnosticRecorder, RunLogger, ScreenshotPolicy


def test_diagnostic_recorder_writes_events_and_error_index(tmp_path) -> None:
    logger = RunLogger(tmp_path / "harvest.log")
    recorder = DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )

    recorder.warning(
        "head_failed",
        "HEAD failed",
        url="https://edu.hse.ru/file.php?sesskey=secret&id=1",
        details={"source": "https://edu.hse.ru/file.php?sesskey=secret&id=1"},
    )

    events = (tmp_path / "debug" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 1
    event = json.loads(events[0])
    assert event["level"] == "warning"
    assert "secret" not in json.dumps(event, ensure_ascii=False)
    assert "%5BREDACTED%5D" in json.dumps(event, ensure_ascii=False)


def test_diagnostic_recorder_redacts_urls_inside_error_messages(tmp_path) -> None:
    logger = RunLogger(tmp_path / "harvest.log")
    recorder = DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )

    asyncio.run(
        recorder.error(
            "navigation_failed",
            "Navigation failed",
            exc=RuntimeError("Failed at https://edu.hse.ru/login/logout.php?sesskey=secret&id=1"),
        )
    )

    errors = json.loads((tmp_path / "debug" / "errors.json").read_text(encoding="utf-8"))
    dumped = json.dumps(errors, ensure_ascii=False)
    assert "secret" not in dumped
    assert "%5BREDACTED%5D" in dumped


def test_diagnostic_recorder_redacts_sensitive_headers_inside_error_messages(tmp_path) -> None:
    logger = RunLogger(tmp_path / "harvest.log")
    recorder = DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )

    asyncio.run(
        recorder.error(
            "head_failed",
            "HEAD failed",
            exc=RuntimeError(
                "Call log:\n  - cookie: MoodleSession=supersecret\n  - authorization: Bearer token"
            ),
        )
    )

    dumped = (tmp_path / "debug" / "errors.json").read_text(encoding="utf-8")
    assert "supersecret" not in dumped
    assert "Bearer token" not in dumped
    assert "cookie: [REDACTED]" in dumped
    assert "authorization: [REDACTED]" in dumped


def test_diagnostic_recorder_writes_error_bundle_without_page(tmp_path) -> None:
    logger = RunLogger(tmp_path / "harvest.log")
    recorder = DiagnosticRecorder(
        tmp_path / "debug",
        logger,
        ScreenshotPolicy(mode="off"),
        dump_mode="off",
    )

    asyncio.run(
        recorder.error(
            "download_failed",
            "Download failed",
            url="https://edu.hse.ru/pluginfile.php/1/a.pdf",
            exc=RuntimeError("boom"),
            details={"label": "A"},
        )
    )

    errors = json.loads((tmp_path / "debug" / "errors.json").read_text(encoding="utf-8"))
    assert errors[0]["id"] == "0001-download-failed"
    assert errors[0]["exception"] == {"type": "RuntimeError", "message": "boom"}
    error_bundle = tmp_path / "debug" / "errors" / "0001-download-failed" / "error.json"
    assert error_bundle.is_file()
    error_bundle_data = json.loads(error_bundle.read_text(encoding="utf-8"))
    assert error_bundle_data["artifacts"]["error"] == "errors/0001-download-failed/error.json"
    errors_md = (tmp_path / "debug" / "errors.md").read_text(encoding="utf-8")
    assert "0001-download-failed" in errors_md
    assert "Download failed" in errors_md
