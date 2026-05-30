from hse_lms_harvest.cleanup import (
    cleanup_attachment_artifacts,
    cleanup_media,
)


def test_cleanup_artifacts_recurses_into_grouped_dumps_without_touching_cache(tmp_path) -> None:
    files_dir = tmp_path / "subject" / "edu.hse.ru-20260512-222250" / "files"
    files_dir.mkdir(parents=True)
    (files_dir.parent / "manifest.json").write_text("{}", encoding="utf-8")
    artifact = files_dir / "chat.txt"
    artifact.write_text("service chat", encoding="utf-8")
    useful = files_dir / "lecture.pdf"
    useful.write_bytes(b"%PDF")

    cache_files = tmp_path / "_file-cache" / "files"
    cache_files.mkdir(parents=True)
    cached_artifact = cache_files / "chat.txt"
    cached_artifact.write_text("cached", encoding="utf-8")

    removed, _ = cleanup_attachment_artifacts(tmp_path, dry_run=False)

    assert removed == 1
    assert not artifact.exists()
    assert useful.exists()
    assert cached_artifact.exists()


def test_cleanup_media_recurses_into_grouped_dumps_without_touching_cache(tmp_path) -> None:
    files_dir = tmp_path / "subject" / "edu.hse.ru-20260512-222250" / "files"
    files_dir.mkdir(parents=True)
    (files_dir.parent / "harvest.log").write_text("", encoding="utf-8")
    media = files_dir / "zoom_0.mp4"
    media.write_bytes(b"video")

    cache_files = tmp_path / "_file-cache" / "files"
    cache_files.mkdir(parents=True)
    cached_media = cache_files / "zoom_0.mp4"
    cached_media.write_bytes(b"video")

    removed, _ = cleanup_media(tmp_path, dry_run=False)

    assert removed == 1
    assert not media.exists()
    assert cached_media.exists()
