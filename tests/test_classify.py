from hse_lms_harvest.classify import (
    classify_link,
    is_ignored_capture_url,
    is_media_content_type,
    is_media_link,
    is_moodle_artifact_link,
    is_submission_file_link,
    looks_like_course_link,
)


def test_classifies_moodle_files() -> None:
    assert (
        classify_link("Матрицы принятия решений", "https://smart-lms.hse.ru/pluginfile.php/1/a.pdf")
        == "file"
    )
    assert (
        classify_link("Презентация MSF", "https://smart-lms.hse.ru/mod/resource/view.php?id=10")
        == "file"
    )


def test_does_not_queue_unsafe_course_links() -> None:
    start = "https://smart-lms.hse.ru/course/view.php?id=123"
    assert looks_like_course_link("https://smart-lms.hse.ru/mod/assign/view.php?id=1", start)
    assert looks_like_course_link("https://smart-lms.hse.ru/my/courses.php", start)
    assert not looks_like_course_link(
        "https://smart-lms.hse.ru/login/logout.php?sesskey=secret", start
    )


def test_user_files_are_not_treated_as_downloadable_course_files() -> None:
    assert classify_link("Личные файлы", "https://edu.hse.ru/user/files.php") == "unsafe"


def test_detects_personal_submission_files() -> None:
    assert is_submission_file_link(
        "https://edu.hse.ru/pluginfile.php/1/assignsubmission_file/submission_files/2/report.pdf"
    )
    assert not is_submission_file_link(
        "https://edu.hse.ru/pluginfile.php/1/mod_resource/content/0/report.pdf"
    )
    assert (
        classify_link(
            "Мой ответ",
            "https://edu.hse.ru/pluginfile.php/1/assignsubmission_file/submission_files/2/report.pdf",
        )
        == "unsafe"
    )


def test_detects_capture_ignored_lms_noise() -> None:
    assert is_ignored_capture_url("https://edu.hse.ru/grade/report/index.php?id=1")
    assert is_ignored_capture_url("https://edu.hse.ru/mod/glossary/showentry.php?eid=1")
    assert is_ignored_capture_url("https://edu.hse.ru/mod/quiz/review.php?attempt=1&cmid=2")
    assert is_ignored_capture_url("https://edu.hse.ru/mod/h5pactivity/report.php?a=1&userid=2")
    assert not is_ignored_capture_url(
        "https://edu.hse.ru/pluginfile.php/1/mod_resource/content/0/report.pdf"
    )


def test_detects_media_links() -> None:
    assert is_media_link("", "https://edu.hse.ru/pluginfile.php/video/zoom_0.mp4")
    assert is_media_link("Видео занятий", "https://edu.hse.ru/mod/url/view.php?id=1")
    assert not is_media_link("Матрицы принятия решений", "https://edu.hse.ru/file.xlsx")


def test_detects_media_content_types() -> None:
    assert is_media_content_type("video/mp4")
    assert is_media_content_type("audio/mpeg; charset=binary")
    assert not is_media_content_type("application/pdf")


def test_detects_moodle_conference_artifacts() -> None:
    assert is_moodle_artifact_link("chat.txt", "https://edu.hse.ru/mod/folder/chat.txt")
    assert is_moodle_artifact_link("playback.m3u", "https://edu.hse.ru/mod/folder/playback.m3u")
    assert is_moodle_artifact_link("zoom_0.mp4", "https://edu.hse.ru/mod/folder/zoom_0.mp4")
    assert not is_moodle_artifact_link(
        "Материалы семинара", "https://edu.hse.ru/mod/resource/view.php?id=1"
    )
