from hse_lms_harvest.text import common_lines, split_visible_text, stable_slug


def test_split_visible_text_collapses_repeated_consecutive_lines() -> None:
    assert split_visible_text(" A  B \nA  B\n\nC") == ["A B", "C"]


def test_common_lines_keeps_assignment_specific_lines() -> None:
    repeated = common_lines(
        [
            ["Smart LMS", "Срок сдачи: воскресенье"],
            ["Smart LMS", "Срок сдачи: воскресенье"],
            ["Smart LMS", "Срок сдачи: воскресенье"],
        ]
    )
    assert "Smart LMS" in repeated
    assert "Срок сдачи: воскресенье" not in repeated


def test_stable_slug_keeps_cyrillic() -> None:
    assert stable_slug("Парные сравнения / HSE") == "парные-сравнения-hse"
