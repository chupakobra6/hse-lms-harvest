from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlparse

FILE_EXTENSIONS = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".md",
    ".mp3",
    ".mp4",
    ".odp",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".txt",
    ".webm",
    ".xls",
    ".xlsx",
    ".zip",
}

IMAGE_EXTENSIONS = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}

MEDIA_EXTENSIONS = {
    ".m3u",
    ".m4a",
    ".m4v",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}

MOODLE_ARTIFACT_FILENAMES = {
    "chat.txt",
    "playback.m3u",
}

MEDIA_TEXT_MARKERS = (
    "audio_only",
    "playback",
    "recording",
    "video",
    "zoom_",
    "аудио",
    "видео",
    "запись",
)

DOWNLOAD_TEXT_MARKERS = (
    "файл",
    "скач",
    "download",
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "zip",
    "архив",
    "презентац",
    "раздатк",
    "учебник",
    "материал",
)

UNSAFE_NAVIGATION_MARKERS = (
    "logout",
    "выход",
    "delete",
    "удалить",
    "cancel",
    "отменить",
    "unenrol",
    "отписаться",
)

STATE_CHANGE_MARKERS = (
    "отметить как выполн",
    "отмечено как выполн",
    "добавить ответ",
    "редактировать ответ",
    "сохранить",
    "отправить",
    "submit",
    "save",
    "mark as done",
    "add submission",
    "edit submission",
)

UNSAFE_PATH_PARTS = (
    "/user/",
    "/calendar/",
    "/message/",
    "/grade/report/",
)

IGNORED_CAPTURE_PATHS = {
    "/mod/glossary/showentry.php",
    "/mod/h5pactivity/report.php",
    "/mod/quiz/review.php",
}


def classify_link(text: str, url: str) -> str:
    lower_text = text.lower()
    lower_url = unquote(url.lower())
    path = urlparse(url).path.lower()
    ext = PurePosixPath(path).suffix

    if is_submission_file_link(url):
        return "unsafe"
    if path in IGNORED_CAPTURE_PATHS:
        return "unsafe"
    if any(part in lower_url for part in UNSAFE_PATH_PARTS):
        return "unsafe"
    if "/mod/resource/view.php" in path:
        return "file"
    if ext in FILE_EXTENSIONS:
        return "file"
    if "pluginfile.php" in lower_url or "/webservice/pluginfile.php" in lower_url:
        return "file"
    if "forcedownload=1" in lower_url or "download=1" in lower_url:
        return "file"
    if any(marker in lower_url or marker in lower_text for marker in UNSAFE_NAVIGATION_MARKERS):
        return "unsafe"
    if any(marker in lower_text for marker in STATE_CHANGE_MARKERS):
        return "action"
    if any(marker in lower_text for marker in DOWNLOAD_TEXT_MARKERS):
        return "maybe-file"
    return "page"


def looks_like_course_link(url: str, start_url: str) -> bool:
    parsed = urlparse(url)
    start = urlparse(start_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc != start.netloc:
        return False

    lower_url = unquote(url.lower())
    if any(marker in lower_url for marker in UNSAFE_NAVIGATION_MARKERS):
        return False
    if any(part in lower_url for part in UNSAFE_PATH_PARTS):
        return False
    return any(
        part in lower_url
        for part in (
            "/course/view.php",
            "/my/courses.php",
            "/mod/",
            "/pluginfile.php",
            "/webservice/pluginfile.php",
        )
    )


def filename_from_url(url: str, fallback: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("filename", "file", "name"):
        if query.get(key):
            return unquote(query[key][0])

    name = PurePosixPath(unquote(parsed.path)).name
    if name and name not in {"view.php", "pluginfile.php"}:
        return name
    return fallback


def is_media_link(text: str, url: str) -> bool:
    lower_text = text.lower()
    lower_url = unquote(url.lower())
    path = urlparse(url).path.lower()
    ext = PurePosixPath(path).suffix
    return ext in MEDIA_EXTENSIONS or any(
        marker in lower_text or marker in lower_url for marker in MEDIA_TEXT_MARKERS
    )


def is_image_link(text: str, url: str) -> bool:
    lower_text = text.lower()
    lower_url = unquote(url.lower())
    path = urlparse(lower_url).path
    ext = PurePosixPath(path).suffix
    return ext in IMAGE_EXTENSIONS or any(
        marker in lower_text or marker in lower_url
        for marker in ("image", "picture", "рисунок", "картинка", "фото")
    )


def is_moodle_artifact_link(text: str, url: str) -> bool:
    lower_text = text.lower()
    lower_url = unquote(url.lower())
    path_name = PurePosixPath(urlparse(lower_url).path).name
    return (
        path_name in MOODLE_ARTIFACT_FILENAMES
        or path_name.startswith(("audio_only", "zoom_"))
        or lower_text in MOODLE_ARTIFACT_FILENAMES
        or lower_text.startswith(("audio_only", "zoom_"))
    )


def is_submission_file_link(url: str) -> bool:
    lower_url = unquote(url.lower())
    return "/assignsubmission_file/submission_files/" in lower_url


def is_ignored_capture_url(url: str) -> bool:
    lower_url = unquote(url.lower())
    path = urlparse(lower_url).path
    return (
        is_submission_file_link(url)
        or path in IGNORED_CAPTURE_PATHS
        or any(part in lower_url for part in UNSAFE_PATH_PARTS)
        or any(marker in lower_url for marker in UNSAFE_NAVIGATION_MARKERS)
    )


def is_media_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith(("audio/", "video/")) or media_type in {
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
    }


def is_image_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith("image/")
